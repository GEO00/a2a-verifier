import asyncio
import json
import os
import time

import httpx

# Set test env variables
os.environ["ALLOW_TEST_PAYMENT_PROOFS"] = "true"
os.environ["PRODUCTION_MODE"] = "false"

from main import app


async def run_asgi_test():
    print("=================================================================")
    print("     TESTING A2A BASE L2 EVM VERIFIER (ASGI DIRECT TEST)         ")
    print("=================================================================\n")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        
        # 1. Health Endpoint Check
        print("1️⃣ [HEALTH CHECK]")
        res_health = await client.get("/health")
        print(f"   Status: {res_health.status_code}")
        print(f"   Payload: {json.dumps(res_health.json(), indent=2)}\n")
        assert res_health.status_code == 200

        # 2. Schema Discovery Endpoint Check
        print("2️⃣ [DISCOVERY SCHEMA CHECK]")
        res_schema = await client.get("/schema")
        print(f"   Status: {res_schema.status_code}")
        print(f"   Agent Name: {res_schema.json().get('agent_name')}")
        print(f"   Schema Version: {res_schema.json().get('schema_version')}\n")
        assert res_schema.status_code == 200

        # 3. HTTP 402 Payment Challenge Test
        print("3️⃣ [x402 PAYMENT REQUIRED CHALLENGE TEST]")
        test_token = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        res_402 = await client.get(f"/verify?token={test_token}")
        print(f"   Status Code: {res_402.status_code} (Expected 402)")
        print(f"   • Header X-402-Price:   {res_402.headers.get('x-402-price')}")
        print(f"   • Header X-402-PayTo:   {res_402.headers.get('x-402-payto')}")
        print(f"   • Header X-402-Network: {res_402.headers.get('x-402-network')}")
        print(f"   • Free Sample Preview:\n{json.dumps(res_402.json().get('free_sample_preview'), indent=4)}\n")
        assert res_402.status_code == 402

        # 4. HTTP 200 Paid EVM Simulation Test
        print("4️⃣ [HTTP 200 PAID EVM SIMULATION TEST]")
        headers = {"X-PAYMENT-PROOF": f"test_proof_tx_{time.time()}_base_settled"}
        res_200 = await client.get(f"/verify?token={test_token}", headers=headers)
        print(f"   Status Code: {res_200.status_code} (Expected 200 OK)")
        print("   • Full Verified EVM Simulation Response:")
        print(json.dumps(res_200.json(), indent=4))
        
        assert res_200.status_code == 200
        payload_200 = res_200.json()
        assert payload_200.get("schema_version") == "1.1", f"Expected schema_version '1.1', got {payload_200.get('schema_version')}"
        sim_res = payload_200.get("simulation_results", {})
        assert "is_honeypot" in sim_res
        assert "is_high_tax" in sim_res
        assert "safety_score" in sim_res
        assert "score_breakdown" in sim_res

        # 5. Prometheus Metrics Test
        print("\n5️⃣ [PROMETHEUS METRICS ENDPOINT CHECK]")
        res_metrics = await client.get("/metrics")
        print(f"   Status: {res_metrics.status_code}")
        print(f"   Metrics Snippet:\n{res_metrics.text[:250]}\n")
        assert res_metrics.status_code == 200
        assert "simulation_latency_seconds" in res_metrics.text

        print("=================================================================")
        print("           ALL TESTS PASSED: PRODUCTION-READY A2A AGENT          ")
        print("=================================================================")

if __name__ == "__main__":
    asyncio.run(run_asgi_test())
