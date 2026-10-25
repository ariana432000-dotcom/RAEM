"""Test setup: a throw-away database, fake runners instead of the real models, and a fake Ollama list.

The real forecasting code and notebooks are never imported, so the suite runs in seconds with only
fastapi, httpx and pytest installed (this is what the GitHub workflow does).
"""
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


@pytest.fixture(autouse=True)
def isolated_archive(tmp_path, monkeypatch):
    """Tests never touch the real run database or the saved archive snapshot."""
    import shutil
    snap = BACKEND / "data" / "session_archive.json.gz"
    if snap.exists():
        shutil.copy(snap, tmp_path / "snapshot.json.gz")
    monkeypatch.setenv("RAEM_SNAPSHOT", str(tmp_path / "snapshot.json.gz"))
    monkeypatch.setenv("RAEM_DB", str(tmp_path / "archive.db"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import config, db, jobs, main

    db.set_path(tmp_path / "test.db")
    monkeypatch.setattr(config, "RUN_TOKEN", "")
    monkeypatch.setattr(config, "REFRESH_TOKEN", "")
    monkeypatch.setattr(config, "llm_models", lambda *a, **k: {"ok": True, "url": "http://ollama.test", "models": ["qwen2.5:7b", "llama3.2:3b"],
                                                         "default": "qwen2.5:7b", "embeddings_ok": True})
    monkeypatch.setattr(config, "notebooks", lambda: {"AAPL": "AAPL.ipynb", "IBM": "IBM.ipynb"})

    def fake_forecast(run):
        time.sleep(0.2)
        return {"ticker": run["ticker"], "forecast": {"paths": {}}, "models": run["params"]["models"]}, {"models": run["params"]["models"]}

    def fake_analysis(run):
        time.sleep(0.2)
        entry = {"sessions": [], "latest": {"date": run["run_date"], "rating": "Hold", "steps": {"Market": {"text": "ok"}}}}
        return {"ticker": run["ticker"], "llm_model": run["model_key"], "entry": entry}, {"rating": "Hold"}

    def fake_refresh(run):
        return {"ok": True}, {}

    monkeypatch.setattr(jobs, "RUNNERS", {"forecast": fake_forecast, "analysis": fake_analysis, "refresh": fake_refresh})
    with TestClient(main.app) as c:
        yield c
        # let queued runs finish with the fake runners before they are swapped back for the real ones
        t0 = time.time()
        while db.active_count() and time.time() - t0 < 20:
            time.sleep(0.1)


def wait_done(client, run_id, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/jobs/{run_id}").json()
        if r["status"] in ("done", "failed"):
            return r
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish")
