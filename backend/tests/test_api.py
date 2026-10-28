import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

from conftest import BACKEND, wait_done


def test_health_and_page(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["tickers"] and h["date"]
    assert h["ollama"]["ok"] is True and h["run_token_required"] is False
    r = client.get("/")
    assert r.status_code in (200, 503)


def test_saved_data_endpoints(client):
    rows = client.get("/api/tickers").json()
    assert len(rows) >= 1 and {"ticker", "last_price", "daily_signal"} <= set(rows[0])
    t = rows[0]["ticker"]
    assert client.get(f"/api/tickers/{t}").json()["ticker"] == t
    assert client.get("/api/tickers/NOPE").status_code == 404
    assert client.get("/api/tickers/..%2F..%2Fsecret").status_code == 404


def test_models_list_matches_exporter(client):
    m = client.get("/api/models").json()
    keys = [x["key"] for x in m["forecast"]]
    defaults = [x["key"] for x in m["forecast"] if x["default"]]
    assert len(keys) == 5 and defaults == keys          # only the best five are offered, and all five run by default
    src = (BACKEND / "app" / "pipeline" / "export_ticker.py").read_text(encoding="utf-8")
    keep = re.search(r"KEEP_MODELS = \[(.*?)\]", src).group(1)
    assert sorted(re.findall(r'"(\w+)"', keep)) == sorted(defaults)
    assert m["llm"]["models"] == ["qwen2.5:7b", "llama3.2:3b"]
    from app.core import config
    assert m["analysis_tickers"] == config.tickers()


def test_forecast_refuses_the_dropped_models(client):
    r = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["ridge", "arima"]})
    assert r.status_code == 400 and "arima" in r.json()["detail"]


def test_model_runs_use_the_container_cpu_limit(monkeypatch):
    from app.core import config
    monkeypatch.setenv("RAEM_THREADS", "2")
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    assert config.cpu_limit() == 2 and config.thread_env()["OMP_NUM_THREADS"] == "2"
    monkeypatch.setenv("OMP_NUM_THREADS", "1")                # a value set on the server wins
    assert config.thread_env()["OMP_NUM_THREADS"] == "1" and config.thread_env()["LOKY_MAX_CPU_COUNT"] == "2"
    monkeypatch.delenv("RAEM_THREADS")
    assert 1 <= config.cpu_limit() <= config.RUN_THREADS   # a 48-vCPU plan still gets 2 threads


def test_forecast_run_then_same_date_reuse(client):
    r = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["ridge", "theta"]}).json()
    assert r["reused"] is None and r["run"]["status"] in ("queued", "running")
    done = wait_done(client, r["run"]["id"])
    assert done["status"] == "done"
    full = client.get(f"/api/runs/{done['id']}").json()
    assert full["result"]["models"] == ["ridge", "theta"]
    # same company, date and models: the stored result comes back at once, with the result
    again = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["theta", "ridge"]}).json()
    assert again["reused"] == "stored" and again["run"]["id"] == done["id"] and again["run"]["result"]
    # different models: a new run
    other = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["ridge"]}).json()
    assert other["reused"] is None and other["run"]["id"] != done["id"]
    wait_done(client, other["run"]["id"])
    # force: a new run even though one is stored
    forced = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["ridge", "theta"], "force": True}).json()
    assert forced["reused"] is None and forced["run"]["id"] != done["id"]
    wait_done(client, forced["run"]["id"])


def test_identical_request_joins_active_run(client):
    a = client.post("/api/forecast", json={"ticker": "IBM", "models": ["lasso"]}).json()
    b = client.post("/api/forecast", json={"ticker": "IBM", "models": ["lasso"]}).json()
    assert b["reused"] in ("active", "stored") and b["run"]["id"] == a["run"]["id"]
    wait_done(client, a["run"]["id"])


