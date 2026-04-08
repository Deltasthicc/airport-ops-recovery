"""
validate.py -- Pre-submission validator (Windows-compatible)
============================================================
Checks:
  1. HF Space is live and responds to /reset
  2. Docker build succeeds
  3. openenv validate passes

Usage:
    python validate.py https://Deltasthic-airport-recovery.hf.space
"""
import os
import sys
import subprocess
import json

def run(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=True)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"

def main():
    space_url = sys.argv[1] if len(sys.argv) > 1 else None
    passed = 0
    failed = 0

    print("=" * 60)
    print("  OpenEnv Pre-Submission Validator")
    print("=" * 60)

    # --- Check 1: HF Space ---
    print("\n[1/4] Checking HF Space...")
    if space_url:
        try:
            import requests
            r = requests.post(f"{space_url}/reset", json={}, timeout=30)
            if r.status_code == 200:
                print(f"  PASSED -- Space responds to /reset (HTTP {r.status_code})")
                passed += 1
            else:
                print(f"  FAILED -- /reset returned HTTP {r.status_code}")
                failed += 1
        except Exception as e:
            print(f"  FAILED -- Cannot reach Space: {e}")
            print(f"  Hint: Make sure your Space is running at {space_url}")
            failed += 1
    else:
        print("  SKIPPED -- No URL provided (pass as argument)")

    # --- Check 2: Docker build ---
    print("\n[2/4] Checking Docker build...")
    if os.path.exists("Dockerfile"):
        code, out, err = run("docker build -t airport-recovery-test .", timeout=300)
        if code == 0:
            print("  PASSED -- Docker build succeeded")
            passed += 1
        else:
            print(f"  FAILED -- Docker build failed")
            print(f"  {err[-200:]}" if err else "")
            failed += 1
    else:
        print("  FAILED -- No Dockerfile found")
        failed += 1

    # --- Check 3: openenv validate ---
    print("\n[3/4] Checking openenv validate...")
    code, out, err = run("openenv validate")
    if code == 0 and "OK" in out:
        print(f"  PASSED -- {out.strip()}")
        passed += 1
    else:
        print(f"  FAILED -- {out.strip()} {err.strip()}")
        failed += 1

    # --- Check 4: inference.py format ---
    print("\n[4/4] Checking inference.py format...")
    if os.path.exists("inference.py"):
        with open("inference.py", "r") as f:
            content = f.read()
        checks = {
            "API_BASE_URL": 'os.getenv("API_BASE_URL")' in content,
            "MODEL_NAME": 'os.getenv("MODEL_NAME")' in content,
            "HF_TOKEN/API_KEY": 'os.getenv("HF_TOKEN")' in content,
            "IMAGE_NAME": 'os.getenv("IMAGE_NAME")' in content,
            "from openai import OpenAI": "from openai import OpenAI" in content,
            "[START] format": "[START] task=" in content,
            "[STEP] format": "[STEP] step=" in content,
            "[END] format": "[END] success=" in content and "score=" in content,
            "from_docker_image": "from_docker_image" in content,
        }
        all_ok = all(checks.values())
        for name, ok in checks.items():
            print(f"  {'OK' if ok else 'FAIL'}: {name}")
        if all_ok:
            print("  PASSED -- All inference.py checks OK")
            passed += 1
        else:
            print("  FAILED -- Some checks failed")
            failed += 1
    else:
        print("  FAILED -- inference.py not found in root")
        failed += 1

    # --- Summary ---
    print(f"\n{'=' * 60}")
    print(f"  {passed} passed, {failed} failed")
    if failed == 0:
        print("  Ready to submit!")
    else:
        print("  Fix the above issues before submitting.")
    print("=" * 60)

if __name__ == "__main__":
    main()
