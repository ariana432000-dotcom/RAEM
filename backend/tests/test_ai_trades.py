"""export_ai_trades: episodes read from a ChromaDB sqlite file become per-ticker call lists."""
import importlib.util
import os
import sqlite3

SPEC = importlib.util.spec_from_file_location("export_ai_trades", os.path.join(os.path.dirname(__file__), "..", "app", "pipeline", "export_ai_trades.py"))
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def make_db(path, episodes):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE collections (id TEXT, name TEXT);
        CREATE TABLE segments (id TEXT, collection TEXT, scope TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER, float_value REAL);
    """)
    con.execute("INSERT INTO collections VALUES ('c1', 'raem_episodic_memory'), ('c2', 'raem_news_corpus')")
    con.execute("INSERT INTO segments VALUES ('s1', 'c1', 'METADATA'), ('s2', 'c2', 'METADATA')")
    for i, ep in enumerate(episodes, 1):
        con.execute("INSERT INTO embeddings VALUES (?, ?)", (i, ep.pop("_seg", "s1")))
        for k, v in ep.items():
            con.execute("INSERT INTO embedding_metadata VALUES (?, ?, ?, NULL, NULL)", (i, k, str(v)))
    con.commit()
    con.close()


def ep(company, day, sig, p0, p1, pnl, status="RESOLVED", hours="58.3", session=None, **kw):
    return {"company": company, "trade_date": day, "final_signal": sig, "entry_price": p0, "exit_price": p1, "pnl_pct": pnl,
            "outcome_status": status, "hours_held": hours, "session_id": session or f"{day}_13-00-00", **kw}


def test_resolved_calls_become_trades(tmp_path):
    db = str(tmp_path / "chroma.sqlite3")
    make_db(db, [
        ep("BPML", "2026-09-28", "SELL", "27.4", "26.1", "4.74"),
        ep("AAPL", "2026-09-28", "HOLD", "341.07", "329.4", "0.00"),
        ep("AAPL", "2026-09-29", "BUY", "338.4", "340.0", "0.47"),
        ep("AAPL", "2026-10-02", "BUY", "340.0", "", "0", status="PENDING"),
        ep("AAPL", "2026-09-28", "news", "1", "1", "0", _seg="s2"),
    ])
    out = mod.build(mod.read_episodes(db))
    assert out["trades"]["BPML"] == [["2026-09-28", "2026-09-30", -1, 27.4, 26.1, 4.74]]   # 13:00 + 58.3 h = Sep 30
    assert [t[2] for t in out["trades"]["AAPL"]] == [0, 1]                                   # HOLD = 0, BUY = 1; pending and news left out
    assert out["pending"] == {"AAPL": 1}


def test_newest_session_wins_for_a_day_and_aliases_apply(tmp_path):
    db = str(tmp_path / "chroma.sqlite3")
    make_db(db, [
        ep("ARAMCO", "2026-09-28", "HOLD", "25", "25", "0", session="2026-09-28_10-00-00"),
        ep("2222.SR", "2026-09-28", "BUY", "25", "26", "4.0", session="2026-09-28_15-00-00"),
        ep("000333.SZ", "2026-09-29", "HOLD", "80", "80", "0"),
    ])
    out = mod.build(mod.read_episodes(db))
    assert out["trades"]["2222.SR"] == [["2026-09-28", "2026-10-01", 1, 25.0, 26.0, 4.0]]
    assert "0300.HK" not in out["trades"] and "000333.SZ" in out["trades"]                   # Midea is not merged into 0300.HK


def test_a_missing_file_keeps_the_old_output(tmp_path, monkeypatch):
    out = tmp_path / "ai_trades.json"
    out.write_text("old")
    monkeypatch.setattr(mod, "DB", str(tmp_path / "missing.sqlite3"))
    monkeypatch.setattr(mod, "OUT", str(out))
    assert mod.main([]) == 0 and out.read_text() == "old"
