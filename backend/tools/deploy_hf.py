"""Put this folder on a Hugging Face Space (Docker; the free CPU has 2 vCPU and 16 GB of memory).

    $env:HF_TOKEN = "hf_..."                              # a token with write access: huggingface.co/settings/tokens
    python backend/tools/deploy_hf.py <hf-username>/ream   # creates the Space if needed and uploads the app

    # optional: copy API keys and RAEM_RUN_TOKEN from this shell into the Space's secrets
    $env:ANTHROPIC_API_KEY = "sk-ant-..."; $env:RAEM_RUN_TOKEN = "..."
    python backend/tools/deploy_hf.py <hf-username>/ream --secrets

It uploads the files git would push (no .env, no run database) and gives the Space the README header Hugging Face
reads (sdk: docker, app_port 8000). Run it again after a change to update the Space; it rebuilds by itself.
On the free CPU the run database starts again when the Space restarts; the saved results in the repository stay.
With Hugging Face's persistent storage add the variable RAEM_DATA_DIR=/data and everything stays (persist.py).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WEB = Path(__file__).resolve().parents[2]
HEADER = """---
title: RAEM Forecast Console
emoji: 📈
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 8000
pinned: false
short_description: 7-day stock forecasts and LLM agent analysis
---

"""
SECRETS = ["RAEM_RUN_TOKEN", "ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY", "OPENAI_API_KEY",
           "GOOGLE_API_KEY", "GROQ_API_KEY", "RAEM_REFRESH_TOKEN", "RAEM_KIMI_BASE_URL",
           "HF_TOKEN"]  # HF_TOKEN lets the Space save its data to the private dataset (hf_sync.py)
VARIABLES = {"PORT": "8000", "TZ": "Asia/Dhaka"}
SKIP = (".gitattributes", ".github/")  # the Space keeps its own .gitattributes (large-file rules); CI is GitHub's


def files_to_upload() -> list[str]:
    """What git would push: tracked files plus new ones that .gitignore does not exclude."""
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=WEB, capture_output=True)
    if out.returncode != 0:
        sys.exit("This needs the web folder as a git repository (git init), so .gitignore decides what is uploaded.")
    files = sorted({p for p in out.stdout.decode("utf-8").split("\0") if p})
    return [p for p in files if not p.startswith(SKIP) and (WEB / p).is_file()]


def space_readme() -> str:
    return HEADER + (WEB / "README.md").read_text(encoding="utf-8")


def wait_for_space(api, space: str, minutes: int = 40) -> str:
    """Follow the build until the Space runs or fails; on failure print the end of the build log."""
    import time
    last, t0 = "", time.time()
    while time.time() - t0 < minutes * 60:
        try:
            stage = str(api.get_space_runtime(space).stage)
        except Exception as e:
            stage = f"unknown ({type(e).__name__})"
        if stage != last:
            print(f"  {time.strftime('%H:%M:%S')}  {stage}", flush=True)
            last = stage
        if stage in ("RUNNING", "BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE"):
            if stage != "RUNNING":
                for kind in ("build", "run"):
                    try:
                        import requests
                        r = requests.get(f"https://huggingface.co/api/spaces/{space}/logs/{kind}", stream=True, timeout=15,
                                         headers={"Authorization": f"Bearer {api.token}"})
                        lines = []
                        for raw in r.iter_lines(decode_unicode=True):
                            if raw and raw.startswith("data:"):
                                lines.append(raw[5:].strip())
                            if len(lines) > 400:
                                break
                        print(f"--- last {kind} log lines ---\n" + "\n".join(lines[-25:]))
                    except Exception:
                        pass
            return stage
        time.sleep(20)
    return last


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("space", nargs="?", help="<hf-username>/<space-name>; default <your-hf-username>/ream")
    ap.add_argument("--private", action="store_true", help="create the Space as private (only you can open it)")
    ap.add_argument("--secrets", action="store_true", help="copy API keys, HF_TOKEN and RAEM_RUN_TOKEN (shell or web/.env) into the Space")
    ap.add_argument("--wait", action="store_true", help="follow the build until the Space runs or fails")
    a = ap.parse_args()
    try:  # keys and tokens may sit in web/.env (git ignores it, so it is never uploaded)
        from dotenv import load_dotenv
        load_dotenv(WEB / ".env", override=False)
    except ImportError:
        pass
    from huggingface_hub import HfApi
    api = HfApi(token=os.environ.get("HF_TOKEN") or None)  # else the login saved by `hf auth login`
    try:
        who = api.whoami()["name"]
    except Exception:
        sys.exit("Not logged in to Hugging Face: put HF_TOKEN=hf_... (a Write token from huggingface.co/settings/tokens) in web/.env.")
    print(f"Hugging Face account: {who}")
    a.space = a.space or f"{who}/ream"
    if a.space.count("/") != 1:
        sys.exit("Give the Space as <hf-username>/<space-name>.")
    api.create_repo(a.space, repo_type="space", space_sdk="docker", private=a.private, exist_ok=True)
    data = f"{a.space}-data"  # private dataset that keeps ChromaDB memory, the run database and logs across restarts
    api.create_repo(data, repo_type="dataset", private=True, exist_ok=True)
    for k, v in dict(VARIABLES, RAEM_HF_DATASET=data).items():
        api.add_space_variable(a.space, k, v)
    if a.secrets:
        for name in SECRETS:
            if os.environ.get(name):
                api.add_space_secret(a.space, name, os.environ[name])
                print(f"secret set: {name}")
    files = files_to_upload()
    with tempfile.TemporaryDirectory() as d:  # a staging copy, so the Space gets its README header and nothing else changes
        for rel in files:
            dst = Path(d) / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(WEB / rel, dst)
        (Path(d) / "README.md").write_text(space_readme(), encoding="utf-8")
        print(f"uploading {len(files)} files to spaces/{a.space} ...", flush=True)
        api.upload_folder(folder_path=d, repo_id=a.space, repo_type="space", commit_message="Deploy RAEM web app",
                          delete_patterns=["backend/**", "frontend/**", "*.md", "*.json", "*.yml", "Dockerfile"])
    owner, name = a.space.split("/")
    print(f"\nDone. Build log and settings: https://huggingface.co/spaces/{a.space}")
    print(f"The site, once the build finishes (10 to 20 minutes): https://{owner.lower()}-{name.lower().replace('_', '-').replace('.', '-')}.hf.space")
    print(f"Data (ChromaDB memory, runs, logs) is kept in the private dataset https://huggingface.co/datasets/{data}")
    if not a.secrets:
        print("Add RAEM_RUN_TOKEN, HF_TOKEN (write token, for saving data) and ANTHROPIC_API_KEY (or MOONSHOT_API_KEY)"
              " under Settings -> Variables and secrets -> New secret.")
    if a.wait:
        print("\nFollowing the build (the first one installs PyTorch: 10 to 20 minutes):", flush=True)
        stage = wait_for_space(api, a.space)
        print(f"Space stage: {stage}")
        return 0 if stage == "RUNNING" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