def test_forecast_validation(client):
    assert client.post("/api/forecast", json={"ticker": "AAPL", "models": ["nope"]}).status_code == 400
    r = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["ridge", "foo"]})
    assert r.status_code == 400 and "Unknown model: foo" in r.json()["detail"]
    assert client.post("/api/forecast", json={"ticker": "AAPL", "models": []}).status_code == 400
    assert client.post("/api/forecast", json={"ticker": "ZZZ"}).status_code == 404


def test_analysis_run_reuse_and_ollama_checks(client, monkeypatch):
    from app.core import config
    r = client.post("/api/analysis", json={"ticker": "AAPL", "llm_model": "qwen2.5:7b"}).json()
    done = wait_done(client, r["run"]["id"])
    assert done["status"] == "done" and done["summary"]["rating"] == "Hold"
    again = client.post("/api/analysis", json={"ticker": "AAPL", "llm_model": "qwen2.5:7b"}).json()
    assert again["reused"] == "stored" and again["run"]["result"]["entry"]["latest"]["rating"] == "Hold"
    # another model is a separate run
    other = client.post("/api/analysis", json={"ticker": "AAPL", "llm_model": "llama3.2:3b"}).json()
    assert other["reused"] is None
    wait_done(client, other["run"]["id"])
    # Ollama down: a stored run is still served, a new one is refused with a clear message
    monkeypatch.setattr(config, "llm_models", lambda *a, **k: {"ok": False, "url": "x", "models": [], "default": "qwen2.5:7b",
                                                         "embeddings_ok": False, "error": "Ollama is not reachable at x."})
    assert client.post("/api/analysis", json={"ticker": "AAPL", "llm_model": "qwen2.5:7b"}).json()["reused"] == "stored"
    down = client.post("/api/analysis", json={"ticker": "AAPL", "llm_model": "qwen2.5:7b", "force": True})
    assert down.status_code == 503 and "Ollama" in down.json()["detail"]


def test_analysis_validation(client):
    assert client.post("/api/analysis", json={"ticker": "AAPL", "llm_model": "gpt-9"}).status_code == 400
    assert client.post("/api/analysis", json={"ticker": "a b"}).status_code == 400            # not a ticker
    assert client.post("/api/analysis", json={"ticker": "TSLA", "market": "mars"}).status_code == 400


def test_run_token(client, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config, "RUN_TOKEN", "s3cret")
    assert client.post("/api/forecast", json={"ticker": "AAPL", "models": ["arima"]}).status_code == 401
    ok = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["arima"]}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 202
    wait_done(client, ok.json()["run"]["id"])
    assert client.get("/api/health").json()["run_token_required"] is True


def test_refresh_needs_token(client, monkeypatch):
    from app.core import config
    assert client.post("/api/refresh").status_code == 403
    monkeypatch.setattr(config, "REFRESH_TOKEN", "r")
    assert client.post("/api/refresh").status_code == 401
    run = client.post("/api/refresh", headers={"Authorization": "Bearer r"}).json()
    assert wait_done(client, run["id"])["status"] == "done"


def test_history_and_latest(client):
    r = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["nbeats"]}).json()
    wait_done(client, r["run"]["id"])
    hist = client.get("/api/runs", params={"kind": "forecast", "ticker": "AAPL"}).json()
    assert hist and "result" not in hist[0]
    assert client.get("/api/runs", params={"status": "queued,running"}).json() == []
    assert client.get("/api/runs", params={"status": "done"}).json()[0]["id"] == r["run"]["id"]
    latest = client.get("/api/runs/latest").json()
    assert "AAPL" in latest["forecast"] and latest["forecast"]["AAPL"]["result"]


def test_failed_run_is_recorded(client, monkeypatch):
    from app.workers import jobs

    def boom(run):
        raise RuntimeError("model crashed")
    monkeypatch.setitem(jobs.RUNNERS, "forecast", boom)
    r = client.post("/api/forecast", json={"ticker": "IBM", "models": ["theta"]}).json()
    done = wait_done(client, r["run"]["id"])
    assert done["status"] == "failed" and "model crashed" in done["error"]
    # a failed run is not reused
    again = client.post("/api/forecast", json={"ticker": "IBM", "models": ["theta"]}).json()
    assert again["reused"] is None
    assert wait_done(client, again["run"]["id"])["status"] == "failed"


