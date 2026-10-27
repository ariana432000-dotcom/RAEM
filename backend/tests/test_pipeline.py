"""The AI-analysis pipeline (app/ai_engine/run_pipeline.py, app/ai_pipeline/) end to end against a fake OpenAI-compatible
server and canned market data: no network, no API key. Skipped where langchain / chromadb are not installed (the CI job
installs only the API test packages)."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("langchain_openai")
pytest.importorskip("langchain_core")
pytest.importorskip("chromadb")
pytest.importorskip("pandas")
pytest.importorskip("bdshare")
pytest.importorskip("stockstats")

BACKEND = Path(__file__).resolve().parents[1]
TEXT = ("**Rating**: Overweight\n\nFINAL TRANSACTION PROPOSAL: **BUY**\n\n"
        "Bull Analyst: growth is solid.\nBear Analyst: valuation is rich.\n"
        "Recommendation: Buy. Confidence: 0.7. Rationale: a balanced view of the evidence.\n\n"
        "| Indicator | Value | Signal | Interpretation |\n|---|---|---|---|\n| RSI | 55 | Neutral | ok |\n")
BILLING = "Your credit balance is running low for account x, please recharge immediately"


class FakeLLM(BaseHTTPRequestHandler):
    calls = 0
    fail_after = None   # answer with a billing error once this many calls were made

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).calls += 1
        if type(self).fail_after is not None and type(self).calls > type(self).fail_after:
            b = json.dumps({"error": {"message": BILLING, "type": "rate_limit_error", "code": "429"}}).encode()
            self.send_response(429)
        else:
            b = json.dumps({"id": "x", "object": "chat.completion", "model": "fake",
                            "choices": [{"index": 0, "message": {"role": "assistant", "content": TEXT}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture()
def server():
    FakeLLM.calls, FakeLLM.fail_after = 0, None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


CSV = "# Stock data for AAA\n\nDate,Open,High,Low,Close,Adj Close,Volume\n" + "\n".join(
    f"2026-10-{d:02d},100,102,99,{100 + d * 0.1:.2f},{100 + d * 0.1:.2f},1000" for d in range(1, 27))


def fake_vendor(method, *args, **kw):
    if method == "get_stock_data":
        return CSV
    if method == "get_indicators":
        return "\n".join(f"2026-10-{d:02d}: {50 + d * 0.5:.2f}" for d in range(26, 20, -1))
    if method in ("get_news", "get_global_news"):
        return "### AAA headline one (source: test)\nAAA wins a contract.\n### AAA headline two (source: test)\nAAA misses estimates.\n"
    if method == "get_fundamentals":
        return "Name: AAA Inc\nMarket Cap: 1000000000\nPE Ratio (TTM): 20.5\nEPS (TTM): 5.0\nSector: Technology\n"
    return "Date,Item,Value\n2026-06-30,Revenue,1000\n"


@pytest.fixture()
def env(tmp_path, monkeypatch, server):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for k, v in {"RAEM_LOG_DIR": str(log_dir), "RAEM_CHROMA_PATH": str(tmp_path / "chroma"), "RAEM_EMBED": "hash",
                 "RAEM_OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}/v1", "OPENAI_API_KEY": "x",
                 "RAEM_LLM_CACHE": str(tmp_path / "cache.json"), "RAEM_ENGINE": str(tmp_path / "engine")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    sys.path.insert(0, str(BACKEND))
    import importlib
    from app.ai_pipeline import config
    importlib.reload(config)
    from app.ai_pipeline import llm
    llm._STATS.update(entries={}, reused=0, new=0)
    llm._PATCHED.clear()
    # canned market data: every vendor call, the DSE code list, the macro snapshot and the outcome-backfill prices
    config.ensure_tradingagents_on_path()
    import tradingagents.dataflows.symbol_utils as su
    import tradingagents.dataflows.interface as iface
    monkeypatch.setattr(su, "is_dse_ticker", lambda s: False)
    monkeypatch.setattr(iface, "route_to_vendor", fake_vendor)
    for name, mod in list(sys.modules.items()):
        if name.startswith("tradingagents.agents.utils") and hasattr(mod, "route_to_vendor"):
            monkeypatch.setattr(mod, "route_to_vendor", fake_vendor)
    from app.ai_pipeline import agents, memory
    monkeypatch.setattr(agents, "fetch_macro_snapshot", lambda *a, **k: {"vix": {"latest": 18.0, "avg": 17.5}, "tnx": {"latest": 4.2, "avg": 4.1}, "dxy": {"latest": 104.0, "avg": 103.5}})
    monkeypatch.setattr(memory, "_fetch_latest_close", lambda *a, **k: 105.0)
    return tmp_path


def run_cli(tmp_path, ticker="AAA", model="fake"):
    sys.path.insert(0, str(BACKEND / "app" / "ai_engine"))
    import importlib
    import run_pipeline
    importlib.reload(run_pipeline)
    out = tmp_path / "out.json"
    old = sys.argv
    sys.argv = ["run_pipeline.py", "--ticker", ticker, "--provider", "openai", "--model", model, "--out", str(out)]
    try:
        code = run_pipeline.main()
    finally:
        sys.argv = old
    return code, out


def test_full_run_writes_a_session_the_website_can_read(env, capsys):
    code, out = run_cli(env)
    log = capsys.readouterr().out
    assert code == 0, log
    assert "== [1/16] Outcome Backfill" in log and "== [16/16] Save Episode" in log
    res = json.load(open(out, encoding="utf-8"))
    latest = res["entry"]["latest"]
    assert latest["rating"] == "Overweight" and latest["signal"] == "BUY"
    assert {"Fundamentals", "Market", "News", "Bull vs bear debate", "Trader", "Risk debate", "Portfolio manager", "Verifier"} <= set(latest["steps"])
    assert latest["debate"]["turns"] and latest["debate"]["risk_turns"] and latest["verifier"]
    assert latest["debate"]["complete"] is True and latest["llm_provider"] == "openai" and res["new_llm_calls"] > 5
    # the trading log is where sessions.py reads it
    assert (env / "logs" / "trading_log_AAA.json").exists()


def test_billing_error_is_reported_at_once_and_continue_reuses_the_saved_answers(env, server, capsys):
    FakeLLM.fail_after = 12
    code, out = run_cli(env)
    log = capsys.readouterr().out
    assert code == 1 and not out.exists()
    assert "!! Cause:" in log and "credit balance" in log and "no credit left" in log
    assert "(in step:" in log
    # no half-written session stays in the trading log
    p = env / "logs" / "trading_log_AAA.json"
    assert not p.exists() or json.load(open(p)) == []
    saved = json.load(open(env / "cache.json"))["entries"]
    assert len(saved) >= 10
    # credit is back: the run continues, reusing what was saved
    FakeLLM.fail_after = None
    before = FakeLLM.calls
    code, out = run_cli(env)
    log = capsys.readouterr().out
    assert code == 0, log
    assert "== Resuming:" in log
    assert json.load(open(out))["reused_answers"] >= 10
    assert FakeLLM.calls - before < 40


def test_hash_memory_and_copy_of_the_original_store(env, monkeypatch):
    """No nomic-embed-text and no downloadable model (Railway): hash embeddings still work and the original store's
    episodes and news chunks are copied into the run's own store, once."""
    import chromadb
    from chromadb.api.types import Documents, EmbeddingFunction
    from app.ai_pipeline import config, embeddings

    class Tiny(EmbeddingFunction[Documents]):   # stands in for nomic: other vectors, other size
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
        def build_from_config(cfg):
            return Tiny()

    src = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    src.get_or_create_collection("raem_news_corpus", embedding_function=Tiny()).add(
        ids=["n1", "n2"], documents=["IBM cloud revenue beats estimates", "Apple iPhone sales rise"],
        metadatas=[{"company": "IBM"}, {"company": "AAPL"}])
    src.get_or_create_collection("raem_episodic_memory", embedding_function=Tiny()).add(
        ids=["e1"], documents=["HOLD in a neutral regime"], metadatas=[{"regime": "NEUTRAL_RANGING"}])
    del src
    assert embeddings.copy_memory("hash") == 3 and embeddings.copy_memory("hash") == 0
    dst = chromadb.PersistentClient(path=embeddings.store_path("hash"))
    ef = embeddings.embedding_function("hash")
    news = dst.get_collection("raem_news_corpus", embedding_function=ef)
    assert news.count() == 2 and dst.get_collection("raem_episodic_memory", embedding_function=ef).count() == 1
    assert news.query(query_texts=["IBM cloud revenue"], n_results=1, where={"company": "IBM"})["ids"][0] == ["n1"]
    monkeypatch.setattr(embeddings, "local_model_ready", lambda *a, **k: False)
    monkeypatch.delenv("RAEM_EMBED")
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://127.0.0.1:9")
    assert embeddings.embed_mode() == "hash"


def test_providers_and_missing_keys(env, monkeypatch):
    from app.ai_pipeline import llm
    monkeypatch.delenv("RAEM_OPENAI_BASE_URL")
    monkeypatch.setenv("RAEM_LLM_PROVIDER", "kimi")
    monkeypatch.setenv("RAEM_LLM_MODEL", "kimi-k3")
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="No API key for kimi"):
        llm.make_llm()
    monkeypatch.setenv("KIMI_API_KEY", "k")   # either key name works; Moonshot's newer models take only temperature 1
    m = llm.make_llm()
    assert m.model_name == "kimi-k3" and m.temperature == 1 and "moonshot" in str(m.openai_api_base)
    monkeypatch.setenv("RAEM_LLM_PROVIDER", "openai")
    monkeypatch.setenv("RAEM_LLM_MODEL", "gpt-5-mini")
    assert llm.make_llm().temperature is None or llm.make_llm().temperature == 1   # reasoning models are not given 0
    monkeypatch.setenv("RAEM_LLM_PROVIDER", "nope")
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        llm.make_llm()
