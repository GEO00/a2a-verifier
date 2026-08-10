#!/usr/bin/env bash
# Deploy base-a2a-verifier to Railway project "reliable-surprise".
# Usage (from repo root):
#   bash scripts/deploy_railway.sh
# Optional: SERVICE_NAME=my-service bash scripts/deploy_railway.sh
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-reliable-surprise}"
ENVIRONMENT="${ENVIRONMENT:-production}"
SERVICE_NAME="${SERVICE_NAME:-}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v railway >/dev/null 2>&1; then
  echo "Railway CLI not found. Install with:"
  echo "  npm i -g @railway/cli"
  echo "  # or: curl -fsSL https://railway.com/install.sh | sh"
  exit 1
fi

echo "==> Auth check"
railway whoami

echo "==> Link project: $PROJECT_NAME ($ENVIRONMENT)"
if [[ -n "$SERVICE_NAME" ]]; then
  railway link --project "$PROJECT_NAME" --environment "$ENVIRONMENT" --service "$SERVICE_NAME"
else
  railway link --project "$PROJECT_NAME" --environment "$ENVIRONMENT"
  echo "No SERVICE_NAME set — pick the app service if prompted."
fi

echo "==> Linked context"
railway status

echo "==> Set production variables (skip auto-redeploy until volume exists)"
railway variable set \
  PRODUCTION_MODE=true \
  ALLOW_TEST_PAYMENT_PROOFS=false \
  PAYMENT_WALLET_ADDRESS=0x1D1173c1465c9a01F6AfA38B36cc1125CC55C71a \
  BASE_RPC_URLS=https://mainnet.base.org,https://base.publicnode.com,https://base.llamarpc.com,https://base.drpc.org \
  PROOF_DB_PATH=/data/used_proofs.db \
  PORT=8000 \
  WEB_CONCURRENCY=1 \
  --skip-deploys

echo "==> Ensure volume at /data"
if railway volume list --json 2>/dev/null | grep -q '"mountPath"[[:space:]]*:[[:space:]]*"/data"'; then
  echo "Volume already mounted at /data"
else
  railway volume add --mount-path /data
fi
railway volume list

echo "==> Deploy from local Dockerfile"
railway up --detach

echo "==> Generate public domain (idempotent if one exists)"
railway domain || true
sleep 2
railway domain list || true

echo "==> Resolve live URL"
LIVE_URL="$(railway domain list --json 2>/dev/null | python3 - <<'PY' || true
import json,sys
try:
    data=json.load(sys.stdin)
except Exception:
    sys.exit(0)
# CLI JSON shape varies; accept list or {domains:[...]}
items = data if isinstance(data, list) else data.get("domains") or data.get("serviceDomains") or []
for d in items:
    if isinstance(d, str) and d:
        print(d if d.startswith("http") else f"https://{d}")
        break
    if isinstance(d, dict):
        host = d.get("domain") or d.get("hostname") or d.get("name")
        if host:
            print(host if str(host).startswith("http") else f"https://{host}")
            break
PY
)"

if [[ -z "${LIVE_URL:-}" ]]; then
  LIVE_URL="$(railway status --json 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
for k in ("url","publicUrl","domain"):
    v=d.get(k) if isinstance(d,dict) else None
    if v:
        print(v if str(v).startswith("http") else f"https://{v}"); break
' || true)"
fi

echo
echo "========================================"
if [[ -n "${LIVE_URL:-}" ]]; then
  echo "LIVE URL: $LIVE_URL"
  echo "Health:   $LIVE_URL/health"
  echo "========================================"
  echo "==> Waiting for health..."
  for i in $(seq 1 30); do
    code=$(curl -s -o /tmp/a2a_health.json -w "%{http_code}" --max-time 5 "$LIVE_URL/health" || echo 000)
    if [[ "$code" == "200" ]]; then
      echo "Health OK:"
      cat /tmp/a2a_health.json; echo
      exit 0
    fi
    echo "  attempt $i: HTTP $code — sleeping 5s"
    sleep 5
  done
  echo "Deploy finished but /health did not return 200 yet. Check: railway logs"
  exit 1
else
  echo "Could not auto-detect URL. Run:"
  echo "  railway domain list"
  echo "  railway status"
  echo "========================================"
  exit 1
fi
