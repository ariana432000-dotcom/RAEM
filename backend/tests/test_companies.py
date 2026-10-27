"""Adding any company from the search box: the registry, the API, add_company.py and the per-company extra export."""
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from conftest import BACKEND, wait_done

ENGINE = BACKEND / "engine"


@pytest.fixture()
def rc(tmp_path, monkeypatch):
    monkeypatch.setenv("RAEM_COMPANIES", str(tmp_path / "companies.json"))
    sys.path.insert(0, str(ENGINE))
    import raem_companies
    return raem_companies


def test_registry_normalize_and_presets(rc):
    assert rc.normalize(" tsla ") == "TSLA" and rc.normalize("7203.t") == "7203.T"
    for bad in ("", "a b", "x/y", "A" * 20):
        with pytest.raises(ValueError):
            rc.normalize(bad)
    m = rc.meta_for("7203.T", "international", "Toyota")
    assert (m["currency"], m["tz"], m["source"], m["group"]) == ("JPY", "Asia/Tokyo", "yfinance", "international")
    assert rc.meta_for("TSLA", "international")["currency"] == "USD"
    d = rc.meta_for("SQURPHARMA", "dse", "Square")
    assert (d["currency"], d["source"], d["weekend"]) == ("BDT", "dse", [4, 5])
    assert rc.file_slug("Tesla, Inc.") == "Tesla_Inc"


def test_registry_merges_without_replacing_builtins(rc):
    rc.add("TSLA", rc.meta_for("TSLA", "international", "Tesla"))
    rc.add("AAPL", rc.meta_for("AAPL", "international", "NOT APPLE"))
    tickers, fx = {"AAPL": {"name": "Apple"}}, {"USD": None}
    rc.add("7203.T", rc.meta_for("7203.T", "international", "Toyota"))
    assert sorted(rc.register(tickers, fx)) == ["7203.T", "TSLA"]
    assert tickers["AAPL"]["name"] == "Apple"                   # a research company is never replaced
    assert tickers["7203.T"]["weekend"] == (5, 6) and "group" not in tickers["TSLA"]
    assert fx["JPY"] == "USDJPY=X"


