#!/usr/bin/env python3
import os
import time
import subprocess
import requests

DAA_PATH = "/home/rutvej/Desktop/DAA"
DEMO_PATH = "/home/rutvej/Desktop/daa-e2e-demo"
DAA_URL = "http://localhost:8000"

# Demo infra postgres (daa-e2e-demo/docker-compose.yml: POSTGRES_USER=payflow)
DEMO_POSTGRES_URL = "postgresql://payflow:payflow_secret@postgres/payflow"
# DAA internal postgres (DAA/docker-compose.yml: POSTGRES_USER=youruser)
DAA_POSTGRES_URL  = "postgresql://youruser:demo_postgres_password@postgres/yourdb"

# Gitea constants — used to seed repos after each reset
GITEA_URL  = "http://localhost:3000"
GITEA_USER = "daa-admin"
GITEA_PASS = "DaaDemo123!"

COMBINATIONS = [
    # 1. True Serverless (Stateless — no DB, no queue, no auth)
    {"staging": "Image",   "db": "none",     "queue": "sync",     "git": "api",   "auth": "false", "policy": "false"},

    # 2. Serverless + External DB (sync queue, auth on/off)
    {"staging": "Image",   "db": "postgres", "queue": "sync",     "git": "api",   "auth": "true",  "policy": "true"},
    {"staging": "Image",   "db": "postgres", "queue": "sync",     "git": "api",   "auth": "false", "policy": "false"},

    # 3. Async Serverless (DB + RabbitMQ, auth on)
    {"staging": "Image",   "db": "postgres", "queue": "rabbitmq", "git": "api",   "auth": "true",  "policy": "true"},

    # 4. Fullstack Local Docker-Compose (auth on/off)
    {"staging": "Compose", "db": "postgres", "queue": "rabbitmq", "git": "local", "auth": "true",  "policy": "true"},
    {"staging": "Compose", "db": "postgres", "queue": "rabbitmq", "git": "local", "auth": "false", "policy": "false"},
]


def run(cmd, cwd=None, check=True):
    print(f"  $ {cmd}")
    subprocess.run(cmd, shell=True, cwd=cwd, check=check)


def wait_for_http(url, label, retries=30, interval=3):
    """Poll a URL until 200 or exhausted."""
    print(f"  [wait] {label} @ {url} ...", flush=True)
    for _ in range(retries):
        try:
            if requests.get(url, timeout=2).status_code == 200:
                print(f"  [wait] {label} ready ✓")
                return True
        except Exception:
            pass
        time.sleep(interval)
    print(f"  [wait] WARNING: {label} not ready after {retries * interval}s")
    return False


def wait_for_postgres(cwd, user):
    """Block until pg_isready returns 0."""
    print(f"  [wait] Postgres (user={user}) ...", flush=True)
    for _ in range(30):
        r = subprocess.run(
            f"docker-compose exec -T postgres pg_isready -U {user}",
            shell=True, cwd=cwd, capture_output=True
        )
        if r.returncode == 0:
            print("  [wait] Postgres ready ✓")
            return True
        time.sleep(2)
    print("  [wait] WARNING: Postgres not ready")
    return False


