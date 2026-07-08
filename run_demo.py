#!/usr/bin/env python3
import os
import sys
import time
import uuid
import json
import subprocess
import requests
import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DAA_URL = "http://localhost:8000"
GITEA_URL = "http://localhost:3000"
GITEA_USER = "daa-admin"
GITEA_PASS = "DaaDemo123!"

def run_cmd(cmd: str, cwd: Path = ROOT_DIR, check: bool = True):
    print(f"$ {cmd}")
    res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if check and res.returncode != 0:
        print(f"STDOUT: {res.stdout}")
        print(f"STDERR: {res.stderr}")
        raise RuntimeError(f"Command failed with code {res.returncode}: {cmd}")
    return res.stdout.strip()

def wait_for_service(url: str, name: str, timeout_sec: int = 240):
    print(f"Waiting for {name} to be ready at {url}...")
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            res = requests.get(url, timeout=3)
            if res.status_code in {200, 404, 302}:
                print(f"✓ {name} is ready!")
                return
        except Exception:
            pass
        time.sleep(3)
    raise TimeoutError(f"Service {name} at {url} did not start within {timeout_sec}s")

def setup_gitea():
    print("Setting up Gitea admin user...")
    # Create admin user via Docker exec
    create_user_cmd = (
        "docker-compose exec -T --user git gitea gitea admin user create "
        f"--admin --username {GITEA_USER} --password '{GITEA_PASS}' --email admin@payflow.dev"
    )
    try:
        run_cmd(create_user_cmd, check=False)
        print("✓ Admin user daa-admin created (or already exists).")
    except Exception as e:
        print(f"Admin creation command failed, might exist: {e}")

    # Generate token
    token_name = f"daa-token-{uuid.uuid4().hex[:8]}"
    print("Generating Gitea access token...")
    token_cmd = (
        "docker-compose exec -T --user git gitea gitea admin user generate-access-token "
        f"-u {GITEA_USER} -t {token_name} --raw"
    )
    token = run_cmd(token_cmd, check=True)
    # Strip any extra whitespace or warning output (warnings are usually on stderr but check just in case)
    token = token.split("\n")[-1].strip()
    print(f"✓ Gitea Token: {token[:6]}...")

    # Create repos
    for repo in ["payment-api", "payment-worker"]:
        print(f"Creating repository '{repo}' via Gitea API...")
        res = requests.post(
            f"{GITEA_URL}/api/v1/user/repos",
            auth=(GITEA_USER, GITEA_PASS),
            json={"name": repo, "auto_init": False}
        )
        if res.status_code == 201:
            print(f"✓ Repository '{repo}' created successfully.")
        elif res.status_code == 409:
            print(f"✓ Repository '{repo}' already exists.")
        else:
            print(f"Error creating repository '{repo}': {res.status_code} - {res.text}")
            res.raise_for_status()

    return token

def push_project_to_gitea(app_name: str, src_dir: Path):
    # Git push
    run_cmd("git init", src_dir)
    run_cmd('git config user.email "sre@example.com"', src_dir)
    run_cmd('git config user.name "SRE Agent"', src_dir)
    try:
        run_cmd("git remote remove origin", src_dir)
    except Exception:
        pass
    remote_url = f"http://{GITEA_USER}:{GITEA_PASS}@localhost:3000/{GITEA_USER}/{app_name}.git"
    run_cmd(f"git remote add origin {remote_url}", src_dir)
    run_cmd("git add .", src_dir)
    try:
        run_cmd('git commit -m "Initial commit"', src_dir)
    except Exception:
        pass
    print(f"Pushing {app_name} to Gitea...")
    run_cmd("git push -u origin master --force", src_dir)

