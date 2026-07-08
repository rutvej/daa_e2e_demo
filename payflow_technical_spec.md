# PayFlow Demo — Complete Technical Spec

> Rebuilding `daa-e2e-demo` into a realistic multi-language payment system

---

## Decision: SDK From Day 1

**SDK is baked into all services at build time.** No mock agent integration phase. No runtime source code modification.

Why:
- Current `run_demo.py` lines 106–231 (the `mock_agent_integrate_sdk` function) do string replacement on source files → fragile, duplicates code 3x (as we saw in checkout-service)
- The SDK is ~10 lines per service — just import and call
- The demo story becomes: "Your app already has our SDK → start it → load test → watch DAA catch and fix real bugs"
- Saves ~130 lines of brittle orchestration code

---

## What Changes From Current Demo

### DELETE (remove entirely)

| Item | Why |
|---|---|
| `test-app/` | Generic error catalog, not a real service. Replace with Go worker |
| `gitlab` service in docker-compose | 3GB+ image, 4-min startup. Replace with Gitea (~100MB, 10s startup) |
| `checkout-service/app.py` lines 65–72 | Fake `RedisCache` class with planted typo bug. Replace with real Redis usage |
| `run_demo.py` → `mock_agent_integrate_sdk()` | Source code modification at runtime. SDK is now baked in |
| `daa-sdk/` copies inside services | No copying at runtime. Services have their own SDK dependency |

### KEEP (reuse/adapt)

| Item | Adapt How |
|---|---|
| Project structure (`docker-compose.yml` + service dirs) | Same pattern, different services |
| `run_demo.py` → `register_daa_apps_and_get_tokens()` | Same flow, different app names |
| `run_demo.py` → `trigger_outage_and_verify()` | Replace 3 POST requests with Apache Benchmark load test |
| `run_demo.py` → `wait_for_service()`, `run_cmd()` | Reuse as-is |
| `.env` token injection pattern | Same |

### ADD (new)

| Item | What |
|---|---|
| `payment-api/` | Python FastAPI — real payment processing with real Redis + PostgreSQL |
| `payment-worker/` | Go — consumes RabbitMQ jobs, processes payments |
| `postgres` service | Transaction storage |
| `rabbitmq` service | Job queue between API and worker |
| `gitea` service | Lightweight git hosting (~100MB) |
| `redis` with `maxmemory 50mb` | Memory-limited to trigger OOM under load |
| Load testing via `ab` (Apache Benchmark) | Real traffic simulation |

---

## New Project Structure

```
daa-e2e-demo/
├── docker-compose.yml          # All infra + app services
├── .env                        # Generated tokens (by run_demo.py)
├── run_demo.py                 # Orchestrator (simplified, no source modification)
├── load_test.sh                # Apache Benchmark script
├── scenarios/
│   ├── scenario_a_redis_oom.sh     # Trigger Redis OOM via load
│   ├── scenario_b_schema_break.py  # Push breaking API change
│   └── scenario_c_slow_ttl.py      # Inject high TTL config
├── payment-api/                # Python FastAPI service
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py                  # Main application
│   ├── models.py               # Pydantic models
│   ├── cache.py                # Redis caching logic (contains realistic bug)
│   ├── database.py             # PostgreSQL models
│   └── test_app.py             # Tests
├── payment-worker/             # Go service
│   ├── Dockerfile
│   ├── go.mod
│   ├── go.sum
│   ├── main.go                 # RabbitMQ consumer + payment processor
│   ├── handler.go              # Payment processing logic
│   └── daa/                    # Embedded Go SDK (copy from DAA/app/daa-sdk/go-sdk)
│       ├── daa.go
│       └── go.mod (reference)
└── README.md
```

---

## Service Specifications

### 1. `payment-api` (Python FastAPI) — Port 8001

**Role**: Customer-facing REST API. Accepts checkout requests, caches sessions in Redis, stores transactions in PostgreSQL, publishes payment jobs to RabbitMQ.

#### API Endpoints