def seed_gitea() -> str:
    """Create Gitea admin user, generate a token, create repos, push code.

    Returns the raw Gitea access token string so callers can pass it to DAA.
    Gitea's data volume is wiped on every reset, so we must re-seed from scratch.
    """
    print("  [gitea] Creating admin user …")
    subprocess.run(
        f"docker-compose exec -T --user git gitea gitea admin user create "
        f"--admin --username {GITEA_USER} --password '{GITEA_PASS}' --email admin@payflow.dev",
        shell=True, cwd=DEMO_PATH, capture_output=True  # ignore error if already exists
    )

    print("  [gitea] Generating access token …")
    import uuid
    token_name = f"daa-token-{uuid.uuid4().hex[:8]}"
    res = subprocess.run(
        f"docker-compose exec -T --user git gitea gitea admin user generate-access-token "
        f"-u {GITEA_USER} -t {token_name} --raw",
        shell=True, cwd=DEMO_PATH, capture_output=True, text=True
    )
    gitea_token = res.stdout.strip().split("\n")[-1].strip()
    print(f"  [gitea] Token: {gitea_token[:6]}…")

    # Create repos — the agent will push fix branches here
    for repo in ["payment-api", "payment-worker"]:
        r = requests.post(
            f"{GITEA_URL}/api/v1/user/repos",
            auth=(GITEA_USER, GITEA_PASS),
            json={"name": repo, "auto_init": True, "default_branch": "main"},
        )
        if r.status_code in (201, 409):
            print(f"  [gitea] Repo '{repo}' ready ✓")
        else:
            print(f"  [gitea] WARNING: repo '{repo}' create returned {r.status_code}")

    return gitea_token


def reset_state():
    """Destroy all volumes and state so each combo starts from absolute zero."""
    print("\n--- Resetting State ---")
    # Wipe agent clone workspace (root-owned directories)
    run("docker run --rm -v /tmp:/tmp alpine rm -rf /tmp/daa", check=False)
    run("docker run --rm -v /var/daa:/var/daa alpine rm -rf /var/daa/*", check=False)
    # Tear down demo infra — destroys pgdata (payflow postgres) + gitea_data volumes
    run("docker-compose down -v", cwd=DEMO_PATH, check=False)
    # Tear down DAA services — destroys postgres_data (DAA internal postgres) volume
    run("docker rm -f daa-standalone", check=False)
    run("docker-compose down -v", cwd=DAA_PATH, check=False)
    # Restart demo infra fresh (Gitea, Redis, Postgres, RabbitMQ)
    run("docker-compose up -d gitea redis postgres rabbitmq", cwd=DEMO_PATH)
    # Wait for each service to actually be healthy — no blind sleep
    wait_for_http("http://localhost:3000/api/v1/version", "Gitea", retries=30, interval=3)
    wait_for_postgres(DEMO_PATH, "payflow")
    # Seed Gitea with admin user, token, and repos (volume was wiped above)
    gitea_token = seed_gitea()
    # Store on module level so run_test() can embed it in .env
    reset_state._gitea_token = gitea_token
    # Start the demo application stack so load_test.sh has a target on :8001
    run("docker-compose up -d payment-api payment-worker", cwd=DEMO_PATH)
    wait_for_http("http://localhost:8001/health", "payment-api", retries=20, interval=3)
    print("--- Reset Complete ---\n")

reset_state._gitea_token = ""


def register_admin():
    try:
        requests.post(
            f"{DAA_URL}/auth/register",
            json={"username": "testuser", "password": "testpassword"},
            timeout=5
        )
    except Exception:
        pass


def login():
    try:
        res = requests.post(
            f"{DAA_URL}/auth/login",
            json={"username": "testuser", "password": "testpassword"},
            timeout=5
        )
        if res.status_code == 200:
            return res.json().get("token")
    except Exception:
        pass
    return None