def test_session_reader_keeps_new_steps_and_drops_nan(tmp_path, monkeypatch):
    monkeypatch.setenv("RAEM_DB", str(tmp_path / "archive.db"))
    monkeypatch.setenv("RAEM_SNAPSHOT", str(tmp_path / "no-snapshot.json.gz"))   # only the log written below counts
    sys.path[:0] = [str(BACKEND / "app" / "pipeline"), str(BACKEND / "app" / "ai_engine")]
    from sessions import ai_entry
    log = [{"company": "AAPL", "trade_date": "2026-10-22", "session_start": "2026-10-22 10:00:00", "session_end": "x",
            "final_decision": "**Rating**: Buy", "summary_table": {"market_indicators": {"rsi": float("nan")}},
            "steps": [{"step": "LLM Setup", "output": "-"}, {"step": "Graph Analyst", "output": "peers"},
                      {"step": "Risk Management Debate (1 Round(s))", "output": "r"}, {"step": "Brand New Step", "output": "kept"}]}]
    (tmp_path / "trading_log_APPLE.json").write_text(json.dumps(log), encoding="utf-8")
    e = ai_entry(str(tmp_path), "AAPL")
    assert list(e["latest"]["steps"]) == ["Graph analyst", "Risk debate", "Brand New Step"]
    assert e["latest"]["rating"] == "Buy" and e["latest"]["summary"]["market_indicators"]["rsi"] is None
    assert ai_entry(str(tmp_path), "AAPL", since="2026-10-23") is None




def test_archive_keeps_sessions_the_notebook_pruned(tmp_path, monkeypatch):
    """The notebooks keep only 10 days in their log; the archive keeps everything."""
    monkeypatch.setenv("RAEM_DB", str(tmp_path / "archive.db"))
    sys.path[:0] = [str(BACKEND / "app" / "pipeline"), str(BACKEND / "app" / "ai_engine")]
    from sessions import ai_entry, archive_logs
    log = tmp_path / "trading_log_IBM.json"
    old = {"company": "IBM", "trade_date": "2026-09-01", "session_start": "2026-09-01 10:00:00", "final_decision": "**Rating**: Hold", "steps": []}
    new = {"company": "IBM", "trade_date": "2026-10-22", "session_start": "2026-10-22 10:00:00", "final_decision": "**Rating**: Buy", "steps": []}
    log.write_text(json.dumps([old]), encoding="utf-8")
    assert archive_logs(str(tmp_path)) == 1
    log.write_text(json.dumps([new]), encoding="utf-8")   # the notebook rewrote its log without the old session
    e = ai_entry(str(tmp_path), "IBM")
    assert [s["date"] for s in e["sessions"]] == ["2026-09-01", "2026-10-22"] and e["latest"]["rating"] == "Buy"


def test_trading_log_keeps_every_session_unless_a_limit_is_set(tmp_path, monkeypatch):
    from app.ai_pipeline import config, logger
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "log_file_for", lambda t: tmp_path / f"trading_log_{t}.json")
    old = {"company": "IBM", "trade_date": "2025-01-02", "session_start": "2025-01-02 10:00:00", "steps": []}
    (tmp_path / "trading_log_IBM.json").write_text(json.dumps([old]), encoding="utf-8")
    log = logger.SessionLog("IBM", "2026-10-22")
    assert log._persist() == 2                                   # the 2025 session is still there: no 10-day limit
    monkeypatch.setattr(config, "LOG_MAX_DAYS", 10)              # RAEM_LOG_MAX_DAYS=10 brings the old rolling window back
    assert logger.SessionLog("IBM", "2026-10-23")._persist() == 2


