# Linting Policy

15 ruff `BLE001`/`S110` findings are intentionally preserved. These are defensive
`except Exception` blocks in RPC fallback paths per `SUMMARY.md` — removing them
would break graceful degradation.

Current clean baseline (verified 2026-08-09):

- `python -m py_compile *.py` — 0 syntax errors
- `mypy *.py` — 0 errors (with project dependencies installed)
- `ruff check .` — 15 findings, all `BLE001` (14) + `S110` (1), all intentional

Locations: `evm_simulator.py` (12), `x402_verifier.py` (2), `main.py` (1).
These guard RPC calls, ABI-decoder fallbacks, and simulation paths where any
upstream failure must degrade to a safe default instead of crashing the request.

Do not "fix" these without understanding the fallback hierarchy in
`evm_simulator.py` (eth_abi -> web3 -> pure-Python decoder) and the multi-endpoint
RPC rotation in `x402_verifier.py`.
