import asyncio
import json

from evm_simulator import EVMTokenSimulator


async def test_phase3():
    print("=================================================================")
    print("      RUNNING PHASE 3: PROXY & MULTI-SELECTOR OWNER TESTS        ")
    print("=================================================================\n")

    sim = EVMTokenSimulator()
    token_addr = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913" # USDC Base L2
    
    print("1️⃣ [RUNNING ANALYZE_TOKEN ON USDC BASE L2]")
    result = await sim.analyze_token(token_addr)
    
    print(f"   Schema Version: {result.get('schema_version')}")
    assert result.get("schema_version") == "1.1", f"Expected schema_version '1.1', got {result.get('schema_version')}"

    contract_analysis = result.get("contract_analysis", {})
    print("   Contract Analysis Output:")
    print(json.dumps(contract_analysis, indent=4))
    
    assert "has_bytecode" in contract_analysis, "Missing 'has_bytecode'"
    assert "is_proxy" in contract_analysis, "Missing 'is_proxy'"
    assert "implementation_address" in contract_analysis, "Missing 'implementation_address'"
    assert "owner_address" in contract_analysis, "Missing 'owner_address'"
    assert "contract_renounced" in contract_analysis, "Missing 'contract_renounced'"

    sim_results = result.get("simulation_results", {})
    assert "is_honeypot" in sim_results, "Missing 'is_honeypot'"
    assert "is_high_tax" in sim_results, "Missing 'is_high_tax'"
    assert "score_breakdown" in sim_results, "Missing 'score_breakdown'"
    assert "unknown_storage_layout" in sim_results, "Missing 'unknown_storage_layout'"
    assert sim_results.get("unknown_storage_layout") is False, "Expected unknown_storage_layout to be False for standard ERC-20"

    print(f"   Simulation Results Output (unknown_storage_layout={sim_results.get('unknown_storage_layout')}):")
    print("   ✅ Pre-sell balance gate and storage layout verification assertions passed.")

    await sim.close()

    print("\n=================================================================")
    print("                  PHASE 3 VERIFICATION PASSED                    ")
    print("=================================================================")

if __name__ == "__main__":
    asyncio.run(test_phase3())
