# Base L2 Advanced EVM Simulation & Token Verifier (x402 Micro-Agent)

A production-grade, low-latency Agent-to-Agent (A2A) micro-service built for Base L2. This service performs real-time EVM transaction simulations (testing buy/sell execution, transfer taxes, factory routing, and honeypot checks) and returns verified metrics via the x402 payment protocol.

---

## 📁 Directory Structure

```text
base-a2a-verifier/
├── .env.example         # Environment variables template
├── main.py              # Production FastAPI server with x402 middleware & OpenAPI docs
├── evm_simulator.py     # EVM simulation engine (eth_call dry-runs, factory DEX routing, tax calc)
├── x402_verifier.py     # On-chain Base L2 USDC payment proof verifier with SQLite replay protection
├── test_client.py       # Client integration test suite
├── run_test.py          # ASGI direct execution test runner
├── test_phase2_verification.py # Phase 2 accuracy test runner
├── test_phase3_verification.py # Phase 3 infrastructure test runner
└── requirements.txt     # Python package requirements
```

---

## 🚀 Deployment

### Running FastAPI Server (Production):
```bash
pip install -r requirements.txt
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

> [!NOTE]
> SQLite persistent replay protection requires running uvicorn with `--workers 1` to prevent write contention across process forks (or upgrade to Redis for multi-worker scaling).

### Running the Test Suites:
```bash
python3 run_test.py
python3 test_client.py
python3 test_phase2_verification.py
python3 test_phase3_verification.py
```

---

## ⚡ x402 Protocol Flow (Schema 1.1)

1. **Unpaid Request:** `GET /verify?token=0x...` (No `X-PAYMENT-PROOF` header).
   * **Server Response:** `HTTP 402 Payment Required`
   * **Headers:** `X-402-Price: 0.05 USDC`, `X-402-PayTo: <Wallet>`, `X-402-Network: base-mainnet`
   * **Body:** Free schema preview of available metrics.
2. **Paid Request:** `GET /verify?token=0x...` with `X-PAYMENT-PROOF: <tx_hash>`.
   * **Server Response:** `HTTP 200 OK` (Schema Version `1.1`)
   * **Body:** Full EVM transaction simulation, honeypot analysis, high-tax detection, and empirical safety score breakdown.
