#!/usr/bin/env bash
# watch_demo.sh — Live DAA demo monitor
# Usage: ./watch_demo.sh [DAA_BASE_URL] [INTERVAL_SECONDS]

DAA_URL="${1:-http://localhost:8000}"
INTERVAL="${2:-5}"

echo "Logging in to DAA backend at $DAA_URL..."
TOKEN=$(curl -sf -X POST "$DAA_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"testpassword"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token') or d.get('access_token'))" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "ERROR: Could not authenticate with DAA backend. Is it running at $DAA_URL?"
  exit 1
fi

while true; do
  clear
  echo "=== DAA Monitor [$(date +%T)] ==="
  echo "DAA URL: $DAA_URL"
  echo "-----------------------------------"

  # --- RabbitMQ queue depth ---
  QUEUE_DEPTH=$(docker exec daa-e2e-demo-rabbitmq-1 \
    rabbitmqctl list_queues name messages 2>/dev/null \
    | grep fix_jobs | awk '{print $2}')
  echo "Queue:      fix_jobs  ${QUEUE_DEPTH:-0} messages"

  # --- Incident states ---
  echo -n "Incidents:  "
  curl -sf -H "Authorization: Bearer $TOKEN" "$DAA_URL/incidents/" \
    | python3 -c "
import sys, json
try:
    logs = json.load(sys.stdin)
    if isinstance(logs, list):
        for l in logs[-5:]:
            print(f'[{l[\"id\"][:8]} {l[\"status\"]}]', end=' ')
    else:
        print('[]', end='')
except Exception:
    print('error', end='')
" 2>/dev/null || echo "(unavailable)"
  echo ""

  # --- Agent last 3 lines ---
  echo "Agent logs:"
  docker logs daa-python-agent-1 --tail 3 2>/dev/null \
    | sed 's/^/  /' || echo "  (container not running)"

  # --- Fix proposals ---
  echo -n "Fixes:      "
  curl -sf -H "Authorization: Bearer $TOKEN" "$DAA_URL/fixes/" \
    | python3 -c "
import sys, json
try:
    fixes = json.load(sys.stdin)
    if isinstance(fixes, list):
        for f in fixes[-3:]:
            print(f'[{f[\"id\"][:8]} {f[\"status\"]}]', end=' ')
    else:
        print('[]', end='')
except Exception:
    print('error', end='')
" 2>/dev/null || echo "(unavailable)"
  echo ""

  sleep "$INTERVAL"
done