```
POST /checkout
  Request:  { "user_id": "usr_abc", "cart_total": 150.00, "currency": "USD", "items": ["item1", "item2"] }
  Response: { "status": "PENDING", "transaction_id": "txn_a1b2c3", "trace_id": "trace-abc123" }
  Logic:
    1. Generate trace_id, transaction_id
    2. Cache session in Redis (THIS IS WHERE THE REALISTIC BUG LIVES)
    3. Insert transaction record in PostgreSQL (status=PENDING)
    4. Publish job to RabbitMQ queue "payment_jobs"
    5. Return immediately (async processing)

GET /status/{transaction_id}
  Response: { "transaction_id": "txn_a1b2c3", "status": "COMPLETED", "amount": 150.00 }
  Logic: Read from PostgreSQL

GET /health
  Response: { "status": "healthy", "redis": "connected", "postgres": "connected", "rabbitmq": "connected" }
```

#### File: `cache.py` — The Realistic Bug

```python
import redis
import json
import time
from uuid import uuid4

class SessionCache:
    def __init__(self, host: str, port: int, max_memory: str = "50mb"):
        self.redis = redis.Redis(host=host, port=port, decode_responses=True)
    
    def cache_checkout_session(self, user_id: str, transaction_id: str, session_data: dict):
        """Cache the checkout session for quick status lookups."""
        # Primary session key - has proper TTL
        session_key = f"session:{transaction_id}"
        self.redis.set(session_key, json.dumps(session_data))
        self.redis.expire(session_key, 3600)  # 1 hour TTL ✓
        
        # User's active sessions index - has proper TTL
        user_key = f"user_sessions:{user_id}"
        self.redis.sadd(user_key, transaction_id)
        self.redis.expire(user_key, 86400)  # 24 hour TTL ✓
        
        # ========================================================
        # Analytics tracking — PER-REQUEST unique key, NO TTL
        # This is a realistic oversight: developer added analytics
        # tracking and forgot the expire() call. Under normal traffic
        # (10 req/min) this is invisible. Under load (1000 req/min)
        # with 50MB Redis limit, it fills up in ~5 minutes.
        # ========================================================
        analytics_key = f"analytics:checkout:{user_id}:{transaction_id}:{int(time.time())}"
        analytics_data = {
            "user_id": user_id,
            "transaction_id": transaction_id,
            "cart": session_data.get("items", []),
            "amount": session_data.get("cart_total", 0),
            "timestamp": time.time(),
            "source": session_data.get("source", "web"),
            "user_agent": session_data.get("user_agent", "unknown"),
        }
        self.redis.set(analytics_key, json.dumps(analytics_data))
        # NOTE: No self.redis.expire() here — this is the bug
        # Each key is ~200 bytes. At 1000 req/min = 200KB/min = 12MB/hr
        # With maxmemory 50mb, Redis OOM in ~4 hours normal traffic
        # With ab -n 10000 -c 50, Redis OOM in ~2 minutes
```

> [!IMPORTANT]
> This is NOT a planted bug with a comment saying "BUG HERE". It's a **real code pattern** — a developer added analytics tracking (legitimate feature) and forgot the TTL (realistic oversight). The other two Redis operations have proper TTLs, making this look even more natural.

#### File: `app.py` — Main Application

```python
import os
import uuid
import json
import pika
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from daa_sdk import DaaSdk           # SDK baked in
from cache import SessionCache
from database import Database

app = FastAPI(title="PayFlow API", version="1.0.0")

# Initialize connections
redis_cache = SessionCache(
    host=os.getenv("REDIS_HOST", "redis"),
    port=int(os.getenv("REDIS_PORT", 6379))
)
db = Database(os.getenv("DATABASE_URL", "postgresql://user:pass@postgres/payflow"))
daa = DaaSdk(backend_url=os.getenv("DAA_BACKEND_API_URL"))

# RabbitMQ connection for publishing
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")

class CheckoutRequest(BaseModel):
    user_id: str
    cart_total: float
    currency: str = "USD"
    items: list[str] = []

@app.post("/checkout")
def checkout(req: CheckoutRequest):
    trace_id = f"trace-{uuid.uuid4().hex[:12]}"
    txn_id = f"txn_{uuid.uuid4().hex[:8]}"
    
    try:
        # 1. Cache session in Redis
        redis_cache.cache_checkout_session(
            user_id=req.user_id,
            transaction_id=txn_id,
            session_data=req.model_dump()
        )
    except Exception as e:
        daa.capture_exception(e)  # SDK reports to DAA
        raise HTTPException(status_code=503, detail=f"Cache error: {str(e)}")
    
    try:
        # 2. Store in PostgreSQL
        db.insert_transaction(txn_id, req.user_id, req.cart_total, req.currency, "PENDING")
    except Exception as e:
        daa.capture_exception(e)
        raise HTTPException(status_code=503, detail=f"Database error: {str(e)}")
    
    try:
        # 3. Publish to RabbitMQ
        connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
        channel = connection.channel()
        channel.queue_declare(queue="payment_jobs", durable=True)
        channel.basic_publish(
            exchange="",
            routing_key="payment_jobs",
            body=json.dumps({
                "transaction_id": txn_id,
                "user_id": req.user_id,
                "amount": req.cart_total,
                "currency": req.currency,
                "trace_id": trace_id
            }),
            properties=pika.BasicProperties(delivery_mode=2)  # persistent
        )
        connection.close()
    except Exception as e:
        daa.capture_exception(e)
        raise HTTPException(status_code=503, detail=f"Queue error: {str(e)}")
    
    return {"status": "PENDING", "transaction_id": txn_id, "trace_id": trace_id}

@app.get("/status/{transaction_id}")
def get_status(transaction_id: str):
    txn = db.get_transaction(transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return txn

@app.get("/health")
def health():
    return {"status": "healthy", "service": "payment-api"}
```

