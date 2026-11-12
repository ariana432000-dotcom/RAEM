"""Analyst cache, Fact Guard, Graph Analyst, peer episodes, news graph and graph memory (ported from the BATBC v10 notebook).

Offline: no network, no real model. Each group skips itself where its packages are missing (the slim CI job has only the API
packages); the pure Fact Guard and analyst cache tests need nothing beyond the standard library.
"""
import json
import os
import types

import pytest

from app.ai_pipeline import analyst_cache, config, fact_guard as fg
from app.ai_pipeline import debate_data as dd


# ───────────────────────────── analyst cache ─────────────────────────────
class Llm:
    model, temperature, num_ctx, num_predict = "m1", 0, 4096, 2048


def counting_invoke(text="analyst text"):
    calls = []

    def invoke(messages):
        calls.append(messages)
        return types.SimpleNamespace(content=text)
    return invoke, calls


@pytest.fixture()
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAEM_ANALYST_CACHE_FILE", str(tmp_path / "cache.json"))
    monkeypatch.delenv("RAEM_ANALYST_CACHE", raising=False)
    analyst_cache.reset_stats()
    return tmp_path


def test_analyst_cache_answers_an_identical_call_from_disk(cache_env):
    invoke, calls = counting_invoke()
    msgs = [{"role": "user", "content": "report on AAA with data X"}]
    a = analyst_cache.cached_invoke("market", Llm(), msgs, invoke, "AAA", "2026-11-12", log=lambda *_: None)
    b = analyst_cache.cached_invoke("market", Llm(), msgs, invoke, "AAA", "2026-11-12", log=lambda *_: None)
    assert a.content == b.content == "analyst text" and len(calls) == 1
    assert analyst_cache.STATS == {"hits": 1, "misses": 1}
    assert len(json.load(open(cache_env / "cache.json"))["entries"]) == 1


def test_analyst_cache_key_covers_prompt_date_company_model_and_agent(cache_env):
    invoke, calls = counting_invoke()
    base = [{"role": "user", "content": "data X"}]
    run = lambda name="market", msgs=base, co="AAA", day="2026-11-12", llm=None: analyst_cache.cached_invoke(  # noqa: E731
        name, llm or Llm(), msgs, invoke, co, day, log=lambda *_: None)
    run()
    run(msgs=[{"role": "user", "content": "data Y"}])        # the data changed
    run(day="2026-11-13")
    run(co="BBB")
    run(name="news")
    other = Llm()
    other.model = "m2"
    run(llm=other)
    assert len(calls) == 6 and analyst_cache.STATS["hits"] == 0


def test_analyst_cache_skips_a_model_that_is_not_repeatable_and_the_off_switch(cache_env, monkeypatch):
    invoke, calls = counting_invoke()
    warm = types.SimpleNamespace(model="c", temperature=0.7)
    none = types.SimpleNamespace(model="c", temperature=None)     # newer Claude models take no temperature
    for llm in (warm, warm, none, none):
        analyst_cache.cached_invoke("market", llm, ["x"], invoke, "AAA", "d", log=lambda *_: None)
    assert len(calls) == 4 and not (cache_env / "cache.json").exists()
    monkeypatch.setenv("RAEM_ANALYST_CACHE", "0")
    for _ in range(2):
        analyst_cache.cached_invoke("market", Llm(), ["x"], invoke, "AAA", "d", log=lambda *_: None)
    assert len(calls) == 6


def test_analyst_cache_does_not_keep_error_text_and_caps_its_size(cache_env, monkeypatch):
    invoke, calls = counting_invoke("Error: the tool failed")
    for _ in range(2):
        analyst_cache.cached_invoke("market", Llm(), ["x"], invoke, "AAA", "d", log=lambda *_: None)
    assert len(calls) == 2
    monkeypatch.setattr(analyst_cache, "MAX_ENTRIES", 3)
    ok, _ = counting_invoke("fine")
    for i in range(6):
        analyst_cache.cached_invoke("market", Llm(), [f"prompt {i}"], ok, "AAA", "d", log=lambda *_: None)
    assert len(json.load(open(cache_env / "cache.json"))["entries"]) == 3


# ───────────────────────────── Fact Guard ─────────────────────────────
PROMPT = """HARD DATA: rsi=28.5000, macd=-1.2500, close_50_sma=100.0000
FACT SHEET (trend facts, fundamentals and news items computed in code -- authoritative):
[F1] last close 90.00 is BELOW the 50-day SMA 100.00 (-10.0%): the SMA is overhead RESISTANCE, NOT support; a long stop-loss cannot sit at the SMA
[F2] RSI 28.5 = oversold (RSI <= 30)
[F3] EPS (latest disclosed period, NOT trailing-12-month): 5.1
[F4] cash dividend trend (code-computed from the dividend history): latest 30% (2025) vs 300% (2024): FELL 90%
[F5] vs sector peers (20d): own -3.00% vs +4.00%: UNDERPERFORMS by 7.00 pts [20-day window only; says nothing about longer horizons]
[N1] (company notice) Board meeting announced for the results date
"""
FILLER = " The overall picture rests on the figures in the fact sheet and on the news items listed above [F1]."


@pytest.fixture(autouse=True)
def fresh_guard():
    fg.reset()


