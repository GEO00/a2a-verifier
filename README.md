# Base L2 Advanced EVM Simulation & Token Verifier (x402 Micro-Agent)

A production-grade, low-latency Agent-to-Agent (A2A) micro-service for Base L2. It simulates buy/sell execution, transfer taxes, DEX routing, and honeypot checks, gated by the x402 payment protocol ($0.05 USDC per request).

**Live production URL:** https://a2a-verifier-production.up.railway.app

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | public | Liveness + production flags |
| `GET /schema` | public | Machine-readable capability schema |
| `GET /openapi.json` | public | OpenAPI spec |
| `GET /metrics` | public | Prometheus metrics |
| `GET /verify?token=0x...` | x402 | Paid EVM simulation |

Payment wallet (Base): `0x1D1173c1465c9a01F6AfA38B36cc1125CC55C71a`

---

## Quick start (local)

```bash
pip install -r requirements.txt
cp .env.example .env   # then edit
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

> **Critical:** Always use `--workers 1`. Replay protection is SQLite-backed; multiple workers can race on `used_proofs.db`.

### Tests

```bash
python3 run_test.py
python3 test_phase2_verification.py
python3 test_phase3_verification.py
python3 scripts/race_test.py direct
```

---

## x402 protocol flow (schema 1.1)

1. **Unpaid:** `GET /verify?token=0x...` → `HTTP 402` with `X-402-Price`, `X-402-PayTo`, free metric preview.
2. **Pay:** send ≥ 0.05 USDC on Base to the payment wallet.
3. **Settle:** same request with header `X-PAYMENT-PROOF: <tx_hash>` → `HTTP 200` full simulation.
4. **Replay:** reusing the same tx hash → `HTTP 402` `"Transaction hash already used"`.

Test proofs (`test_proof_*`) are rejected when `PRODUCTION_MODE=true`.

---

## Railway deploy

Artifacts: `Dockerfile`, `railway.toml`, `scripts/deploy_railway.sh`.

```bash
# Install CLI (Fedora/Linux user install)
curl -fsSL https://railway.com/install.sh | sh
source "$HOME/.railway/env"
railway login

cd /path/to/base-a2a-verifier
railway link --project reliable-surprise --environment production --service a2a-verifier

railway variable set \
  PRODUCTION_MODE=true \
  ALLOW_TEST_PAYMENT_PROOFS=false \
  PAYMENT_WALLET_ADDRESS=0x1D1173c1465c9a01F6AfA38B36cc1125CC55C71a \
  BASE_RPC_URLS=https://mainnet.base.org,https://base.publicnode.com,https://base.llamarpc.com,https://base.drpc.org \
  PROOF_DB_PATH=/data/used_proofs.db \
  PORT=8000 \
  WEB_CONCURRENCY=1 \
  --skip-deploys

railway volume add --mount-path /data   # REQUIRED — without this, proofs reset on redeploy
railway up --detach
railway domain
```

Or: `bash scripts/deploy_railway.sh`

### Required production variables

| Variable | Value |
|---|---|
| `PRODUCTION_MODE` | `true` |
| `ALLOW_TEST_PAYMENT_PROOFS` | `false` |
| `PAYMENT_WALLET_ADDRESS` | your Base wallet |
| `BASE_RPC_URLS` | comma-separated RPCs (prepend a private Alchemy/QuickNode URL when you have one) |
| `PROOF_DB_PATH` | `/data/used_proofs.db` |
| `PORT` | `8000` |
| `WEB_CONCURRENCY` | `1` |

Do **not** upload `.env` to Railway — set variables in the dashboard/CLI only.

---

## Monitoring

- Prometheus scrape: `docs/monitoring/prometheus.yml` (target `a2a-verifier-production.up.railway.app`)
- Grafana dashboard JSON: `docs/monitoring/grafana-dashboard.json`
- External health poller:

```bash
HEALTH_URL=https://a2a-verifier-production.up.railway.app/health \
ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/... \
./scripts/health_monitor.sh
```

Suggested alerts: p99 simulation latency > 3s, sustained `rpc_errors_total`, scrape/`/health` down.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Application not found` 404 | Wrong hostname | Use `https://a2a-verifier-production.up.railway.app` (service domain, not project name) |
| Every token is `HONEYPOT` | Bad RPC / rate limits / old image | Confirm latest deploy; prepend a private RPC to `BASE_RPC_URLS` |
| `Test payment proofs strictly disabled` | Expected in prod | Use a real Base USDC tx hash |
| `Transaction hash already used` | Replay protection working | Pay again with a new tx |
| SQLite / proof DB resets after deploy | Missing volume | `railway volume add --mount-path /data` and `PROOF_DB_PATH=/data/used_proofs.db` |
| `EACCES` on `npm i -g @railway/cli` | Global npm needs root | Use `curl -fsSL https://railway.com/install.sh \| sh` |
| `railway: command not found` after install | PATH not loaded | `source "$HOME/.railway/env"` |

---

## Directory layout

```text
base-a2a-verifier/
├── main.py                 # FastAPI + x402 middleware
├── evm_simulator.py        # Buy/sell simulation engine
├── x402_verifier.py        # On-chain USDC proof + SQLite replay DB
├── Dockerfile / railway.toml
├── scripts/deploy_railway.sh
├── scripts/race_test.py
├── scripts/health_monitor.sh
├── docs/DISTRIBUTION.md
└── docs/monitoring/
```

Distribution / directory submission copy: see `docs/DISTRIBUTION.md`.
