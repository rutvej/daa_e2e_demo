# DAA E2E Matrix & Test-Runner Specification

This specification details the test configurations, backing service requirements, and environment variables used to validate the DAA platform across different execution flows.

---

## 1. Orthogonal Variable Definition

DAA features 5 core independent variables that control platform behavior:
* **`DAA_DB_PROVIDER`**: `none` (bypassed), `sqlite` (local file), `postgres` (external DB).
* **`DAA_GIT_MODE`**: `api` (uses remote Git REST APIs), `local` (clones repositories to local disk).
* **`DAA_QUEUE_MODE`**: `sync` (inline background execution), `rabbitmq` (distributed AMQP queue).
* **`DAA_AUTH_ENABLED`**: `true` (enforces password login), `false` (bypasses tokens).
* **`DAA_POLICY_ENABLED`**: `true` (requires sliding-window error threshold breach), `false` (immediate triage).

---

## 2. Test Combinations Matrix (7 Profiles)

| Profile | Name | DB Provider | Git Mode | Queue Mode | Auth | Policy | Ingest Type | Required Services |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Profile 1** | Strict Serverless | `none` | `api` | `sync` | `false` | `false` | `webhook` | Gitea |
| **Profile 2** | Serverless Hybrid | `postgres` | `api` | `sync` | `true` | `true` | `sdk` | Gitea, Postgres |
| **Profile 3** | Single-Container Edge | `sqlite` | `local` | `sync` | `true` | `true` | `webhook` | Gitea |
| **Profile 4** | Scale-Out Distributed | `postgres` | `local` | `rabbitmq` | `true` | `true` | `webhook` | Gitea, Postgres, RabbitMQ |
| **Profile 5** | Auth-Bypassed Distributed| `postgres` | `local` | `rabbitmq` | `false` | `true` | `sdk` | Gitea, Postgres, RabbitMQ |
| **Profile 6** | Policy-Bypassed Edge | `sqlite` | `local` | `sync` | `true` | `false` | `sdk` | Gitea |
| **Profile 7** | DB Persistence Only | `sqlite` | `local` | `sync` | `false` | `false` | `webhook` | Gitea |

---

## 3. Test-Runner Verification Mechanics

The E2E test harness evaluates two container styles due to their different local test execution models:

### A. Fullstack / Edge Mode (Docker Compose)
* **Execution Environment:** Backing services (Postgres, RabbitMQ, Gitea) run in separate dedicated containers. The DAA agent worker runs inside its own container with volume-mounted access to the docker socket and language compiler tools.
* **Test Verification Flow:** 
  1. The agent clones the codebase to local disk and modifies the files.
  2. The agent executes the **`run_tests`** tool.
  3. The local test-runner (`pytest` or `go test`) runs inside the worktree environment.
  4. The test result must report `success` before the agent pushes the branch.
* **Harness Verification:** Confirm the agent executes tests locally and that failure to pass tests blocks the PR.

### B. Serverless Mode (Single Docker Image)
* **Execution Environment:** API and inline worker run inside a single lightweight Docker container. This container **does not have test runners** installed for every application language (e.g. no Go SDK, no database dependencies needed to spin up application tests).
* **Test Verification Flow:**
  1. The agent uses Git REST APIs to fetch and modify files directly on the remote server (Gitea).
  2. If the agent tries to call `run_tests`, it encounters a missing test environment error (e.g. command not found / exit code 127).
  3. **Fail-Forward Behavior:** The agent must handle this missing test tool exception gracefully, bypassing local test suite verification and immediately opening the Pull Request.
* **Harness Verification:** Confirm the serverless agent bypasses test suite execution and opens the PR directly without crashing or hanging.

---

## 4. Isolation & Cleanup Workflow

To ensure tests do not pollute database state or git worktree cache folders between runs:
1. **Clean Slate:** Stop all containers and wipe database volumes:
   ```bash
   docker-compose down -v
   ```
2. **Filesystem Purge:** Delete all active worker caches and worktrees:
   ```bash
   rm -rf /tmp/daa/* /var/daa/repo-cache/*
   ```
3. **Targeted Boot:** Spin up only the backing containers needed for that specific profile.
4. **Deploy DAA Services:** Update DAA's environment variables and run `daa redeploy`.
5. **Telemetry Ingestion:** Push logs to test policy thresholds or direct ingestion paths.
6. **Polled Verification:** Audit SRE agent logs to verify correct PR generation or escalation status.
