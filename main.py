import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from evm_simulator import EVMTokenSimulator
from x402_verifier import X402PaymentVerifier

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
ALLOW_TEST_PROOFS = os.getenv("ALLOW_TEST_PAYMENT_PROOFS", "false").lower() == "true"
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

# Multi-Worker SQLite Startup Safety Warning (Audit Fix 2.5)
workers = int(os.getenv("WEB_CONCURRENCY", os.getenv("WORKERS", "1")))
if workers > 1:
    logger.critical("CRITICAL WARNING: SQLite replay cache is NOT safe with multiple workers (workers > 1). Use --workers 1 or configure Redis.")

# Initialize Core Services
payment_verifier = X402PaymentVerifier(
    rpc_url=BASE_RPC_URLS,
    pay_to_wallet=PAYMENT_WALLET_ADDRESS,
    required_usdc=USDC_PRICE,
    allow_test_proofs=ALLOW_TEST_PROOFS,
    production_mode=PRODUCTION_MODE,
    db_path=os.getenv("PROOF_DB_PATH", "used_proofs.db")
)
evm_simulator = EVMTokenSimulator(rpc_urls=BASE_RPC_URLS)

# Lifespan Handler for Connection Cleanup (Audit Fix 2.3)
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    logger.info("Shutting down A2A Verifier service... closing HTTP clients.")
    await evm_simulator.close()
    await payment_verifier.close()

# Initialize FastAPI App with Lifespan Context Manager
app = FastAPI(
    title="Base L2 EVM Simulation & Token Verifier (x402 Agent)",
    description="A high-performance Agent-to-Agent (A2A) micro-service providing EVM transaction simulation, honeypot analysis, proxy resolution, and tax verification via the x402 payment protocol.",
    version="1.1.0",
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

    has_proof = bool(request.headers.get("X-PAYMENT-PROOF"))

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


# --- Pydantic Data Models (Schema 1.1) ---
class FreePreviewSchema(BaseModel):
    token: str
    available_metrics: list[str] = [
        "simulated_buy_success",
        "simulated_sell_success",
        "effective_buy_tax_pct",
        "effective_sell_tax_pct",
        "is_honeypot",
        "is_high_tax",
        "safety_score",
        "liquidity_usd"
    ]

class HTTP402PaymentRequiredResponse(BaseModel):
    status_code: int = 402
    error: str = "Payment Required"
    x402_price: str = f"{USDC_PRICE} USDC"
    pay_to: str = PAYMENT_WALLET_ADDRESS
    network: str = "base-mainnet"
    free_sample_preview: FreePreviewSchema

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
        "x402_price": f"{USDC_PRICE} USDC",
        "pay_to": PAYMENT_WALLET_ADDRESS,
        "production_mode": PRODUCTION_MODE
    }


@app.get("/schema", tags=["Metadata"])
async def get_agent_schema():
    """Returns machine-readable JSON schema for autonomous agent discovery."""
    return {
        "agent_name": "Base L2 EVM Token Verifier",
        "protocol": "x402",
        "version": "1.1.0",
        "schema_version": "1.1",
        "endpoint": "/verify?token={token_address}",
        "method": "GET",
        "payment": {
            "token": "USDC",
            "amount": USDC_PRICE,
            "chain": "Base L2",
            "destination": PAYMENT_WALLET_ADDRESS
        },
        "capabilities": [
            "EVM Buy/Sell Transaction Simulation",
            "Effective Transfer Tax Calculation",
            "Honeypot & High Tax Detection",
            "On-Chain Factory DEX Detection",
            "Contract Bytecode Verification",
            "EIP-1967 Proxy Resolution",
            "Multi-Selector Ownership Checking",
            "Persistent SQLite Replay Protection"
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
        f"# HELP payments_verified_total Total x402 payment proofs verified\n"
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
        402: {"model": HTTP402PaymentRequiredResponse, "description": "Payment Required via x402 Protocol"},
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
    Main A2A Verification Endpoint.
    - If X-PAYMENT-PROOF header is missing or invalid: Returns HTTP 402 with price headers & free preview.
    - If X-PAYMENT-PROOF header is verified: Returns full EVM simulation & honeypot analysis.
    """
    start_time = time.perf_counter()
    _metrics_data["requests_total"] += 1
    client_ip = request.client.host if request.client else "127.0.0.1"

    # --- STRICT INPUT VALIDATION ---
    raw_token = token.strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", raw_token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token address. Must be 42-character hex."
        )
    
    token_address = raw_token.lower()
    payment_proof = request.headers.get("X-PAYMENT-PROOF")

    # --- 1. x402 PAYMENT PROOF INTERCEPTION ---
    is_valid, proof_msg, _proof_meta = await payment_verifier.verify_payment_proof(
        payment_proof, token_address=token_address
    ) if payment_proof else (False, "No payment proof header attached", {})

    if not is_valid:
        logger.info(f"402 Challenge issued to IP={client_ip} for token={token_address}. Reason: {proof_msg}")
        headers = {
            "X-402-Price": f"{USDC_PRICE} USDC",
            "X-402-PayTo": PAYMENT_WALLET_ADDRESS,
            "X-402-Network": "base-mainnet"
        }
        
        response_body = {
            "status_code": 402,
            "error": "Payment Required",
            "message": proof_msg,
            "x402_price": f"{USDC_PRICE} USDC",
            "pay_to": PAYMENT_WALLET_ADDRESS,
            "network": "base-mainnet",
            "free_sample_preview": {
                "token": token_address,
                "available_metrics": [
                    "simulated_buy_success",
                    "simulated_sell_success",
                    "effective_buy_tax_pct",
                    "effective_sell_tax_pct",
                    "is_honeypot",
                    "is_high_tax",
                    "safety_score",
                    "liquidity_usd"
                ]
            }
        }
        return JSONResponse(status_code=402, content=response_body, headers=headers)

    _metrics_data["payments_verified_total"] += 1

    # --- 2. FULL EVM SIMULATION EXECUTION (PAYMENT VERIFIED) ---
    try:
        # SLA Timeout set to 3.0 seconds
        simulation_data = await asyncio.wait_for(
            evm_simulator.analyze_token(token_address),
            timeout=3.0
        )
        
        elapsed = time.perf_counter() - start_time
        _observe_latency(elapsed)

        if simulation_data.get("simulation_results", {}).get("is_honeypot"):
            _metrics_data["honeypots_detected_total"] += 1

        logger.info(f"Verified simulation completed for token={token_address} in {elapsed:.4f}s IP={client_ip}")
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
