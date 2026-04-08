"""
validate.py -- Pre-submission validator (Windows-compatible)
"""
import os, sys, subprocess, json

def run(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=True)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"

def main():
    space_url = sys.argv[1] if len(sys.argv) > 1 else None
    passed = failed = 0

    print("=" * 60)
    print("  OpenEnv Pre-Submission Validator")
    print("=" * 60)

    print("\n[1/4] Checking HF Space...")
    if space_url:
        try:
            import requests
            r = requests.post(f"{space_url}/reset", json={}, timeout=30)
            if r.status_code == 200:
                print(f"  PASSED -- Space responds to /reset"); passed += 1
            else:
                print(f"  FAILED -- /reset returned HTTP {r.status_code}"); failed += 1
        except Exception as e:
            print(f"  FAILED -- Cannot reach Space: {e}"); failed += 1
    else:
        print("  SKIPPED -- No URL provided")

    print("\n[2/4] Checking Docker build...")
    if os.path.exists("Dockerfile"):
        code, out, err = run("docker build -t airport-recovery-test .", timeout=300)
        if code == 0: print("  PASSED"); passed += 1
        else: print(f"  FAILED -- {err[-200:]}"); failed += 1
    else:
        print("  FAILED -- No Dockerfile"); failed += 1

    print("\n[3/4] Checking openenv validate...")
    code, out, err = run("openenv validate")
    if code == 0 and "OK" in out: print(f"  PASSED -- {out.strip()}"); passed += 1
    else: print(f"  FAILED -- {out.strip()} {err.strip()}"); failed += 1

    print("\n[4/4] Checking inference.py format...")
    if os.path.exists("inference.py"):
        with open("inference.py", "r") as f: content = f.read()
        checks = {
            "API_BASE_URL": 'getenv("API_BASE_URL")' in content or 'getenv("API_BASE_URL",' in content,
            "MODEL_NAME": 'getenv("MODEL_NAME")' in content or 'getenv("MODEL_NAME",' in content,
            "HF_TOKEN/API_KEY": 'getenv("HF_TOKEN")' in content,
            "IMAGE_NAME": "IMAGE_NAME" in content and "getenv" in content,
            "from openai import OpenAI": "from openai import OpenAI" in content,
            "[START] format": "[START] task=" in content,
            "[STEP] format": "[STEP] step=" in content,
            "[END] format": "[END] success=" in content and "score=" in content,
            "from_docker_image": "from_docker_image" in content,
        }
        all_ok = all(checks.values())
        for name, ok in checks.items(): print(f"  {'OK' if ok else 'FAIL'}: {name}")
        if all_ok: print("  PASSED"); passed += 1
        else: print("  FAILED"); failed += 1
    else:
        print("  FAILED -- inference.py not found"); failed += 1

    print(f"\n{'='*60}")
    print(f"  {passed} passed, {failed} failed")
    print("  Ready to submit!" if failed == 0 else "  Fix issues before submitting.")
    print("=" * 60)

if __name__ == "__main__":
    main()
