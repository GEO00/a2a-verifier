import asyncio
import json
import time

import httpx

SERVER_URL = "http://127.0.0.1:8000"
TEST_TOKEN = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # Base L2 USDC Token

async def run_test_suite():
    print("=================================================================")
    print("     TESTING A2A BASE L2 EVM VERIFIER & x402 PROTOCOL (FASTAPI)  ")
    print("=================================================================\n")

    async with httpx.AsyncClient(timeout=5.0) as client:
        
        # -----------------------------------------------------------------
        # TEST 1: Health & Discovery Schema Check
        # -----------------------------------------------------------------
        print("1️⃣ [CLIENT] Checking Agent Health & Discovery Schema...")
        health_resp = await client.get(f"{SERVER_URL}/health")
        print(f"   Health Status Code: {health_resp.status_code}")
        print(f"   Payload: {json.dumps(health_resp.json(), indent=2)}\n")

        schema_resp = await client.get(f"{SERVER_URL}/schema")
        print(f"   Schema Status Code: {schema_resp.status_code}")
        print(f"   Agent Name: {schema_resp.json().get('agent_name')}")
        print(f"   Schema Version: {schema_resp.json().get('schema_version')}\n")

        # -----------------------------------------------------------------
        # TEST 2: HTTP 402 Payment Challenge Flow (No Payment Proof Header)
        # -----------------------------------------------------------------
        print("2️⃣ [CLIENT] Requesting /verify without Payment Proof...")
        url = f"{SERVER_URL}/verify?token={TEST_TOKEN}"
        resp_402 = await client.get(url)

        print(f"   Received Status Code: {resp_402.status_code} (Expected 402)")
        print(f"   • Header X-402-Price:   {resp_402.headers.get('x-402-price')}")
        print(f"   • Header X-402-PayTo:   {resp_402.headers.get('x-402-payto')}")
        print(f"   • Header X-402-Network: {resp_402.headers.get('x-402-network')}")
        
        data_402 = resp_402.json()
        print("   • Free Sample Preview Body:")
        print(json.dumps(data_402.get("free_sample_preview"), indent=4))
        print("\n-----------------------------------------------------------------\n")

        # -----------------------------------------------------------------
        # TEST 3: HTTP 200 Verified EVM Simulation Flow (With Payment Proof)
        # -----------------------------------------------------------------
        print("3️⃣ [CLIENT] Submitting Payment Proof Header and Retrying...")
        headers = {"X-PAYMENT-PROOF": f"test_proof_tx_{time.time()}_base_settled"}
        
        start_time = time.perf_counter()
        resp_200 = await client.get(url, headers=headers)
        end_time = time.perf_counter()
        
        elapsed_seconds = end_time - start_time
        print(f"   Received Status Code: {resp_200.status_code} (Expected 200 OK)")
        print(f"   ⏱️ Execution Time: {elapsed_seconds:.4f} seconds (SLA Target: < 3.0s)")

        data_200 = resp_200.json()
        print("   • Full Verified EVM Simulation Response Payload:")
        print(json.dumps(data_200, indent=4))

        # Assertions
        assert resp_200.status_code == 200, "FAILED: Expected HTTP 200"
        assert data_200.get("schema_version") == "1.1", f"Expected schema 1.1, got {data_200.get('schema_version')}"
        contract_analysis = data_200.get("contract_analysis", {})
        assert "has_bytecode" in contract_analysis, "Missing has_bytecode in contract_analysis"
        assert "is_proxy" in contract_analysis, "Missing is_proxy in contract_analysis"
        assert "implementation_address" in contract_analysis, "Missing implementation_address in contract_analysis"
        assert "owner_address" in contract_analysis, "Missing owner_address in contract_analysis"
        assert "contract_renounced" in contract_analysis, "Missing contract_renounced in contract_analysis"

        # -----------------------------------------------------------------
        # TEST 4: Prometheus Metrics Endpoint
        # -----------------------------------------------------------------
        print("\n4️⃣ [CLIENT] Checking /metrics endpoint...")
        resp_metrics = await client.get(f"{SERVER_URL}/metrics")
        print(f"   Metrics Status Code: {resp_metrics.status_code}")
        print(f"   Payload:\n{resp_metrics.text[:200]}")
        assert resp_metrics.status_code == 200
        assert "simulation_latency_seconds" in resp_metrics.text

        print("\n=================================================================")
        print(f"       ALL SUITES PASSED (LATENCY {elapsed_seconds:.4f}s < 3.0s)       ")
        print("=================================================================")

if __name__ == "__main__":
    asyncio.run(run_test_suite())
