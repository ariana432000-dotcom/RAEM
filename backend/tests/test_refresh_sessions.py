"""Session history rows (source, interrupted runs, signal fallback) and the start-up refresh that keeps every listed row."""
import importlib.util
import json
import sys

import pytest

from conftest import BACKEND

sys.path.insert(0, str(BACKEND / "app" / "pipeline"))
import sessions  # noqa: E402

DONE = "**Rating**: Buy\n\n**Executive Summary**: ..."


def nb_session(start, decision=DONE, signal=None):
    s = {"company": "ABBANK", "session_start": start, "trade_date": start[:10], "final_decision": decision,
         "steps": [{"step": "LLM Setup", "timestamp": start[11:], "input": "Ollama Qwen2.5 7B", "output": "LLM initialized"},
                   {"step": "Market Analyst", "timestamp": start[11:], "output": "report"}]}
    if signal:
        s["summary_table"] = {"final_signal": signal}
    return s


def test_session_row_labels_source_and_interrupted_runs():
    nb = sessions.session_row(nb_session("2026-11-18 22:52:20"), "SELL")
    assert nb == {"date": "2026-11-18", "start": "2026-11-18 22:52:20", "signal": "SELL", "rating": "Buy", "finished": True,
                  "source": "notebook", "model": "Ollama Qwen2.5 7B"}
    web = sessions.session_row({"company": "ABBANK", "session_start": "2026-11-09 10:00:00", "trade_date": "2026-11-09",
                                "final_decision": "**Rating**: Hold", "steps": [{"step": "Fundamentals Analyst"}]}, None)
    assert web["source"] == "website" and web["model"] is None and web["signal"] == "HOLD"      # signal from its own rating
    cut = sessions.session_row(nb_session("2026-11-18 22:41:58", decision=None), None)
    assert cut["finished"] is False and cut["rating"] is None and cut["signal"] is None       # nothing invented for it


def test_session_row_signal_prefers_csv_then_logged_signal():
    assert sessions.session_row(nb_session("2026-11-18 10:00:00", signal="HOLD"), "nan")["signal"] == "HOLD"
    assert sessions.session_row(nb_session("2026-11-18 10:00:00", signal="HOLD"), "BUY")["signal"] == "BUY"


@pytest.fixture()
def refresher(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("refresh_sessions", BACKEND / "tools" / "refresh_sessions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    nb = tmp_path / "notebooks"
    nb.mkdir()
    monkeypatch.setattr(mod, "NB", nb)
    monkeypatch.setenv("RAEM_SNAPSHOT", str(tmp_path / "no_snapshot.json.gz"))   # only this test's sessions, not the saved archive
    return mod, nb


def test_refresh_keeps_listed_rows_and_the_shown_website_entry(refresher):
    mod, nb = refresher
    logs = [nb_session("2026-11-18 22:41:58", decision=None), nb_session("2026-11-18 22:52:20")]
    (nb / "trading_log_ABBANK.json").write_text(json.dumps(logs), encoding="utf-8")
    old_row = {"date": "2026-11-09", "start": "2026-11-09 10:00:00", "signal": None, "rating": "Hold"}   # a website run
    shown = {"date": "2026-11-18", "start": "2026-11-18 22:52:20", "debate": {"complete": True}, "llm_model": "anthropic:x"}
    extra = {"ai": {"ABBANK": {"sessions": [old_row], "latest": shown}}}
    assert mod.refresh_ai(extra) == 1
    rows = extra["ai"]["ABBANK"]["sessions"]
    assert [r["start"] for r in rows] == ["2026-11-09 10:00:00", "2026-11-18 22:41:58", "2026-11-18 22:52:20"]
    assert rows[0]["signal"] == "HOLD" and rows[1]["finished"] is False and rows[2]["source"] == "notebook"
    assert extra["ai"]["ABBANK"]["latest"] is shown                       # the newest session is the one shown: kept as it is
    assert mod.refresh_ai(extra) == 0                                    # a second run changes nothing


def test_refresh_takes_a_newer_session_as_latest(refresher):
    mod, nb = refresher
    (nb / "trading_log_ABBANK.json").write_text(json.dumps([nb_session("2026-11-19 12:27:16")]), encoding="utf-8")
    extra = {"ai": {"ABBANK": {"sessions": [], "latest": {"date": "2026-11-09", "start": "2026-11-09 10:00:00"}}}}
    mod.refresh_ai(extra)
    assert extra["ai"]["ABBANK"]["latest"]["start"] == "2026-11-19 12:27:16"
