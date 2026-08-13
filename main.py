import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from cdp.x402 import create_facilitator_config
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from x402.extensions.bazaar import (
    OutputConfig,
    bazaar_resource_server_extension,
    declare_discovery_extension,
)
from x402.http import HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.schemas import Network
from x402.server import x402ResourceServer

from evm_simulator import EVMTokenSimulator

# Load .env for local runs; platform-injected env vars take precedence.
load_dotenv()

# Setup Structured Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

# Load configuration from environment variables or defaults
BASE_RPC_URLS = os.getenv("BASE_RPC_URLS", os.getenv("BASE_RPC_URL", "https://mainnet.base.org"))
PAYMENT_WALLET_ADDRESS = os.getenv("PAYMENT_WALLET_ADDRESS", "0x742d35Cc6634C0532925a3b844Bc454e4438f44e")
USDC_PRICE = float(os.getenv("USDC_PRICE", "0.05"))
USDC_BASE_CONTRACT = os.getenv(
    "USDC_BASE_CONTRACT",
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
)
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
# CAIP-2 network id for Base mainnet (required by x402 v2 / CDP Facilitator)
EVM_NETWORK: Network = "eip155:8453"
# Price string for PaymentOption (USDC, 6 decimals). Keep >= $0.001 for Bazaar.
X402_PRICE = f"${USDC_PRICE}"
# Atomic USDC units for docs / health (0.05 USDC -> 50000)
USDC_AMOUNT_ATOMIC = str(int(Decimal(str(USDC_PRICE)) * Decimal(1_000_000)))

# Public HTTPS origin for x402 resource.url (Railway terminates TLS at the edge,
# so request.url is often http://... which CDP Bazaar rejects).
_public_host = (
    os.getenv("PUBLIC_BASE_URL")
    or os.getenv("RAILWAY_PUBLIC_DOMAIN")
    or os.getenv("RAILWAY_STATIC_URL")
    or ""
).strip().rstrip("/")
if _public_host and not _public_host.startswith(("http://", "https://")):
    _public_host = f"https://{_public_host}"
PUBLIC_BASE_URL = _public_host or "https://a2a-verifier-production.up.railway.app"
VERIFY_RESOURCE_URL = f"{PUBLIC_BASE_URL}/verify"

# Multi-Worker Startup Safety Warning
workers = int(os.getenv("WEB_CONCURRENCY", os.getenv("WORKERS", "1")))
if workers > 1:
    logger.critical(
        "CRITICAL WARNING: Prefer --workers 1 (WEB_CONCURRENCY=1). "
        "Scale via replicas, not multi-worker processes."
    )

if not os.getenv("CDP_API_KEY_ID") or not os.getenv("CDP_API_KEY_SECRET"):
    raise RuntimeError(
        "CDP_API_KEY_ID and CDP_API_KEY_SECRET are required. "
        "The CDP Facilitator rejects unauthenticated /supported, /verify, and /settle. "
        "Create keys at https://portal.cdp.coinbase.com and set them in the environment."
    )

# Initialize Core Services
evm_simulator = EVMTokenSimulator(rpc_urls=BASE_RPC_URLS)

# --- x402 v2 resource server (CDP Facilitator + Bazaar) ---
# create_facilitator_config() targets https://api.cdp.coinbase.com/platform/v2/x402
# (verify/settle at .../verify and .../settle). Only CDP-settled endpoints are
# cataloged in the CDP Bazaar.
facilitator = HTTPFacilitatorClient(create_facilitator_config())
x402_server = x402ResourceServer(facilitator)
x402_server.register(EVM_NETWORK, ExactEvmServerScheme())
x402_server.register_extension(bazaar_resource_server_extension)

# Representative probe input: Bazaar uses extensions.bazaar.info.input when probing.
_EXAMPLE_TOKEN = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
_EXAMPLE_VERIFY_RESPONSE = {
    "schema_version": "1.1",
    "token": _EXAMPLE_TOKEN.lower(),
    "chain": "base-mainnet",
    "simulation_results": {
        "simulated_buy_success": True,
        "simulated_sell_success": True,
        "effective_buy_tax_pct": 0.0,
        "effective_sell_tax_pct": 0.0,
        "is_honeypot": False,
        "is_high_tax": False,
        "safety_score": 85,
        "unknown_storage_layout": False,
    },
    "contract_analysis": {
        "has_bytecode": True,
        "is_proxy": False,
        "implementation_address": None,
        "owner_address": None,
        "contract_renounced": True,
    },
    "liquidity_metrics": {"liquidity_usd": 1_000_000.0},
    "recommendation": "SAFE",
}

