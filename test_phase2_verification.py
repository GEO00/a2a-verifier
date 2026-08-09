import asyncio
import os
import time

from evm_simulator import AERODROME_ROUTER, SIMULATION_WALLET, EVMTokenSimulator
from x402_verifier import X402PaymentVerifier


async def test_all():
    print("=================================================================")
    print("       RUNNING PHASE 1 REPAIR & PHASE 2 VERIFICATION TESTS       ")
    print("=================================================================\n")

    # 1. State Override Prefix & Storage Slot Probing Verification
    sim = EVMTokenSimulator()
    token_addr = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    override, _layout_found = await sim._smart_storage_override(token_addr, AERODROME_ROUTER)
    
    token_diffs = override.get(token_addr.lower(), {}).get("stateDiff", {})
    wallet_override = override.get(SIMULATION_WALLET.lower(), {})
    
    first_slot_val = next(iter(token_diffs.values()))
    wallet_bal_val = wallet_override.get("balance")

    print("1️⃣ [STATE OVERRIDE '0x' PREFIX VERIFICATION]")
    print(f"   • Storage Slot Value: {first_slot_val[:10]}... (StartsWith '0x': {first_slot_val.startswith('0x')})")
    print(f"   • Wallet Balance:     {wallet_bal_val} (StartsWith '0x': {wallet_bal_val.startswith('0x')})")
    assert first_slot_val.startswith("0x"), "FAILED: Storage slot hex value missing '0x' prefix!"
    assert wallet_bal_val.startswith("0x"), "FAILED: Wallet balance hex value missing '0x' prefix!"
    print("   ✅ PASSED: State override maps use valid 0x-prefixed hex strings.\n")

    # 2. Payment Verifier & Replay Protection Verification (SQLite / aiosqlite)
    db_file = "test_used_proofs.db"
    if os.path.exists(db_file):
        os.remove(db_file)

    verifier = X402PaymentVerifier(
        rpc_url="https://mainnet.base.org",
        pay_to_wallet="0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
        allow_test_proofs=True,
        production_mode=False,
        db_path=db_file
    )

    print("2️⃣ [PAYMENT PROOF VERIFICATION & PERSISTENT REPLAY PROTECTION]")
    
    # Test valid proof first time
    test_proof = "test_proof_tx_0x123456789_base"
    token_a = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    token_b = "0x4200000000000000000000000000000000000006"

    is_valid_1, msg_1, _ = await verifier.verify_payment_proof(test_proof, token_address=token_a)
    print(f"   • First Usage of Proof '{test_proof}' for Token A:")
    print(f"     Valid: {is_valid_1} | Message: '{msg_1}'")
    assert is_valid_1 is True, "FAILED: First usage of proof should be valid!"

    # Test replay double-spend attack (same proof for same token)
    is_valid_2, msg_2, _ = await verifier.verify_payment_proof(test_proof, token_address=token_a)
    print(f"   • Second Usage (Double-Spend Replay Attack) of '{test_proof}':")
    print(f"     Valid: {is_valid_2} | Message: '{msg_2}'")
    assert is_valid_2 is False, "FAILED: Replayed payment proof was not rejected!"
    assert "already used" in msg_2.lower() or "double-spend" in msg_2.lower(), "FAILED: Incorrect replay error message!"

    # Test request-bound token proof binding (using proof spent on Token A for Token B)
    is_valid_3, msg_3, _ = await verifier.verify_payment_proof(test_proof, token_address=token_b)
    print("   • Attempting to reuse Token A's proof for Token B:")
    print(f"     Valid: {is_valid_3} | Message: '{msg_3}'")
    assert is_valid_3 is False, "FAILED: Proof used for Token A was allowed for Token B!"
    print("   ✅ PASSED: Double-spend replay attack and cross-token reuse strictly rejected.\n")

    # Concurrent Atomic Claim Race Condition Test (Fix 5)
    print("2️⃣b [CONCURRENT ATOMIC CLAIM RACE CONDITION TEST]")
    test_proof_concurrent = f"test_proof_concurrent_{time.time()}"
    t1 = verifier.verify_payment_proof(test_proof_concurrent, token_address=token_a)
    t2 = verifier.verify_payment_proof(test_proof_concurrent, token_address=token_a)
    r1, r2 = await asyncio.gather(t1, t2)
    successes = [r for r in [r1, r2] if r[0] is True]
    failures = [r for r in [r1, r2] if r[0] is False]
    print(f"   • Concurrent execution results: {len(successes)} succeeded, {len(failures)} failed")
    assert len(successes) == 1, f"FAILED: Expected exactly 1 success, got {len(successes)}"
    assert len(failures) == 1, f"FAILED: Expected exactly 1 failure, got {len(failures)}"
    assert "already used" in failures[0][1].lower() or "double-spend" in failures[0][1].lower(), "FAILED: Incorrect error on concurrent duplicate!"
    print("   ✅ PASSED: Exactly 1 concurrent claim succeeded and 1 was atomically rejected.\n")

    # 3. Production Mode Backdoor Rejection Test
    print("3️⃣ [PRODUCTION MODE BACKDOOR REJECTION TEST]")
    prod_verifier = X402PaymentVerifier(
        rpc_url="https://mainnet.base.org",
        pay_to_wallet="0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
        allow_test_proofs=True,
        production_mode=True, # PRODUCTION MODE ENABLED
        db_path="test_prod_proofs.db"
    )
    is_valid_prod, msg_prod, _ = await prod_verifier.verify_payment_proof("test_proof_123", token_address=token_a)
    print("   • Production mode test proof rejection check:")
    print(f"     Valid: {is_valid_prod} | Message: '{msg_prod}'")
    assert is_valid_prod is False, "FAILED: Test proof was accepted in production mode!"
    print("   ✅ PASSED: Test proofs hard-rejected in production mode.\n")

    # Cleanup temp db files
    await verifier.close()
    await prod_verifier.close()
    if os.path.exists(db_file):
        os.remove(db_file)
    if os.path.exists("test_prod_proofs.db"):
        os.remove("test_prod_proofs.db")

    print("=================================================================")
    print("      ALL REPAIRS & PHASE 2 REPLAY PROTECTION TESTS PASSED       ")
    print("=================================================================")

if __name__ == "__main__":
    asyncio.run(test_all())
