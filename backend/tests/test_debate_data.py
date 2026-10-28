"""Hard data, fact sheet and shared context for the debate agents (ported from the research notebook)."""
import types

import pytest

pytest.importorskip("langchain_core")   # debate_data imports the agents; the slim CI job has only the API packages

from app.ai_pipeline import debate_data as dd  # noqa: E402

CSV = "Date,Open,High,Low,Close,Volume\n2026-10-01,10,11,9,100.0,5\n2026-10-02,10,11,9,90.0,5\n"


def state(**kw):
    s = {
        "company_of_interest": "AAPL",
        "indicators_parsed": {"rsi": 28.5, "macd": -1.25, "close_50_sma": 100.0, "boll_ub": 120.0, "boll_lb": 95.0},
        "market_raw_data": {"stock_data": CSV},
        "fund_metrics": {"eps_ttm": 5.1, "pe_ratio": 18.2, "dividend_yield": "2.1%",
                         "cash_dividend": "30% 2025, 300% 2024, 100% 2023"},
        "macro_regime": "RISK_OFF", "macro_report": "VIX high",
        "news_rag_context": "[2026-10-01] Company X missed estimates",
        "news_raw_full": {"company_news": "- 2026-10-02 Board meeting announced for results date",
                          "web_news": "- [2026-10-01] Analyst cuts target (reuters.com)"},
    }
    s.update(kw)
    return s


def test_trend_facts_are_computed_in_code():
    f = dd.derive_trend_facts(state())
    assert "BELOW the 50-day SMA" in f and "RESISTANCE" in f
    assert "oversold" in f and "negative (bearish momentum)" in f
    assert "BELOW the lower Bollinger band" in f
    up = dd.derive_trend_facts(state(market_raw_data={"stock_data": CSV.replace("90.0", "130.0")}))
    assert "ABOVE the 50-day SMA" in up and "SUPPORT" in up


def test_dividend_trend_is_not_called_increasing():
    t = dd.dividend_trend_fact("30% 2025, 300% 2024, 100% 2023")
    assert "FELL 90%" in t and "highest listed 300% (2024)" in t and "interim" in t
    assert dd.dividend_trend_fact("10% 2025") == ""
    assert "ROSE" in dd.dividend_trend_fact("200% 2025, 100% 2024")


def test_fact_sheet_has_numbered_ids():
    sheet = dd.build_fact_sheet(state())
    assert sheet.startswith("[F1] last close 90.00")
    assert "[F" in sheet and "[N1]" in sheet and "[W1]" in sheet
    assert "cash dividend trend" in sheet and "macro regime: RISK_OFF" in sheet
    assert "P/E: 18.2" in sheet
    assert "BDT" not in sheet


def test_dse_ticker_gets_taka_and_long_only_rules(monkeypatch):
    monkeypatch.setattr(dd, "is_dse", lambda s: True)
    block = dd.format_hard_indicators(state(company_of_interest="BATBC"))
    assert "90.00 BDT" in block and "LONG-ONLY" in block and "never '$'" in block
    monkeypatch.setattr(dd, "is_dse", lambda s: False)
    block = dd.format_hard_indicators(state())
    assert "LONG-ONLY" not in block and "CITATION RULE" in block


def test_hard_indicators_never_truncated_and_missing_is_explicit():
    block = dd.format_hard_indicators(state())
    assert "rsi=28.5000" in block and "macd=-1.2500" in block
    assert "do NOT guess" in dd.format_hard_indicators({"company_of_interest": "X"})


def test_shared_context_switches():
    s = state(memory_context="Regime: bearish. Past BUY failed.")
    allc = dd.format_shared_context(s)
    assert "NEWS RAG" in allc and "MACRO REGIME: RISK_OFF" in allc and "MEMORY" in allc
    macro_only = dd.format_shared_context(s, news=False, macro=True, memory=False)
    assert "NEWS RAG" not in macro_only and "MEMORY" not in macro_only and "RISK_OFF" in macro_only
    assert dd.format_shared_context({}) == "(none available)"


def test_agents_put_hard_data_in_every_debate_prompt(monkeypatch):
    from app.ai_pipeline import agents
    seen = []

    def fake_invoke(llm, prompt, *a, **k):
        seen.append(prompt)
        return types.SimpleNamespace(content="ok")

    monkeypatch.setattr(agents, "invoke_llm_with_retry", fake_invoke)
    s = state(market_report="m", sentiment_report="s", news_report="n", fundamentals_report="f",
              trader_investment_plan="BUY",
              investment_debate_state={"history": "", "bull_history": "", "bear_history": "", "count": 0},
              risk_debate_state={"history": "", "aggressive_history": "", "conservative_history": "",
                                 "neutral_history": "", "count": 0})
    agents.create_bull_researcher(None)(s)
    agents.create_bear_researcher(None)(s)
    agents.create_investment_facilitator(None)(s)
    for make in (agents.create_aggressive_debator, agents.create_conservative_debator, agents.create_neutral_debator,
                 agents.create_risk_facilitator):
        make(None)(s)
    assert len(seen) == 7
    for p in seen:
        assert "rsi=28.5000" in p and "[F1]" in p, p[:200]
    assert "Evidence rule" in seen[0] and "misattribution" in seen[0]
    assert "Evidence rule" in seen[1]
    assert all("RISK_OFF" in p for p in seen)