x402_routes: dict[str, RouteConfig] = {
    "GET /verify": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=PAYMENT_WALLET_ADDRESS,
                price=X402_PRICE,
                network=EVM_NETWORK,
                max_timeout_seconds=300,
            )
        ],
        resource=VERIFY_RESOURCE_URL,
        mime_type="application/json",
        description=(
            "Base L2 EVM token safety verifier: simulates buy/sell, transfer taxes, "
            "honeypot checks, proxy resolution, and liquidity. "
            "Pass ?token=<Base ERC-20 address>."
        ),
        extensions=declare_discovery_extension(
            input={"token": _EXAMPLE_TOKEN},
            input_schema={
                "properties": {
                    "token": {
                        "type": "string",
                        "description": "Base L2 ERC-20 token contract address (0x...)",
                        "pattern": "^0x[0-9a-fA-F]{40}$",
                    }
                },
                "required": ["token"],
            },
            output=OutputConfig(
                example=_EXAMPLE_VERIFY_RESPONSE,
                schema={
                    "properties": {
                        "schema_version": {"type": "string"},
                        "token": {"type": "string"},
                        "chain": {"type": "string"},
                        "simulation_results": {"type": "object"},
                        "contract_analysis": {"type": "object"},
                        "liquidity_metrics": {"type": "object"},
                        "recommendation": {"type": "string"},
                    },
                    "required": [
                        "token",
                        "simulation_results",
                        "contract_analysis",
                        "liquidity_metrics",
                        "recommendation",
                    ],
                },
            ),
        ),
    ),
}


# Lifespan Handler for Connection Cleanup
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    logger.info("Shutting down A2A Verifier service... closing HTTP clients.")
    await evm_simulator.close()
    await facilitator.aclose()

# Initialize FastAPI App with Lifespan Context Manager
app = FastAPI(
    title="Base L2 EVM Simulation & Token Verifier (x402 Agent)",
    description=(
        "A high-performance Agent-to-Agent (A2A) micro-service providing EVM transaction "
        "simulation, honeypot analysis, proxy resolution, and tax verification via the "
        "x402 v2 payment protocol (CDP Facilitator)."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Enable CORS for cross-agent web queries
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# x402 v2 payment middleware (PAYMENT-REQUIRED / PAYMENT-SIGNATURE).
# Added after CORS so it wraps protected routes; free paths pass through.
app.add_middleware(PaymentMiddlewareASGI, routes=x402_routes, server=x402_server)

# --- IP-BASED RATE LIMITING MIDDLEWARE ---
# Tracks { ip: {"unpaid": [timestamps], "paid": [timestamps]} }
_rate_limit_store: dict[str, dict[str, list[float]]] = {}
_metrics_data = {
    "requests_total": 0,
    "payments_verified_total": 0,
    "honeypots_detected_total": 0,
    "rpc_errors_total": 0,
    "latency_sum_seconds": 0.0,
    "latency_count": 0
}
# Prometheus histogram buckets for simulation_latency_seconds.
# Upper bound 3.0 matches the SLA timeout; counts are cumulative per bucket.
_LATENCY_BUCKET_BOUNDS = [0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0]
_latency_bucket_counts = [0] * len(_LATENCY_BUCKET_BOUNDS)


def _observe_latency(elapsed: float) -> None:
    _metrics_data["latency_sum_seconds"] += elapsed
    _metrics_data["latency_count"] += 1
    for i, bound in enumerate(_LATENCY_BUCKET_BOUNDS):
        if elapsed <= bound:
            _latency_bucket_counts[i] += 1


def _has_payment_header(request: Request) -> bool:
    """True if the client attached an x402 v2 (or legacy v1) payment proof header."""
    return bool(
        request.headers.get("PAYMENT-SIGNATURE")
        or request.headers.get("X-PAYMENT")
        or request.headers.get("X-PAYMENT-PROOF")  # legacy custom header
    )


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    # Exempt /health, /schema, /metrics, and OpenAPI docs from rate limits
    if path in ("/health", "/schema", "/metrics", "/docs", "/redoc", "/openapi.json"):
        return await call_next(request)

    client_ip = request.client.host if request.client else "127.0.0.1"
    now = time.time()

    if client_ip not in _rate_limit_store:
        _rate_limit_store[client_ip] = {"unpaid": [], "paid": []}

    ip_record = _rate_limit_store[client_ip]
    # Prune timestamps older than 60s
    ip_record["unpaid"] = [t for t in ip_record["unpaid"] if now - t < 60.0]
    ip_record["paid"] = [t for t in ip_record["paid"] if now - t < 60.0]

    # FIX 2: Clear lists instead of delete-then-recreate to avoid race conditions under concurrency
    if not ip_record["unpaid"] and not ip_record["paid"]:
        ip_record["unpaid"].clear()
        ip_record["paid"].clear()

    has_proof = _has_payment_header(request)

    if has_proof:
        # Limit: 30 requests/minute for paid requests
        if len(ip_record["paid"]) >= 30:
            logger.warning(f"Rate limit exceeded (paid) for IP: {client_ip}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Maximum 30 paid requests per minute."},
                headers={"Retry-After": "60"}
            )
        ip_record["paid"].append(now)
    else:
        # Limit: 10 requests/minute for unpaid (402) requests
        if len(ip_record["unpaid"]) >= 10:
            logger.warning(f"Rate limit exceeded (unpaid) for IP: {client_ip}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Maximum 10 unpaid requests per minute."},
                headers={"Retry-After": "60"}
            )
        ip_record["unpaid"].append(now)

    return await call_next(request)


