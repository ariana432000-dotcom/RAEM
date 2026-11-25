"""Single vs multi agent export (Evaluation page, second tab): scoring rule, same-dates rule, merged vs all data, the HOLD rule,
3- and 5-day windows, waiting dates, fail-soft run, the input subfolder and the shipped agent_comparison.json. Offline, standard library only."""
import json
from pathlib import Path

import pytest

from app.pipeline import export_agent_comparison as ac
from app.pipeline import export_evaluation as ev

BACKEND = Path(__file__).resolve().parents[1]
DAYS = ["2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]


def put(path, header, rows):
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


@pytest.fixture()
def box(tmp_path, monkeypatch):
    (tmp_path / "ac").mkdir()
    monkeypatch.setattr(ac, "AC_DIR", str(tmp_path / "ac"))
    monkeypatch.setattr(ac, "MERGED", str(tmp_path / "merged.csv"))
    monkeypatch.setattr(ac, "OUT", str(tmp_path / "out.json"))
    monkeypatch.setattr(ac, "HOLD_RULE", "first2")
    return tmp_path


def closes(box, ticker, values):
    put(box / "ac" / "closes.csv", "ticker,date,close", [f"{ticker},{d},{c}" for d, c in zip(DAYS, values)])


def test_call_right_first2_and_any3(monkeypatch):
    monkeypatch.setattr(ac, "HOLD_RULE", "first2")
    assert ac.call_right("HOLD", [-1.0, 2.0, -3.0], 3)            # day 2 is up: HOLD is right
    assert ac.call_right("HOLD", [-1.0, 0.0, -3.0], 3)            # day 2 is level, not below the entry
    assert not ac.call_right("HOLD", [-1.0, -2.0, 5.0], 3)        # both first closes below the entry: wrong, day 3 is not looked at
    assert not ac.call_right("HOLD", [-1.0, -2.0, 5.0], 5)        # the same in the 5-day window
    monkeypatch.setattr(ac, "HOLD_RULE", "any3")
    assert ac.call_right("HOLD", [-1.0, -2.0, 5.0], 3)            # the old rule: some close at or above the entry
    assert ac.call_right("BUY", [-1.0, 0.5, -1.0], 3) and not ac.call_right("BUY", [-1.0, -0.5, -1.0], 3)
    assert ac.call_right("SELL", [1.0, -0.01, 1.0], 3) and not ac.call_right("SELL", [1.0, 0.0, 1.0], 3)


def test_exact_p():
    assert ac.exact_p(0, 0) == 1.0
    assert ac.exact_p(5, 5) == 1.0
    assert ac.exact_p(5, 2) == pytest.approx(0.4531, abs=1e-4)
    assert ac.exact_p(0, 10) == pytest.approx(2 / 1024)


def test_same_dates_merged_and_all(box):
    closes(box, "AAA", [10, 11, 12, 11, 10, 9, 10, 11, 12, 13])
    put(box / "ac" / "single_agent_signals.csv", "ticker,trade_date,signal",
        ["AAA,2026-10-04,HOLD", "AAA,2026-10-05,HOLD", "AAA,2026-10-06,HOLD", "AAA,2026-10-07,HOLD", "AAA,2026-10-08,HOLD", "AAA,2026-10-20,HOLD"])
    put(box / "ac" / "multi_replay_signals.csv", "ticker,trade_date,signal", ["AAA,2026-10-04,BUY", "AAA,2026-10-05,SELL", "AAA,2026-10-30,BUY"])
    put(box / "merged.csv", "trade_date,ticker,final_signal,finalized,filled",
        ["2026-10-06,AAA,SELL,True,False", "2026-10-07,AAA,BUY,False,False", "2026-10-08,AAA,BUY,True,True", "2026-10-04,AAA,SELL,True,False",
         "2026-10-05,BBB,BUY,True,False"])       # BBB: the single agent has no calls for it; 10-07 is not finalized; 10-04 stays the replay's call
    d = ac.build()
    (co,) = d["companies"]
    assert co["ticker"] == "AAA"
    assert [r[0] for r in co["rows"]] == ["2026-10-04", "2026-10-05", "2026-10-06", "2026-10-08"]      # 10-07: unfinalized; 10-20 and 10-30: only one agent
    assert {r[0]: r[1] for r in co["rows"]} == {"2026-10-04": "replay", "2026-10-05": "replay", "2026-10-06": "merged", "2026-10-08": "merged"}
    assert co["n"] == {"all": 4, "merged": 2, "replay": 2}
    assert co["rows"][0][4] == "BUY"                                                                   # replay wins on the same date
    assert co["left_out"]["multi_only"] == 1 and co["left_out"]["single_only"] == 2   # 10-30 only the multi agent; 10-07 (unfinalized) and 10-20 only the single agent
    for w in ("3", "5"):
        assert co["overall"][w]["all"]["n"] == 4 and co["overall"][w]["merged"]["n"] == 2
        assert co["overall"][w]["merged"]["multi"]["k"] <= 2


def test_windows_use_the_same_dates_and_waiting(box):
    closes(box, "AAA", [10, 11, 12, 11, 10, 9, 10, 11, 12, 13])
    put(box / "ac" / "single_agent_signals.csv", "ticker,trade_date,signal", [f"AAA,{d},HOLD" for d in DAYS])
    put(box / "ac" / "multi_replay_signals.csv", "ticker,trade_date,signal", [f"AAA,{d},BUY" for d in DAYS])
    co = ac.build()["companies"][0]
    assert len(co["rows"]) == 5 and co["left_out"]["waiting"] == DAYS[5:]       # needs five later closes, so the last five dates wait
    assert co["overall"]["3"]["all"]["n"] == co["overall"]["5"]["all"]["n"] == 5
    row = co["rows"][0]
    assert row[2] == 10 and row[3] == [11, 12, 11, 10, 9]
    assert row[6] == 1 and row[8] == 1                  # BUY right: the next close is above the entry
    last = co["rows"][-1]
    assert len(last[3]) == 5


def test_by_signal_and_baselines(box):
    closes(box, "AAA", [10, 9, 8, 9, 10, 11, 12, 11, 10, 9])
    put(box / "ac" / "single_agent_signals.csv", "ticker,trade_date,signal", [f"AAA,{d},HOLD" for d in DAYS[:5]])
    put(box / "ac" / "multi_replay_signals.csv", "ticker,trade_date,signal", [f"AAA,{d},{s}" for d, s in zip(DAYS[:5], ["SELL", "SELL", "BUY", "HOLD", "BUY"])])
    co = ac.build()["companies"][0]
    sg = co["signals"]["3"]["all"]
    assert sg["single"]["HOLD"]["n"] == 5 and sg["single"]["BUY"]["n"] == 0
    assert sg["multi"]["SELL"]["n"] == 2 and sg["multi"]["BUY"]["n"] == 2 and sg["multi"]["HOLD"]["n"] == 1
    assert sg["every"]["HOLD"]["k"] == co["overall"]["3"]["all"]["hold"]["k"]
    assert sg["bs"]["multi"]["n"] == 4


def test_missing_and_broken_inputs_fail_soft(box):
    assert ac.build()["companies"] == []
    (box / "ac" / "single_x.csv").write_bytes(b"\xff\xfe\x00bad")
    d = ac.build()
    assert d["companies"] == []
    assert ac.main([]) == 0 and not Path(ac.OUT).exists()       # nothing to compare: no file written, never a failed refresh


def test_main_keeps_the_old_file_on_error(box, monkeypatch):
    Path(ac.OUT).write_text('{"keep": 1}', encoding="utf-8")
    monkeypatch.setattr(ac, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ac.main([]) == 0
    assert json.loads(Path(ac.OUT).read_text(encoding="utf-8")) == {"keep": 1}


def test_subfolder_is_not_read_as_ai_calls():
    inputs = BACKEND / "data" / "evaluation_inputs"
    assert (inputs / "agent_comparison").is_dir()
    names = [p.name for p in inputs.glob("*.csv")]
    assert not any(n.startswith(("multi_", "single_")) for n in names)       # export_evaluation's glob is not recursive


def test_refresh_step_follows_evaluation():
    import refresh
    labels = [s[0] for s in refresh.steps(build_only=True)]
    assert labels.index("Agent comparison") == labels.index("Evaluation") + 1
    assert "Agent comparison" not in [s[0] for s in refresh.steps(build_only=True, evaluation=False)]


def test_shipped_json_is_consistent():
    p = BACKEND / "data" / "agent_comparison.json"
    if not p.is_file():
        pytest.skip("agent_comparison.json not exported yet")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["windows"] == [3, 5] and d["companies"]
    for c in d["companies"]:
        for w in ("3", "5"):
            for g, o in c["overall"][w].items():
                n = c["n"][g]
                assert o["n"] == n and o["multi"]["n"] == n and o["single"]["n"] == n
                assert o["only_multi"] + o["only_single"] <= n
                assert o["multi"]["k"] - o["single"]["k"] == o["only_multi"] - o["only_single"]
        assert len(c["rows"]) == c["n"]["all"]
        for r in c["rows"]:
            assert len(r[3]) == 5 and r[4] in ev.SIGNALS and r[5] in ev.SIGNALS
        m = sum(r[6] for r in c["rows"] if r[1] == "merged")
        if "merged" in c["overall"]["3"]:
            assert m == c["overall"]["3"]["merged"]["multi"]["k"]
