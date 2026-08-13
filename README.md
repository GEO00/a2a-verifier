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

> **Critical:** Set `CDP_API_KEY_ID` and `CDP_API_KEY_SECRET` (required for CDP Facilitator auth). Prefer `--workers 1`.

### Tests

```bash
python3 run_test.py
python3 test_phase2_verification.py
python3 test_phase3_verification.py
python3 scripts/race_test.py direct
```

---

## x402 v2 protocol flow (CDP Facilitator + Bazaar)

1. **Unpaid:** `GET /verify?token=0x...` → `HTTP 402` with a base64 `PAYMENT-REQUIRED` header
   (`x402Version: 2`, `accepts[]`, `extensions.bazaar`).
2. **Pay:** client signs an EIP-3009 USDC authorization for `$0.05` on Base (`eip155:8453`)
   to `PAYMENT_WALLET_ADDRESS` (via an x402-compatible wallet/client).
3. **Settle:** retry with `PAYMENT-SIGNATURE: <base64 payment payload>`. Middleware verifies+settles
   through the **CDP Facilitator** (`https://api.cdp.coinbase.com/platform/v2/x402`), then returns
   the full EVM simulation.
4. **Discovery:** `extensions.bazaar` (via `declare_discovery_extension`) makes the route eligible
   for CDP Bazaar indexing after the first successful verify+settle.

Validate with:

```bash
curl -X POST https://api.cdp.coinbase.com/platform/v2/x402/validate \
  -H "Content-Type: application/json" \
  -d '{"resource":"https://a2a-verifier-production.up.railway.app/verify","method":"GET"}'
```

### Pay with Agentic Wallet (recommended payer)

Buyers/agents should pay from a wallet that is **not** `PAYMENT_WALLET_ADDRESS`
(CDP rejects self-send). Use [Agentic Wallet CLI](https://docs.cdp.coinbase.com/agentic-wallet/cli/quickstart)
(`awal`) — requires **Node.js 24+**.

```bash
# one-time: install agent skills into this repo
npx skills add coinbase/agentic-wallet-skills

# 1) Auth (email OTP — interactive)
npx awal@latest auth login you@example.com
npx awal@latest auth verify <flowId> <6-digit-otp>

# 2) Confirm + fund USDC on Base (Onramp UI, or transfer USDC to the address)
npx awal@latest status
npx awal@latest address          # must != 0x1D1173… merchant
npx awal@latest show             # Fund button / onramp
npx awal@latest balance --chain base

# 3) Paid call to this service ($0.05)
./scripts/pay_with_awal.sh
# or:
npx awal@latest x402 pay \
  'https://a2a-verifier-production.up.railway.app/verify?token=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913' \
  --max-amount 60000 --json
```

Optional for Cursor/Claude Desktop: [Agentic Wallet MCP](https://docs.cdp.coinbase.com/agentic-wallet/mcp/quickstart)
via `npx @coinbase/payments-mcp` (set spending limits in the wallet UI).

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
  PAYMENT_WALLET_ADDRESS=0x1D1173c1465c9a01F6AfA38B36cc1125CC55C71a \
  BASE_RPC_URLS=https://mainnet.base.org,https://base.publicnode.com,https://base.llamarpc.com,https://base.drpc.org \
  PORT=8000 \
  WEB_CONCURRENCY=1 \
  --skip-deploys

# Set CDP keys from the dashboard (do not paste secrets into shell history):
# CDP_API_KEY_ID / CDP_API_KEY_SECRET

railway up --detach
railway domain
```

Or: `bash scripts/deploy_railway.sh`

### Required production variables

| Variable | Value |
|---|---|
| `CDP_API_KEY_ID` | CDP portal API key id |
| `CDP_API_KEY_SECRET` | CDP portal API key secret |
| `PRODUCTION_MODE` | `true` |
| `PAYMENT_WALLET_ADDRESS` | your Base wallet (receives USDC) |
| `BASE_RPC_URLS` | comma-separated RPCs (prepend a private Alchemy/QuickNode URL when you have one) |
| `USDC_PRICE` | `0.05` (optional) |
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
| `CDP_API_KEY_ID and CDP_API_KEY_SECRET are required` | Missing facilitator auth | Set both vars from https://portal.cdp.coinbase.com |
| `Facilitator get_supported failed (401)` | Bad/missing CDP keys | Rotate keys; confirm they are set on the Railway service |
| Validator: `x402Version is <nil>` | Still on custom/v1 402 body | Deploy this x402 v2 middleware build |
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
