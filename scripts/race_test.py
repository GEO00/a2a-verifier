"""Double-spend race test: N concurrent submissions of the SAME payment proof.

Expected: exactly 1 success; every other attempt rejected as a double-spend
(or 429 if the per-IP paid rate limit is hit first in HTTP mode).

Usage:
    # Direct SQLite atomicity test (no server needed):
    python scripts/race_test.py direct

    # Full HTTP path (server must run with ALLOW_TEST_PAYMENT_PROOFS=true):
    python scripts/race_test.py http http://127.0.0.1:8129
"""
import asyncio
import os
import sys
import time
from collections import Counter

CONCURRENCY = 50
TOKEN = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


async def direct_mode() -> int:
    os.environ["ALLOW_TEST_PAYMENT_PROOFS"] = "true"
    os.environ["PRODUCTION_MODE"] = "false"
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from x402_verifier import X402PaymentVerifier

    db = f"/tmp/race_test_{int(time.time())}.db"
    verifier = X402PaymentVerifier(db_path=db)
    proof = f"test_proof_race_{int(time.time())}"

    results = await asyncio.gather(
        *[verifier.verify_payment_proof(proof, token_address=TOKEN) for _ in range(CONCURRENCY)]
    )
    await verifier.close()

    successes = [r for r in results if r[0]]
    rejections = Counter(r[1] for r in results if not r[0])

    print(f"[direct] concurrency={CONCURRENCY} db={db}")
    print(f"[direct] successes: {len(successes)}")
    for msg, count in rejections.items():
        print(f"[direct] rejected x{count}: {msg}")
    ok = len(successes) == 1
    print(f"[direct] {'PASS' if ok else 'FAIL'}: expected exactly 1 success")
    return 0 if ok else 1


async def http_mode(base_url: str) -> int:
    import httpx

    proof = f"test_proof_race_http_{int(time.time())}"
    url = f"{base_url}/verify?token={TOKEN}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        responses = await asyncio.gather(
            *[client.get(url, headers={"X-PAYMENT-PROOF": proof}) for _ in range(CONCURRENCY)]
        )

    codes = Counter(r.status_code for r in responses)
    double_spend = sum(
        1 for r in responses
        if r.status_code == 402 and "already used" in r.text
    )
    print(f"[http] concurrency={CONCURRENCY} url={url}")
    print(f"[http] status codes: {dict(codes)}")
    print(f"[http] double-spend rejections: {double_spend}")
    ok = (
        codes.get(200, 0) == 1
        and double_spend + codes.get(429, 0) == CONCURRENCY - 1
    )
    print(f"[http] {'PASS' if ok else 'FAIL'}: expected exactly one 200; rest double-spend 402 or rate-limit 429")
    return 0 if ok else 1


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "direct"
    if mode == "direct":
        raise SystemExit(asyncio.run(direct_mode()))
    if mode == "http":
        raise SystemExit(asyncio.run(http_mode(sys.argv[2].rstrip("/"))))
    raise SystemExit(f"Unknown mode: {mode}")