def test_queue_limit(client, monkeypatch):
    from app.core import config
    from app.workers import jobs
    monkeypatch.setattr(config, "MAX_QUEUE", 1)

    def slow(run):
        import time
        time.sleep(1.0)
        return {"forecast": {}}, {}
    monkeypatch.setitem(jobs.RUNNERS, "forecast", slow)
    first = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["ridge"]}).json()
    second = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["lasso"]})
    assert second.status_code == 429 and "waiting" in second.json()["detail"]
    wait_done(client, first["run"]["id"])


CLOUD_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY")


def test_cloud_providers_listed(client, monkeypatch):
    for k in CLOUD_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("RAEM_GROQ_MODELS", "llama-x, llama-y")
    provs = {p["key"]: p for p in client.get("/api/models").json()["llm_providers"]}
    assert list(provs) == ["ollama", "openai", "anthropic", "google", "groq", "kimi"]
    assert provs["ollama"]["models"] == ["qwen2.5:7b", "llama3.2:3b"]
    assert provs["openai"]["ok"] is True and provs["anthropic"]["ok"] is False
    assert provs["groq"]["models"] == ["llama-x", "llama-y"] and provs["groq"]["key_env"] == "GROQ_API_KEY"
    assert "sk-test" not in json.dumps(provs)  # keys never leave the server


def test_cloud_analysis_run(client, monkeypatch):
    from app.core import config
    for k in CLOUD_KEYS:
        monkeypatch.delenv(k, raising=False)
    no_key = client.post("/api/analysis", json={"ticker": "AAPL", "llm_provider": "openai"})
    assert no_key.status_code == 400 and "OPENAI_API_KEY" in no_key.json()["detail"]
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # a cloud run does not need Ollama
    monkeypatch.setattr(config, "llm_models", lambda *a, **k: {"ok": False, "url": "x", "models": [], "default": "qwen2.5:7b",
                                                         "embeddings_ok": False, "error": "Ollama is not reachable at x."})
    r = client.post("/api/analysis", json={"ticker": "AAPL", "llm_provider": "openai"}).json()
    assert r["run"]["model_key"] == "openai:gpt-4.1-mini" and r["run"]["params"]["provider"] == "openai"
    wait_done(client, r["run"]["id"])
    again = client.post("/api/analysis", json={"ticker": "AAPL", "llm_provider": "openai", "llm_model": "gpt-4.1-mini"}).json()
    assert again["reused"] == "stored"
    bad = client.post("/api/analysis", json={"ticker": "AAPL", "llm_provider": "openai", "llm_model": "qwen2.5:7b"})
    assert bad.status_code == 400 and "RAEM_OPENAI_MODELS" in bad.json()["detail"]
    assert client.post("/api/analysis", json={"ticker": "AAPL", "llm_provider": "nope"}).status_code == 400








def test_hold_and_resume(client, monkeypatch):
    """Hold stops a running analysis and keeps it; asking again resumes the same run."""
    from app.workers import jobs
    attempts = []

    def slow_analysis(run):
        attempts.append(run["id"])
        if len(attempts) == 1:  # first attempt: runs until it is held
            jobs._log(run["id"], "== [13/46] Market Analyst")
            for _ in range(600):
                if run["id"] in jobs._held:
                    raise RuntimeError("stopped")
                time.sleep(0.05)
        entry = {"sessions": [], "latest": {"date": run["run_date"], "rating": "Buy", "steps": {}}}
        return {"ticker": run["ticker"], "entry": entry}, {"rating": "Buy"}

    monkeypatch.setitem(jobs.RUNNERS, "analysis", slow_analysis)
    r = client.post("/api/analysis", json={"ticker": "AAPL", "llm_model": "qwen2.5:7b"}).json()["run"]
    t0 = time.time()
    while client.get(f"/api/jobs/{r['id']}").json()["status"] != "running" and time.time() - t0 < 15:
        time.sleep(0.05)
    time.sleep(0.2)
    assert client.post(f"/api/runs/{r['id']}/hold").status_code == 200
    t0 = time.time()
    while (held := client.get(f"/api/jobs/{r['id']}").json())["status"] != "held" and time.time() - t0 < 15:
        time.sleep(0.05)
    assert held["status"] == "held" and held["step"] == "[13/46] Market Analyst"
    again = client.post("/api/analysis", json={"ticker": "AAPL", "llm_model": "qwen2.5:7b"}).json()
    assert again["reused"] == "resumed" and again["run"]["id"] == r["id"]
    done = wait_done(client, r["id"])
    assert done["status"] == "done" and attempts == [r["id"], r["id"]]
    assert client.post(f"/api/runs/{r['id']}/hold").status_code == 409  # finished: nothing to hold


