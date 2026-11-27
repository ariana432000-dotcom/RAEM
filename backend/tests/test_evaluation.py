"""Evaluation export: scoring rule, calls on closed days, merging of logs, archive and input files, fail-soft run,
the refresh step and the shipped evaluation.json. Offline, standard library only."""
import csv
import gzip
import json
from pathlib import Path

import pytest

from app.pipeline import export_evaluation as ev

BACKEND = Path(__file__).resolve().parents[1]
DAYS = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-11", "2026-10-12"]  # Mon, Tue, Wed, Thu, Sun, Mon


def window(closes: dict) -> str:
    return "# Stock data\n\nDate,Open,High,Low,Close,Volume\n" + "\n".join(f"{d},{c},{c},{c},{c},1" for d, c in closes.items())


def sess(company, date, sid, signal="HOLD", end=True, closes=None, steps=None):
    s = {"session_id": sid, "company": company, "trade_date": date, "session_start": f"{date} 10:00:00", "steps": steps or [{"step": "Market Analyst"}],
         "summary_table": {"final_signal": signal if end else None}, "final_decision": f"**Rating**: {signal.title()}"}
    if end:
        s["session_end"] = f"{date} 11:00:00"
    if closes:
        s["summary_table"]["market_stock_data"] = window(closes)
    return s