#### File: `database.py` — PostgreSQL Models

```python
import psycopg2
import os
from datetime import datetime

class Database:
    def __init__(self, url: str):
        self.url = url
        self._ensure_tables()
    
    def _get_conn(self):
        return psycopg2.connect(self.url)
    
    def _ensure_tables(self):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id VARCHAR(50) PRIMARY KEY,
                user_id VARCHAR(100) NOT NULL,
                amount DECIMAL(10,2) NOT NULL,
                currency VARCHAR(3) DEFAULT 'USD',
                status VARCHAR(20) DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        conn.close()
    
    def insert_transaction(self, txn_id, user_id, amount, currency, status):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO transactions (transaction_id, user_id, amount, currency, status) VALUES (%s, %s, %s, %s, %s)",
            (txn_id, user_id, amount, currency, status)
        )
        conn.commit()
        conn.close()
    
    def get_transaction(self, txn_id):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT transaction_id, user_id, amount, currency, status, created_at FROM transactions WHERE transaction_id = %s", (txn_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "transaction_id": row[0], "user_id": row[1],
            "amount": float(row[2]), "currency": row[3],
            "status": row[4], "created_at": str(row[5])
        }
    
    def update_status(self, txn_id, status):
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE transactions SET status = %s, updated_at = NOW() WHERE transaction_id = %s", (status, txn_id))
        conn.commit()
        conn.close()
```

#### File: `requirements.txt`

```
fastapi==0.115.0
uvicorn==0.30.0
redis==5.0.0
psycopg2-binary==2.9.9
pika==1.3.2
pydantic==2.9.0
requests==2.32.0
daa-sdk @ file:./daa-sdk
```

#### File: `Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY . /app/

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
```

---

### 2. `payment-worker` (Go) — No exposed port

**Role**: Background worker. Consumes payment jobs from RabbitMQ, simulates payment processing, updates PostgreSQL status. Reports errors to DAA via Go SDK.

#### File: `main.go`

```go
package main

import (
    "encoding/json"
    "fmt"
    "log"
    "os"
    "time"
    
    "payment-worker/daa"
    
    amqp "github.com/rabbitmq/amqp091-go"
    "github.com/lib/pq"
    "database/sql"
)

type PaymentJob struct {
    TransactionID string  `json:"transaction_id"`
    UserID        string  `json:"user_id"`
    Amount        float64 `json:"amount"`
    Currency      string  `json:"currency"`
    TraceID       string  `json:"trace_id"`
}

var db *sql.DB
var daaClient *daa.Client