def register_daa_apps_and_get_tokens(gitea_token: str):
    print("\n==================================================")
    print("      DAA Initialization & Token Configuration   ")
    print("==================================================")
    
    # Authenticate SRE Admin
    admin_payload = {"username": "testuser", "password": "testpassword"}
    try:
        requests.post(f"{DAA_URL}/auth/register", json=admin_payload)
    except Exception:
        pass
    res = requests.post(f"{DAA_URL}/auth/login", json=admin_payload)
    res.raise_for_status()
    admin_token = res.json().get("access_token") or res.json().get("token")
    
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }

    tokens = {}
    apps = ["payment-api", "payment-worker"]
    for app in apps:
        print(f"Registering application '{app}' in DAA backend...")
        lang = "python" if app == "payment-api" else "go"
        app_payload = {
            "name": app,
            "description": f"Microservice {app}",
            "language": lang,
            "allowed_ip": "172.22.0.1" # Host gateway IP
        }
        res_create = requests.post(f"{DAA_URL}/applications/", json=app_payload, headers=headers)
        if res_create.status_code == 400 and "already exists" in res_create.text:
            # Fetch application token
            res_list = requests.get(f"{DAA_URL}/applications/", headers=headers)
            res_list.raise_for_status()
            app_data = next(a for a in res_list.json() if a["name"] == app)
        else:
            res_create.raise_for_status()
            app_data = res_create.json()
            
        app_token = app_data["token"]
        tokens[app] = app_token
        print(f"✓ Registered '{app}'! Token: {app_token[:25]}...")

        # Create Escalation Policy (Threshold = 3 errors in 60s)
        requests.post(
            f"{DAA_URL}/applications/{app_data['id']}/escalation-policies",
            headers=headers,
            json={
                "rule_type": "error_rate_threshold",
                "condition_value": 3,
                "window_seconds": 60,
                "cooldown_minutes": 30,
                "severity_keywords": ["FATAL", "OOMKill", "RedisConnectionError", "ConnectionRefusedError"]
            }
        )

        # Register Project Connection
        requests.post(
            f"{DAA_URL}/projects/",
            headers=headers,
            json={
                "app_name": app,
                "repo_provider": "gitea",
                "repo_url": f"http://host.docker.internal:3000/{GITEA_USER}/{app}.git",
                "repo_token": gitea_token,
                "jira_url": f"{DAA_URL}/mock-jira",
                "jira_token": "mock-token",
                "jira_project_key": "MOCK"
            }
        )

    # Write tokens to .env file for Docker Compose
    env_content = (
        f"DAA_TOKEN_PAYMENT_API={tokens['payment-api']}\n"
        f"DAA_TOKEN_PAYMENT_WORKER={tokens['payment-worker']}\n"
    )
    (ROOT_DIR / ".env").write_text(env_content)
    print("✓ Wrote application tokens to .env file.")

    # Recreate containers to load tokens
    print("Recreating containers with application tokens...")
    run_cmd("docker-compose up -d --force-recreate payment-api payment-worker")

    print("Waiting for payment-api to be ready after recreation...")
    for _ in range(30):
        try:
            res = requests.get("http://localhost:8001/health")
            if res.status_code == 200:
                print("✓ payment-api is ready!")
                break
        except Exception:
            pass
        time.sleep(2)

