#!/usr/bin/env python3
import os
import sys
import time
import uuid
import json
import subprocess
import requests
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DAA_URL = "http://localhost:8000"
GITLAB_URL = "http://localhost:8082"
GITLAB_TOKEN = "c8d8f8fa6ec414fdf8de3193b8391f0077e7089d"

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

def ensure_gitlab_token():
    print("Checking if GitLab personal access token is already valid...")
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}
    try:
        res = requests.get(f"{GITLAB_URL}/api/v4/personal_access_tokens", headers=headers, timeout=5)
        if res.status_code == 200:
            print("✓ GitLab token is already valid. Skipping rails runner setup.")
            return
    except Exception as e:
        print(f"Token check failed: {e}")

    print("Setting up root personal access token in GitLab container via rails runner...")
    runner = (
        "begin; "
        "user = User.find_by_username('root'); "
        "user.personal_access_tokens.where(name: 'demo-token').destroy_all; "
        "token = user.personal_access_tokens.create!(scopes: [:api, :read_repository, :write_repository], name: 'demo-token', expires_at: 365.days.from_now.to_date); "
        f"token.set_token('{GITLAB_TOKEN}'); "
        "token.save!; "
        "rescue => e; "
        "puts 'Token may already exist: ' + e.message; "
        "end; "
        "puts 'Token configured!'"
    )
    cmd = f"docker-compose exec -T gitlab gitlab-rails runner \"{runner}\""
    run_cmd(cmd, check=True)


def push_project_to_gitlab(app_name: str, src_dir: Path):
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}
    # Check if project exists
    proj_url = f"{GITLAB_URL}/api/v4/projects/root%2F{app_name}"
    res = requests.get(proj_url, headers=headers)
    if not res.ok:
        print(f"Creating GitLab repository for {app_name}...")
        create_res = requests.post(
            f"{GITLAB_URL}/api/v4/projects",
            headers=headers,
            data={"name": app_name, "visibility": "public"},
            timeout=15
        )
        create_res.raise_for_status()
    
    # Delete branch protection for master to allow force push if it already exists
    try:
        requests.delete(f"{GITLAB_URL}/api/v4/projects/root%2F{app_name}/protected_branches/master", headers=headers, timeout=5)
    except Exception:
        pass

    # Git push
    run_cmd("git init", src_dir)
    run_cmd('git config user.email "sre@example.com"', src_dir)
    run_cmd('git config user.name "SRE Agent"', src_dir)
    try:
        run_cmd("git remote remove origin", src_dir)
    except Exception:
        pass
    remote_url = f"http://root:StrongPassword123@localhost:8082/root/{app_name}.git"
    run_cmd(f"git remote add origin {remote_url}", src_dir)
    run_cmd("git add .", src_dir)
    try:
        run_cmd('git commit -m "Initial commit"', src_dir)
    except Exception:
        pass
    print(f"Pushing {app_name} to GitLab...")
    run_cmd("git push -u origin master --force", src_dir)