def test_predictor_lib_hook_is_present():
    src = (ENGINE / "predictor_lib.py").read_text(encoding="utf-8")
    assert "_raem_companies.register(TICKERS, FX_PAIRS)" in src
    # sync_engine puts the same hook back after copying the research project's predictor_lib.py
    assert "_raem_companies.register(TICKERS, FX_PAIRS)" in (BACKEND / "tools" / "sync_engine.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- API

def test_add_company_api(client, tmp_path, monkeypatch):
    monkeypatch.setenv("RAEM_COMPANIES", str(tmp_path / "companies.json"))
    from app.core import config
    monkeypatch.setattr(config, "tickers", lambda: ["AAPL", "IBM"])
    r = client.post("/api/companies", json={"ticker": " tsla ", "market": "international"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["run"]["kind"] == "company" and body["run"]["ticker"] == "TSLA" and body["run"]["params"]["market"] == "international"
    done = wait_done(client, body["run"]["id"])
    assert done["status"] == "done"
    again = client.post("/api/companies", json={"ticker": "TSLA", "market": "international"}).json()
    assert again["reused"] == "stored"
    assert client.post("/api/companies", json={"ticker": "AAPL"}).json() == {"run": None, "reused": "exists", "ticker": "AAPL"}
    assert client.post("/api/companies", json={"ticker": "a b"}).status_code == 400
    assert client.post("/api/companies", json={"ticker": "TSLA", "market": "mars"}).status_code == 400


def test_add_company_needs_the_token_when_set(client, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config, "RUN_TOKEN", "secret")
    assert client.post("/api/companies", json={"ticker": "TSLA"}).status_code == 401
    ok = client.post("/api/companies", json={"ticker": "TSLA"}, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 202


def test_companies_list_and_bundle(client, tmp_path, monkeypatch):
    monkeypatch.setenv("RAEM_COMPANIES", str(tmp_path / "companies.json"))
    from app.core import config
    sys.path.insert(0, str(ENGINE))
    import raem_companies as rc
    rc.add("TSLA", rc.meta_for("TSLA", "international", "Tesla"))
    assert client.get("/api/companies").json() == [{"ticker": "TSLA", "name": "Tesla", "group": "international", "currency": "USD", "ready": False}]
    b = client.get("/api/companies/AAPL").json()          # a saved company: forecast + the page's extra data
    assert b["ticker"] == "AAPL" and b["data"]["forecast"]["last_price"] and isinstance(b["dataset"], list) and "USD" in b["fx"]
    assert client.get("/api/companies/NOPE").status_code == 404


def test_search_lists_site_companies_first(client):
    r = client.get("/api/search", params={"q": "aap"}).json()
    assert r and r[0]["symbol"] == "AAPL" and r[0]["on_site"] is True
    assert client.get("/api/search", params={"q": ""}).json() == []


# ---------------------------------------------------------------- add_company.py

@pytest.fixture()
def ac(tmp_path, monkeypatch):
    """add_company.py loaded against a throw-away engine folder."""
    eng = tmp_path / "engine"
    (eng / "data").mkdir(parents=True)
    (eng / "notebooks").mkdir()
    (tmp_path / "companies.json").write_text("{}")
    monkeypatch.setenv("RAEM_ENGINE", str(eng))
    monkeypatch.setenv("RAEM_COMPANIES", str(tmp_path / "companies.json"))
    spec = importlib.util.spec_from_file_location("add_company_under_test", BACKEND / "app" / "pipeline" / "add_company.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.TICKERS_DIR = str(tmp_path / "tickers")
    os.makedirs(m.TICKERS_DIR)
    return m


def _yf_frame(n=300):
    import pandas as pd
    idx = pd.date_range("2025-01-01", periods=n, freq="B", tz="America/New_York", name="Date")
    return pd.DataFrame({"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.5, "Volume": 1000, "Dividends": 0.0, "Stock Splits": 0.0}, index=idx)


def _dse_frame(n=300):
    import pandas as pd
    return pd.DataFrame({"date": pd.date_range("2025-01-01", periods=n, freq="B").strftime("%Y-%m-%d"), "symbol": "SQURPHARMA", "ltp": 5.0,
                         "high": 5.2, "low": 4.8, "open": 5.0, "close": 5.1, "ycp": 5.0, "trade": 10, "value": 1.0, "volume": 5000})


def test_prices_saved_in_the_yfinance_layout(ac, monkeypatch):
    monkeypatch.setattr(ac, "fetch_international", lambda t: (_yf_frame(), {"name": "Tesla, Inc.", "currency": "USD", "tz": "America/New_York"}))
    market, info = ac.save_prices("TSLA", "auto", None)
    assert market == "international" and info["name"] == "Tesla, Inc."
    path = ac.csv_for("TSLA")
    assert os.path.basename(path) == "Tesla_Inc_TSLA_2y.csv"
    head = open(path).read().splitlines()
    assert head[0] == "Date,Open,High,Low,Close,Volume,Dividends,Stock Splits" and head[1].startswith("2025-01-01 00:00:00-05:00")
    # a second call keeps the file and does not ask the price feed again
    monkeypatch.setattr(ac, "fetch_international", lambda t: (_ for _ in ()).throw(AssertionError("fetched again")))
    assert ac.save_prices("TSLA", "auto", None)[0] == "international"


def test_auto_market_falls_back_to_dse(ac, monkeypatch):
    import pandas as pd
    monkeypatch.setattr(ac, "fetch_international", lambda t: (pd.DataFrame(), {}))
    monkeypatch.setattr(ac, "fetch_dse", lambda t: _dse_frame())
    market, _ = ac.save_prices("SQURPHARMA", "auto", "Square Pharmaceuticals")
    assert market == "dse"
    head = open(ac.csv_for("SQURPHARMA")).read().splitlines()[0]
    assert head == "date,symbol,ltp,high,low,open,close,ycp,trade,value,volume"


def test_too_little_history_is_refused(ac, monkeypatch, capsys):
    monkeypatch.setattr(ac, "fetch_international", lambda t: (_yf_frame(40), {}))
    with pytest.raises(SystemExit):
        ac.save_prices("NEWCO", "international", None)
    assert "No usable price history" in capsys.readouterr().out and ac.csv_for("NEWCO") is None


def test_main_runs_every_step_once_and_can_repeat(ac, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(ac, "fetch_international", lambda t: (_yf_frame(), {"name": "Tesla, Inc.", "currency": "USD", "tz": "America/New_York"}))
    calls = []
    monkeypatch.setattr(ac, "create_notebook", lambda t, m, n, s: calls.append(("notebook", t, m, n)))

    def fake_run(label, argv):
        calls.append(("script", os.path.basename(argv[3]), argv[4:]))
        if "export_ticker.py" in argv[3]:
            Path(ac.TICKERS_DIR, "TSLA.json").write_text("{}")
    monkeypatch.setattr(ac, "run_script", fake_run)
    assert ac.main(["--ticker", "tsla"]) == 0
    out = capsys.readouterr().out
    steps = [l for l in out.splitlines() if l.startswith("== ")]
    assert steps[0] == "== Prices for TSLA" and steps[-1] == "== TSLA added" and len(steps) == 6
    assert calls == [("notebook", "TSLA", "international", "Tesla, Inc."), ("script", "export_ticker.py", ["TSLA"]),
                     ("script", "export_extra.py", ["--only", "TSLA"])]
    reg = json.load(open(os.environ["RAEM_COMPANIES"]))
    assert reg["TSLA"]["name"] == "Tesla, Inc." and reg["TSLA"]["currency"] == "USD"
    assert ac.main(["--ticker", "TSLA"]) == 0 and "already on the site" in capsys.readouterr().out


def test_notebook_command_picks_the_regional_template(ac, monkeypatch):
    seen = []

    class R:
        returncode, stdout, stderr = 0, "Created x", ""
    monkeypatch.setattr(ac, "notebook_exists", lambda t: False)
    monkeypatch.setattr(ac.subprocess, "run", lambda argv, **kw: seen.append(argv) or R())
    ac.create_notebook("0700.HK", "international", "Tencent", None)
    ac.create_notebook("SQURPHARMA", "dse", "Square Pharmaceuticals", "Health Care")
    ac.create_notebook("TSLA", "international", "Tesla", None)
    assert seen[0][seen[0].index("--like") + 1] == "0300.HK"
    assert seen[1][seen[1].index("--group") + 1] == "dse" and seen[1][seen[1].index("--sector") + 1] == "Health Care"
    assert "--like" not in seen[2]


# ---------------------------------------------------------------- export_extra.py --only (stub engine, no models needed)

def test_export_extra_only_updates_one_company(tmp_path):
    root = tmp_path / "backend"
    (root / "app").mkdir(parents=True)
    for d in ("pipeline",):
        (root / "app" / d).mkdir()
    for f in ("export_extra.py", "export_ticker.py", "sessions.py"):
        (root / "app" / "pipeline" / f).write_text((BACKEND / "app" / "pipeline" / f).read_text(encoding="utf-8"), encoding="utf-8")
    eng = root / "engine"
    (eng / "notebooks").mkdir(parents=True)
    (eng / "forecast_log").mkdir()
    (root / "data").mkdir()
    (root / "data" / "extra.json").write_text(json.dumps({"ai": {"AAPL": {"x": 1}}, "dataset": {"AAPL": [["2026-01-01", 1, 1, 1, 1, 1, ""]]},
                                                         "backtest": {"AAPL": {}}, "memory": {"keep": 1}, "fx": {"USD": 1.0}, "asof": "2026-10-01"}))
    (eng / "predictor_lib.py").write_text(textwrap.dedent('''
        TICKERS = {"AAPL": {"currency": "USD"}, "TSLA": {"name": "Tesla", "currency": "JPY"}}
        def safe_filename(t): return t.replace(".", "_")
        def get_fx_rate_to_usd(c): return 150.0
    '''))
    (eng / "forecast_agent.py").write_text("")
    (eng / "forecast_lib.py").write_text(textwrap.dedent('''
        import pandas as pd
        def load_combined_history(t, sync=True):
            return pd.DataFrame({"Open": [1.0, 2.0], "High": [1.5, 2.5], "Low": [.5, 1.5], "Close": [1.2, 2.2], "Volume": [10, 20]},
                                index=pd.to_datetime(["2026-10-01", "2026-10-02"]))
    '''))
    r = subprocess.run([sys.executable, "-I", str(root / "app" / "pipeline" / "export_extra.py"), "--only", "TSLA"],
                       capture_output=True, text=True, env=dict(os.environ, RAEM_ENGINE=str(eng), PYTHONPATH=""), timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads((root / "data" / "extra.json").read_text())
    assert got["dataset"]["TSLA"][1][0] == "2026-10-02" and got["dataset"]["TSLA"][1][4] == 2.2
    assert got["backtest"]["TSLA"] == {}                       # no forecast log yet: empty, not a crash
    assert got["fx"]["JPY"] == 150.0 and got["fx"]["USD"] == 1.0
    assert got["memory"] == {"keep": 1} and got["ai"] == {"AAPL": {"x": 1}} and got["dataset"]["AAPL"]   # the other company is untouched


# ---------------------------------------------------------------- AI analysis of any company (no hand-made notebook file)

def test_analysis_accepts_a_company_without_a_notebook(client):
    from app.db import runs as db
    r = client.post("/api/analysis", json={"ticker": "tsla", "llm_model": "qwen2.5:7b", "market": "international", "name": "Tesla"})
    assert r.status_code == 202, r.text
    run = r.json()["run"]
    assert run["ticker"] == "TSLA"
    p = db.get(run["id"], with_result=False)["params"]
    assert p["notebook"] is None and p["market"] == "international" and p["name"] == "Tesla"
    wait_done(client, run["id"])
    # a company that has a notebook keeps working as before
    p2 = db.get(client.post("/api/analysis", json={"ticker": "aapl", "llm_model": "qwen2.5:7b"}).json()["run"]["id"], with_result=False)["params"]
    assert p2["notebook"] == "AAPL.ipynb"


def test_runner_gets_market_and_name_only_without_a_notebook(monkeypatch, tmp_path):
    from app.workers import jobs
    seen = []
    monkeypatch.setattr(jobs, "_stream", lambda rid, argv, timeout, env=None: seen.append(argv) or False)
    for nb in (None, "AAPL.ipynb"):
        run = {"id": "x", "ticker": "TSLA", "model_key": "qwen2.5:7b",
               "params": {"provider": "ollama", "llm_model": "qwen2.5:7b", "notebook": nb, "market": "dse", "name": "Tesla"}}
        with pytest.raises(RuntimeError):
            jobs._run_analysis(run)
    assert seen[0][-4:] == ["--market", "dse", "--name", "Tesla"] and "--market" not in seen[1]


def _runner():
    spec = importlib.util.spec_from_file_location("run_notebook_under_test", BACKEND / "app" / "ai_engine" / "run_notebook.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_prepare_notebook_makes_a_missing_notebook_once(monkeypatch, capsys):
    rn = _runner()
    have = {}
    monkeypatch.setattr(rn, "notebook_map", lambda d: dict(have))
    calls = []

    class P:
        def __init__(self, argv, **kw):
            calls.append(argv)
            have["TSLA"] = "TSLA.ipynb"
            self.stdout = iter(["== Prices for TSLA\n", "== TSLA is ready for the AI analysis\n"])

        def wait(self):
            return 0
    monkeypatch.setattr(rn.subprocess, "Popen", P)
    assert rn.prepare_notebook("TSLA", "international", "Tesla") == "TSLA.ipynb"
    a = calls[0]
    assert a[a.index("--ticker") + 1] == "TSLA" and "--prepare" in a and a[a.index("--market") + 1] == "international" and a[a.index("--name") + 1] == "Tesla"
    assert "== Prices for TSLA" in capsys.readouterr().out
    assert rn.prepare_notebook("TSLA") == "TSLA.ipynb" and len(calls) == 1      # there now: nothing is made again


def test_prepare_notebook_reports_failure(monkeypatch):
    rn = _runner()
    monkeypatch.setattr(rn, "notebook_map", lambda d: {})

    class P:
        def __init__(self, argv, **kw):
            self.stdout = iter(["!! No usable price history for NOPE.\n"])

        def wait(self):
            return 1
    monkeypatch.setattr(rn.subprocess, "Popen", P)
    assert rn.prepare_notebook("NOPE") is None


class _Node(dict):
    __getattr__ = dict.__getitem__


def test_failure_cause_names_the_step_and_the_reason(monkeypatch):
    rn = _runner()
    nb = _Node(cells=[
        _Node(cell_type="code", source="log_step('Market analyst')", outputs=[]),
        _Node(cell_type="code", source="log_step('Trader')", outputs=[{"output_type": "error", "ename": "NotFoundError"}]),
    ])
    nb.cells = nb["cells"]
    err = "An error occurred while executing the following cell:\n...\n\x1b[0;31mNotFoundError\x1b[0m: Error code: 404 - {'error': {'message': 'Not found the model kimi-k2-0905-preview'}}\n"
    monkeypatch.setenv("RAEM_LLM_PROVIDER", "kimi")
    lines = rn.failure_cause(nb, err)
    assert lines[0].startswith("!! Cause: NotFoundError: Error code: 404") and "(in step: Trader)" in lines[0] and "\x1b" not in lines[0]
    assert "RAEM_KIMI_MODELS" in lines[1]
    keyed = rn.failure_cause(nb, "AuthenticationError: Error code: 401 - invalid api key")
    assert "API key" in keyed[1]
    assert len(rn.failure_cause(nb, "ZeroDivisionError: division by zero")) == 1
    # a traceback that only quotes code mentioning a model, and a missing package, are not a refused model
    assert len(rn.failure_cause(nb, "---> 82 ChatOllama(model=m)  # unknown model\n\nModuleNotFoundError: No module named 'langchain_ollama'")) == 1


def test_add_company_prepare_stops_before_the_forecast(ac, monkeypatch, capsys):
    monkeypatch.setattr(ac, "fetch_international", lambda t: (_yf_frame(), {"name": "Tesla, Inc.", "currency": "USD", "tz": "America/New_York"}))
    made = []
    monkeypatch.setattr(ac, "create_notebook", lambda t, m, n, s: made.append(t))
    monkeypatch.setattr(ac, "run_script", lambda *a: (_ for _ in ()).throw(AssertionError("no forecast in --prepare")))
    assert ac.main(["--ticker", "TSLA", "--prepare"]) == 0
    out = capsys.readouterr().out
    assert made == ["TSLA"] and out.strip().splitlines()[-1] == "== TSLA is ready for the AI analysis"
    Path(ac.TICKERS_DIR, "TSLA.json").write_text("{}")             # on the site already: --prepare still checks the notebook
    assert ac.main(["--ticker", "TSLA", "--prepare"]) == 0 and made == ["TSLA", "TSLA"]