def msgs_of(issues):
    return " | ".join(i["msg"] for i in issues)


def test_grounded_text_is_not_flagged():
    text = "The last close of 90.00 sits below the 50-day SMA of 100.00 [F1], and RSI 28.5 is oversold [F2]. EPS was 5.1 [F3]." + FILLER
    assert fg.guard_check(PROMPT, text) == []


def test_an_invented_number_is_flagged_and_its_sentence_cut():
    text = "Revenue grew 47.3% in the last quarter [F3]. The stock is oversold at RSI 28.5 [F2]." + FILLER + " It needs close watching."
    issues = fg.guard_check(PROMPT, text)
    assert "47.3%" in msgs_of(issues)
    new, n = fg.guard_repair(text, issues)
    assert "47.3" not in new and "oversold" in new and n == 1
    assert fg.guard_check(PROMPT, new) == []


def test_sma_support_is_corrected_to_resistance_not_cut():
    text = "The 50-day SMA at 100.00 acts as strong support for the price [F1]. Momentum is weak and RSI 28.5 is oversold [F2]." + FILLER
    issues = fg.guard_check(PROMPT, text)
    assert "RESISTANCE" in msgs_of(issues)
    new, _ = fg.guard_repair(text, issues)
    assert "acts as strong resistance" in new and "Momentum is weak" in new


def test_rsi_zone_label_is_corrected_and_the_word_small_is_not_an_sma():
    text = "RSI at 28.5 is in neutral territory, so there is no edge [F2]. The small cap status is not a factor here." + FILLER
    issues = fg.guard_check(PROMPT, text)
    new, _ = fg.guard_repair(text, issues)
    assert "oversold" in new and "neutral territory" not in new
    assert not [i for i in issues if "SMA" in i["msg"]]


def test_a_flipped_dividend_trend_and_a_flipped_peer_comparison_are_cut():
    text = ("The cash dividend increased sharply compared with last year [F4]. The stock outperforms its sector peers over the last month [F5]. "
            "RSI 28.5 is oversold [F2]." + FILLER * 3)
    issues = fg.guard_check(PROMPT, text)
    joined = msgs_of(issues)
    assert "Dividend trend" in joined and "Peer comparison" in joined
    new, _ = fg.guard_repair(text, issues)
    assert "increased sharply" not in new and "outperforms" not in new and "oversold" in new


def test_the_relation_and_memory_claim_checks_have_switches(monkeypatch):
    text = "The cash dividend increased sharply compared with last year [F4]. RSI 28.5 is oversold [F2]." + FILLER
    assert fg.guard_check(PROMPT, text)
    monkeypatch.setenv("RAEM_GUARD_RELATIONS", "0")
    assert not fg.guard_check(PROMPT, text)
    mem = "Similar past episodes were profitable, so a buy has worked well before. RSI 28.5 is oversold [F2]." + FILLER
    assert "Memory claim" in msgs_of(fg.guard_check(PROMPT, mem))
    monkeypatch.setenv("RAEM_GUARD_MEMORY_CLAIMS", "0")
    assert "Memory claim" not in msgs_of(fg.guard_check(PROMPT, mem))


def test_an_unknown_citation_and_an_uncited_event_claim_are_flagged():
    text = ("Support is thin [F9]. Management unveiled a surprising takeover bid, acquiring a Malaysian competitor, according to rumours circulating yesterday. "
            "RSI 28.5 is oversold [F2]." + FILLER)
    joined = msgs_of(fg.guard_check(PROMPT, text))
    assert "[F9] does not exist" in joined and "no [F#]/[N#]/[W#] citation" in joined


def test_a_notice_in_the_fact_sheet_may_be_discussed():
    text = "A board meeting was announced for the results date [N1]. RSI 28.5 is oversold [F2]." + FILLER
    assert fg.guard_check(PROMPT, text) == []


DSE_PROMPT = PROMPT + "[RULES] DSE retail investors are LONG-ONLY: SELL = exit an existing holding (no shorting). All prices are BDT, never '$'.\n"


def test_dse_rules_taka_long_only_and_a_stop_loss_on_a_sell():
    text = ("The stock trades near $ 90 after the drop [F1]. A short position could profit from more weakness here. "
            "FINAL TRANSACTION PROPOSAL: **SELL** with a stop-loss placed just above resistance. RSI 28.5 is oversold [F2]." + FILLER)
    joined = msgs_of(fg.guard_check(DSE_PROMPT, text))
    assert "never '$'" in joined and "long-only" in joined and "no stop-loss" in joined
    assert not fg.guard_check(PROMPT, "The stock trades near 90.00 [F1]. RSI 28.5 is oversold [F2]." + FILLER)   # not a DSE prompt: no market rules


def test_sizing_checks_are_off_unless_asked(monkeypatch):
    text = "Allocate 5.5% of the portfolio to this idea given the setup. RSI 28.5 is oversold [F2]." + FILLER
    assert not [i for i in fg.guard_check(DSE_PROMPT, text) if "Position size" in i["msg"]]
    monkeypatch.setenv("RAEM_GUARD_SIZING", "1")
    assert [i for i in fg.guard_check(DSE_PROMPT, "Position sizing of 7.7% is advised for this trade here. RSI 28.5 is oversold [F2]." + FILLER)
            if "Position size" in i["msg"]]