def mock_agent_integrate_sdk():
    print("\n==================================================")
    print("      Mock External Agent: Integrating SDK        ")
    print("==================================================")
    
    DAA_SDK_SRC = Path("/home/rutvej/Desktop/DAA/app/daa-sdk")

    # 1. Push DAA SDK to GitLab (for reference / history)
    print("Pushing DAA SDK to GitLab...")
    push_project_to_gitlab("daa-sdk", DAA_SDK_SRC)

    # 2. Copy SDK locally into each service and modify code
    for svc_name, svc_dir in [("test-app", ROOT_DIR / "test-app"), ("checkout-service", ROOT_DIR / "checkout-service")]:
        # Copy SDK into service directory
        sdk_dest = svc_dir / "daa-sdk"
        if sdk_dest.exists():
            import shutil
            shutil.rmtree(sdk_dest)
        import shutil
        shutil.copytree(DAA_SDK_SRC, sdk_dest)
        print(f"Copied DAA SDK into {svc_name}/daa-sdk/")

    # 3. Modify test-app to integrate SDK
    print("Adding DAA SDK to test-app...")
    main_py_path = ROOT_DIR / "test-app" / "main.py"
    content = main_py_path.read_text()
    
    old_init = "app = Flask(__name__)"
    new_init = (
        "from daa_sdk import DaaSdk\n"
        "app = Flask(__name__)\n"
        "daa_sdk = DaaSdk(backend_url=os.environ.get('DAA_BACKEND_API_URL'))"
    )
    content = content.replace(old_init, new_init)
    
    errors = ["AttributeError", "ImportError", "IndexError", "NameError", "RecursionError", "KeyError", "TypeError", "ValueError"]
    for err in errors:
        content = content.replace(
            f'logging.error("{err} occurred", exc_info=True)',
            f'logging.error("{err} occurred", exc_info=True)\n        daa_sdk.capture_exception(e)'
        )
    main_py_path.write_text(content)
    
    # Add local SDK to test-app requirements.txt
    reqs_path = ROOT_DIR / "test-app" / "requirements.txt"
    reqs_content = reqs_path.read_text()
    if "daa-sdk" not in reqs_content:
        reqs_content += "\n./daa-sdk\n"
    reqs_path.write_text(reqs_content)

    # Update test-app Dockerfile to not need git (local install)
    dockerfile_path = ROOT_DIR / "test-app" / "Dockerfile"
    dockerfile_path.write_text(
        "FROM python:3.8-slim\n\n"
        "WORKDIR /app\n\n"
        "COPY . /app/\n\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n"
        "RUN pip install gunicorn\n\n"
        "EXPOSE 8081\n\n"
        'CMD [\"gunicorn\", \"-k\", \"gevent\", \"-b\", \"0.0.0.0:8081\", \"main:app\"]\n'
    )

    # 4. Modify checkout-service to integrate DAA telemetry
    print("Adding DAA telemetry to checkout-service...")
    checkout_py_path = ROOT_DIR / "checkout-service" / "app.py"
    chk_content = checkout_py_path.read_text()
    
    old_chk_init = 'app = FastAPI(title="Mock Checkout Service", version="1.0.0")'
    new_chk_init = (
        'app = FastAPI(title="Mock Checkout Service", version="1.0.0")\n'
        'DAA_LOGS_URL = os.environ.get("DAA_LOGS_URL")\n'
        'DAA_TOKEN = os.environ.get("DAA_TOKEN")\n'
        'def report_error_to_daa(exception_type: str, content: str, trace_id: str):\n'
        '    if not DAA_LOGS_URL or not DAA_TOKEN:\n'
        '        return\n'
        '    payload = {\n'
        '        "app_name": "checkout-service",\n'
        '        "content": content,\n'
        '        "exception_type": exception_type,\n'
        '        "trace_id": trace_id,\n'
        '        "correlation_id": str(uuid.uuid4())\n'
        '    }\n'
        '    headers = {"Authorization": f"Bearer {DAA_TOKEN}"}\n'
        '    try:\n'
        '        requests.post(DAA_LOGS_URL, json=payload, headers=headers, timeout=2.0)\n'
        '    except Exception as e:\n'
        '        print(f"Failed to report to DAA: {e}")'
    )
    chk_content = chk_content.replace(old_chk_init, new_chk_init)
    
    old_redis_fail = (
        '    # 1. Simulate Redis connection check\n'
        '    if "fail_redis" in req.user_id:\n'
        '        cache = RedisCache()\n'
        '        # Buggy line to be fixed by SRE agent:\n'
        '        cache.connec()'
    )
    new_redis_fail = (
        '    # 1. Simulate Redis connection check\n'
        '    if "fail_redis" in req.user_id:\n'
        '        cache = RedisCache()\n'
        '        try:\n'
        '            # Buggy line to be fixed by SRE agent:\n'
        '            cache.connec()\n'
        '        except Exception as e:\n'
        '            report_error_to_daa("AttributeError", str(e), trace_id)\n'
        '            raise'
    )
    chk_content = chk_content.replace(old_redis_fail, new_redis_fail)
    checkout_py_path.write_text(chk_content)

    # checkout-service uses inline DAA reporting (no SDK package needed)
    # Update checkout Dockerfile to remove git dependency
    chk_dockerfile_path = ROOT_DIR / "checkout-service" / "Dockerfile"
    chk_dockerfile_path.write_text(
        "FROM python:3.12-slim\n\n"
        "WORKDIR /app\n\n"
        "COPY . /app/\n\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n\n"
        "EXPOSE 8001\n\n"
        'CMD [\"uvicorn\", \"app:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8001\"]\n'
    )

    # Push integrated code to GitLab
    push_project_to_gitlab("test-app", ROOT_DIR / "test-app")
    push_project_to_gitlab("checkout-service", ROOT_DIR / "checkout-service")
    push_project_to_gitlab("payment-service", ROOT_DIR / "payment-service")

    # Redeploy fresh with SDK active
    print("Rebuilding and restarting microservices in Docker...")
    run_cmd("docker-compose up --build -d test-app checkout-service payment-service")

