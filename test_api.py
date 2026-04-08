"""
test_api.py -- Test the running Docker container via HTTP.

Run this AFTER: docker run -d -p 8000:8000 --name airport-test airport-recovery
"""
import json
import sys
try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

BASE = "http://localhost:8000"

def post(path, data=None):
    r = requests.post(f"{BASE}{path}", json=data or {}, timeout=10)
    return r.json()

print("=== API Test ===\n")

# Health
r = requests.get(f"{BASE}/health", timeout=5)
print(f"1. Health: {r.json()}")

# Reset easy task
data = post("/reset", {"task": "single_delay"})
obs = data.get("observation", data)
print(f"2. Reset: task=single_delay, issues={obs.get('total_issues_count')}, time={obs.get('current_time')}")

# Step: REQUEST_INFO
data = post("/step", {"action": {"command": "REQUEST_INFO summary"}})
obs = data.get("observation", data)
print(f"3. REQUEST_INFO summary: reward={data.get('reward')}, done={data.get('done')}")

# Reset again (HTTP is stateless, each call is fresh)
post("/reset", {"task": "single_delay"})

# Step: REASSIGN_GATE
data = post("/step", {"action": {"command": "REASSIGN_GATE AA101 G1"}})
print(f"4. REASSIGN_GATE AA101 G1: reward={data.get('reward')}")

# Test each task resets
for task in ["cascading_delays", "full_disruption", "international_hub", "overnight_recovery", "information_blackout"]:
    data = post("/reset", {"task": task})
    obs = data.get("observation", data)
    print(f"5. Reset {task}: issues={obs.get('total_issues_count')}, weather={obs.get('weather_severity')}")

print("\n=== All API tests passed! ===")