def test_structured_output_is_fixed_field_by_field():
    obj = {"rating": "Sell", "reasoning": "Revenue grew 47.3% last quarter [F3]. RSI 28.5 is oversold [F2]." + FILLER, "stop_loss": 95.0, "price_target": None}
    out, msgs, left = fg._g_json_fix(DSE_PROMPT, json.dumps(obj))
    fixed = json.loads(out)
    assert "47.3" not in fixed["reasoning"] and msgs
    assert fixed["stop_loss"] is None          # a SELL is an exit for a long-only investor


def ai(text):
    pytest.importorskip("langchain_core")
    from langchain_core.messages import AIMessage
    return AIMessage(content=text)


def test_guard_output_cuts_in_code_and_logs(capsys):
    llm = types.SimpleNamespace(format=None)
    bad = "Revenue grew 47.3% in the last quarter [F3]. The stock is oversold at RSI 28.5 [F2]." + FILLER + " It needs close watching."
    out = fg.guard_output(llm, [{"role": "user", "content": PROMPT}], ai(bad), reask=lambda m: pytest.fail("a clean cut needs no re-ask"), who="create_x")
    assert "47.3" not in out.content and "oversold" in out.content
    assert fg.GUARD_STATS == {"checked": 1, "flagged": 1, "repaired": 1, "retried": 0, "fixed": 1}
    assert "FactGuard [create_x]" in capsys.readouterr().out and fg.summary()["unresolved"] == []


def test_guard_output_asks_the_model_again_only_when_a_cut_is_not_possible():
    llm = types.SimpleNamespace(format=None)
    bad = "Revenue grew 47.3% in the last quarter."                    # the whole answer is the bad sentence: a cut would leave nothing
    bad += " Margins rose 38.2% on the same basis, which is a strong result for the period."
    asked = []

    def reask(messages):
        asked.append(messages)
        return ai("Revenue and margins are not in the data I was given, so I cannot size them. RSI 28.5 is oversold [F2]." + FILLER)

    out = fg.guard_output(llm, PROMPT, ai(bad), reask)
    assert len(asked) == 1 and "FACT CHECK FAILED" in asked[0][-1].content and "47.3" not in out.content
    assert fg.GUARD_STATS["retried"] == 1 and fg.GUARD_STATS["fixed"] == 1


def test_guard_output_bypasses_json_mode_short_text_the_auditor_and_the_off_switch(monkeypatch):
    bad = "Revenue grew 47.3% in the last quarter [F3]. The stock is oversold at RSI 28.5 [F2]." + FILLER
    msg = ai(bad)
    assert fg.guard_output(types.SimpleNamespace(format="json"), PROMPT, msg, None) is msg
    assert fg.guard_output(types.SimpleNamespace(format=None), PROMPT, ai("short answer 47.3%"), None).content == "short answer 47.3%"
    assert fg.guard_output(types.SimpleNamespace(format=None), "You are a fact-checking auditor.\n" + PROMPT, msg, None) is msg
    monkeypatch.setenv("RAEM_FACT_GUARD", "0")
    assert fg.guard_output(types.SimpleNamespace(format=None), PROMPT, msg, None) is msg
    assert fg.GUARD_STATS["checked"] == 0


def test_guard_postprocess_cleans_rendered_text_and_never_raises():
    bad = "**Action**: Sell\n\nRevenue grew 47.3% in the last quarter [F3]. The stock is oversold at RSI 28.5 [F2]." + FILLER + " Exit the position."
    new = fg.guard_postprocess(PROMPT, bad, "create_trader")
    assert "47.3" not in new and "Exit the position" in new
    assert fg.guard_postprocess(PROMPT, None, "x") is None


def test_the_llm_wrapper_applies_the_guard(monkeypatch):
    pytest.importorskip("langchain_core")
    from app.ai_pipeline import llm as llm_mod
    bad = "Revenue grew 47.3% in the last quarter [F3]. The stock is oversold at RSI 28.5 [F2]." + FILLER + " It needs close watching."

    class Fake:
        format, temperature = None, 0

        def invoke(self, msgs):
            return ai(bad)

    assert "47.3" not in llm_mod.invoke_llm_with_retry(Fake(), PROMPT).content
    assert "47.3" in llm_mod.invoke_llm_with_retry(Fake(), PROMPT, guard=False).content


# ───────────────────────── debate data carries the graph ─────────────────────────
def dstate(**kw):
    s = {"company_of_interest": "AAPL", "indicators_parsed": {"rsi": 28.5}, "market_raw_data": {}, "fund_metrics": {}, "macro_regime": ""}
    s.update(kw)
    return s


def test_peer_relation_facts_are_code_computed_with_the_wording_the_guard_reads():
    f = dd.peer_relation_facts({"own_ret20_pct": -3.0, "sector_peer_ret20_pct": 4.0, "corr_peer_ret20_pct": -3.4})
    assert f[0].startswith("vs sector peers (20d): own -3.00% vs +4.00%: UNDERPERFORMS by 7.00 pts")
    assert f[1].startswith("vs correlated peers (20d):") and "IN LINE" in f[1]
    assert dd.peer_relation_facts({}) == []


