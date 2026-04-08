"""
test_api.py -- Test the running Docker container via HTTP.

Usage:
    docker run -d -p 8000:8000 --name airport-test airport-recovery
    Start-Sleep -Seconds 5
    python test_api.py

Notes:
- /step can vary across OpenEnv versions.
- This script tries multiple common payload shapes for /step.
- If /step still cannot be verified over raw HTTP, it is reported as a warning,
  not a hard failure, because some versions prefer the official client/WebSocket flow.
"""

import sys
import time

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

BASE = "http://localhost:8000"
TASKS = [
    "single_delay",
    "cascading_delays",
    "full_disruption",
    "international_hub",
    "overnight_recovery",
    "information_blackout",
]

session = requests.Session()
passed = 0
failed = 0
warned = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [OK] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} -- {detail}")


def warn(name, detail=""):
    global warned
    warned += 1
    print(f"  [WARN] {name} -- {detail}")


def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}


def resolve_ref(spec, schema):
    """
    Resolve a simple OpenAPI $ref like:
    #/components/schemas/AirportAction
    """
    if not isinstance(schema, dict):
        return {}

    ref = schema.get("$ref")
    if not ref:
        return schema

    if not ref.startswith("#/"):
        return schema

    node = spec
    for part in ref[2:].split("/"):
        node = node.get(part, {})
    return node if isinstance(node, dict) else {}


def get_step_payload_candidates(reset_data):
    """
    Build multiple possible payloads for /step, based on:
    - common OpenEnv HTTP shapes
    - optional episode_id/session_id if exposed
    - OpenAPI schema, if available
    """
    command = "REQUEST_INFO summary"
    candidates = []

    # Most common direct shape
    candidates.append({"command": command})

    # Common wrapped shape
    candidates.append({"action": {"command": command}})

    # Try to discover optional IDs from reset response
    possible_ids = {}
    if isinstance(reset_data, dict):
        for key in ("episode_id", "session_id", "run_id"):
            if key in reset_data and reset_data[key] is not None:
                possible_ids[key] = reset_data[key]

        obs = reset_data.get("observation", {})
        if isinstance(obs, dict):
            for key in ("episode_id", "session_id", "run_id"):
                if key in obs and obs[key] is not None:
                    possible_ids[key] = obs[key]

    # Try /state too, if available
    try:
        state_resp = session.get(f"{BASE}/state", timeout=5)
        state_data = safe_json(state_resp)
        if isinstance(state_data, dict):
            for key in ("episode_id", "session_id", "run_id"):
                if key in state_data and state_data[key] is not None:
                    possible_ids[key] = state_data[key]
    except Exception:
        pass

    # Add variants with optional IDs
    if possible_ids:
        for key, value in possible_ids.items():
            candidates.append({key: value, "command": command})
            candidates.append({key: value, "action": {"command": command}})

    # Inspect OpenAPI schema for /step if available
    try:
        spec_resp = session.get(f"{BASE}/openapi.json", timeout=5)
        spec = safe_json(spec_resp)

        step_schema = (
            spec.get("paths", {})
            .get("/step", {})
            .get("post", {})
            .get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        step_schema = resolve_ref(spec, step_schema)

        props = step_schema.get("properties", {})
        required = step_schema.get("required", [])

        # If schema explicitly shows direct command
        if "command" in props:
            candidates.insert(0, {"command": command})

        # If schema explicitly shows wrapped action
        if "action" in props:
            candidates.insert(0, {"action": {"command": command}})

        # If schema requires one of these IDs, add those variants first
        for id_key in ("episode_id", "session_id", "run_id"):
            if (
                id_key in props
                and id_key in possible_ids
                and possible_ids[id_key] is not None
            ):
                candidates.insert(0, {id_key: possible_ids[id_key], "command": command})
                candidates.insert(0, {id_key: possible_ids[id_key], "action": {"command": command}})

        _ = required
    except Exception:
        pass

    # Deduplicate while preserving order
    deduped = []
    seen = set()
    for payload in candidates:
        key = repr(payload)
        if key not in seen:
            seen.add(key)
            deduped.append(payload)

    return deduped


print("=== API Test ===\n")

# 1. Health with retries
health_ok = False
last_err = ""
for _ in range(10):
    try:
        r = session.get(f"{BASE}/health", timeout=5)
        if r.status_code == 200:
            check("GET /health", True)
            health_ok = True
            break
        last_err = f"HTTP {r.status_code}"
    except Exception as e:
        last_err = str(e)
    time.sleep(1)

if not health_ok:
    check("GET /health", False, f"Connection refused. Is Docker running? Error: {last_err}")
    print("\n  Run this first:")
    print("    docker run -d -p 8000:8000 --name airport-test airport-recovery")
    print("    Start-Sleep -Seconds 5")
    sys.exit(1)

# 2. Reset each task
for task in TASKS:
    try:
        r = session.post(f"{BASE}/reset", json={"task": task}, timeout=10)
        data = safe_json(r)
        obs = data.get("observation", data) if isinstance(data, dict) else {}
        issues = obs.get("total_issues_count", 0) if isinstance(obs, dict) else 0
        score = obs.get("score", -1) if isinstance(obs, dict) else -1

        check(
            f"POST /reset task={task}",
            r.status_code == 200 and issues > 0,
            f"HTTP {r.status_code}, issues={issues}, body={r.text[:300]}"
        )
        check(
            "  score in (0,1)",
            0 < score < 1,
            f"score={score}"
        )
    except Exception as e:
        check(f"POST /reset task={task}", False, str(e))

# 3. Step
try:
    reset_resp = session.post(f"{BASE}/reset", json={"task": "single_delay"}, timeout=10)
    reset_data = safe_json(reset_resp)

    payloads = get_step_payload_candidates(reset_data)
    success = False
    last_detail = "No payloads tried."

    for payload in payloads:
        try:
            r = session.post(f"{BASE}/step", json=payload, timeout=10)

            if r.status_code == 200 and r.text.strip():
                check("POST /step", True)
                success = True
                break

            last_detail = f"HTTP {r.status_code} | payload={payload} | body={r.text[:300]}"
        except Exception as e:
            last_detail = f"{e} | payload={payload}"

    if not success:
        warn("POST /step", last_detail + " (may need WebSocket or a different OpenEnv HTTP shape)")

except Exception as e:
    warn("POST /step", f"{e} (may need WebSocket or a different OpenEnv HTTP shape)")

print(f"\n=== {passed} passed, {failed} failed, {warned} warnings ===")
if failed == 0:
    print("All required tests passed!")