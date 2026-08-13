#!/usr/bin/env bash
# Pay the production A2A verifier once via Coinbase Agentic Wallet (awal).
#
# Prerequisites (you do these interactively):
#   1. Node.js 24+  (docs: https://docs.cdp.coinbase.com/agentic-wallet/cli/quickstart)
#   2. npx awal@latest auth login <your-email>
#   3. npx awal@latest auth verify <flowId> <otp>
#   4. Fund the Agentic Wallet with USDC on Base (npx awal@latest show → Fund)
#      IMPORTANT: this payer address must NOT equal PAYMENT_WALLET_ADDRESS
#      (CDP rejects self_send_not_allowed).
#
# Usage:
#   ./scripts/pay_with_awal.sh
#   ./scripts/pay_with_awal.sh 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
#
set -euo pipefail

TOKEN="${1:-0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913}"
BASE_URL="${BASE_A2A_VERIFIER_URL:-https://a2a-verifier-production.up.railway.app}"
URL="${BASE_URL}/verify?token=${TOKEN}"
# $0.05 USDC = 50000 atomic units; allow a tiny buffer
MAX_AMOUNT="${MAX_AMOUNT:-60000}"

if ! [[ "$TOKEN" =~ ^0x[0-9a-fA-F]{40}$ ]]; then
  echo "error: token must be a 42-char hex address" >&2
  exit 1
fi

echo "==> Checking Agentic Wallet status..."
npx --yes awal@latest status

echo "==> Payer address (must differ from merchant payTo):"
npx --yes awal@latest address

echo "==> Balance:"
npx --yes awal@latest balance --chain base

echo "==> Paying ${URL} (max ${MAX_AMOUNT} atomic USDC)..."
npx --yes awal@latest x402 pay "$URL" \
  -X GET \
  --max-amount "$MAX_AMOUNT" \
  --json