def test_graph_context_and_peer_facts_reach_every_debater_prompt():
    gm = {"own_ret20_pct": -3.0, "sector_peer_ret20_pct": 4.0, "universe_ret20_pct": 1.0}
    out = dd.format_hard_indicators(dstate(graph_context="X sits in the 'Food' sector in a 20-stock DSE peer graph.", graph_metrics=gm))
    assert "PEER-GRAPH CONTEXT" in out and "'Food' sector" in out
    assert "[F" in out and "UNDERPERFORMS" in out and "peer graph (secondary evidence" in out
    assert "PEER-GRAPH CONTEXT" not in dd.format_hard_indicators(dstate())


def test_the_news_cap_can_grow_for_the_graph_block():
    rag = "x" * 2000
    assert len(dd.format_shared_context(dstate(news_rag_context=rag), macro=False, memory=False)) < 1700
    assert len(dd.format_shared_context(dstate(news_rag_context=rag, news_rag_cap=2200), macro=False, memory=False)) > 2000


# ───────────────────────────── graph analyst ─────────────────────────────
def panel(n_days=280, n_stocks=10, seed=1):
    pd = pytest.importorskip("pandas")
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-01", periods=n_days)
    common = rng.normal(0, 0.01, n_days)
    cols = ["AAA"] + [f"P{i}" for i in range(1, n_stocks)]
    close = {}
    for k, c in enumerate(cols):
        grp = rng.normal(0, 0.008, n_days) if k < 5 else rng.normal(0, 0.008, n_days)
        r = common * 0.5 + (grp if k < 5 else rng.normal(0, 0.008, n_days)) + rng.normal(0, 0.006, n_days)
        close[c] = 100 * np.exp(np.cumsum(r))
    close = pd.DataFrame(close, index=idx)
    volume = pd.DataFrame(rng.integers(1000, 5000, size=close.shape).astype(float), index=idx, columns=cols)
    sectors = {c: ("Food" if k < 5 else "Bank") for k, c in enumerate(cols)}
    return close, volume, sectors


def test_sector_edges_correlation_edges_and_neighbour_features():
    from app.ai_pipeline import graph_analyst as ga
    close, volume, sectors = panel()
    rets = close.pct_change()
    adj = ga.build_graph(rets, sectors)
    assert adj.shape == (10, 10) and adj.diagonal().all() and (adj == adj.T).all()
    assert adj[0, 1:5].all()                               # the four other Food stocks are sector peers of AAA
    X = ga.build_feature_panel(close, volume)
    assert X.shape == (280, 10, len(ga.GRAPH_FEATURES)) and abs(X[-1].mean(axis=0)).max() < 1e-6    # z-scored per date
    nb = ga.neighbor_features(X[-1], adj)
    assert nb.shape == (10, len(ga.GRAPH_FEATURES))


def test_run_graph_analyst_builds_the_context_and_validates_the_gat(monkeypatch):
    pytest.importorskip("torch")
    from app.ai_pipeline import graph_analyst as ga
    monkeypatch.setattr(ga, "GRAPH_EPOCHS", 2)
    monkeypatch.setattr(ga, "GRAPH_N_SEEDS", 1)
    lines = []
    res = ga.run_graph_analyst("AAA", "2026-01-30", panel=panel(), log=lines.append)
    m = res["metrics"]
    assert m["status"] == "OK" and m["n_stocks"] == 10 and m["sector"] == "Food"
    assert set(m["sector_peers"]) == {"P1", "P2", "P3", "P4"} and m["gnn_status"] in ("VALIDATED", "NOT_VALIDATED")
    assert {"holdout_ic_gat", "holdout_ic_no_graph", "holdout_ic_raw_baseline", "holdout_ic_shuffled_control", "gnn_rank"} <= set(m)
    assert "'Food' sector in a 10-stock DSE peer graph" in res["graph_context"] and "GNN" in res["graph_context"]
    assert res["close"].shape[1] == 10 and res["graph_context"] in lines[-1]
    # an unvalidated GNN is labelled so the debaters ignore it
    if m["gnn_status"] == "NOT_VALIDATED":
        assert "IGNORE it" in res["graph_context"]


def test_gnn_switch_and_short_history(monkeypatch):
    from app.ai_pipeline import graph_analyst as ga
    monkeypatch.setenv("RAEM_GRAPH_GNN", "0")
    res = ga.run_graph_analyst("AAA", "2026-01-30", panel=panel(), log=lambda *_: None)
    assert res["metrics"]["gnn_status"] == "DISABLED" and "RAEM_GRAPH_GNN=0" in res["graph_context"] and res["metrics"]["status"] == "OK"
    monkeypatch.delenv("RAEM_GRAPH_GNN")
    pytest.importorskip("torch")
    short = ga.run_graph_analyst("AAA", "2026-01-30", panel=panel(n_days=120), log=lambda *_: None)
    assert short["metrics"]["gnn_status"] == "INSUFFICIENT_DATA" and "GNN not trained" in short["graph_context"]


