# PayFlow Demo — Multi-Language Payment System

This repository implements `daa-e2e-demo`, a realistic multi-language payment system designed to showcase the capabilities of the Distributed Autonomous Agent (DAA).

## Architecture

The demo consists of the following components:

- **`payment-api`** (Python FastAPI): Customer-facing REST API. Accepts checkout requests, caches sessions in Redis, stores transactions in PostgreSQL, and publishes payment jobs to RabbitMQ.
- **`payment-worker`** (Go): Background queue worker. Consumes jobs from RabbitMQ, simulates payment processing, and updates status in PostgreSQL.
- **Infrastructure Services**:
  - `redis`: Session cache, configured with a `50mb` memory limit to trigger OOM under load.
  - `postgres`: Transaction database.
  - `rabbitmq`: Job queue.
  - `gitea`: Lightweight git repository hosting (replaces GitLab for faster startups).

## Scenario A: Redis OOM (Primary Demo)

1. The API caches user checkout sessions.
2. A developer added checkout analytics tracking in `payment-api/cache.py` but forgot to set a TTL (Time-to-Live) on the analytics keys.
3. Under normal load, memory growth is negligible. But during a simulated flash sale load test (`ab` load test), Redis memory utilization hits the 50MB limit and triggers an Out-of-Memory (OOM) error.
4. The DAA SDK captures the Redis exception and escalates it to the DAA backend.
5. The DAA agent identifies the root cause (unbounded analytics keys in `cache.py`), proposes a fix (adding `expire()`), and pushes a Pull Request to Gitea.

## Setup & Running

To run the entire E2E demonstration flow:

```bash
./run_demo.py
```

This orchestrator script will:
1. Spin up the infrastructure in Docker.
2. Configure Gitea, create the repositories, and push the code.
3. Build and launch `payment-api` and `payment-worker`.
4. Register the applications with the DAA backend.
5. Execute the Apache Benchmark load test (`load_test.sh`) to trigger the Redis OOM.
6. Poll the DAA backend for the proposed fix, auto-approve it, and verify that the Pull Request has been pushed successfully to Gitea.

## Additional Scenarios

- **Scenario B: Cascading Schema Break** (`scenarios/scenario_b_schema_break.py`): Push a breaking change renaming a JSON field in `payment-api` response to trigger queue consumer errors in `payment-worker`.
- **Scenario C: High Cache TTL Tuning** (`scenarios/scenario_c_slow_ttl.py`): Pushes a configuration change to Gitea setting very long cache TTLs to trigger high eviction warnings.