def trigger_outage_and_verify(gitea_token: str):
    print("\n==================================================")
    print("      Simulating Outage & Verifying Resolution    ")
    print("==================================================")

    # Trigger failure
    print("Triggering Redis OOM failure on payment-api via load test...")
    run_cmd("./load_test.sh", check=False)

    print("Failure triggered! Monitoring DAA backend for generated incident and fix...")
    
    # Login as admin to poll fixes
    admin_payload = {"username": "testuser", "password": "testpassword"}
    res = requests.post(f"{DAA_URL}/auth/login", json=admin_payload)
    res.raise_for_status()
    admin_token = res.json().get("access_token") or res.json().get("token")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Poll until a fix is found (up to 30 min)
    fix_id = None
    incident_id = None
    for poll_idx in range(360):
        try:
            res_inc = requests.get(f"{DAA_URL}/incidents/", headers=headers, timeout=10)
            if res_inc.ok and res_inc.json():
                inc_list = res_inc.json()
                if poll_idx % 6 == 0:
                    print("Current active incidents:", [f"INC-{i['id'][:8]} ({i['status']})" for i in inc_list])
                # Check if any incident already has fix_proposed status
                for inc in inc_list:
                    if inc.get("status") == "fix_proposed" and inc.get("fix_id"):
                        fix_id = inc["fix_id"]
                        incident_id = inc["id"]
                        print(f"✓ Incident transitioned to fix_proposed! Fix: {fix_id[:8]}")
                        break
                if not incident_id and inc_list:
                    incident_id = inc_list[0]["id"]
        except Exception as e:
            print(f"  [poll] incident check error: {e}")

        if not fix_id and incident_id:
            # Check fixes for this specific incident via the by-log lookup on the incident's log
            try:
                res_inc_detail = requests.get(f"{DAA_URL}/incidents/{incident_id}", headers=headers, timeout=10)
                if res_inc_detail.ok:
                    inc_detail = res_inc_detail.json()
                    fid = inc_detail.get("fix_id")
                    if fid:
                        fix_res = requests.get(f"{DAA_URL}/fixes/{fid}", headers=headers, timeout=10)
                        if fix_res.ok:
                            fix = fix_res.json()
                            if fix.get("status") in ("awaiting_approval", "fix_proposed", "completed"):
                                fix_id = fid
                                print(f"✓ Found Fix {fix_id[:8]} (status: {fix.get('status')})!")
            except Exception as e:
                print(f"  [poll] fix check error: {e}")

        if fix_id:
            break
        if poll_idx % 6 == 0:
            print(f"  [{poll_idx * 5}s] Still waiting for agent to complete...")
        time.sleep(5)

    if not fix_id:
        print("✗ Timeout waiting for fix generation. SRE agent may still be executing.")
        sys.exit(1)

    # Approve the fix via backend API (acting as human SRE)
    print(f"Approving Fix {fix_id[:8]}...")
    approve_res = requests.post(f"{DAA_URL}/fixes/{fix_id}/approve", headers=headers)
    approve_res.raise_for_status()
    print("✓ Fix approved successfully! Response:", approve_res.json())

    # Verify Pull Request exists in Gitea
    print("Verifying Pull Request on Gitea...")
    gitea_headers = {"Authorization": f"token {gitea_token}"}
    pr_url = f"http://localhost:3000/api/v1/repos/daa-admin/payment-api/pulls"
    for _ in range(10):
        try:
            mrs_res = requests.get(pr_url, headers=gitea_headers)
            if mrs_res.ok and mrs_res.json():
                prs = mrs_res.json()
                print("✓ Found Pull Request on Gitea!")
                print(f"Title: {prs[0]['title']}")
                print(f"URL: {prs[0]['html_url']}")
                print("\nE2E WALKTHROUGH COMPLETED SUCCESSFULLY!")
                return
        except Exception as e:
            print(f"Check failed: {e}")
        time.sleep(2)
    print("✗ No Pull Request found on Gitea.")
    sys.exit(1)

def main():
    print("Starting E2E DAA Walkthrough Orchestrator...")
    
    # 1. Spin up Gitea, Redis, and clean apps
    print("Starting Infrastructure (Gitea, Redis, Postgres, RabbitMQ)...")
    run_cmd("docker-compose up -d --build gitea redis postgres rabbitmq")
    
    # 2. Wait for Gitea to be healthy
    wait_for_service(f"{GITEA_URL}/api/v1/version", "Gitea")
    gitea_token = setup_gitea()

    # 3. Push code to Gitea repositories
    push_project_to_gitea("payment-api", ROOT_DIR / "payment-api")
    push_project_to_gitea("payment-worker", ROOT_DIR / "payment-worker")

    # 4. Spin up the apps now that repos are populated
    print("Starting application containers...")
    run_cmd("docker-compose up -d --build payment-api payment-worker")

    # 5. Wait for DAA backend and apps
    wait_for_service(DAA_URL, "DAA Backend API")
    wait_for_service("http://localhost:8001/health", "payment-api")
    
    register_daa_apps_and_get_tokens(gitea_token)

    # 6. Trigger failure and verify
    trigger_outage_and_verify(gitea_token)

if __name__ == "__main__":
    main()
