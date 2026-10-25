"""Free persistent storage for a Hugging Face Space: the app's changing data lives in a private Hugging Face dataset.

The Space's own disk starts over on every restart. With these two settings in the Space
    RAEM_HF_DATASET=<hf-username>/ream-data      (variable; deploy_hf.py sets it)
    HF_TOKEN=hf_...                              (secret: a token with write access)
the container restores the last saved state when it starts (`python backend/tools/hf_sync.py restore`), and the server
saves it after every finished run (`save()`): ChromaDB memory, the run database, held runs, prices, trading logs and
saved results. Hugging Face only stores the parts of files that changed, so a save after a run is small.

Same rule as persist.py: a file the image (git) changed since the last save keeps the image's version; any other saved
file replaces the image's copy. Code (notebooks, .py) always comes from the image. Without the two settings nothing
happens, so local runs, Railway and docker compose are unaffected.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from persist import FOLDERS  # noqa: E402  (the same folders Railway keeps on its volume)

WEB = Path(__file__).resolve().parents[2]
MANIFEST = ".raem_image_files.json"                     # in the dataset: the image's hash of each file at the last save
BOOT = Path(tempfile.gettempdir()) / "raem_image_files.json"  # the running image's hashes, written by restore()
SKIP_SUFFIX = (".ipynb", ".py", ".pyc", ".tmp")
SKIP_PARTS = {"__pycache__", "notebook_runs"}           # executed notebook copies are large and only for debugging
_lock = threading.Lock()


def repo() -> str:
    return os.environ.get("RAEM_HF_DATASET", "").strip()


def token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or None


def enabled() -> bool:
    return bool(repo() and token())


def _hash(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _keep(rel: str) -> bool:
    return rel.startswith(tuple(f + "/" for f in FOLDERS)) and not rel.endswith(SKIP_SUFFIX) and not SKIP_PARTS & set(rel.split("/"))


def state_files(root: Path | None = None) -> list[str]:
    root = root or WEB
    out = []
    for folder in FOLDERS:
        base = root / folder
        if base.exists():
            out += [p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file()]
    return sorted(r for r in out if _keep(r))


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix in (".db", ".sqlite3"):  # a consistent copy even if the server is writing
        s, d = sqlite3.connect(src), sqlite3.connect(dst)
        try:
            s.backup(d)
        finally:
            s.close()
            d.close()
    else:
        shutil.copy2(src, dst)


def restore(api=None, download=None) -> int:
    """At container start: lay the last saved state over the image's files."""
    if not enabled():
        return 0
    if api is None or download is None:
        from huggingface_hub import HfApi, snapshot_download
        api = api or HfApi(token=token())
        download = download or snapshot_download
    image = {rel: _hash(WEB / rel) for rel in state_files()}
    BOOT.write_text(json.dumps(image), encoding="utf-8")
    api.create_repo(repo(), repo_type="dataset", private=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        try:
            local = Path(download(repo_id=repo(), repo_type="dataset", local_dir=d, token=token()))
        except Exception as e:
            print(f"hf_sync: nothing restored ({type(e).__name__}: {str(e)[:120]})", flush=True)
            return 0
        saved_image = json.loads((local / MANIFEST).read_text(encoding="utf-8")) if (local / MANIFEST).exists() else {}
        n = kept = 0
        for f in local.rglob("*"):
            rel = f.relative_to(local).as_posix()
            if not f.is_file() or not _keep(rel):
                continue
            if rel in image and saved_image.get(rel) != image[rel]:  # the image brought a newer version: it wins
                kept += 1
                continue
            _copy(f, WEB / rel)
            n += 1
    print(f"hf_sync: restored {n} files from datasets/{repo()} ({kept} newer in the image)", flush=True)
    return n


def save(reason: str = "update", api=None) -> bool:
    """After a run: upload the current state (only changed parts travel). Never raises."""
    if not enabled():
        return False
    with _lock:
        try:
            if api is None:
                from huggingface_hub import HfApi
                api = HfApi(token=token())
            with tempfile.TemporaryDirectory() as d:
                for rel in state_files():
                    _copy(WEB / rel, Path(d) / rel)
                image = json.loads(BOOT.read_text(encoding="utf-8")) if BOOT.exists() else {}
                (Path(d) / MANIFEST).write_text(json.dumps(image), encoding="utf-8")
                api.create_repo(repo(), repo_type="dataset", private=True, exist_ok=True)
                api.upload_folder(folder_path=d, repo_id=repo(), repo_type="dataset", commit_message=f"save after {reason}",
                                  delete_patterns=[f + "/**" for f in FOLDERS])
            print(f"hf_sync: saved after {reason}", flush=True)
            return True
        except Exception as e:  # the run itself is done and stored; the next save tries again
            print(f"hf_sync: save failed ({type(e).__name__}: {str(e)[:160]})", flush=True)
            return False


def save_in_background(reason: str) -> None:
    if enabled():
        threading.Thread(target=save, args=(reason,), daemon=True).start()


if __name__ == "__main__":
    if sys.argv[1:] == ["restore"]:
        try:
            restore()
        except Exception as e:  # never block the server from starting
            print(f"hf_sync: restore skipped ({type(e).__name__}: {str(e)[:160]})", flush=True)
    elif sys.argv[1:] == ["save"]:
        save("manual save")
    else:
        sys.exit("usage: hf_sync.py restore|save")