def test_hold_rules(client, monkeypatch):
    from app.workers import jobs
    f = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["ridge"]}).json()["run"]
    assert client.post(f"/api/runs/{f['id']}/hold").status_code == 400  # forecasts are short
    wait_done(client, f["id"])
    assert client.post("/api/runs/nope/hold").status_code == 404

    def slow(run):
        time.sleep(1.0)
        return {"ticker": run["ticker"], "entry": {"sessions": [], "latest": {"date": run["run_date"], "steps": {}}}}, {}

    monkeypatch.setitem(jobs.RUNNERS, "analysis", slow)
    first = client.post("/api/analysis", json={"ticker": "AAPL", "llm_model": "qwen2.5:7b"}).json()["run"]
    second = client.post("/api/analysis", json={"ticker": "IBM", "llm_model": "qwen2.5:7b"}).json()["run"]
    assert client.post(f"/api/runs/{second['id']}/hold").json()["status"] == "held"  # a queued run is held at once
    wait_done(client, first["id"])
    assert client.get(f"/api/jobs/{second['id']}").json()["status"] == "held"


def test_restart_puts_running_runs_on_hold(tmp_path):
    from app.db import runs as db
    db.set_path(tmp_path / "restart.db")
    db.init()
    r = db.create("analysis", "AAPL", "2026-10-22", "qwen2.5:7b", {})
    db.update(r["id"], status="running", step="[20/46] Graph analyst")
    db.init()  # the server starts again
    after = db.get(r["id"])
    assert after["status"] == "held" and after["step"] == "[20/46] Graph analyst" and "continue" in after["error"]


def test_latest_runs_any_date(client):
    from app.db import runs as db
    old = db.create("forecast", "IBM", "2026-10-01", "ridge", {"models": ["ridge"]})
    db.update(old["id"], status="done", finished_at="2026-10-01 10:00:00", result={"ticker": "IBM"})
    assert "IBM" not in client.get("/api/runs/latest").json()["forecast"]  # today only by default
    assert client.get("/api/runs/latest", params={"date": "all"}).json()["forecast"]["IBM"]["id"] == old["id"]


def test_kimi_provider_with_either_key_name(client, monkeypatch):
    for k in CLOUD_KEYS + ("MOONSHOT_API_KEY", "KIMI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KIMI_API_KEY", "sk-kimi")
    provs = {p["key"]: p for p in client.get("/api/models").json()["llm_providers"]}
    assert provs["kimi"]["ok"] is True and provs["kimi"]["models"][0].startswith("kimi")
    r = client.post("/api/analysis", json={"ticker": "IBM", "llm_provider": "kimi"}).json()["run"]
    assert r["model_key"] == "kimi:" + provs["kimi"]["models"][0]
    wait_done(client, r["id"])




def test_rating_formats():
    sys.path[:0] = [str(BACKEND / "app" / "pipeline"), str(BACKEND / "app" / "ai_engine")]
    from sessions import rating
    assert rating("**Rating**: Hold\n\n**Executive Summary**: ...") == "Hold"
    assert rating("BUY") == "Buy" and rating("**SELL**") == "Sell"
    assert rating("Rating: overweight\nThesis ...") == "Overweight"
    assert rating("... FINAL TRANSACTION PROPOSAL: **SELL**") == "Sell"
    assert rating("The market looks fine today.") is None and rating(None) is None


