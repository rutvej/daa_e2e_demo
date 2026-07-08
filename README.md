# PayFlow Demo — Multi-Language E2E Payment System

This repository implements `daa-e2e-demo`, a realistic multi-language payment system designed to showcase the capabilities of the **Distributed Autonomous Agent (DAA)**. It demonstrates how DAA automatically detects, diagnoses, and remediates system failures (like Redis OOM and cascading queue consumer breaks).

---

## 🏗️ Architecture

The demo system consists of two primary microservices and their supporting infrastructure:

- **`payment-api`** (Python/FastAPI): Accepts customer checkout requests, caches sessions in Redis, records transactions in PostgreSQL, and publishes payment processing jobs to RabbitMQ.
- **`payment-worker`** (Go): Consumes payment processing jobs from RabbitMQ, simulates external payment gateway interactions, and updates transaction records in PostgreSQL.
- **Infrastructure Services**:
  - `redis`: Session cache (hard-limited to `50MB` to trigger OOM).
  - `postgres`: Database for transaction state.
  - `rabbitmq`: Messaging broker for background jobs.
  - `gitea`: Lightweight git repository server (hosting the source code pushed from this workspace).

---

## 🚀 Quick Start (Automated Walkthrough)

To run the entire E2E demonstration flow automatically:

```bash
./run_demo.py
```

The orchestrator script will:
1. Spin up the infrastructure in Docker (`gitea`, `redis`, `postgres`, `rabbitmq`).
2. Initialize Gitea, create repositories, and push the local microservice source code.
3. Automatically configure Gitea credentials inside the DAA platform, then invoke the **DAA CLI client** (`daa register` and `daa policy`) to register the microservices and set up SRE thresholds.
4. Launch the microservices with their telemetry tokens injected.
5. Execute the Apache Benchmark load test (`load_test.sh`) to flood the payment cache and trigger the Redis OOM error.
6. Poll the DAA backend for the proposed fix, auto-approve it, and verify that the Pull Request has been pushed successfully to Gitea.

---

## 🛠️ Redesigned SRE Workflow (Manual CLI Steps)

If you prefer to run the installation and registration steps manually to inspect the redesigned DAA SRE workflow, follow this guide:

### 1. Initialize Gitea & Code Repositories
Start the infrastructure and push the microservice repositories to Gitea:
```bash
./run_demo.py --skip-registration --skip-load-test
```
*Take note of the Git Token generated and printed by the script (or retrieve it from `.gitea_token`).*

### 2. Configure the DAA CLI
Navigate to the DAA workspace directory, initialize your configuration, and point it to the local Gitea instance:
```bash
cd ../DAA
./daa init
```
- Select **Gitea** as the Git provider.
- Provide the Git URL (`http://localhost:3000`) and the generated Git Token.
- Enter your Gemini API key and select your preferred active model (e.g. `gemini-2.0-flash`, `gemma-4-31b-it`).
- Cloud Logging credentials (AWS, GCP, Datadog) can be skipped for local development.

### 3. Register the Applications via DAA CLI
Register the microservices and obtain their telemetry tokens:
```bash
./daa register --name payment-api --repo-url http://host.docker.internal:3000/daa-admin/payment-api.git --language python
./daa register --name payment-worker --repo-url http://host.docker.internal:3000/daa-admin/payment-worker.git --language go
```

### 4. Configure Escalation Policies
Set up SRE escalation rules for the registered applications using the CLI:
```bash
./daa policy --app payment-api --threshold 3 --window 60
./daa policy --app payment-worker --threshold 3 --window 60
```
*(This triggers an SRE agent run if more than 3 errors are recorded within a 60-second window).*

### 5. Inject Telemetry Tokens & Deploy
Save the returned `DAA_TOKEN`s inside the demo's `.env` file:
```bash
# In daa-e2e-demo/.env
DAA_TOKEN_PAYMENT_API=<payment-api-token>
DAA_TOKEN_PAYMENT_WORKER=<payment-worker-token>
```
Deploy the microservice containers:
```bash
docker-compose up -d --build payment-api payment-worker
```

### 6. Simulate the Outage
Trigger the outage by flooding the cache:
```bash
./load_test.sh
```

---

## 🔍 Additional Scenarios

- **Scenario B: Cascading Schema Break** (`scenarios/scenario_b_schema_break.py`): Pushes a breaking change renaming a JSON field in `payment-api` to trigger consumer failures in `payment-worker`.
- **Scenario C: High Cache TTL Tuning** (`scenarios/scenario_c_slow_ttl.py`): Pushes a configuration change setting very long cache TTLs to trigger high eviction warnings.
