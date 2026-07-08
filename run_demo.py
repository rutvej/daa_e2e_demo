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

    # Save to local file for resume support
    (ROOT_DIR / ".gitea_token").write_text(token)

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
    
    daa_path = os.path.abspath(os.path.join(ROOT_DIR, "../DAA"))
    python_exe = os.path.join(daa_path, ".venv/bin/python")
    daa_script = os.path.join(daa_path, "daa")

    # 1. Update Gitea token in ~/.daa/config.json
    config_path = os.path.expanduser("~/.daa/config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
            cfg["GIT_TOKEN"] = gitea_token
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)
            print("✓ Updated Gitea token in DAA configuration.")
        except Exception as e:
            print(f"Warning: Failed to update DAA config: {e}")

    # 2. Register applications via CLI
    tokens = {}
    apps = ["payment-api", "payment-worker"]
    for app in apps:
        print(f"\nRegistering application '{app}' via DAA CLI...")
        lang = "python" if app == "payment-api" else "go"
        repo_url = f"http://host.docker.internal:3000/{GITEA_USER}/{app}.git"
        
        # Run: daa register --name app --repo-url repo_url --language lang
        cmd_register = [
            python_exe,
            daa_script,
            "register",
            "--name", app,
            "--repo-url", repo_url,
            "--language", lang
        ]
        try:
            res = subprocess.run(cmd_register, capture_output=True, text=True, check=True)
            # Extract DAA_TOKEN from stdout
            match = re.search(r"DAA_TOKEN=([a-zA-Z0-9_\-\.]+)", res.stdout)
            if match:
                app_token = match.group(1)
                tokens[app] = app_token
                print(f"✓ Registered '{app}'! Token: {app_token[:25]}...")
            else:
                raise RuntimeError(f"Could not extract DAA_TOKEN from DAA CLI output:\n{res.stdout}")
        except subprocess.CalledProcessError as e:
            print(f"Error registering app via CLI: {e.stderr}")
            raise e

        # 3. Configure Escalation Policy via CLI (Threshold = 3 errors in 60s)
        print(f"Setting escalation policy for '{app}' via DAA CLI...")
        cmd_policy = [
            python_exe,
            daa_script,
            "policy",
            "--app", app,
            "--threshold", "3",
            "--window", "60"
        ]
        try:
            subprocess.run(cmd_policy, check=True)
            print(f"✓ Set escalation policy for '{app}' successfully.")
        except subprocess.CalledProcessError as e:
            print(f"Error setting policy via CLI: {e.stderr}")
            raise e

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

def poll_approve_verify(gitea_token: str):
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

def trigger_outage_and_verify(gitea_token: str):
    print("\n==================================================")
    print("      Simulating Outage & Verifying Resolution    ")
    print("==================================================")

    # Trigger failure
    print("Triggering Redis OOM failure on payment-api via load test...")
    run_cmd("./load_test.sh", check=False)

    print("Failure triggered! Monitoring DAA backend for generated incident and fix...")
    poll_approve_verify(gitea_token)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="E2E DAA Walkthrough Orchestrator")
    parser.add_argument("--skip-infra", action="store_true", help="Skip Gitea/Redis/Postgres/RabbitMQ startup")
    parser.add_argument("--skip-registration", action="store_true", help="Skip app registration & token write")
    parser.add_argument("--skip-load-test", action="store_true", help="Skip load test, use inject instead")
    parser.add_argument("--watch-only", action="store_true", help="Just poll for existing fix")
    args = parser.parse_args()

    if args.watch_only:
        args.skip_infra = True
        args.skip_registration = True
        args.skip_load_test = True

    print("Starting E2E DAA Walkthrough Orchestrator...")
    
    gitea_token = None
    token_file = ROOT_DIR / ".gitea_token"
    if token_file.exists():
        gitea_token = token_file.read_text().strip()

    # 1. Spin up Gitea, Redis, and clean apps
    if not args.skip_infra:
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
    else:
        print("Skipping Infrastructure Setup.")

    if not args.skip_registration:
        if not gitea_token:
            if token_file.exists():
                gitea_token = token_file.read_text().strip()
            else:
                print("Warning: Gitea token file not found. Setting up Gitea to generate one...")
                gitea_token = setup_gitea()
        register_daa_apps_and_get_tokens(gitea_token)
    else:
        print("Skipping Application Registration in DAA.")
        if not gitea_token and token_file.exists():
            gitea_token = token_file.read_text().strip()

    # 6. Trigger failure and verify
    if args.watch_only:
        print("Watch-only mode active. Monitoring DAA backend for existing/proposed fixes...")
        poll_approve_verify(gitea_token)
    elif args.skip_load_test:
        print("Skipping load test. Calling direct incident injection...")
        # Purge queue first
        run_cmd("docker exec daa-e2e-demo-rabbitmq-1 rabbitmqctl purge_queue fix_jobs 2>/dev/null || true", check=False)
        # Run inject_incident.sh
        run_cmd("./inject_incident.sh", check=True)
        print("Incident injected! Monitoring DAA backend for generated incident and fix...")
        poll_approve_verify(gitea_token)
    else:
        trigger_outage_and_verify(gitea_token)

if __name__ == "__main__":
    main()