def test_unfinished_runs_report_saved_answers(client, monkeypatch, tmp_path):
    """A held or failed analysis says how many saved LLM answers the next run of it will reuse."""
    from app.core import config
    from app.db import runs as db
    monkeypatch.setattr(config, "STORAGE", tmp_path)
    r = db.create("analysis", "AAPL", config.today(), "qwen2.5:7b", {"provider": "ollama", "llm_model": "qwen2.5:7b"})
    db.update(r["id"], status="failed", step="[37/46] Prediction Engine", error="CellTimeoutError")
    path = config.llm_cache_path("AAPL", "qwen2.5:7b")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"entries": {"a": "x", "b": "y"}, "reused": 0, "new": 2}), encoding="utf-8")
    row = client.get("/api/runs", params={"kind": "analysis", "ticker": "AAPL"}).json()[0]
    assert row["id"] == r["id"] and row["saved_answers"] == 2
    assert client.get("/api/runs", params={"status": "failed"}).json()[0]["id"] == r["id"]


def test_debate_turns_and_final_signal():
    sys.path[:0] = [str(BACKEND / "app" / "pipeline"), str(BACKEND / "app" / "ai_engine")]
    from sessions import debate_turns, entry_from_session
    turns = debate_turns("\nBull Analyst: up\nBear Analyst: down\nBull Analyst: more up\nBear Analyst: more down")
    assert [(t["side"], t["round"]) for t in turns] == [("bull", 1), ("bear", 1), ("bull", 2), ("bear", 2)]
    s = {"trade_date": "2026-10-23", "session_start": "2026-10-23 10:00:00", "final_decision": "**Rating**: Overweight",
         "summary_table": {}, "steps": [
             {"step": "Investment Debate (Bull vs Bear)", "output": "Bull Analyst: up\nBear Analyst: down"},
             {"step": "Risk Management Debate (3 Rounds)", "output": {"aggressive": "go", "conservative": "wait", "neutral": "half"}},
             {"step": "Decision Verifier", "output": {"status": "OVERRIDDEN", "effective_signal": "HOLD"}}]}
    L = entry_from_session(s, [(s, None)])["latest"]
    assert L["signal"] == "HOLD"  # the verifier's effective signal wins over the Overweight rating
    assert len(L["debate"]["turns"]) == 2 and [t["side"] for t in L["debate"]["risk_turns"]] == ["aggressive", "conservative", "neutral"]
    s["steps"] = s["steps"][:2]
    assert entry_from_session(s, [(s, None)])["latest"]["signal"] == "BUY"  # no verifier: Overweight -> BUY


def test_live_session_endpoint(client):
    from app.db import runs as db
    r = db.create("analysis", "AAPL", "2026-10-23", "qwen2.5:7b", {})
    assert client.get(f"/api/runs/{r['id']}/live").json() == {"status": "queued", "entry": None}
    db.update(r["id"], status="done", result={"entry": {"latest": {"rating": "Buy"}}})
    assert client.get(f"/api/runs/{r['id']}/live").json()["entry"]["latest"]["rating"] == "Buy"
    f = db.create("forecast", "AAPL", "2026-10-23", "ridge", {})
    assert client.get(f"/api/runs/{f['id']}/live").status_code == 404


def test_persist_keeps_runtime_files_and_takes_pushed_ones(tmp_path):
    """One volume (Railway): first start seeds it; later, a pushed file wins and a run-time change is kept."""
    sys.path.insert(0, str(BACKEND / "tools"))
    from persist import sync_tree
    img, vol, seed = tmp_path / "image", tmp_path / "vol", {}
    (img / "logs").mkdir(parents=True)
    (img / "logs" / "trading_log.json").write_text("v1", encoding="utf-8")
    (img / "prices.csv").write_text("p1", encoding="utf-8")
    assert sync_tree(img, vol, seed, "x") == 2 and (vol / "prices.csv").read_text() == "p1"
    (vol / "logs" / "trading_log.json").write_text("written at run time", encoding="utf-8")
    (vol / "runtime_only.db").write_text("db", encoding="utf-8")
    (img / "prices.csv").write_text("p2 pushed", encoding="utf-8")  # a new deploy brings a new file version
    assert sync_tree(img, vol, seed, "x") == 1
    assert (vol / "prices.csv").read_text() == "p2 pushed"
    assert (vol / "logs" / "trading_log.json").read_text() == "written at run time"
    assert (vol / "runtime_only.db").read_text() == "db"


