import asyncio
import json
import os
import time

import httpx

# Set test environment
os.environ["ALLOW_TEST_PAYMENT_PROOFS"] = "true"
os.environ["PRODUCTION_MODE"] = "false"

from main import app


async def run_stdlib_test_suite():
    print("=================================================================")
    print("     TESTING A2A BASE L2 EVM VERIFIER (ASGI COMPATIBILITY)       ")
    print("=================================================================\n")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

        # 1. Health Endpoint Test
        print("1️⃣ [HEALTH CHECK]")
        resp_health = await client.get("/health")
        print(f"   Status: {resp_health.status_code}")
        print(f"   Payload:\n{json.dumps(resp_health.json(), indent=2)}\n")
        assert resp_health.status_code == 200

        # 2. Discovery Schema Test
        print("2️⃣ [DISCOVERY SCHEMA TEST]")
        resp_schema = await client.get("/schema")
        print(f"   Status: {resp_schema.status_code}")
        print(f"   Payload:\n{json.dumps(resp_schema.json(), indent=2)}\n")
        assert resp_schema.status_code == 200

        # 3. HTTP 402 Payment Challenge Test (No Payment Proof)
        print("3️⃣ [x402 PAYMENT REQUIRED CHALLENGE TEST]")
        test_token = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        resp_402 = await client.get(f"/verify?token={test_token}")
        print(f"   Status Code: {resp_402.status_code} (Expected 402 Payment Required)")
        print(f"   • Header X-402-Price:   {resp_402.headers.get('x-402-price')}")
        print(f"   • Header X-402-PayTo:   {resp_402.headers.get('x-402-payto')}")
        print(f"   • Header X-402-Network: {resp_402.headers.get('x-402-network')}")
        print(f"   • Free Sample Preview Payload:\n{json.dumps(resp_402.json().get('free_sample_preview'), indent=4)}\n")
        assert resp_402.status_code == 402

        # 4. HTTP 200 Paid EVM Simulation Test (With Payment Proof Header)
        print("4️⃣ [HTTP 200 PAID EVM SIMULATION EXECUTION TEST]")
        resp_200 = await client.get(
            f"/verify?token={test_token}",
            headers={"X-PAYMENT-PROOF": f"test_proof_tx_{time.time()}_base_settled"}
        )
        print(f"   Status Code: {resp_200.status_code} OK (Payment Verified)")
        print("   • Full Verified EVM Simulation Response Payload:")
        print(json.dumps(resp_200.json(), indent=4))

        assert resp_200.status_code == 200
        data = resp_200.json()
        assert data.get("schema_version") == "1.1"

        print("\n=================================================================")
        print("                   ALL VERIFIER TESTS PASSED                     ")
        print("=================================================================")

if __name__ == "__main__":
    asyncio.run(run_stdlib_test_suite())
