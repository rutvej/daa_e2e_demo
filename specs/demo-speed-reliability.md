# daa-e2e-demo — Speed & Reliability Specs

> These specs cut a full demo run from ~18 minutes to ~3 minutes for re-runs,
> and eliminate the most common failure modes (stale queue messages, load test flake).

---

## Spec 1 — Direct Incident Injection (No Load Test)

**File:** `inject_incident.sh` *(already exists — verify payload matches below)*

### Problem
The Apache Benchmark load test takes 5–10 minutes to trigger Redis OOM and is unreliable —
Redis may evict silently under certain conditions or the ab request volume may be insufficient.
Stale RabbitMQ messages from a previous run can also cause the agent to analyze the wrong error.

### Solution
POST a synthetic Redis OOM payload directly to `POST /logs/`, bypassing the load test entirely.
The orchestrator queues the log_id into `fix_jobs` immediately.

### Contract
| Field | Value |
|-------|-------|
| **Input** | `app_name` (string), `error_message` (string) |
| **Output** | `log_id` queued in RabbitMQ `fix_jobs` exchange |
| **Time** | ~3 seconds (vs 5–10 minutes for load test) |
| **Endpoint** | `POST http://localhost:8000/logs/` |

### Reference Payload
```json
{
  "app_name": "payment-api",
  "error_message": "OOM command not allowed when used memory > 'maxmemory'. Consider enabling clustering. (error code: 9)",
  "stack_trace": "redis.exceptions.ResponseError: OOM command not allowed...",
  "severity": "critical",
  "environment": "production"
}
```

### Why this also fixes the stale-queue bug
When `ab` fires 1000 requests, multiple OOM logs can land in the queue. If the agent nacks one,
RabbitMQ requeues it — interleaved with the next run's messages. `inject_incident.sh` sends
exactly one message, making queue state deterministic.

**Pre-injection steps (add to script):**
```bash
# Purge stale messages before injecting
docker exec daa-e2e-demo-rabbitmq-1 \
  rabbitmqctl purge_queue fix_jobs 2>/dev/null || true
```

---

## Spec 2 — `run_demo.py` Resume Flags

**File:** `run_demo.py`

### Problem
Every failed run restarts from scratch: Gitea setup, container rebuild, token registration —
even when the failure happened in the agent's last polling loop.

### Solution
Add CLI flags that skip expensive setup phases on re-runs.

### Flag Table
| Flag | Phase skipped | Time saved |
|------|--------------|-----------|
| `--skip-infra` | Gitea, Redis, Postgres, RabbitMQ container startup | ~3 min |
| `--skip-registration` | App registration + DAA token write to `.env` | ~30 sec |
| `--skip-load-test` | `ab` load test; calls `inject_incident.sh` instead | ~8 min |
| `--watch-only` | Everything — polls for an existing fix on a running job | instant |

### Typical re-run command
```bash
./run_demo.py --skip-infra --skip-registration --skip-load-test
```

### Implementation sketch
```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--skip-infra",         action="store_true")
parser.add_argument("--skip-registration",  action="store_true")
parser.add_argument("--skip-load-test",     action="store_true")
parser.add_argument("--watch-only",         action="store_true")
args = parser.parse_args()

if not args.skip_infra and not args.watch_only:
    start_infrastructure()          # docker-compose up

if not args.skip_registration and not args.watch_only:
    register_apps_and_write_tokens()

if not args.skip_load_test and not args.watch_only:
    run_ab_load_test()
else:
    inject_incident()               # calls inject_incident.sh

poll_for_fix()                      # always runs (unless --watch-only handles its own loop)
```

### State assumptions for `--skip-infra`
All containers from the previous run must still be up. Check with:
```bash
docker ps --filter "name=daa-e2e-demo" --format "table {{.Names}}\t{{.Status}}"
```

---

## Spec 6 — `watch_demo.sh` Live Monitor

**File:** `watch_demo.sh` *(new file — create at repo root)*

### Problem
Debugging the demo requires opening 3–4 separate `docker logs` windows and manually
inspecting RabbitMQ queue depth, DAA incident state, and agent output.

### Solution
A single shell script that refreshes every 5 seconds and shows all live state in one terminal.