def test_persist_links_folders_onto_the_volume(tmp_path, monkeypatch):
    sys.path.insert(0, str(BACKEND / "tools"))
    import persist
    web = tmp_path / "web"
    (web / "backend" / "storage").mkdir(parents=True)
    (web / "backend" / "storage" / "raem.db").write_text("db", encoding="utf-8")
    monkeypatch.setattr(persist, "WEB", web)
    monkeypatch.setattr(persist, "FOLDERS", ["backend/storage"])
    monkeypatch.setenv("RAEM_DATA_DIR", str(tmp_path / "data"))
    try:
        persist.main()
    except OSError as e:  # Windows without the right to make links
        pytest.skip(f"no symlinks here: {e}")
    link = web / "backend" / "storage"
    assert link.is_symlink() and (link / "raem.db").read_text() == "db"
    (link / "new.txt").write_text("kept", encoding="utf-8")
    assert (tmp_path / "data" / "backend" / "storage" / "new.txt").read_text() == "kept"
    persist.main()  # a second call in the same container changes nothing
    monkeypatch.delenv("RAEM_DATA_DIR")
    assert persist.main() == 0




def test_hugging_face_upload_list_and_header():
    """The Space gets what git would push (no .env, no run database), its own .gitattributes, and a docker header."""
    sys.path.insert(0, str(BACKEND / "tools"))
    import deploy_hf
    files = deploy_hf.files_to_upload()
    assert "Dockerfile" in files and "backend/app/main.py" in files and "frontend/dist/index.html" in files
    assert not any(p == ".env" or p.startswith(("backend/storage/", ".github/")) or p == ".gitattributes" for p in files)
    head = deploy_hf.space_readme().split("---")[1]
    assert "sdk: docker" in head and "app_port: 8000" in head


def test_hf_dataset_keeps_state_across_restarts(tmp_path, monkeypatch):
    """Free Space storage: save() uploads the changing data; restore() brings it back, an image change wins, code is not synced."""
    import sqlite3
    sys.path.insert(0, str(BACKEND / "tools"))
    import hf_sync
    web = tmp_path / "web"
    for rel, text in {"backend/engine/notebooks/raem_chroma_db/x.bin": "memory v1", "backend/engine/notebooks/AAPL.py": "code",
                      "backend/engine/data/IBM.csv": "p1", "backend/data/extra.json": "{}"}.items():
        (web / rel).parent.mkdir(parents=True, exist_ok=True)
        (web / rel).write_text(text, encoding="utf-8")
    (web / "backend" / "storage").mkdir(parents=True)
    c = sqlite3.connect(web / "backend" / "storage" / "raem.db"); c.execute("create table runs(id)"); c.execute("insert into runs values ('r1')"); c.commit(); c.close()
    monkeypatch.setattr(hf_sync, "WEB", web)
    monkeypatch.setattr(hf_sync, "BOOT", tmp_path / "boot.json")
    monkeypatch.setenv("RAEM_HF_DATASET", "me/raem-data")
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    remote = tmp_path / "remote"

    class FakeApi:
        def create_repo(self, *a, **k):
            pass

        def upload_folder(self, folder_path, **k):
            shutil.rmtree(remote, ignore_errors=True)
            shutil.copytree(folder_path, remote)

    def fake_download(**k):
        if not remote.exists():
            raise FileNotFoundError("empty dataset")
        shutil.copytree(remote, k["local_dir"], dirs_exist_ok=True)
        return k["local_dir"]

    import shutil
    assert hf_sync.restore(api=FakeApi(), download=fake_download) == 0      # first start: nothing saved yet
    (web / "backend/engine/notebooks/raem_chroma_db/x.bin").write_text("memory v2 (written by a run)", encoding="utf-8")
    assert hf_sync.save("analysis AAPL", api=FakeApi())
    assert not (remote / "backend/engine/notebooks/AAPL.py").exists()   # code is never synced
    assert (remote / "backend/storage/raem.db").exists()
    # restart with a new image: the disk is back to the image, and one price file was pushed in git
    (web / "backend/engine/notebooks/raem_chroma_db/x.bin").write_text("memory v1", encoding="utf-8")
    (web / "backend/engine/data/IBM.csv").write_text("p2 pushed", encoding="utf-8")
    (web / "backend/storage/raem.db").unlink()
    hf_sync.restore(api=FakeApi(), download=fake_download)
    assert (web / "backend/engine/notebooks/raem_chroma_db/x.bin").read_text() == "memory v2 (written by a run)"
    assert (web / "backend/engine/data/IBM.csv").read_text() == "p2 pushed"
    assert sqlite3.connect(web / "backend/storage/raem.db").execute("select id from runs").fetchall() == [("r1",)]
    monkeypatch.delenv("HF_TOKEN")
    assert hf_sync.restore(api=FakeApi(), download=fake_download) == 0 and not hf_sync.save("x", api=FakeApi())


