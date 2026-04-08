"""
deploy.py -- Push to HuggingFace Spaces directly.
Bypasses openenv push (which has Windows encoding bugs).

Step 1: Go to https://huggingface.co/settings/tokens
Step 2: Create a NEW token with "Write" permission
Step 3: Run:
    python deploy.py --token hf_YOUR_WRITE_TOKEN
"""
import argparse
import os
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="Deltasthic/airport-recovery")
    parser.add_argument("--token", required=True, help="HF token with WRITE permission")
    args = parser.parse_args()

    try:
        from huggingface_hub import HfApi
    except ImportError:
        os.system(f"{sys.executable} -m pip install huggingface_hub")
        from huggingface_hub import HfApi

    api = HfApi(token=args.token)

    # Verify token works
    try:
        user = api.whoami()
        print(f"Authenticated as: {user['name']}")
    except Exception as e:
        print(f"ERROR: Token invalid or expired: {e}")
        print("Go to https://huggingface.co/settings/tokens and create a Write token")
        return

    # Create Space
    print(f"\nCreating Space: {args.repo}")
    try:
        api.create_repo(
            repo_id=args.repo,
            repo_type="space",
            space_sdk="docker",
            exist_ok=True,
        )
        print("  Space created/exists")
    except Exception as e:
        print(f"  ERROR creating Space: {e}")
        print("\n  FIX: Your token needs WRITE permission.")
        print("  Go to https://huggingface.co/settings/tokens")
        print("  Create a new token -> select 'Write' permission")
        return

    # Collect files
    skip_names = {"deploy.py", "test_environment.py", "clean_ascii.py",
                  ".gitignore", ".dockerignore", "uv.lock"}
    skip_ext = {".pyc", ".pyo", ".lock", ".rtf"}
    skip_dirs = {"__pycache__", ".git", ".venv", "venv"}

    project_dir = os.path.dirname(os.path.abspath(__file__))
    files = []
    for root, dirs, fnames in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fn in fnames:
            if fn in skip_names or fn.startswith("."):
                continue
            if any(fn.endswith(ext) for ext in skip_ext):
                continue
            fp = os.path.join(root, fn)
            rp = os.path.relpath(fp, project_dir).replace("\\", "/")
            files.append((fp, rp))

    print(f"\nUploading {len(files)} files in one commit:")
    for _, rp in files:
        print(f"  {rp}")

    # Upload everything in a single commit (much faster)
    from huggingface_hub import CommitOperationAdd
    operations = []
    for fp, rp in files:
        operations.append(CommitOperationAdd(
            path_in_repo=rp,
            path_or_fileobj=fp,
        ))

    try:
        api.create_commit(
            repo_id=args.repo,
            repo_type="space",
            operations=operations,
            commit_message="Deploy airport-recovery environment",
        )
        print("\n  All files uploaded successfully!")
    except Exception as e:
        print(f"\n  ERROR: {e}")
        return

    space_url = f"https://huggingface.co/spaces/{args.repo}"
    health_url = f"https://{args.repo.replace('/', '-')}.hf.space/health"
    print(f"\n  Space: {space_url}")
    print(f"  Health (wait 3-5 min): {health_url}")

if __name__ == "__main__":
    main()