func main() {
    // Init DAA SDK
    daaClient = daa.NewClient(
        os.Getenv("DAA_BACKEND_API_URL"),
        os.Getenv("DAA_TOKEN"),
        "payment-worker",
    )
    
    // Init PostgreSQL
    var err error
    db, err = sql.Open("postgres", os.Getenv("DATABASE_URL"))
    if err != nil {
        log.Fatalf("Failed to connect to DB: %v", err)
    }
    defer db.Close()
    
    // Connect to RabbitMQ with retry
    var conn *amqp.Connection
    for i := 0; i < 30; i++ {
        conn, err = amqp.Dial(fmt.Sprintf("amqp://guest:guest@%s:5672/", os.Getenv("RABBITMQ_HOST")))
        if err == nil {
            break
        }
        log.Printf("Waiting for RabbitMQ... (%d/30)", i+1)
        time.Sleep(2 * time.Second)
    }
    if err != nil {
        log.Fatalf("Failed to connect to RabbitMQ: %v", err)
    }
    defer conn.Close()
    
    ch, _ := conn.Channel()
    defer ch.Close()
    
    q, _ := ch.QueueDeclare("payment_jobs", true, false, false, false, nil)
    msgs, _ := ch.Consume(q.Name, "", false, false, false, false, nil)
    
    log.Println("Payment worker started. Waiting for jobs...")
    
    for msg := range msgs {
        var job PaymentJob
        if err := json.Unmarshal(msg.Body, &job); err != nil {
            daaClient.CaptureException(fmt.Errorf("JSON unmarshal error: %w", err))
            msg.Nack(false, false)
            continue
        }
        
        if err := processPayment(job); err != nil {
            daaClient.CaptureException(err)
            msg.Nack(false, true) // requeue
        } else {
            msg.Ack(false)
        }
    }
}

func processPayment(job PaymentJob) error {
    log.Printf("[%s] Processing payment: $%.2f for user %s", job.TraceID, job.Amount, job.UserID)
    
    // Simulate payment gateway processing
    time.Sleep(100 * time.Millisecond)
    
    // Reject payments over $10,000
    if job.Amount > 10000 {
        updateStatus(job.TransactionID, "DECLINED")
        return fmt.Errorf("payment declined: amount $%.2f exceeds limit", job.Amount)
    }
    
    // Update transaction status in PostgreSQL
    return updateStatus(job.TransactionID, "COMPLETED")
}

func updateStatus(txnID, status string) error {
    _, err := db.Exec(
        "UPDATE transactions SET status = $1, updated_at = NOW() WHERE transaction_id = $2",
        status, txnID,
    )
    if err != nil {
        return fmt.Errorf("DB update failed for %s: %w", txnID, err)
    }
    log.Printf("Transaction %s → %s", txnID, status)
    return nil
}
```

#### File: `Dockerfile`

```dockerfile
FROM golang:1.22-alpine AS builder

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o payment-worker .

FROM alpine:3.19
RUN apk --no-cache add ca-certificates
WORKDIR /app
COPY --from=builder /app/payment-worker .
CMD ["./payment-worker"]
```

#### File: `go.mod`

```
module payment-worker

go 1.22

require (
    github.com/rabbitmq/amqp091-go v1.10.0
    github.com/lib/pq v1.10.9
)
```

#### DAA SDK: Copy `/Desktop/DAA/app/daa-sdk/go-sdk/` → `payment-worker/daa/`
The Go SDK is already implemented and functional (108 lines, has `CaptureException`, `SendLog`).

---

### 3. Docker Compose — `docker-compose.yml`

```yaml
services:
  # ============ INFRASTRUCTURE ============
  
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    command: >
      redis-server
      --maxmemory 50mb
      --maxmemory-policy allkeys-lru
    deploy:
      resources:
        limits:
          memory: 64M
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: payflow
      POSTGRES_USER: payflow
      POSTGRES_PASSWORD: payflow_secret
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U payflow"]
      interval: 5s
      timeout: 3s
      retries: 5

  rabbitmq:
    image: rabbitmq:3-management-alpine
    ports:
      - "5672:5672"
      - "15672:15672"
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  gitea:
    image: gitea/gitea:latest
    environment:
      - GITEA__database__DB_TYPE=sqlite3
      - GITEA__server__ROOT_URL=http://localhost:3000
      - GITEA__server__HTTP_PORT=3000
      - GITEA__security__INSTALL_LOCK=true
      - GITEA__service__DISABLE_REGISTRATION=false
    ports:
      - "3000:3000"
    volumes:
      - gitea_data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000/api/v1/version"]
      interval: 10s
      timeout: 5s
      retries: 10

  # ============ APPLICATION ============

  payment-api:
    build:
      context: ./payment-api
    restart: always
    ports:
      - "8001:8001"
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - DATABASE_URL=postgresql://payflow:payflow_secret@postgres/payflow
      - RABBITMQ_HOST=rabbitmq
      - DAA_BACKEND_API_URL=http://host.docker.internal:8000
      - DAA_LOGS_URL=http://host.docker.internal:8000/logs/
      - DAA_TOKEN=${DAA_TOKEN_PAYMENT_API}
      - REPO_NAME=payment-api
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
      rabbitmq:
        condition: service_healthy
    extra_hosts:
      - "host.docker.internal:host-gateway"

  payment-worker:
    build:
      context: ./payment-worker
    restart: always
    environment:
      - DATABASE_URL=postgresql://payflow:payflow_secret@postgres/payflow
      - RABBITMQ_HOST=rabbitmq
      - DAA_BACKEND_API_URL=http://host.docker.internal:8000
      - DAA_TOKEN=${DAA_TOKEN_PAYMENT_WORKER}
      - REPO_NAME=payment-worker
    depends_on:
      rabbitmq:
        condition: service_healthy
      postgres:
        condition: service_healthy
    extra_hosts:
      - "host.docker.internal:host-gateway"

