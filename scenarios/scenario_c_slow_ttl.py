import os
import subprocess
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
API_DIR = ROOT_DIR / "payment-api"

def run_cmd(cmd: str, cwd: Path = ROOT_DIR):
    print(f"$ {cmd}")
    res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"STDOUT: {res.stdout}")
        print(f"STDERR: {res.stderr}")
        raise RuntimeError(f"Command failed: {cmd}")
    return res.stdout.strip()

def main():
    print("Starting Scenario C: TTL Tuning Demo...")
    
    # 1. Modify cache.py to increase TTLs to very high values
    cache_py_path = API_DIR / "cache.py"
    content = cache_py_path.read_text()
    
    # Replace TTLs
    new_content = content.replace("3600)  # 1 hour TTL ✓", "604800)  # 7 days TTL")
    new_content = new_content.replace("86400)  # 24 hour TTL ✓", "2592000)  # 30 days TTL")
    
    if new_content == content:
        print("Warning: Could not replace TTLs in cache.py. Maybe already modified?")
    else:
        cache_py_path.write_text(new_content)
        print("Modified payment-api/cache.py to use high TTLs (7 days / 30 days).")
        
    # 2. Push change to Gitea
    print("Committing and pushing high TTL change to Gitea...")
    run_cmd("git add cache.py", cwd=API_DIR)
    try:
        run_cmd('git commit -m "Increase session cache TTL for better UX"', cwd=API_DIR)
    except Exception:
        pass
    
    run_cmd("git push origin master --force", cwd=API_DIR)
    
    # 3. Rebuild and restart payment-api
    print("Rebuilding and restarting payment-api container...")
    run_cmd("docker-compose up --build -d payment-api", cwd=ROOT_DIR)
    
    print("Scenario C TTL configuration change deployed.")

if __name__ == "__main__":
    main()