def register_daa_apps_and_get_tokens():
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
    apps = ["test-app", "checkout-service", "payment-service"]
    for app in apps:
        print(f"Registering application '{app}' in DAA backend...")
        app_payload = {
            "name": app,
            "description": f"Microservice {app}",
            "language": "python",
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

        # Create Escalation Policy (Threshold = 2 errors in 60s)
        requests.post(
            f"{DAA_URL}/applications/{app_data['id']}/escalation-policies",
            headers=headers,
            json={
                "rule_type": "error_rate_threshold",
                "condition_value": 2,
                "window_seconds": 60,
                "cooldown_minutes": 30,
                "severity_keywords": ["FATAL", "OOMKill", "RedisTimeoutError", "AttributeError"]
            }
        )

        # Register Project Connection
        requests.post(
            f"{DAA_URL}/projects/",
            headers=headers,
            json={
                "app_name": app,
                "repo_provider": "gitlab",
                "repo_url": f"http://gitlab:8082/root/{app}.git",
                "repo_token": GITLAB_TOKEN,
                "jira_url": f"{DAA_URL}/mock-jira",
                "jira_token": "mock-token",
                "jira_project_key": "MOCK"
            }
        )

    # Write tokens to .env file for Docker Compose
    env_content = (
        f"DAA_TOKEN_TEST_APP={tokens['test-app']}\n"
        f"DAA_TOKEN_CHECKOUT={tokens['checkout-service']}\n"
        f"DAA_TOKEN_PAYMENT={tokens['payment-service']}\n"
    )
    (ROOT_DIR / ".env").write_text(env_content)
    print("✓ Wrote application tokens to .env file.")

    # Recreate containers to load tokens
    print("Recreating containers with application tokens...")
    run_cmd("docker-compose up -d --force-recreate test-app checkout-service payment-service")

def trigger_outage_and_verify():
    print("\n==================================================")
    print("      Simulating Outage & Verifying Resolution    ")
    print("==================================================")

    # Trigger failure
    print("Triggering Redis connection failure on checkout-service...")
    checkout_url = "http://localhost:8001/checkout"
    for _ in range(3):
        try:
            requests.post(checkout_url, json={"user_id": "fail_redis", "cart_total": 150.0}, timeout=5)
        except Exception:
            pass
        time.sleep(0.5)

    print("Failure triggered! Monitoring DAA backend for generated incident and fix...")
    
    # Login as admin to poll fixes
    admin_payload = {"username": "testuser", "password": "testpassword"}
    res = requests.post(f"{DAA_URL}/auth/login", json=admin_payload)
    res.raise_for_status()
    admin_token = res.json().get("access_token") or res.json().get("token")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Poll until a fix awaiting approval is found (up to 15 min)
    fix_id = None
    incident_id = None
    for poll_idx in range(180):
        res_inc = requests.get(f"{DAA_URL}/incidents/", headers=headers)
        if res_inc.ok and res_inc.json():
            inc_list = res_inc.json()
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

        if not fix_id:
            # Also check fixes endpoint directly
            res_fixes = requests.get(f"{DAA_URL}/fixes/", headers=headers)
            if res_fixes.ok:
                for fix in res_fixes.json():
                    if fix.get("status") in ("awaiting_approval", "fix_proposed", "completed"):
                        fix_id = fix["id"]
                        print(f"✓ Found Fix {fix_id[:8]} (status: {fix.get('status')})!")
                        break

        if not fix_id:
            # Check logs for fixId
            res_logs = requests.get(f"{DAA_URL}/logs/", headers=headers)
            if res_logs.ok:
                for log in res_logs.json():
                    if log.get("fixId"):
                        fix_id = log["fixId"]
                        print(f"✓ Found Fix {fix_id[:8]} via logs!")
                        break

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

    # Verify Merge Request exists in GitLab
    print("Verifying Merge Request on GitLab...")
    gitlab_headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}
    mr_url = f"{GITLAB_URL}/api/v4/projects/root%2Fcheckout-service/merge_requests"
    for _ in range(10):
        mrs = requests.get(mr_url, headers=gitlab_headers).json()
        if mrs:
            print("✓ Found Merge Request on GitLab!")
            print(f"Title: {mrs[0]['title']}")
            print(f"URL: {mrs[0]['web_url']}")
            print("\nE2E WALKTHROUGH COMPLETED SUCCESSFULLY!")
            return
        time.sleep(2)
    print("✗ No Merge Request found on GitLab.")
    sys.exit(1)

def main():
    print("Starting E2E DAA Walkthrough Orchestrator...")
    
    # 1. Spin up GitLab, Redis, and clean apps
    print("Starting GitLab, Redis, and clean microservices...")
    run_cmd("docker-compose up -d --build gitlab redis-cache test-app checkout-service payment-service")
    
    # 2. Wait for GitLab to be healthy
    wait_for_service(f"{GITLAB_URL}/-/health", "GitLab")
    ensure_gitlab_token()

    # 3. Mock agent SDK integration
    mock_agent_integrate_sdk()

    # 4. Wait for DAA backend
    wait_for_service(DAA_URL, "DAA Backend API")
    register_daa_apps_and_get_tokens()

    # 5. Trigger failure and verify
    trigger_outage_and_verify()

if __name__ == "__main__":
    main()