def test_archive_is_read_from_the_snapshot_when_there_is_no_database(tmp_path, monkeypatch):
    """A checkout without a run database (fresh clone, an export run) still sees the saved session history."""
    import gzip, json, sys
    sys.path.insert(0, str(BACKEND / "app" / "pipeline"))
    import sessions
    snap = tmp_path / "snap.json.gz"
    rows = [{"company": "AAPL", "session_start": "2026-10-22 10:00:00", "trade_date": "2026-10-22", "signal": "HOLD", "data": {"company": "AAPL", "n": 2}},
            {"company": "AAPL", "session_start": "2026-10-05 10:00:00", "trade_date": "2026-10-05", "signal": "BUY", "data": {"company": "AAPL", "n": 1}},
            {"company": "IBM", "session_start": "2026-10-05 10:00:00", "trade_date": "2026-10-05", "signal": "HOLD", "data": {"company": "IBM", "n": 9}}]
    with gzip.open(snap, "wt", encoding="utf-8") as fh:
        json.dump(rows, fh)
    monkeypatch.setenv("RAEM_SNAPSHOT", str(snap))
    monkeypatch.setenv("RAEM_DB", str(tmp_path / "missing.db"))
    got = sessions.archived("AAPL")
    assert [d["n"] for d, _ in got] == [1, 2] and [g for _, g in got] == ["BUY", "HOLD"]
    assert sessions.archived("NOPE") == []


def test_failed_analysis_error_carries_the_cause(monkeypatch):
    """The run's error names the reason the pipeline runner printed, not just 'did not finish'."""
    from app.workers import jobs
    lines = ["== [12/16] Risk Debate", "   Traceback ...", "!! The analysis failed:",
             "!! Cause: RuntimeError: provider went away  (in step: Risk Debate)"]

    def fake_stream(rid, argv, timeout, env=None):
        jobs._live[rid] = list(lines)
        return False
    monkeypatch.setattr(jobs, "_stream", fake_stream)
    run = {"id": "r1", "ticker": "AAPL", "model_key": "kimi:kimi-k3", "params": {"provider": "kimi", "llm_model": "kimi-k3"}}
    with pytest.raises(RuntimeError) as e:
        jobs._run_analysis(run)
    assert "Cause: RuntimeError: provider went away (in step: Risk Debate)" in str(e.value).replace("  (", " (")
    jobs._live.pop("r1", None)