def test_graph_analyst_skips_a_non_dse_ticker_a_tiny_universe_and_the_off_switch(monkeypatch):
    from app.ai_pipeline import graph_analyst as ga
    monkeypatch.setattr(ga, "is_dse", lambda c: False)
    r = ga.run_graph_analyst("AAPL", "2026-01-30", log=lambda *_: None)
    assert r["metrics"]["status"] == "SKIPPED" and r["graph_context"] == "" and "DSE" in r["metrics"]["reason"]
    small = ga.run_graph_analyst("AAA", "2026-01-30", panel=panel(n_stocks=4), log=lambda *_: None)
    assert small["metrics"]["status"] == "SKIPPED" and "need >= 6" in small["metrics"]["reason"]
    monkeypatch.setenv("RAEM_GRAPH_ANALYST", "0")
    assert ga.run_graph_analyst("AAA", "2026-01-30", panel=panel(), log=lambda *_: None)["metrics"]["reason"].startswith("Graph analyst is off")


def fake_dse(rows_by_symbol, sectors):
    pd = pytest.importorskip("pandas")
    asked = []

    def ohlcv(sym, start, end):
        asked.append(sym)
        n = rows_by_symbol.get(sym, 0)
        idx = pd.bdate_range("2025-06-01", periods=n)
        return pd.DataFrame({"Close": [100.0 + i * 0.1 for i in range(n)], "Volume": [1000.0] * n}, index=idx)

    return ohlcv, (lambda sym: {"Sector": sectors.get(sym, "Unknown")}), asked


def test_panel_is_cached_per_symbol_and_bad_symbols_are_not_asked_again(tmp_path, monkeypatch):
    from app.ai_pipeline import graph_analyst as ga
    monkeypatch.setenv("RAEM_GRAPH_CACHE_FILE", str(tmp_path / "panel.json"))
    ohlcv, snap, asked = fake_dse({"AAA": 120, "P1": 120, "P2": 20}, {"AAA": "Food", "P1": "Food"})
    close, volume, sectors = ga.load_graph_panel("AAA", "2026-01-30", peers=["P1", "P2"], fetch_ohlcv=ohlcv, fetch_snapshot=snap, log=lambda *_: None)
    assert list(close.columns) == ["AAA", "P1"] and sectors == {"AAA": "Food", "P1": "Food"} and asked == ["AAA", "P1", "P2"]
    # a second company the same day: only what is missing is fetched, the bad symbol is remembered
    ohlcv2, snap2, asked2 = fake_dse({"AAA": 120, "P1": 120, "P3": 120}, {"P3": "Food"})
    close2, _, _ = ga.load_graph_panel("P3", "2026-01-30", peers=["AAA", "P1", "P2", "P3"], fetch_ohlcv=ohlcv2, fetch_snapshot=snap2, log=lambda *_: None)
    assert asked2 == ["P3"] and set(close2.columns) == {"AAA", "P1", "P3"}
    # another as-of date is another panel
    ohlcv3, snap3, asked3 = fake_dse({"AAA": 120, "P1": 120}, {})
    ga.load_graph_panel("AAA", "2026-02-02", peers=["P1"], fetch_ohlcv=ohlcv3, fetch_snapshot=snap3, log=lambda *_: None)
    assert asked3 == ["AAA", "P1"]


def test_fetch_budget_stops_a_slow_first_fetch(tmp_path, monkeypatch):
    from app.ai_pipeline import graph_analyst as ga
    monkeypatch.setenv("RAEM_GRAPH_CACHE_FILE", str(tmp_path / "panel.json"))
    monkeypatch.setenv("RAEM_GRAPH_FETCH_BUDGET", "-1")
    ohlcv, snap, asked = fake_dse({"AAA": 120, "P1": 120}, {})
    lines = []
    close, _, _ = ga.load_graph_panel("AAA", "2026-01-30", peers=["P1"], fetch_ohlcv=ohlcv, fetch_snapshot=snap, log=lines.append)
    assert asked == ["AAA"] and list(close.columns) == ["AAA"] and any("fetch budget" in x for x in lines)


