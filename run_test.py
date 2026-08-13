"""ASGI smoke test for x402 v2 402 challenge + free endpoints.

Paid settlement requires a real PAYMENT-SIGNATURE and CDP Facilitator auth,
so the EVM simulation path is exercised directly (not via a fake payment proof).
"""
import asyncio
import base64
import json
import os
from unittest.mock import MagicMock

# Dummy CDP keys so create_facilitator_config builds an auth-capable client.
# get_supported is mocked below so no live CDP call is made.
os.environ.setdefault("CDP_API_KEY_ID", "test-cdp-key-id")
os.environ.setdefault("CDP_API_KEY_SECRET", "test-cdp-key-secret")
os.environ["PRODUCTION_MODE"] = "false"

from x402.schemas import SupportedKind, SupportedResponse

import main as main_mod
from main import app

_fake_supported = SupportedResponse(
    kinds=[
        SupportedKind(x402_version=2, scheme="exact", network="eip155:8453"),
        SupportedKind(x402_version=2, scheme="exact", network="eip155:*"),
    ],
    extensions=["bazaar"],
)
for _client in main_mod.x402_server._facilitator_clients:
    _client.get_supported = MagicMock(return_value=_fake_supported)


def _decode_payment_required(header_value: str) -> dict:
    pad = "=" * (-len(header_value) % 4)
    return json.loads(base64.b64decode(header_value + pad))


async def run_asgi_test():
    print("=================================================================")
    print("     TESTING A2A BASE L2 EVM VERIFIER (x402 v2 ASGI TEST)        ")
    print("=================================================================\n")

    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Health
        print("1️⃣ [HEALTH CHECK]")
        res_health = await client.get("/health")
        print(f"   Status: {res_health.status_code}")
        print(f"   Payload: {json.dumps(res_health.json(), indent=2)}\n")
        assert res_health.status_code == 200
        assert res_health.json().get("x402_version") == 2

        # 2. Schema
        print("2️⃣ [DISCOVERY SCHEMA CHECK]")
        res_schema = await client.get("/schema")
        print(f"   Status: {res_schema.status_code}")
        print(f"   Agent Name: {res_schema.json().get('agent_name')}")
        print(f"   x402_version: {res_schema.json().get('x402_version')}\n")
        assert res_schema.status_code == 200
        assert res_schema.json().get("x402_version") == 2

        # 3. HTTP 402 with PAYMENT-REQUIRED header
        print("3️⃣ [x402 v2 PAYMENT-REQUIRED CHALLENGE]")
        test_token = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        res_402 = await client.get(f"/verify?token={test_token}")
        print(f"   Status Code: {res_402.status_code} (Expected 402)")
        pr = res_402.headers.get("payment-required")
        assert res_402.status_code == 402, res_402.text
        assert pr, "Missing PAYMENT-REQUIRED header"
        envelope = _decode_payment_required(pr)
        print(f"   • x402Version: {envelope.get('x402Version')}")
        print(f"   • accepts[0]: {json.dumps(envelope.get('accepts', [None])[0], indent=4)}")
        bazaar = (envelope.get("extensions") or {}).get("bazaar") or {}
        print(f"   • bazaar.info.input: {json.dumps(bazaar.get('info', {}).get('input'), indent=4)}\n")
        assert envelope.get("x402Version") == 2
        assert envelope.get("accepts")
        assert envelope["accepts"][0]["network"] == "eip155:8453"
        assert envelope["accepts"][0]["scheme"] == "exact"
        assert int(envelope["accepts"][0]["amount"]) >= 1000
        assert bazaar.get("info", {}).get("input", {}).get("method") == "GET"
        assert "bazaar" in (envelope.get("extensions") or {})

        # 4. EVM simulation (direct — paid HTTP path needs a real PAYMENT-SIGNATURE)
        print("4️⃣ [EVM SIMULATION DIRECT CHECK]")
        simulation = await main_mod.evm_simulator.analyze_token(test_token.lower())
        print(json.dumps(simulation, indent=2)[:800], "...\n")
        assert simulation.get("schema_version") == "1.1"
        assert "is_honeypot" in simulation.get("simulation_results", {})

        # 5. Metrics
        print("5️⃣ [PROMETHEUS METRICS ENDPOINT CHECK]")
        res_metrics = await client.get("/metrics")
        print(f"   Status: {res_metrics.status_code}")
        print(f"   Metrics Snippet:\n{res_metrics.text[:250]}\n")
        assert res_metrics.status_code == 200

        print("=================================================================")
        print("           ALL TESTS PASSED: x402 v2 CHALLENGE SHAPE OK          ")
        print("=================================================================")


if __name__ == "__main__":
    asyncio.run(run_asgi_test())
