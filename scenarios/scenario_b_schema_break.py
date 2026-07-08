import os
import re
import subprocess
import time
from pathlib import Path
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
API_DIR = ROOT_DIR / "payment-api"
GITEA_URL = "http://localhost:3000"
GITEA_USER = "daa-admin"
GITEA_PASS = "DaaDemo123!"

def run_cmd(cmd: str, cwd: Path = ROOT_DIR):
    print(f"$ {cmd}")
    res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"STDOUT: {res.stdout}")
        print(f"STDERR: {res.stderr}")
        raise RuntimeError(f"Command failed: {cmd}")
    return res.stdout.strip()

def main():
    print("Starting Scenario B: Cascading Schema Change...")
    
    # 1. Read app.py and replace "transaction_id" with "txn_id" in RabbitMQ message body
    app_py_path = API_DIR / "app.py"
    content = app_py_path.read_text()
    
    broken_content = content.replace('"transaction_id": txn_id,', '"txn_id": txn_id,')
    if broken_content == content:
        print("Error: Could not locate transaction_id in app.py to break it.")
        return
        
    app_py_path.write_text(broken_content)
    print("Modified payment-api/app.py to publish 'txn_id' instead of 'transaction_id'.")
    
    # 2. Push change to Gitea repo so SRE agent can inspect git changes
    print("Committing and pushing breaking change to Gitea...")
    run_cmd("git add app.py", cwd=API_DIR)
    try:
        run_cmd('git commit -m "Optimize checkout payload structure"', cwd=API_DIR)
    except Exception:
        # Ignore if no changes
        pass
    
    run_cmd("git push origin master --force", cwd=API_DIR)
    
    # 3. Rebuild and restart payment-api
    print("Rebuilding and restarting payment-api container...")
    run_cmd("docker-compose up --build -d payment-api", cwd=ROOT_DIR)
    
    # Wait for API to be healthy
    time.sleep(5)
    
    # 4. Trigger failures by calling checkout API
    print("Sending checkout requests to trigger Go worker errors...")
    checkout_url = "http://localhost:8001/checkout"
    for i in range(5):
        try:
            res = requests.post(checkout_url, json={
                "user_id": f"usr_schema_break_{i}",
                "cart_total": 45.50,
                "currency": "USD",
                "items": ["hat"]
            }, timeout=5)
            print(f"Checkout Response {i}: {res.status_code} - {res.text}")
        except Exception as e:
            print(f"Request failed: {e}")
        time.sleep(1)

    print("Scenario B failure triggered. Go worker is now failing on incoming queue messages due to schema mismatch.")

if __name__ == "__main__":
    main()