volumes:
  pgdata:
  gitea_data:
```

> [!NOTE]
> **Key detail**: Redis is configured with `maxmemory 50mb` and a Docker memory limit of `64M`. This means under sustained load, the analytics keys without TTL will fill Redis → eviction starts → eventually OOM when write pressure exceeds eviction rate.

---

## Demo Scenarios

### Scenario A: Redis OOM Under Load (Primary Demo)

**Story**: "Your payment API has been running fine for weeks. Today, marketing launched a flash sale. Traffic spikes 10x. Redis crashes. DAA catches it, diagnoses it, and fixes it."

**Reproduction steps** (in `run_demo.py`):

```bash
# Step 1: Start everything, verify healthy
docker-compose up -d
# Wait for all healthchecks

# Step 2: Normal traffic works fine
curl -X POST http://localhost:8001/checkout \
  -H "Content-Type: application/json" \
  -d '{"user_id": "usr_001", "cart_total": 49.99, "items": ["shirt"]}'
# → {"status": "PENDING", "transaction_id": "txn_abc123"}

# Step 3: Flash sale load test
ab -n 10000 -c 50 -p payload.json -T application/json http://localhost:8001/checkout

# Step 4: Watch Redis fill up (monitor in another terminal)
docker exec demo-redis redis-cli INFO memory
# used_memory: 48.5M / 50M → then OOM

# Step 5: API starts returning 503s → DAA SDK reports errors
# DAA escalation triggers (3 RedisConnectionError in 60s)
# Agent investigates → finds analytics keys without TTL → raises PR
```

**What DAA's agent should find**:
1. **Dimension 2 (Infra)**: Redis at 100% memory, OOM kill detected
2. **Dimension 4 (Code)**: `cache.py` line where `analytics_key` is set without `expire()`
3. **Fix**: Add `self.redis.expire(analytics_key, 3600)` after the `set()` call
4. **Postmortem**: "Redis OOM caused by unbounded analytics key growth. Analytics keys in `cache.py` lack TTL. Under flash sale load (10K requests), 50MB Redis filled in ~2 minutes."

### Scenario B: Cascading Schema Change (Advanced Demo)

**Story**: "A developer renamed `transaction_id` to `txn_id` in the API response. The Go worker expects `transaction_id` from RabbitMQ. Payments stop processing."

**Reproduction steps**:

```python
# scenario_b_schema_break.py
# 1. Push a commit that changes the JSON field name in payment-api
# 2. Rebuild and restart payment-api
# 3. Run load test → Go worker starts failing on every message
# 4. DAA detects spike in Go worker errors
# 5. Agent: Dimension 1 (Change) → finds the rename commit
# 6. Agent: Dimension 4 (Code) → traces field through both services
# 7. PR: Updates Go worker to use new field name
```

### Scenario C: TTL Too High (Tuning Demo)

**Story**: "TTLs are set to 24 hours but Redis only has 50MB. Under normal traffic, eviction rate is too high. DAA suggests reducing TTL."

This is a lighter scenario — DAA detects high eviction rates and suggests config change.

---

## New `run_demo.py` — Simplified Orchestrator

The new orchestrator is **much simpler** than the current one because:
- No `mock_agent_integrate_sdk()` (SDK is baked in)
- No GitLab rails runner hacks (Gitea is simpler)
- No source code modification at runtime

```
Phase 1: docker-compose up -d (all services)
Phase 2: Wait for healthchecks (Gitea, Redis, PostgreSQL, RabbitMQ, payment-api, payment-worker)
Phase 3: Setup Gitea (create user, create repos, push code via API)
Phase 4: Register apps in DAA backend (same as current, adapted for 2 apps)
Phase 5: Run Scenario A (load test → wait for DAA incident → verify fix → approve → check MR on Gitea)
```

**Estimated lines**: ~250 (vs current 440)

---

## Gitea Setup (Replacing GitLab)

Gitea API is simpler than GitLab:

```python
GITEA_URL = "http://localhost:3000"
GITEA_USER = "daa-admin"
GITEA_PASS = "DaaDemo123!"