# --- Pydantic Data Models ---
class EVMSimulationResults(BaseModel):
    simulated_buy_success: bool
    simulated_sell_success: bool
    effective_buy_tax_pct: float
    effective_sell_tax_pct: float
    is_honeypot: bool
    is_high_tax: bool
    safety_score: int
    unknown_storage_layout: bool | None = False
    score_breakdown: dict[str, Any] | None = None

class ContractAnalysisSchema(BaseModel):
    has_bytecode: bool
    is_proxy: bool
    implementation_address: str | None = None
    owner_address: str | None = None
    contract_renounced: bool

class VerifiedResponsePayload(BaseModel):
    schema_version: str = "1.1"
    token: str
    chain: str = "base-mainnet"
    simulation_results: EVMSimulationResults
    contract_analysis: ContractAnalysisSchema
    liquidity_metrics: dict[str, Any]
    recommendation: str


@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint for agent monitors."""
    return {
        "status": "online",
        "service": "Base L2 EVM Simulation Agent",
        "network": "base-mainnet",
        "x402_version": 2,
        "x402_price": f"{USDC_PRICE} USDC",
        "x402_amount_atomic": USDC_AMOUNT_ATOMIC,
        "x402_asset": USDC_BASE_CONTRACT,
        "x402_network": EVM_NETWORK,
        "pay_to": PAYMENT_WALLET_ADDRESS,
        "facilitator": "https://api.cdp.coinbase.com/platform/v2/x402",
        "production_mode": PRODUCTION_MODE,
    }


@app.get("/schema", tags=["Metadata"])
async def get_agent_schema():
    """Returns machine-readable JSON schema for autonomous agent discovery."""
    return {
        "agent_name": "Base L2 EVM Token Verifier",
        "protocol": "x402",
        "x402_version": 2,
        "version": "2.0.0",
        "schema_version": "1.1",
        "endpoint": "/verify?token={token_address}",
        "method": "GET",
        "payment": {
            "scheme": "exact",
            "token": "USDC",
            "asset": USDC_BASE_CONTRACT,
            "amount": USDC_AMOUNT_ATOMIC,
            "price": X402_PRICE,
            "network": EVM_NETWORK,
            "pay_to": PAYMENT_WALLET_ADDRESS,
            "max_timeout_seconds": 300,
            "facilitator": "https://api.cdp.coinbase.com/platform/v2/x402",
            "headers": {
                "challenge": "PAYMENT-REQUIRED (base64 JSON envelope on HTTP 402)",
                "proof": "PAYMENT-SIGNATURE (base64 payment payload on retry)",
            },
        },
        "capabilities": [
            "EVM Buy/Sell Transaction Simulation",
            "Effective Transfer Tax Calculation",
            "Honeypot & High Tax Detection",
            "On-Chain Factory DEX Detection",
            "Contract Bytecode Verification",
            "EIP-1967 Proxy Resolution",
            "Multi-Selector Ownership Checking",
            "CDP Facilitator verify+settle (Bazaar-indexable)",
        ]
    }


@app.get("/metrics", tags=["System"])
async def get_metrics():
    """Returns Prometheus-style metrics."""
    c_hits, c_misses = evm_simulator.get_cache_stats()

    bucket_lines = "".join(
        f'simulation_latency_seconds_bucket{{le="{bound}"}} {count}\n'
        for bound, count in zip(_LATENCY_BUCKET_BOUNDS, _latency_bucket_counts)
    )
    metrics_text = (
        f"# HELP requests_total Total HTTP requests processed\n"
        f"# TYPE requests_total counter\n"
        f"requests_total {_metrics_data['requests_total']}\n\n"
        f"# HELP simulation_latency_seconds EVM simulation request latency\n"
        f"# TYPE simulation_latency_seconds histogram\n"
        f"{bucket_lines}"
        f'simulation_latency_seconds_bucket{{le="+Inf"}} {_metrics_data["latency_count"]}\n'
        f"simulation_latency_seconds_sum {_metrics_data['latency_sum_seconds']:.4f}\n"
        f"simulation_latency_seconds_count {_metrics_data['latency_count']}\n\n"
        f"# HELP rpc_errors_total Total RPC errors encountered\n"
        f"# TYPE rpc_errors_total counter\n"
        f"rpc_errors_total {evm_simulator.rpc_errors_total}\n\n"
        f"# HELP payments_verified_total Total x402 payments verified via CDP Facilitator\n"
        f"# TYPE payments_verified_total counter\n"
        f"payments_verified_total {_metrics_data['payments_verified_total']}\n\n"
        f"# HELP honeypots_detected_total Total honeypots detected\n"
        f"# TYPE honeypots_detected_total counter\n"
        f"honeypots_detected_total {_metrics_data['honeypots_detected_total']}\n\n"
        f"# HELP cache_hits_total Total simulation LRU cache hits\n"
        f"# TYPE cache_hits_total counter\n"
        f"cache_hits_total {c_hits}\n\n"
        f"# HELP cache_misses_total Total simulation LRU cache misses\n"
        f"# TYPE cache_misses_total counter\n"
        f"cache_misses_total {c_misses}\n"
    )
    return PlainTextResponse(content=metrics_text, media_type="plain")


@app.get(
    "/verify",
    response_model=VerifiedResponsePayload,
    responses={
        402: {"description": "Payment Required via x402 v2 (see PAYMENT-REQUIRED header)"},
        400: {"description": "Invalid parameters"},
        429: {"description": "Rate limit exceeded"},
        504: {"description": "RPC / Simulation Gateway Timeout"}
    },
    tags=["Verification Service"]
)
async def verify_token(
    request: Request,
    token: str = Query(..., description="The Base L2 token contract address (e.g. 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913)")
):
    """
    Main A2A Verification Endpoint (x402 v2).

    Unpaid requests are intercepted by PaymentMiddlewareASGI and receive HTTP 402 with a
    base64 ``PAYMENT-REQUIRED`` header (x402Version=2 + extensions.bazaar).

    Paid requests attach ``PAYMENT-SIGNATURE``; the CDP Facilitator verifies+settles, then
    this handler returns the full EVM simulation.
    """
    start_time = time.perf_counter()
    _metrics_data["requests_total"] += 1
    client_ip = request.client.host if request.client else "127.0.0.1"

    # Payment middleware only forwards here after a valid payment (or if the route
    # were free). Count verified payments when the middleware attached state.
    if getattr(request.state, "payment_payload", None) is not None:
        _metrics_data["payments_verified_total"] += 1

    # --- STRICT INPUT VALIDATION ---
    raw_token = token.strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", raw_token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token address. Must be 42-character hex."
        )

    token_address = raw_token.lower()

    # --- FULL EVM SIMULATION EXECUTION (PAYMENT VERIFIED BY MIDDLEWARE) ---
    try:
        simulation_data = await asyncio.wait_for(
            evm_simulator.analyze_token(token_address),
            timeout=3.0
        )

        elapsed = time.perf_counter() - start_time
        _observe_latency(elapsed)

        if simulation_data.get("simulation_results", {}).get("is_honeypot"):
            _metrics_data["honeypots_detected_total"] += 1

        logger.info(
            f"Verified simulation completed for token={token_address} "
            f"in {elapsed:.4f}s IP={client_ip}"
        )
        return simulation_data

    except asyncio.TimeoutError:
        _metrics_data["rpc_errors_total"] += 1
        logger.error(f"EVM simulation gateway timeout for token={token_address} after 3.0s")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="EVM Simulation Gateway Timeout. Analysis took longer than 3.0 seconds."
        )
    except Exception as e:
        _metrics_data["rpc_errors_total"] += 1
        logger.error(f"EVM simulation exception for token={token_address}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"EVM Simulation Failure: {e!s}"
        )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, workers=1, reload=True)