def test_execution(combo):
    wait_for_http(f"{DAA_URL}/health", "DAA API")

    if combo['auth'] == 'true':
        register_admin()
        token = login()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
    else:
        headers = {}

    # ── db=none: stateless webhook-ingest mode ────────────────────────────────
    # The SDK path (POST /logs/) is broken by design — MockSession never persists
    # anything, so no incident is tracked and no dedup works.  load_test.sh
    # exercises exactly that SDK path (payment-api → /logs/), which is wrong here.
    #
    # The correct trigger for stateless mode is a single pre-qualified alert
    # posted directly to /ingest/prometheus — the external system (Prometheus,
    # Alertmanager) is expected to threshold before calling DAA, so DAA just
    # processes the one event and returns.  Policy is the app's problem, not DAA's.
    if combo['db'] == 'none':
        # ── Stateless end-to-end: single pre-qualified Prometheus alert ──────
        # Success = Gitea has a new open PR on payment-api.
        # ingest.py derives repo_url dynamically from GIT_REPO_URL template +
        # app_name, so no DB registry is needed.  Gitea was seeded in reset_state().
        print("  [stateless] Firing single Prometheus alert webhook …")
        alert_payload = {
            "version": "4",
            "status": "firing",
            "alerts": [{
                "status": "firing",
                "labels": {
                    "alertname": "HighErrorRate",
                    "service": "payment-api",
                    "severity": "critical"
                },
                "annotations": {
                    "summary": "payment-api error rate exceeded threshold",
                    "description": "RedisConnectionError: max connections exceeded\n  at checkout() line 42"
                }
            }]
        }
        try:
            res = requests.post(
                f"{DAA_URL}/ingest/prometheus",
                json=alert_payload,
                headers=headers,
                timeout=10,
            )
            if res.status_code != 200:
                print(f"  ✗ Ingest rejected: {res.status_code} {res.text}")
                return False
            print(f"  Ingest accepted: {res.json()}")
        except Exception as e:
            print(f"  ✗ Ingest request failed: {e}")
            return False

        # Poll Gitea for a PR on payment-api — the real end-to-end success signal
        print("  [stateless] Polling Gitea for PR on payment-api (up to 60s) …")
        for attempt in range(20):
            time.sleep(3)
            try:
                prs = requests.get(
                    f"{GITEA_URL}/api/v1/repos/{GITEA_USER}/payment-api/pulls",
                    params={"state": "open"},
                    auth=(GITEA_USER, GITEA_PASS),
                    timeout=5,
                ).json()
                if prs:
                    pr_url = prs[0].get("html_url", "")
                    print(f"  ✓ Stateless PR created: {pr_url}")
                    return True
            except Exception:
                pass
        print("  ✗ Stateless test failed: no PR appeared in Gitea within 60 s.")
        return False

    # ── All other combos: trigger via load_test.sh + poll for resolution ─────
    print("  Triggering incident via load_test.sh ...")
    run("./load_test.sh", cwd=DEMO_PATH, check=False)

    for _ in range(20):
        try:
            res = requests.get(f"{DAA_URL}/incidents", headers=headers, timeout=5)
            incidents = res.json() if res.ok else []
            if incidents:
                incident_id = incidents[0]['id']
                status = incidents[0]['status']
                print(f"  Incident {incident_id[:8]} → '{status}'")

                # Approve fix if policy is on and fix is waiting
                if combo['policy'] == 'true' and status in ('fix_proposed', 'awaiting_approval'):
                    fix_id = incidents[0].get('fix_id')
                    if not fix_id:
                        by_log = requests.get(f"{DAA_URL}/fixes/by-log/{incident_id}", headers=headers, timeout=5)
                        if by_log.ok:
                            fix_id = by_log.json().get('id')
                    if fix_id:
                        print(f"  Policy enforced! Approving fix {fix_id[:8]}...")
                        requests.post(f"{DAA_URL}/fixes/{fix_id}/approve", headers=headers, timeout=5)
                        time.sleep(2)

                if status in ('resolved', 'completed'):
                    print("  ✓ Incident resolved!")
                    return True
        except Exception:
            pass
        time.sleep(2)

    print("  ✗ Timeout: incident did not resolve.")
    return False


