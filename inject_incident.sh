#!/usr/bin/env bash
# inject_incident.sh — Directly inject a Redis OOM incident into DAA
# without running the full load test. Use this for fast agent debugging.
# Usage: ./inject_incident.sh [app_name] [error_message]

set -e

DAA_URL="${DAA_URL:-http://localhost:8000}"
APP_NAME="${1:-payment-api}"
ERROR_MSG="${2:-FATAL: OOM command not allowed when used memory > maxmemory: COMMAND SET analytics:checkout:usr_test:txn_abc123:$(date +%s)}"

echo "=== DAA Incident Injector ==="
echo "DAA Backend: $DAA_URL"
echo "App: $APP_NAME"
echo ""

# Step 0: Purge RabbitMQ queue
echo "[0/3] Purging stale messages from fix_jobs queue..."
docker exec daa-e2e-demo-rabbitmq-1 rabbitmqctl purge_queue fix_jobs 2>/dev/null || true

# Step 1: Login and get token
echo "[1/3] Authenticating with DAA backend..."
TOKEN=$(curl -sf -X POST "$DAA_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"testpassword"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token') or d.get('access_token'))")

if [ -z "$TOKEN" ]; then
  echo "ERROR: Could not get auth token. Is the DAA backend running at $DAA_URL?"
  exit 1
fi
echo "✓ Got token: ${TOKEN:0:20}..."

# Step 2: Register the app (idempotent — safe to run repeatedly)
echo "[2/3] Registering app '$APP_NAME'..."
REG_RESULT=$(curl -sf -X POST "$DAA_URL/applications/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"name\": \"$APP_NAME\", \"repo_name\": \"$APP_NAME\"}" 2>/dev/null || echo '{}')
echo "✓ App registration: done"

# Step 3: Inject the fake exception log
echo "[3/3] Injecting Redis OOM exception..."
TIMESTAMP=$(python3 -c "from datetime import datetime, timezone; print(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))")
LOG_RESULT=$(curl -sf -X POST "$DAA_URL/logs/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"app_name\": \"$APP_NAME\",
    \"content\": \"{\\\"message\\\": \\\"$ERROR_MSG\\\", \\\"stack_trace\\\": \\\"redis.exceptions.ResponseError: OOM command not allowed when used memory > maxmemory.\\\\n  File cache.py line 38 in cache_checkout_session\\\\n    self.redis.set(analytics_key, json.dumps(analytics_data))\\\", \\\"timestamp\\\": \\\"$TIMESTAMP\\\"}\",
    \"exception_type\": \"redis.exceptions.ResponseError\"
  }")

LOG_ID=$(echo "$LOG_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id','UNKNOWN'))")
echo "✓ Injected log: $LOG_ID"
echo ""
echo "=== Incident queued! Watch the SRE agent: ==="
echo "  docker logs -f daa-python-agent-1"
echo ""
echo "=== Poll for fix status: ==="
echo "  watch -n 5 'curl -s -H \"Authorization: Bearer $TOKEN\" $DAA_URL/incidents/ | python3 -m json.tool'"
