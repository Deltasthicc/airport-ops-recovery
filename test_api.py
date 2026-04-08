"""
test_api.py -- Test the running Docker container via HTTP.

Usage:
    docker run -d -p 8000:8000 --name airport-test airport-recovery
    Start-Sleep -Seconds 5
    python test_api.py
"""
import sys
try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

BASE = "http://localhost:8000"
session = requests.Session()
passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [OK] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} -- {detail}")

print("=== API Test ===\n")

# 1. Health
try:
    r = session.get(f"{BASE}/health", timeout=5)
    check("GET /health", r.status_code == 200, f"HTTP {r.status_code}")
except Exception as e:
    check("GET /health", False, f"Connection refused. Is Docker running? Error: {e}")
    print("\n  Run this first:")
    print("    docker run -d -p 8000:8000 --name airport-test airport-recovery")
    print("    Start-Sleep -Seconds 5")
    sys.exit(1)

# 2. Reset each task
for task in ["single_delay", "cascading_delays", "full_disruption",
             "international_hub", "overnight_recovery", "information_blackout"]:
    try:
        r = session.post(f"{BASE}/reset", json={"task": task}, timeout=10)
        data = r.json()
        obs = data.get("observation", data)
        issues = obs.get("total_issues_count", 0)
        score = obs.get("score", -1)
        check(f"POST /reset task={task}", r.status_code == 200 and issues > 0,
              f"HTTP {r.status_code}, issues={issues}")
        check(f"  score in (0,1)", 0 < score < 1, f"score={score}")
    except Exception as e:
        check(f"POST /reset task={task}", False, str(e))

# 3. Step (may or may not work via HTTP depending on openenv version)
try:
    session.post(f"{BASE}/reset", json={"task": "single_delay"}, timeout=10)
    r = session.post(f"{BASE}/step",
                     json={"action": {"command": "REQUEST_INFO summary"}},
                     timeout=10)
    if r.status_code == 200 and r.text:
        check("POST /step", True)
    else:
        check("POST /step", False, f"HTTP {r.status_code} (may need WebSocket)")
except Exception as e:
    check("POST /step", False, f"{e} (may need WebSocket -- OK for submission)")

print(f"\n=== {passed} passed, {failed} failed ===")
if failed == 0:
    print("All tests passed!")
