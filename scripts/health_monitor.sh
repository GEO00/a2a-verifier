#!/usr/bin/env bash
# Polls GET /health every 30s and alerts on non-200 responses.
#
# Usage:
#   HEALTH_URL=https://a2a-verifier-production.up.railway.app/health \
#   ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/... \
#   ./scripts/health_monitor.sh
#
# ALERT_WEBHOOK_URL accepts any Slack/Discord-compatible JSON webhook.
# If unset, alerts are only printed to stderr.

set -u

HEALTH_URL="${HEALTH_URL:?Set HEALTH_URL to the deployed /health endpoint}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-30}"
FAILURES_BEFORE_ALERT="${FAILURES_BEFORE_ALERT:-2}"

consecutive_failures=0

alert() {
    local msg="$1"
    echo "[ALERT] $(date -u +%FT%TZ) ${msg}" >&2
    if [[ -n "${ALERT_WEBHOOK_URL}" ]]; then
        curl -sf -X POST -H "Content-Type: application/json" \
            -d "{\"content\": \"[base-a2a-verifier] ${msg}\", \"text\": \"[base-a2a-verifier] ${msg}\"}" \
            "${ALERT_WEBHOOK_URL}" > /dev/null || echo "[WARN] webhook delivery failed" >&2
    fi
}

echo "Monitoring ${HEALTH_URL} every ${INTERVAL_SECONDS}s (alert after ${FAILURES_BEFORE_ALERT} consecutive failures)"

while true; do
    status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${HEALTH_URL}" || echo "000")
    if [[ "${status}" == "200" ]]; then
        if (( consecutive_failures >= FAILURES_BEFORE_ALERT )); then
            alert "RECOVERED: /health returning 200 again"
        fi
        consecutive_failures=0
    else
        consecutive_failures=$((consecutive_failures + 1))
        echo "[WARN] $(date -u +%FT%TZ) /health returned ${status} (failure ${consecutive_failures})" >&2
        if (( consecutive_failures == FAILURES_BEFORE_ALERT )); then
            alert "DOWN: /health returned ${status} for ${consecutive_failures} consecutive checks"
        fi
    fi
    sleep "${INTERVAL_SECONDS}"
done