# ───────────────────── memory-backed pieces: peer episodes, news graph, graph memory ─────────────────────
@pytest.fixture()
def memory(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    pytest.importorskip("networkx")
    monkeypatch.setenv("RAEM_EMBED", "hash")
    monkeypatch.setenv("RAEM_CHROMA_PATH", str(tmp_path / "chroma"))
    from app.ai_pipeline import embeddings, memory as mem
    monkeypatch.setattr(embeddings, "store_path", lambda mode: str(tmp_path / "chroma"))
    m = mem.RAEMMemory()
    m.connect()
    return m


def test_peer_observations_know_their_outcome_and_never_look_ahead():
    pytest.importorskip("pandas")
    from app.ai_pipeline import peer_episodes as pe
    close, _, _ = panel()
    obs = pe.build_peer_observations(close, "P1", 5, 8, 10)
    assert len(obs) == 8 and obs == sorted(obs, key=lambda o: o["date"], reverse=True)
    assert all(o["outcome_date"] > o["date"] for o in obs)
    last_day = close.index[-1].strftime("%Y-%m-%d")
    assert obs[0]["outcome_date"] <= last_day
    assert pe.build_peer_observations(close, "NOPE", 5, 8, 10) == []


def test_peer_episodes_are_stored_once_under_the_peers_name_and_skip_unknown_outcomes(memory):
    from app.ai_pipeline import peer_episodes as pe
    close, _, _ = panel()
    as_of = close.index[-1].strftime("%Y-%m-%d")
    st = pe.store_peer_episodes(memory, "AAA", as_of, ["P1", "P2", "AAA"], close, log=lambda *_: None)
    assert st["peers"] == ["P1", "P2"] and st["stored"] > 8 and st["already_there"] == 0
    again = pe.store_peer_episodes(memory, "AAA", as_of, ["P1", "P2"], close, log=lambda *_: None)
    assert again["stored"] == 0 and again["already_there"] == st["stored"]
    got = memory.episodic.get(where={"company": "P1"})
    assert got["ids"] and all(m["source"] == "PEER_PRICE" and m["final_signal"] == "N/A" for m in got["metadatas"])
    assert memory.episodic.get(where={"company": "AAA"})["ids"] == []           # nothing lands in the company's own memory
    assert all(m["outcome_date"] < as_of for m in got["metadatas"])
    # an earlier as-of date stores only the observations whose outcome was already known then
    early = close.index[150].strftime("%Y-%m-%d")
    pe_early = pe.store_peer_episodes(memory, "AAA", early, ["P3"], close, log=lambda *_: None)
    assert all(m["outcome_date"] < early for m in memory.episodic.get(where={"company": "P3"})["metadatas"]) and pe_early["stored"] > 0


def test_peer_episodes_switch(memory, monkeypatch):
    from app.ai_pipeline import peer_episodes as pe
    monkeypatch.setenv("RAEM_PEER_EPISODES", "0")
    close, _, _ = panel()
    assert pe.store_peer_episodes(memory, "AAA", "2026-02-01", ["P1"], close, log=lambda *_: None)["stored"] == 0
    assert memory.episodic.count() == 0


NEWS = [
    ("BATBC board meeting on 2026-11-10 to consider the dividend", {"company": "BATBC", "scope": "company", "source": "company_news", "pub_date": "2026-11-10"}),
    ("NBR raises supplementary duty on cigarettes in the new budget", {"company": "DSE_SECTOR", "scope": "sector", "source": "sector_web", "pub_date": "2026-11-05"}),
    ("SQURPHARMA signs an export agreement with a European distributor", {"company": "DSE_MARKET", "scope": "market", "source": "global_news", "pub_date": "2026-11-08"}),
    ("UNILEVERCL reports quarterly EPS growth and declares dividend", {"company": "DSE_MARKET", "scope": "market", "source": "global_news", "pub_date": "2026-11-09"}),
    ("A leaked item from the future should never be used", {"company": "DSE_SECTOR", "scope": "sector", "source": "sector_web", "pub_date": "2026-12-30"}),
]


def fill_news(memory):
    memory.news.upsert(ids=[f"n{i}" for i in range(len(NEWS))], documents=[t for t, _ in NEWS], metadatas=[m for _, m in NEWS])


def test_news_graph_links_sector_regulator_and_peer_news_and_never_looks_ahead(memory):
    from app.ai_pipeline import news_graph as ng
    fill_news(memory)
    G = ng.build_news_graph(memory.news, "BATBC", "2026-11-12", universe={"SQURPHARMA", "UNILEVERCL"}, log=lambda *_: None)
    assert "NEWS:n4" not in G                                            # dated after the as-of day
    assert G.has_edge("NEWS:n0", "TICKER:BATBC") and G.has_edge("NEWS:n1", "TOPIC:TOBACCO") and G.has_edge("NEWS:n1", "REG:NBR")
    items, topics = ng.graph_news_retrieve(G, "BATBC", ["UNILEVERCL"], ["SQURPHARMA"])
    why = {G.nodes[n]["text"][:12]: w for _, w, n in items}
    assert any("sector peer UNILEVERCL" in w for w in why.values()) and any("correlated peer SQURPHARMA" in w for w in why.values())
    assert any("sector/regulatory link" in w for w in why.values())
    assert all("NEWS:n0" != n for _, _, n in items)                      # the company's own news is not repeated
    assert "DIVIDEND" in topics and "EARNINGS" not in topics or topics


def test_news_graph_block_goes_before_the_exchange_wide_notices_and_labels_peer_news(memory):
    from app.ai_pipeline import news_graph as ng
    fill_news(memory)
    base = ("COMPANY NEWS RETRIEVED VIA RAG (BATBC):\n- (company_news): BATBC board meeting\n"
            "EXCHANGE-WIDE NOTICES (mostly about OTHER companies/funds -- context only):\n- (pub 2026-11-08): SQURPHARMA export")
    out = ng.run_news_graph(memory, "BATBC", "2026-11-12", base, ["UNILEVERCL"], ["SQURPHARMA"], universe={"SQURPHARMA", "UNILEVERCL"},
                            today_regime="NEUTRAL_RANGING", log=lambda *_: None)
    ctx = out["context"]
    assert ctx.index("GRAPH-LINKED NEWS") < ctx.index("EXCHANGE-WIDE NOTICES") and "NOT news about BATBC" in ctx
    assert out["cap"] == 2200 and "DIVIDEND" in out["topics"] and out["result"]["items"] >= 2
    # re-running replaces the block instead of stacking a second one
    again = ng.run_news_graph(memory, "BATBC", "2026-11-12", ctx, ["UNILEVERCL"], ["SQURPHARMA"], universe={"SQURPHARMA", "UNILEVERCL"}, log=lambda *_: None)
    assert again["context"].count("GRAPH-LINKED NEWS") == 1


def test_news_graph_adds_peer_episodes_in_the_same_regime_first(memory):
    from app.ai_pipeline import news_graph as ng
    fill_news(memory)
    memory.episodic.upsert(ids=["a", "b", "c"], documents=["x", "y", "z"], metadatas=[
        {"company": "UNILEVERCL", "trade_date": "2026-09-01", "regime": "TRENDING_BEAR", "source": "PEER_PRICE", "pnl_pct": "-2.10", "horizon_days": "5", "outcome_date": "2026-09-08"},
        {"company": "UNILEVERCL", "trade_date": "2026-10-01", "regime": "NEUTRAL_RANGING", "source": "PEER_PRICE", "pnl_pct": "1.20", "horizon_days": "5", "outcome_date": "2026-10-08"},
        {"company": "UNILEVERCL", "trade_date": "2026-11-10", "regime": "NEUTRAL_RANGING", "source": "PEER_PRICE", "pnl_pct": "9.99", "horizon_days": "5", "outcome_date": "2026-11-17"}])
    lines = ng.peer_episode_lines(memory.episodic, ["UNILEVERCL"], "2026-11-12", today_regime="NEUTRAL_RANGING")
    assert len(lines) == 2 and "2026-10-01" in lines[0] and "price-only observation" in lines[0]   # same regime first; the unresolved one is left out
    assert "9.99" not in " ".join(lines)
    assert ng.peer_episode_lines(memory.episodic, [], "2026-11-12") == []


def test_news_graph_switch(memory, monkeypatch):
    from app.ai_pipeline import news_graph as ng
    fill_news(memory)
    monkeypatch.setenv("RAEM_NEWS_GRAPH", "0")
    out = ng.run_news_graph(memory, "BATBC", "2026-11-12", "plain rag", ["UNILEVERCL"], [], log=lambda *_: None)
    assert out["context"] == "plain rag" and out["topics"] == [] and out["result"]["enabled"] is False


def test_llm_extraction_keeps_only_grounded_entities_and_valid_relations():
    from app.ai_pipeline import news_graph as ng
    text = "NBR raises supplementary duty on cigarettes. BATBC said sales fell in Bangladesh."
    stats = {}
    out = ng.validate_extraction({
        "entities": [{"name": "NBR", "type": "REGULATOR"}, {"name": "supplementary duty", "type": "POLICY"},
                     {"name": "Philip Morris", "type": "COMPANY"},            # not in the text: hallucinated
                     {"name": "Bangladesh", "type": "COMPANY"},                # a generic hub
                     {"name": "12.5", "type": "EVENT"}, {"name": "BATBC", "type": "WIDGET"}],
        "relations": [{"subj": "NBR", "rel": "raises", "obj": "supplementary duty"},
                      {"subj": "NBR", "rel": "loves", "obj": "supplementary duty"},          # not in the verb list
                      {"subj": "NBR", "rel": "regulates", "obj": "Philip Morris"}]}, text, stats)
    assert dict(out["entities"]) == {"NBR": "REGULATOR", "SUPPLEMENTARY DUTY": "POLICY"}
    assert out["relations"] == [["NBR", "raises", "SUPPLEMENTARY DUTY"]]
    assert stats["entities_ok"] == 2 and stats["entities_dropped"] == 4 and stats["relations_ok"] == 1 and stats["relations_dropped"] == 2
    assert ng.validate_extraction("not a dict", text) == {"entities": [], "relations": []}
    assert ng._llm_json("Sure! ```json\n{\"entities\": []}\n```") == {"entities": []}


class JsonLlm:
    """A stand-in model: answers every extraction prompt with the same JSON, or fails."""
    format, temperature = None, 0

    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.calls = answer, fail, 0

    def invoke(self, prompt):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Your credit balance is too low")
        return types.SimpleNamespace(content=json.dumps(self.answer))


def test_llm_graph_adds_entity_and_relation_hops_and_is_cached_on_disk(memory, tmp_path, monkeypatch):
    pytest.importorskip("langchain_core")
    from app.ai_pipeline import news_graph as ng
    monkeypatch.setenv("RAEM_LLM_GRAPH_CACHE_FILE", str(tmp_path / "llm_graph.json"))
    memory.news.upsert(ids=["c1", "s1", "s2"], documents=[
        "BATBC says the Moonlight Council plans a review of cigarette pricing this quarter",
        "The Moonlight Council met on Tuesday to discuss new labour rules for factories",
        "Workers ask the Moonlight Council for a hearing on overtime pay this month"],
        metadatas=[{"company": "BATBC", "scope": "company", "pub_date": "2026-11-10", "source": "company_news"},
                   {"company": "DSE_SECTOR", "scope": "sector", "pub_date": "2026-11-09", "source": "sector_web"},
                   {"company": "DSE_SECTOR", "scope": "sector", "pub_date": "2026-11-08", "source": "sector_web"}])
    llm = JsonLlm({"entities": [{"name": "Moonlight Council", "type": "REGULATOR"}], "relations": []})
    G = ng.build_news_graph(memory.news, "BATBC", "2026-11-12", universe={"BATBC"}, llm=llm, log=lambda *_: None)
    assert llm.calls == 3 and G.has_edge("NEWS:c1", "ENT:MOONLIGHT COUNCIL") and G.has_edge("NEWS:s1", "ENT:MOONLIGHT COUNCIL")
    items, _ = ng.graph_news_retrieve(G, "BATBC", [], [], use_llm=True)
    assert any("shares entity Moonlight Council" in why for _, why, _ in items)
    again = ng.build_news_graph(memory.news, "BATBC", "2026-11-12", universe={"BATBC"}, llm=llm, log=lambda *_: None)
    assert llm.calls == 3 and again.has_edge("NEWS:c1", "ENT:MOONLIGHT COUNCIL")        # a chunk costs one model call, ever
    off = JsonLlm({"entities": [], "relations": []})
    monkeypatch.setenv("RAEM_LLM_GRAPH", "0")
    ng.build_news_graph(memory.news, "BATBC", "2026-11-12", llm=off, log=lambda *_: None)
    assert off.calls == 0


def test_llm_graph_stops_after_three_failures_and_never_stops_the_run(memory, tmp_path, monkeypatch):
    pytest.importorskip("langchain_core")
    from app.ai_pipeline import news_graph as ng
    monkeypatch.setenv("RAEM_LLM_GRAPH_CACHE_FILE", str(tmp_path / "llm_graph.json"))
    memory.news.upsert(ids=[f"x{i}" for i in range(8)], documents=[f"BATBC news item number {i} about the board" for i in range(8)],
                       metadatas=[{"company": "BATBC", "scope": "company", "pub_date": "2026-11-10", "source": "company_news"}] * 8)
    llm = JsonLlm(fail=True)
    G = ng.build_news_graph(memory.news, "BATBC", "2026-11-12", universe={"BATBC"}, llm=llm, log=lambda *_: None)
    assert llm.calls == 3 and sum(1 for n in G.nodes if n.startswith("NEWS:")) == 8


def test_graph_memory_matches_regime_and_macro_then_relaxes_and_follows_topics(memory):
    from app.ai_pipeline import graph_memory as gm
    base = {"company": "BATBC", "outcome_status": "RESOLVED", "news_topics": "NONE"}
    rows = [("e1", "2026-09-01", "TRENDING_BEAR", "RISK_OFF_HIGH_VOL", "BUY", "-2.0", "TAX_DUTY"),
            ("e2", "2026-09-10", "TRENDING_BEAR", "RISK_OFF_HIGH_VOL", "BUY", "-1.0", "NONE"),
            ("e3", "2026-09-20", "TRENDING_BEAR", "NEUTRAL_MACRO", "SELL", "3.0", "NONE"),
            ("e4", "2026-11-12", "TRENDING_BEAR", "RISK_OFF_HIGH_VOL", "BUY", "9.0", "NONE")]       # today's own run: excluded
    memory.episodic.upsert(ids=[r[0] for r in rows], documents=["ep"] * 4, metadatas=[
        {**base, "trade_date": d, "regime": rg, "macro_regime": mc, "final_signal": sg, "pnl_pct": p, "news_topics": tp} for _, d, rg, mc, sg, p, tp in rows])
    lines = []
    ctx = gm.episode_graph_context(memory, "BATBC", "2026-11-12", "TRENDING_BEAR", "RISK_OFF_HIGH_VOL", topics=["TAX_DUTY"], log=lines.append)
    assert "regime=TRENDING_BEAR AND macro=RISK_OFF_HIGH_VOL" in ctx and "BUY: 2 episodes, 2 resolved, mean P&L -1.50%" in ctx
    assert "SELL" not in ctx.split("same news topics")[0] and "same news topics TAX_DUTY" in ctx and "9.00" not in ctx
    relaxed = gm.episode_graph_context(memory, "BATBC", "2026-11-12", "TRENDING_BEAR", "RATES_RISING", log=lambda *_: None)
    assert "regime=TRENDING_BEAR only" in relaxed and "SELL: 1 episodes" in relaxed
    assert gm.episode_graph_context(memory, "BATBC", "2026-11-12", "OVERBOUGHT", "RATES_RISING", log=lambda *_: None) == ""
    assert gm.episode_graph_context(memory, "OTHER", "2026-11-12", "TRENDING_BEAR", "RISK_OFF_HIGH_VOL", log=lambda *_: None) == ""


def test_graph_memory_switch(memory, monkeypatch):
    from app.ai_pipeline import graph_memory as gm
    memory.episodic.upsert(ids=["e1"], documents=["ep"], metadatas=[{"company": "BATBC", "trade_date": "2026-09-01", "regime": "NEUTRAL_RANGING",
                                                                    "macro_regime": "N/A", "final_signal": "HOLD", "outcome_status": "PENDING", "pnl_pct": "N/A"}])
    assert gm.episode_graph_context(memory, "BATBC", "2026-11-12", "NEUTRAL_RANGING", "N/A", log=lambda *_: None)
    monkeypatch.setenv("RAEM_GRAPH_MEMORY", "0")
    assert gm.episode_graph_context(memory, "BATBC", "2026-11-12", "NEUTRAL_RANGING", "N/A", log=lambda *_: None) == ""