def score(calls, prices):
    return ev.score({k: {"signal": v, "filled": False, "src": "t"} for k, v in calls.items()}, prices, {})


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    """Empty log folder, archive, input folder, engine folder and output file for one test."""
    for name in ("logs", "inputs", "engine/data"):
        (tmp_path / name).mkdir(parents=True)
    monkeypatch.setattr(ev, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(ev, "ARCHIVE", str(tmp_path / "archive.json.gz"))
    monkeypatch.setattr(ev, "INPUTS", str(tmp_path / "inputs"))
    monkeypatch.setattr(ev, "ENGINE", str(tmp_path / "engine"))
    monkeypatch.setattr(ev, "OUT", str(tmp_path / "evaluation.json"))
    return tmp_path


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(header); w.writerows(rows)


# ------------------------------------------------------------------ scoring
def test_window_rule_for_buy_sell_and_hold():
    # entry 100, then 99, 101, 98: some close above (BUY), some below (SELL), HOLD right while some close is not below
    out = score({("X", DAYS[0]): "BUY", ("X", DAYS[1]): "SELL", ("X", DAYS[2]): "HOLD"},
                {"X": dict(zip(DAYS, [100, 99, 101, 98, 97, 96]))})
    rows = {r[0]: r for r in out["rows"]}
    assert out["scored"] == 3 and out["pending"] == 0
    # BUY on day 0: returns -1, +1, -2 -> right from the 2nd day on, wrong in a 1-day window
    b = out["companies"][0]["signals"]["BUY"]
    assert b["1"]["k"] == 0 and b["2"]["k"] == 1 and b["3"]["k"] == 1
    assert rows[DAYS[0]][4] == [-1.0, 1.0, -2.0]
    s = out["companies"][0]["signals"]["SELL"]          # SELL on day 1 (99): 101, 98, 97 -> right on day 2
    assert s["1"]["k"] == 0 and s["2"]["k"] == 1
    h = out["companies"][0]["signals"]["HOLD"]          # HOLD on day 2 (101): 98, 97, 96 all below -> wrong
    assert h["1"]["k"] == 0 and h["3"]["k"] == 0


def test_hold_is_right_when_a_close_equals_the_entry():
    out = score({("X", DAYS[0]): "HOLD"}, {"X": dict(zip(DAYS, [100, 95, 100, 94, 90, 90]))})
    assert out["overall"]["3"]["k"] == 1 and out["overall"]["1"]["k"] == 0


def test_returns_are_rounded_before_they_are_compared():
    # +0.004 % rounds to 0.00: a BUY is not right on a move that rounds to zero, a HOLD still is
    out = score({("X", DAYS[0]): "BUY", ("X", DAYS[1]): "HOLD"}, {"X": dict(zip(DAYS, [100, 100.004, 100.004, 100.004, 100.004, 100.004]))})
    sig = out["companies"][0]["signals"]
    assert sig["BUY"]["3"]["k"] == 0 and sig["HOLD"]["3"]["k"] == 1


def test_call_on_a_closed_day_keeps_its_own_date():
    prices = {"X": {"2026-10-09": 100.0, "2026-10-12": 101.0, "2026-10-13": 102.0, "2026-10-14": 103.0, "2026-10-15": 104.0}}
    out = score({("X", "2026-10-09"): "BUY", ("X", "2026-10-10"): "BUY"}, prices)      # Saturday: no bar
    by = {r[0]: r for r in out["rows"]}
    assert out["scored"] == 2 and out["carried"] == 1                                    # both count, the Saturday call is flagged
    assert by["2026-10-10"][3] == 100.0 and by["2026-10-10"][5] == 1 and by["2026-10-09"][5] == 0


def test_a_call_waits_until_three_later_closes_exist():
    out = score({("X", DAYS[0]): "HOLD", ("X", DAYS[3]): "HOLD"}, {"X": dict(zip(DAYS, [100, 101, 102, 103, 104, 105]))})
    assert out["scored"] == 1 and out["pending"] == 1
    assert out["rows"][1][7] == 0 and out["overall"]["3"]["n"] == 1
    out = score({("X", DAYS[3]): "HOLD"}, {})                                           # no prices at all
    assert out["scored"] == 0 and out["companies"][0]["w"]["3"]["acc"] is None


def test_interval_and_baselines():
    lo, hi = ev.wilson(190, 270)
    assert 64.5 < lo < 66 and 75 < hi < 76.5
    assert ev.wilson(0, 0) == (None, None)
    # always-HOLD baseline and the share of rises / falls
    out = score({("X", DAYS[0]): "BUY", ("X", DAYS[1]): "SELL"}, {"X": dict(zip(DAYS, [100, 90, 91, 92, 93, 94]))})
    # day 0 (100): later closes 90, 91, 92 are all below; day 1 (90): later closes 91, 92, 93 are all above
    assert out["overall"]["3"]["base"] == 50.0                 # HOLD is right on day 1 only
    assert out["signals"]["3"]["BUY"]["base"] == 50.0          # the price rose after one call of two
    assert out["signals"]["3"]["SELL"]["base"] == 50.0         # and fell after the other
    assert out["signals"]["3"]["HOLD"]["base"] == 50.0


# ------------------------------------------------------------------ one call per company and day
def test_one_call_per_day_finished_beats_unfinished_and_trader_fills_gaps():
    done = sess("A", "2026-10-05", "1", "SELL")
    early = sess("A", "2026-10-05", "2", "BUY", end=False, steps=[{"step": "Trader Decision", "output": "FINAL TRANSACTION PROPOSAL: **BUY**"}])
    stopped = sess("B", "2026-10-05", "3", end=False, steps=[{"step": "Trader Decision", "output": "...\nFINAL TRANSACTION PROPOSAL: **SELL**"}])
    nothing = sess("C", "2026-10-05", "4", end=False)
    for s in (done, early, stopped, nothing):
        s["_source"] = "logs"
    calls = ev.pick_calls([early, done, stopped, nothing])
    assert calls[("A", "2026-10-05")]["signal"] == "SELL" and not calls[("A", "2026-10-05")]["filled"]
    assert calls[("B", "2026-10-05")] == {"signal": "SELL", "filled": True, "src": "logs"}
    assert ("C", "2026-10-05") not in calls


def test_short_names_map_to_the_log_tickers():
    assert ev.ticker_of("ARAMCO") == "2222.SR" and ev.ticker_of("MIDEA") == "000333.SZ" and ev.ticker_of("AAPL") == "AAPL"


# ------------------------------------------------------------------ merging
def test_logs_archive_and_input_files_are_merged(dirs):
    closes = dict(zip(DAYS, [100, 101, 102, 103, 104, 105]))
    # trading log: a call on day 0; archive: a call on day 1 and a repeat of day 0 (same session id, not counted twice)
    (dirs / "logs" / "trading_log_A.json").write_text(json.dumps([sess("A", DAYS[0], "s1", "BUY", closes=closes)]), encoding="utf-8")
    arch = [{"company": "A", "trade_date": DAYS[1], "signal": "HOLD", "data": sess("A", DAYS[1], "s2", "HOLD", closes=closes)},
            {"company": "A", "trade_date": DAYS[0], "signal": "BUY", "data": sess("A", DAYS[0], "s1", "BUY")}]
    with gzip.open(dirs / "archive.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(arch, fh)
    # input files: a signal that replaces the log's call, one imputed and one unfinished row that must be ignored, an extra close
    write_csv(dirs / "inputs" / "signals.csv", ["trade_date", "ticker", "final_signal", "finalized", "filled", "imputed"],
              [[DAYS[0], "A", "SELL", "True", "False", "False"], [DAYS[2], "A", "BUY", "True", "False", "True"], [DAYS[3], "A", "BUY", "False", "False", "False"],
               [DAYS[2], "MIDEA", "HOLD", "True", "True", "False"]])
    write_csv(dirs / "inputs" / "closes.csv", ["ticker", "date", "close", "source"], [["A", "2026-10-13", 106, "test"], ["A", "2026-10-14", "bad", "test"]])
    write_csv(dirs / "inputs" / "notes.csv", ["a", "b"], [[1, 2]])
    out = ev.build({})
    keys = {(r[1], r[0]): r for r in out["rows"]}
    assert set(keys) == {("A", DAYS[0]), ("A", DAYS[1]), ("000333.SZ", DAYS[2])}
    assert keys[("A", DAYS[0])][2] == "SELL"                                   # the input file wins over the log
    assert keys[("000333.SZ", DAYS[2])][6] == 1                                # filled flag kept
    assert out["sources"]["sessions"] == {"logs": 1, "archive": 2}
    assert out["sources"]["signal_rows_from_files"] == 2 and out["sources"]["closes"]["input_files"] == 1
    assert any("notes.csv" in x for x in out["sources"]["skipped"])
    assert out["scored"] == 2                                                  # MIDEA has no prices


def test_engine_csv_extends_the_prices_only_when_it_agrees(dirs):
    base = dict(zip(DAYS[:3], [100, 101, 102]))
    (dirs / "logs" / "trading_log_A.json").write_text(json.dumps([sess("A", DAYS[0], "s1", "BUY", closes=base)]), encoding="utf-8")
    rows = [[d, c] for d, c in zip(DAYS, [100, 101, 102, 103, 104, 105])]
    write_csv(dirs / "engine" / "data" / "Name_A_2y.csv", ["Date", "Close"], [[d + " 00:00:00-04:00", c] for d, c in rows])
    out = ev.build({})
    assert out["sources"]["closes"]["engine_csv"] == 3 and out["scored"] == 1  # DAYS[3:] added: three later closes now exist
    write_csv(dirs / "engine" / "data" / "Name_A_2y.csv", ["Date", "Close"], [[d, c * 1.5] for d, c in rows])   # a different price basis
    out = ev.build({})
    assert out["sources"]["closes"]["engine_csv"] == 0 and out["scored"] == 0


def test_calls_before_the_start_date_are_left_out(dirs, monkeypatch):
    closes = dict(zip(DAYS, [100, 101, 102, 103, 104, 105]))
    (dirs / "logs" / "trading_log_A.json").write_text(json.dumps([sess("A", DAYS[0], "s1", "BUY", closes=closes),
                                                                  sess("A", DAYS[1], "s2", "SELL", closes=closes)]), encoding="utf-8")
    monkeypatch.setattr(ev, "FROM", DAYS[1])
    out = ev.build({})
    assert out["from"] == DAYS[1] and [r[0] for r in out["rows"]] == [DAYS[1]]
    monkeypatch.setattr(ev, "FROM", "")
    assert [r[0] for r in ev.build({})["rows"]] == [DAYS[0], DAYS[1]]


def test_buy_and_sell_alone():
    out = score({("X", DAYS[0]): "BUY", ("X", DAYS[1]): "HOLD", ("X", DAYS[2]): "SELL"}, {"X": dict(zip(DAYS, [100, 101, 102, 103, 104, 105]))})
    assert out["overall"]["3"]["n"] == 3 and out["bs"]["3"]["n"] == 2 and out["bs"]["3"]["k"] == 1      # BUY right, SELL wrong, HOLD left out
    assert out["companies"][0]["bs"]["3"] == out["bs"]["3"]


# ------------------------------------------------------------------ never stops a refresh
def test_a_failed_run_keeps_the_old_file_and_exits_zero(dirs, capsys):
    Path(ev.OUT).write_text("KEEP", encoding="utf-8")
    assert ev.main([]) == 0                                                    # nothing to evaluate
    assert Path(ev.OUT).read_text(encoding="utf-8") == "KEEP"
    assert "evaluation skipped" in capsys.readouterr().out
    (dirs / "logs" / "trading_log_A.json").write_text("{ not json", encoding="utf-8")
    assert ev.main([]) == 0 and Path(ev.OUT).read_text(encoding="utf-8") == "KEEP"


def test_main_writes_the_file_and_check_writes_nothing(dirs):
    closes = dict(zip(DAYS, [100, 101, 102, 103, 104, 105]))
    (dirs / "logs" / "trading_log_A.json").write_text(json.dumps([sess("A", DAYS[0], "s1", "BUY", closes=closes)]), encoding="utf-8")
    assert ev.main(["--check"]) == 0 and not Path(ev.OUT).exists()
    assert ev.main([]) == 0
    first = Path(ev.OUT).read_bytes()
    data = json.loads(first)
    assert data["scored"] == 1 and data["overall"]["3"]["k"] == 1 and b"\r" not in first
    assert ev.main([]) == 0 and Path(ev.OUT).read_bytes() == first             # same input, same bytes


# ------------------------------------------------------------------ pipeline and shipped data
def test_refresh_runs_the_evaluation_before_the_page_is_built():
    import refresh
    for kw in ({}, {"build_only": True}):
        labels = [l for l, _ in refresh.steps(**kw)]
        assert "Evaluation" in labels and labels.index("Evaluation") < labels.index("Merge page sources")
    assert "Evaluation" not in [l for l, _ in refresh.steps(evaluation=False)]
    assert [l for l, _ in refresh.steps(build_only=True, evaluation=False)] == ["Merge page sources", "Build page"]


def test_shipped_evaluation_json_is_consistent():
    p = BACKEND / "data" / "evaluation.json"
    if not p.exists():
        pytest.skip("evaluation.json not exported yet")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["calls"] == len(d["rows"]) == sum(c["calls"] for c in d["companies"])
    assert all(r[0] >= d["from"] for r in d["rows"])
    for w in map(str, d["windows"]):
        assert d["bs"][w]["n"] == sum(d["signals"][w][s]["n"] for s in ("BUY", "SELL"))
        assert d["bs"][w]["k"] == sum(d["signals"][w][s]["k"] for s in ("BUY", "SELL")) == sum(c["bs"][w]["k"] for c in d["companies"])
    for w in map(str, d["windows"]):
        o = d["overall"][w]
        assert o["n"] == d["scored"] == sum(c["w"][w]["n"] for c in d["companies"]) == sum(d["signals"][w][s]["n"] for s in ("BUY", "HOLD", "SELL"))
        assert o["k"] == sum(c["w"][w]["k"] for c in d["companies"]) == sum(d["signals"][w][s]["k"] for s in ("BUY", "HOLD", "SELL"))
        assert 0 <= o["lo"] <= o["acc"] <= o["hi"] <= 100
        assert d["trend"][w][-1][1:3] == [o["n"], o["k"]]
    # accuracy rises with the window: a longer window gives each call more chances
    assert d["overall"]["1"]["k"] <= d["overall"]["2"]["k"] <= d["overall"]["3"]["k"]