def run_test(combo):
    reset_state()
    print(f"\n==============================================")
    print(f"Testing: {combo}")
    print(f"==============================================")



    # Image-mode: standalone container joins demo network → uses DEMO's postgres
    # Compose-mode: DAA brings up its own postgres → uses DAA's internal postgres
    if combo['staging'] == "Image":
        db_url = DEMO_POSTGRES_URL if combo['db'] == "postgres" else ""
        network = "daa-e2e-demo_default"
    else:
        db_url = DAA_POSTGRES_URL
        network = "daa_default"

    # GIT_REPO_URL is set for ALL combos.  ingest.py parses this as a URL
    # template (scheme+host+org) and substitutes app_name as the repo name,
    # so the agent receives a correct per-app repo_url without any DB lookup.
    # This is what enables PR creation in stateless (db=none) mode.
    git_repo_url = f"http://host.docker.internal:3000/{GITEA_USER}/payment-api.git"
    # Use the fresh Gitea token generated by seed_gitea() in reset_state()
    git_token = reset_state._gitea_token or os.environ.get("DAA_GIT_TOKEN", "")
    # In Image staging mode the single container runs both the API and the agent
    # as BackgroundTasks.  The agent calls DAA_BACKEND_API_URL to update fix
    # status and run FingerprintDedup.check().  There is no separate backend-api
    # container — the agent must call back to its own uvicorn on localhost:8080.
    # In Compose mode the backend-api service is a separate container at backend-api:80.
    backend_api_url = (
        "http://localhost:8080" if combo['staging'] == "Image"
        else "http://backend-api:80"
    )
    env_content = (
        f"LLM_PROVIDER=mock\n"
        f"DAA_DB_PROVIDER={combo['db']}\n"
        f"DAA_QUEUE_MODE={combo['queue']}\n"
        f"DAA_GIT_MODE={combo['git']}\n"
        f"DAA_AUTH_ENABLED={combo['auth']}\n"
        f"DAA_POLICY_ENABLED={combo['policy']}\n"
        f"SECRET_KEY=demo_secret_key\n"
        f"DATABASE_URL={db_url}\n"
        f"RABBITMQ_HOST=rabbitmq\n"
        f"DAA_BACKEND_API_URL={backend_api_url}\n"
        f"DAA_GIT_TOKEN={git_token}\n"
        f"GIT_REPO_URL={git_repo_url}\n"
        # Explicit deployment-level git config for dynamic repo_url construction.
        # ingest.py uses these to derive {GIT_HOST}/{GIT_ORG}/{app_name}.git
        # for any app without needing a per-app DB record.
        f"GIT_HOST=http://host.docker.internal:3000\n"
        f"GIT_ORG={GITEA_USER}\n"
    )
    env_path = os.path.join(DAA_PATH, ".env")
    with open(env_path, "w") as f:
        f.write(env_content)
    print(f"  Wrote .env: db={combo['db']} queue={combo['queue']} auth={combo['auth']}")

    if combo['staging'] == "Compose":
        run("docker-compose up -d --build backend-api python-agent", cwd=DAA_PATH)
    else:
        run("docker build -t daa-standalone .", cwd=DAA_PATH)
        run(
            f"docker run -d --name daa-standalone"
            f" --network {network}"
            f" --env-file {env_path}"
            f" -p 8000:8080"
            f" daa-standalone",
            cwd=DAA_PATH
        )

    success = test_execution(combo)
    print(f"  Result: {'✓ PASS' if success else '✗ FAIL'}")

    # Teardown
    if combo['staging'] == "Compose":
        run("docker-compose down -v", cwd=DAA_PATH)
    else:
        run("docker rm -f daa-standalone", check=False)

    return success


if __name__ == "__main__":
    results = []
    for c in COMBINATIONS:
        ok = run_test(c)
        results.append((c, ok))

    print("\n\n========== MATRIX RESULTS ==========")
    for combo, ok in results:
        tag = f"staging={combo['staging']:7} db={combo['db']:8} queue={combo['queue']:8} auth={combo['auth']:5} policy={combo['policy']}"
        print(f"  {'✓ PASS' if ok else '✗ FAIL'}  {tag}")
    print("=====================================")
    all_pass = all(ok for _, ok in results)
    print(f"\nOverall: {'ALL PASS ✓' if all_pass else 'SOME FAILURES ✗'}\n")
    exit(0 if all_pass else 1)
