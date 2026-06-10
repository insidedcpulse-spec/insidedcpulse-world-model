#!/bin/bash
# One-time, idempotent setup: enable Grafana Public Dashboards for the
# World Stability Index and Event Flow Timeline dashboards, used by /status.
#
# Usage:
#   GRAFANA_ADMIN_PASSWORD=<password> ./setup-public-dashboards.sh [base_url] [resolve_ip]
#
#   base_url   - Grafana base URL behind the nginx proxy
#                (default: https://insidedcpulse.com/grafana)
#   resolve_ip - optional IP to pin base_url's host to via curl --resolve
#                (useful when running on the VPS itself, e.g. 127.0.0.1)
#
# Prints one line per dashboard: "<uid> <created|already-public> accessToken=<token>"
set -euo pipefail

BASE_URL="${1:-https://insidedcpulse.com/grafana}"
RESOLVE_IP="${2:-}"
AUTH="admin:${GRAFANA_ADMIN_PASSWORD}"

CURL_OPTS=(-s -u "$AUTH")
if [ -n "$RESOLVE_IP" ]; then
  HOST=$(echo "$BASE_URL" | sed -E 's#^https?://([^/]+).*#\1#')
  CURL_OPTS+=(--resolve "${HOST}:443:${RESOLVE_IP}")
fi

for uid in idc-world-stability idc-event-flow-timeline; do
  existing=$(curl "${CURL_OPTS[@]}" "$BASE_URL/api/dashboards/uid/$uid/public-dashboards")
  if echo "$existing" | grep -q '"isEnabled":true'; then
    token=$(echo "$existing" | grep -o '"accessToken":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo "$uid already-public accessToken=$token"
  else
    created=$(curl "${CURL_OPTS[@]}" -X POST "$BASE_URL/api/dashboards/uid/$uid/public-dashboards" \
      -H "Content-Type: application/json" \
      -d '{"isEnabled": true, "share": "public"}')
    token=$(echo "$created" | grep -o '"accessToken":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo "$uid created accessToken=$token"
  fi
done
