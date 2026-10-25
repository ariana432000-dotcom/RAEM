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
    assert len(keys) == 14 and len(defaults) == 6
    src = (BACKEND / "exporter" / "export_ticker.py").read_text(encoding="utf-8")
    keep = re.search(r"KEEP_MODELS = \[(.*?)\]", src).group(1)
    assert sorted(re.findall(r'"(\w+)"', keep)) == sorted(defaults)
    assert m["llm"]["models"] == ["qwen2.5:7b", "llama3.2:3b"]
    assert m["analysis_tickers"] == ["AAPL", "IBM"]


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
    from app import config
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
    assert client.post("/api/analysis", json={"ticker": "BATBC"}).status_code == 404


def test_run_token(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "RUN_TOKEN", "s3cret")
    assert client.post("/api/forecast", json={"ticker": "AAPL", "models": ["arima"]}).status_code == 401
    ok = client.post("/api/forecast", json={"ticker": "AAPL", "models": ["arima"]}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 202
    wait_done(client, ok.json()["run"]["id"])
    assert client.get("/api/health").json()["run_token_required"] is True


def test_refresh_needs_token(client, monkeypatch):
    from app import config
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
    from app import jobs

    def boom(run):
        raise RuntimeError("model crashed")
    monkeypatch.setitem(jobs.RUNNERS, "forecast", boom)
    r = client.post("/api/forecast", json={"ticker": "IBM", "models": ["tft"]}).json()
    done = wait_done(client, r["run"]["id"])
    assert done["status"] == "failed" and "model crashed" in done["error"]
    # a failed run is not reused
    again = client.post("/api/forecast", json={"ticker": "IBM", "models": ["tft"]}).json()
    assert again["reused"] is None
    assert wait_done(client, again["run"]["id"])["status"] == "failed"


def test_session_reader_keeps_new_steps_and_drops_nan(tmp_path, monkeypatch):
    monkeypatch.setenv("RAEM_DB", str(tmp_path / "archive.db"))
    sys.path.insert(0, str(BACKEND / "exporter"))
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


def test_notebook_rewrite():
    sys.path.insert(0, str(BACKEND / "exporter"))
    from run_notebook import rewrite
    src = '%pip install x -q\nif ok:\n    %pip install -r r.txt\nllm = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", repeat_penalty=1.15)\nu = "http://localhost:11434/api/embeddings"'
    out, n = rewrite(src)
    assert "%pip" not in out and "    pass" in out
    assert 'model=RAEM_LLM_MODEL' in out and 'base_url=RAEM_OLLAMA_URL + ""' in out
    assert 'RAEM_OLLAMA_URL + "/api/embeddings"' in out and "repeat_penalty=1.15" in out
    assert n == 5
    compile(out.replace("%", "#"), "cell", "exec")


def test_archive_keeps_sessions_the_notebook_pruned(tmp_path, monkeypatch):
    """The notebooks keep only 10 days in their log; the archive keeps everything."""
    monkeypatch.setenv("RAEM_DB", str(tmp_path / "archive.db"))
    sys.path.insert(0, str(BACKEND / "exporter"))
    from sessions import ai_entry, archive_logs
    log = tmp_path / "trading_log_IBM.json"
    old = {"company": "IBM", "trade_date": "2026-09-01", "session_start": "2026-09-01 10:00:00", "final_decision": "**Rating**: Hold", "steps": []}
    new = {"company": "IBM", "trade_date": "2026-10-22", "session_start": "2026-10-22 10:00:00", "final_decision": "**Rating**: Buy", "steps": []}
    log.write_text(json.dumps([old]), encoding="utf-8")
    assert archive_logs(str(tmp_path)) == 1
    log.write_text(json.dumps([new]), encoding="utf-8")   # the notebook rewrote its log without the old session
    e = ai_entry(str(tmp_path), "IBM")
    assert [s["date"] for s in e["sessions"]] == ["2026-09-01", "2026-10-22"] and e["latest"]["rating"] == "Buy"


def test_queue_limit(client, monkeypatch):
    from app import config, jobs
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
    from app import config
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


def test_notebook_rewrite_for_cloud_and_memory():
    sys.path.insert(0, str(BACKEND / "exporter"))
    from run_notebook import rewrite
    src = ('from langchain_ollama import ChatOllama\nllm = ChatOllama(model="qwen2.5:7b", num_predict=2048)\n'
           '_prev = ChatOllama.invoke\nChatOllama.invoke = _guard\n'
           'ef = embedding_functions.OllamaEmbeddingFunction(url="http://localhost:11434/api/embeddings", model_name="nomic-embed-text")\n'
           'CHROMA_DB_PATH = "./raem_chroma_db"')
    local, _ = rewrite(src)
    assert "ChatOllama(model=RAEM_LLM_MODEL" in local and "RAEMCloudChat" not in local
    assert "RAEM_embedding_function(url=RAEM_OLLAMA_URL" in local and "CHROMA_DB_PATH = RAEM_CHROMA_PATH" in local
    cloud, _ = rewrite(src, cloud=True)
    assert "llm = RAEMCloudChat(model=RAEM_LLM_MODEL, num_predict=2048)" in cloud
    assert "import ChatOllama" in cloud and "_prev = ChatOllama.invoke" in cloud and "ChatOllama.invoke = _guard" in cloud


def test_cloud_adapter_keeps_the_fact_guard():
    """RAEMCloudChat is a ChatOllama, so a patch on ChatOllama.invoke (the notebooks' Fact Guard) still wraps it."""
    pytest.importorskip("langchain_ollama")
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import HumanMessage
    sys.path.insert(0, str(BACKEND / "exporter"))
    from run_notebook import CONFIG_CELL
    from langchain_ollama import ChatOllama
    ns = {}
    os.environ["RAEM_LLM_PROVIDER"], os.environ["RAEM_LLM_MODEL"] = "openai", "gpt-test"
    try:
        exec(CONFIG_CELL, ns)
    finally:
        os.environ.pop("RAEM_LLM_PROVIDER"), os.environ.pop("RAEM_LLM_MODEL")
    calls = []

    def fake_cloud(temperature, max_tokens):
        calls.append((temperature, max_tokens))
        if temperature is not None and len(calls) == 1:
            class NoTemp(FakeListChatModel):
                def _generate(self, *a, **k):
                    raise ValueError("Unsupported value: 'temperature' does not support 0 with this model.")
            return NoTemp(responses=["never"])
        return FakeListChatModel(responses=["cloud text"])

    ns["_raem_cloud_model"] = fake_cloud
    original = ChatOllama.invoke
    ChatOllama.invoke = lambda self, inp, *a, **k: original(self, inp, *a, **k).model_copy(update={"content": "guarded: cloud text"})
    try:
        llm = ns["RAEMCloudChat"](model="gpt-test", temperature=0, num_predict=900)
        assert isinstance(llm, ChatOllama) and llm.model == "gpt-test"
        assert llm.invoke([HumanMessage(content="hi")]).content == "guarded: cloud text"
        assert calls == [(0, 900), (None, 900)]  # retried without a temperature
    finally:
        ChatOllama.invoke = original


def test_memory_falls_back_to_hashing_when_no_model(monkeypatch):
    """No Ollama nomic-embed-text and no downloadable chromadb model: the run still gets a working memory store."""
    pytest.importorskip("chromadb")
    pytest.importorskip("langchain_ollama")
    sys.path.insert(0, str(BACKEND / "exporter"))
    import run_notebook as rn
    monkeypatch.setattr(rn, "local_model_ready", lambda *a, **k: False)
    monkeypatch.setenv("RAEM_EMBED", "local")
    assert rn.embed_mode("http://127.0.0.1:9") == "hash"
    monkeypatch.delenv("RAEM_EMBED")
    assert rn.embed_mode("http://127.0.0.1:9") == "hash"  # Ollama unreachable, model unavailable
    monkeypatch.setenv("RAEM_EMBED", "hash")
    ns = {}
    exec(rn.CONFIG_CELL, ns)
    assert ns["RAEM_CHROMA_PATH"] == "./raem_chroma_db_hash"
    ef = ns["RAEM_embedding_function"](url="x", model_name="nomic-embed-text")
    a, b, c = ef(["Apple iPhone sales rise", "apple iphone sales", "Federal Reserve raises rates"])
    assert len(a) == 384 and abs(float((a * a).sum()) - 1) < 1e-5
    assert float(a @ b) > float(a @ c)  # shared words score higher


def test_hold_and_resume(client, monkeypatch):
    """Hold stops a running analysis and keeps it; asking again resumes the same run."""
    from app import jobs
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
    from app import jobs
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
    from app import db
    db.set_path(tmp_path / "restart.db")
    db.init()
    r = db.create("analysis", "AAPL", "2026-10-22", "qwen2.5:7b", {})
    db.update(r["id"], status="running", step="[20/46] Graph analyst")
    db.init()  # the server starts again
    after = db.get(r["id"])
    assert after["status"] == "held" and after["step"] == "[20/46] Graph analyst" and "continue" in after["error"]


def test_latest_runs_any_date(client):
    from app import db
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


def test_saved_answers_survive_a_new_kernel(tmp_path, monkeypatch):
    """The resume file: a new kernel (CONFIG_CELL run again) answers an unchanged prompt without calling the model."""
    pytest.importorskip("langchain_ollama")
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import HumanMessage
    from langchain_ollama import ChatOllama
    sys.path.insert(0, str(BACKEND / "exporter"))
    from run_notebook import CONFIG_CELL, saved_counts
    cache = tmp_path / "AAPL__openai_x.json"
    monkeypatch.setenv("RAEM_LLM_PROVIDER", "openai")
    monkeypatch.setenv("RAEM_LLM_MODEL", "x")
    monkeypatch.setenv("RAEM_LLM_CACHE", str(cache))
    original = ChatOllama._generate
    calls = []

    def kernel(answer):
        ChatOllama._generate = original
        ns = {}
        exec(CONFIG_CELL, ns)

        def fake(temperature, max_tokens):
            calls.append(answer)
            return FakeListChatModel(responses=[answer])
        ns["_raem_cloud_model"] = fake
        return ns["RAEMCloudChat"](model="x", temperature=0, num_predict=100)

    try:
        llm = kernel("first answer")
        assert llm.invoke([HumanMessage(content="AAPL close 339.50")]).content == "first answer"
        llm2 = kernel("NEW answer")  # held, then resumed in a new kernel
        assert llm2.invoke([HumanMessage(content="AAPL close 339.50")]).content == "first answer"  # same prompt: reused
        assert llm2.invoke([HumanMessage(content="AAPL close 341.20")]).content == "NEW answer"    # price changed: asked again
        assert calls == ["first answer", "NEW answer"]
        assert saved_counts(str(cache)) == {"entries": 2, "reused": 1, "new": 1}
    finally:
        ChatOllama._generate = original


def test_rating_formats():
    sys.path.insert(0, str(BACKEND / "exporter"))
    from sessions import rating
    assert rating("**Rating**: Hold\n\n**Executive Summary**: ...") == "Hold"
    assert rating("BUY") == "Buy" and rating("**SELL**") == "Sell"
    assert rating("Rating: overweight\nThesis ...") == "Overweight"
    assert rating("... FINAL TRANSACTION PROPOSAL: **SELL**") == "Sell"
    assert rating("The market looks fine today.") is None and rating(None) is None


def test_unfinished_runs_report_saved_answers(client, monkeypatch, tmp_path):
    """A held or failed analysis says how many saved LLM answers the next run of it will reuse."""
    from app import config, db
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
    sys.path.insert(0, str(BACKEND / "exporter"))
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
    from app import db
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


def test_past_memory_is_copied_when_ollama_is_missing(tmp_path, monkeypatch):
    """No nomic-embed-text (Railway): the notebooks' episodes and news are re-embedded into the run's own store."""
    chromadb = pytest.importorskip("chromadb")
    pytest.importorskip("langchain_ollama")
    from chromadb.api.types import Documents, EmbeddingFunction
    sys.path.insert(0, str(BACKEND / "exporter"))
    from run_notebook import CONFIG_CELL

    class Tiny(EmbeddingFunction[Documents]):  # stands in for nomic: other vectors, other size
        def __init__(self):
            pass

        def __call__(self, input):
            import numpy as np
            return [np.full(8, float(len(d)), dtype=np.float32) for d in input]

        @staticmethod
        def name():
            return "tiny-test"

        def get_config(self):
            return {}

        @staticmethod
        def build_from_config(config):
            return Tiny()

    src = chromadb.PersistentClient(path=str(tmp_path / "raem_chroma_db"))
    src.get_or_create_collection("raem_news_corpus", embedding_function=Tiny()).add(
        ids=["n1", "n2"], documents=["IBM cloud revenue beats estimates", "Apple iPhone sales rise"],
        metadatas=[{"company": "IBM"}, {"company": "AAPL"}])
    src.get_or_create_collection("raem_episodic_memory", embedding_function=Tiny()).add(
        ids=["e1"], documents=["HOLD in a neutral regime"], metadatas=[{"regime": "NEUTRAL_RANGING"}])
    del src
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAEM_EMBED", "hash")
    ns = {}
    exec(CONFIG_CELL, ns)
    exec(CONFIG_CELL, {})  # a second run copies nothing twice
    dst = chromadb.PersistentClient(path=str(tmp_path / "raem_chroma_db_hash"))
    ef = ns["RAEM_embedding_function"]("", "nomic-embed-text")
    news = dst.get_collection("raem_news_corpus", embedding_function=ef)
    assert news.count() == 2 and dst.get_collection("raem_episodic_memory", embedding_function=ef).count() == 1
    hit = news.query(query_texts=["IBM cloud revenue"], n_results=1, where={"company": "IBM"})
    assert hit["ids"][0] == ["n1"]


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
    for rel, text in {"backend/engine/notebooks/raem_chroma_db/x.bin": "memory v1", "backend/engine/notebooks/AAPL.ipynb": "code",
                      "backend/engine/data/IBM.csv": "p1", "backend/data/extra.json": "{}"}.items():
        (web / rel).parent.mkdir(parents=True, exist_ok=True)
        (web / rel).write_text(text, encoding="utf-8")
    (web / "backend" / "storage").mkdir(parents=True)
    c = sqlite3.connect(web / "backend" / "storage" / "raem.db"); c.execute("create table runs(id)"); c.execute("insert into runs values ('r1')"); c.commit(); c.close()
    monkeypatch.setattr(hf_sync, "WEB", web)
    monkeypatch.setattr(hf_sync, "BOOT", tmp_path / "boot.json")
    monkeypatch.setenv("RAEM_HF_DATASET", "me/ream-data")
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
    assert not (remote / "backend/engine/notebooks/AAPL.ipynb").exists()   # code is never synced
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