def setup_gitea():
    # Create admin user via API
    requests.post(f"{GITEA_URL}/api/v1/admin/users", json={
        "username": GITEA_USER,
        "password": GITEA_PASS,
        "email": "admin@payflow.dev",
        "must_change_password": False
    })
    
    # Create repos
    for repo in ["payment-api", "payment-worker"]:
        requests.post(
            f"{GITEA_URL}/api/v1/user/repos",
            auth=(GITEA_USER, GITEA_PASS),
            json={"name": repo, "auto_init": False}
        )
    
    # Push code via git
    # (same pattern as current demo, but with Gitea URL)
```

> [!TIP]
> Gitea starts in ~10 seconds vs GitLab's ~4 minutes. No rails runner hacks needed. API is REST-based and straightforward.

---

## Build Order (Token-Optimized)

```mermaid
graph LR
    A["1. docker-compose.yml<br/>(infra only)"] --> B["2. payment-api<br/>(Python FastAPI)"]
    B --> C["3. payment-worker<br/>(Go + SDK)"]
    C --> D["4. run_demo.py<br/>(orchestrator)"]
    D --> E["5. load_test.sh<br/>(Apache Benchmark)"]
    E --> F["6. Test E2E flow"]
```

| Step | What to Build | Estimated AGY Sessions | Can Test Independently? |
|---|---|---|---|
| 1 | `docker-compose.yml` (infra only: redis, postgres, rabbitmq, gitea) | ~0.5 session | ✅ `docker-compose up` → all healthy |
| 2 | `payment-api/` (all files) | ~1 session | ✅ `curl POST /checkout` → transaction in DB + Redis |
| 3 | `payment-worker/` (all files) | ~1 session | ✅ Job consumed from RabbitMQ → status updated in DB |
| 4 | `run_demo.py` (orchestrator) | ~1 session | ✅ Full setup automation |
| 5 | `load_test.sh` + scenario scripts | ~0.5 session | ✅ Redis OOM triggered |
| 6 | E2E test with DAA running | manual testing | ✅ Full demo |

**Total estimated: ~4 AGY sessions**

---

## DAA Backend Changes Needed

> [!NOTE]
> The DAA core platform (`/Desktop/DAA`) should NOT need changes. The demo is a consumer of DAA, not a modification of it. However, you may need to:

1. **Update escalation policy keywords** to include `RedisConnectionError`, `OOM`, `ConnectionRefusedError` (in `run_demo.py` registration)
2. **Verify Go SDK `exception_type` field** — the current Go SDK sends `content` and `app_name` but the backend expects `exception_type` too. May need a small fix in `daa.go` to add this field.
3. **Update project connection** to point to Gitea instead of GitLab (change `repo_provider` to `gitea` or keep as `gitlab` since Gitea is API-compatible)

---

## Summary of Decisions Made

| Decision | Choice | Rationale |
|---|---|---|
| SDK integration timing | Day 1, baked in | Eliminates fragile runtime source modification |
| Git hosting | Gitea (Docker) | 30x lighter than GitLab, 10s startup |
| Jira | DAA's built-in mock Jira | Already works, zero setup |
| Bug approach | Realistic deterministic | Reproducible for demos but looks natural |
| Languages | Python API + Go Worker | Showcases multi-language SDK |
| Load testing | Apache Benchmark (`ab`) | Simple, no dependencies, already on most Linux |
| Primary scenario | Redis OOM under load | Most common real-world SRE issue |
