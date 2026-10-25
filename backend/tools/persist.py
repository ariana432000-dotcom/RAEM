"""Keep the app's changing folders on one persistent volume (Railway gives a service a single volume).

    RAEM_DATA_DIR=/data python backend/tools/persist.py     # run at container start, before the server

Without RAEM_DATA_DIR it does nothing (local runs, docker compose with its own mounts).

Each folder below moves to <RAEM_DATA_DIR>/<folder> and its old path becomes a link to it, so the code keeps using
the same paths. The rule for each file the image (the git repository) brings:
  * first start: everything is copied to the volume;
  * later starts: a file the image changed since the last start (you pushed a new version) replaces the volume copy;
    a file the image did not change keeps the volume copy, so what the app wrote at run time (prices, trading logs,
    memory, the run database, refreshed results) survives redeploys.
Files that only exist on the volume (written at run time) are never touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[2]
FOLDERS = [
    "backend/storage",
    "backend/data",
    "backend/engine/data",
    "backend/engine/daily_data",
    "backend/engine/forecast_log",
    "backend/engine/cache",
    "backend/engine/chroma_memory",
    "backend/engine/notebooks",
]
SEED_FILE = ".image_files.json"  # hash of every image file at the last start


def _hash(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sync_tree(src: Path, dst: Path, seed: dict, prefix: str) -> int:
    """Copy image files into the volume folder where the image changed them (or the volume lacks them)."""
    copied = 0
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        key = f"{prefix}/{rel.as_posix()}"
        h = _hash(f)
        target = dst / rel
        if not target.exists() or seed.get(key) != h:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            copied += 1
        seed[key] = h
    return copied


def main() -> int:
    root = os.environ.get("RAEM_DATA_DIR")
    if not root:
        return 0
    vol = Path(root)
    vol.mkdir(parents=True, exist_ok=True)
    seed_path = vol / SEED_FILE
    try:
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seed = {}
    for rel in FOLDERS:
        src, dst = WEB / rel, vol / rel
        if src.is_symlink():  # already linked in this container
            continue
        dst.mkdir(parents=True, exist_ok=True)
        n = sync_tree(src, dst, seed, rel) if src.exists() else 0
        if src.exists():
            shutil.rmtree(src)
        src.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(dst, src, target_is_directory=True)
        print(f"persist: {rel} -> {dst} ({n} files from the image)", flush=True)
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