### Output Format
```
=== DAA Monitor [09:11:31] ===
Queue:      fix_jobs  0 messages
Incidents:  [abc12345 processing] [def67890 fix_proposed]
Agent:      [last 3 log lines from daa-python-agent-1]
Fixes:      [fix-id-1 awaiting_approval]
```

### Implementation
```bash
#!/usr/bin/env bash
# watch_demo.sh — Live DAA demo monitor
# Usage: ./watch_demo.sh [DAA_BASE_URL] [INTERVAL_SECONDS]

DAA_URL="${1:-http://localhost:8000}"
INTERVAL="${2:-5}"
TOKEN="${DAA_TOKEN:-}"          # read from env or .env file

while true; do
  clear
  echo "=== DAA Monitor [$(date +%T)] ==="

  # --- RabbitMQ queue depth ---
  QUEUE_DEPTH=$(docker exec daa-e2e-demo-rabbitmq-1 \
    rabbitmqctl list_queues name messages 2>/dev/null \
    | grep fix_jobs | awk '{print $2}')
  echo "Queue:      fix_jobs  ${QUEUE_DEPTH:-?} messages"

  # --- Incident states ---
  echo -n "Incidents:  "
  curl -sf -H "Authorization: Bearer $TOKEN" "$DAA_URL/logs/" \
    | python3 -c "
import json,sys
logs = json.load(sys.stdin)
for l in logs[-5:]:
    print(f'[{l[\"id\"][:8]} {l[\"status\"]}]', end=' ')
print()
" 2>/dev/null || echo "(unavailable)"

  # --- Agent last 3 lines ---
  echo "Agent:"
  docker logs daa-python-agent-1 --tail 3 2>/dev/null \
    | sed 's/^/  /' || echo "  (container not running)"

  # --- Fix proposals ---
  echo -n "Fixes:      "
  curl -sf -H "Authorization: Bearer $TOKEN" "$DAA_URL/fixes/" \
    | python3 -c "
import json,sys
fixes = json.load(sys.stdin)
for f in fixes[-3:]:
    print(f'[{f[\"id\"][:8]} {f[\"status\"]}]', end=' ')
print()
" 2>/dev/null || echo "(unavailable)"

  sleep "$INTERVAL"
done
```

### Usage
```bash
# Basic (reads DAA_TOKEN from env)
source .env && ./watch_demo.sh

# Custom URL and 10-second refresh
./watch_demo.sh http://localhost:8000 10
```

---

## Open Bugs — Status

| Bug | Root Cause | Resolved by |
|-----|-----------|------------|
| Agent analyzes wrong error (SSL instead of OOM) | Stale RabbitMQ messages survive queue purge when agent nacks | Spec 1 — purge queue before inject |
| Redis silently evicts under `allkeys-lru` | Policy allows eviction without error | ✅ Fixed — `noeviction` in docker-compose |
| `sslmode=disable` missing from Go worker `DATABASE_URL` | Go worker Dockerfile ENV omitted it | ✅ Fixed — added to docker-compose |
| `update_analysis_processing` crashes on stale `log_id` | Non-critical DB call was fatal | ✅ Fixed — wrapped in `try/except` |
| LangChain template KeyError crash | Single curly braces in `PlanningValidator` formatted string parsed as variables | ✅ Fixed — doubled to `{{` and `}}` in `agent_safety.py` |
| Python-agent backend 404 on `/apps/...` | Route was only registered under `/applications` in backend | ✅ Fixed — added `/apps` router alias in backend `main.py` |
| Backend 404 when querying by app name | Backend routes assumed all path parameters were database UUIDs | ✅ Fixed — added name-based fallback lookup in backend routers |
| SRE agent fails to resolve repository URL | Mismatch between agent's `"repo_url"` key expectation and backend's `"repository_url"` field | ✅ Fixed — exposed both keys in backend `ApplicationResponse` |
| git worktree creation fails with exit code 128 | SRE agent hardcoded `main` branch checkout, but demo repository default branch is `master` | ✅ Fixed — added automatic `master` fallback checkout in `orchestrator.py` |
| E2E demo walkthrough uses raw HTTP for registration | Walkthrough registered apps and policies via API instead of validating new CLI | ✅ Fixed — refactored `run_demo.py` to use `daa register` and `daa policy` |

