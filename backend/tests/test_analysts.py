"""The news corpus and the rule-based sentiment of the research notebooks (offline, no Chroma server)."""
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
pytest.importorskip("pandas")

from app.ai_pipeline import news_rag  # noqa: E402


class FakeCollection:
    """The few Chroma calls the corpus uses, kept in a dict."""
    def __init__(self):
        self.rows = {}

    def get(self, ids=None):
        keys = [i for i in (ids if ids is not None else self.rows) if i in self.rows]
        return {"ids": keys, "documents": [self.rows[k][0] for k in keys], "metadatas": [self.rows[k][1] for k in keys]}

    def upsert(self, ids, documents, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, m)

    def count(self):
        return len(self.rows)


def day(offset=0):
    return (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")


def test_lexicon_scores_a_chunk():
    assert news_rag.score_text_sentiment("The board declared a cash dividend and profit rose 12%.")[0] == "POSITIVE"
    assert news_rag.score_text_sentiment("The company reported a net loss; the regulator imposed a penalty.")[0] == "NEGATIVE"
    assert news_rag.score_text_sentiment("Record date for the AGM is 5 November.")[0] == "NEUTRAL"


def test_ingest_tags_scope_dedupes_and_skips_junk_and_offtopic_web(monkeypatch):
    col = FakeCollection()
    today = day()
    company = f"- [{today}] AAA declared a cash dividend for the year.\n- No DSE news found for AAA."
    market = "- Trading halt rule changed for the whole exchange today.\n- BBB Ltd announced a record date for dividend."
    web = f"- [{today}] AAA profit rose sharply in the quarter. (press.com)\n- [{today}] Unrelated firm posts big profit. (press.com)\n- [no date] AAA old story about growth. (press.com)"
    n = news_rag.ingest_news(col, "AAA", today, company, market, web_news=web, log=lambda *a: None)
    assert n == len(col.rows)
    scopes = sorted((m["company"], m["scope"]) for _d, m in col.rows.values())
    assert ("AAA", "company") in scopes and ("AAA", "web") in scopes and (news_rag.MARKET_TAG, "market") in scopes
    texts = " ".join(d for d, _m in col.rows.values())
    assert "Unrelated firm" not in texts and "No DSE news" not in texts and "old story" not in texts   # off-topic web, placeholder, undated web
    assert news_rag.ingest_news(col, "AAA", today, company, market, web_news=web, log=lambda *a: None) == 0   # same text, same ids


def test_web_news_is_not_ingested_in_replay_mode():
    col = FakeCollection()
    old = day(30)
    web = f"- [{old}] AAA profit rose sharply in the quarter. (press.com)"
    assert news_rag.ingest_news(col, "AAA", old, "", "", web_news=web, log=lambda *a: None) == 0


def test_rule_based_sentiment_uses_company_news_only_and_never_looks_ahead():
    col = FakeCollection()
    today = day()
    col.upsert(["a", "b", "c", "d", "e"],
               ["AAA declared a cash dividend and profit rose 12% this year.",
                "AAA reported a net loss and faces an investigation by the regulator.",
                "BBB posted record profit and a stock dividend for shareholders.",               # another company
                "AAA announced a bonus share and strong earnings growth next week.",             # published after the as-of date
                "AAA profit rose again and the board recommended a cash dividend."],
               [{"company": "AAA", "scope": "company", "pub_date": today},
                {"company": "AAA", "scope": "company", "pub_date": today},
                {"company": "BBB", "scope": "company", "pub_date": today},
                {"company": "AAA", "scope": "company", "pub_date": day(-3)},
                {"company": "AAA", "scope": "web", "pub_date": day(60)}])                         # older than 30 days
    rs = news_rag.rule_based_news_sentiment(col, "AAA", today)
    assert rs["n"] == 2 and rs["pos"] == 1 and rs["neg"] == 1 and rs["score"] == 5.5 and rs["overall"] == "NEUTRAL"
    assert rs["confidence"] == "Low" and "RULE-BASED" in rs["text"]
    assert news_rag.rule_based_news_sentiment(col, "ZZZ", today) == {"n": 0}


def test_company_name_and_aliases(monkeypatch):
    monkeypatch.delenv("RAEM_COMPANY_NAME", raising=False)
    assert news_rag.company_name("BATBC").startswith("British American Tobacco")
    assert "BATBC" in news_rag.aliases_for("BATBC") and any("BRITISH AMERICAN" in a for a in news_rag.aliases_for("BATBC"))
    monkeypatch.setenv("RAEM_COMPANY_NAME", "GreenPower Motor Company Inc.")
    assert any("GREENPOWER MOTOR" in a for a in news_rag.aliases_for("GP"))


def test_market_analyst_wording_is_neutralised():
    from app.ai_pipeline import agents
    out = agents._neutralise_bull_bear("A bullish MACD in a bear market; the bears are active, Bulls retreat.")
    assert not re.search(r"bull|bear", out, re.I)
    assert "upward-leaning" in out and "downtrend" in out and "sellers" in out and "buyers" in out


class _Tool:
    def __init__(self, text):
        self.text = text

    def invoke(self, args):
        return self.text


class _LLM:
    def __init__(self, text="Report body.\nFINAL TRANSACTION PROPOSAL: **BUY**"):
        self.text = text
        self.prompts = []

    def invoke(self, messages, *a, **k):
        import types
        self.prompts.append(str(messages[-1].content))
        return types.SimpleNamespace(content=self.text)


def test_dse_fundamentals_follow_the_notebook_and_hide_the_verdict(monkeypatch):
    pytest.importorskip("langchain_core")
    from app.ai_pipeline import agents, config
    monkeypatch.setattr(agents, "FUND_SHARE_BOTH", False)   # the old numbers-only mode
    config.ensure_tradingagents_on_path()
    import tradingagents.dataflows.symbol_utils as su
    monkeypatch.setattr(su, "is_dse_ticker", lambda s: True)
    tools = {
        "build_instrument_context": lambda c, *a: f"Instrument: {c}",
        "get_language_instruction": lambda: "",
        "get_fundamentals": _Tool("COMPANY: AAA (DSE)\nSector: Pharmaceuticals\nLast Trading Price: 120.5\nPE Ratio: N/A\nEPS (TTM): N/A\n"
                                  "Market Cap: 52000000000\nRevenue (TTM): N/A\nBeta: N/A\nDividend Yield: N/A\n52 Week High: 150\n52 Week Low: 90\n"),
        "get_balance_sheet": _Tool("Authorized Capital: 6000000000\nPaid-up Capital: 4000000000\nReserve & Surplus: 8000000000\nOther Comprehensive Income: 120000\n"),
        "get_cashflow": _Tool("Cash Dividend: 2025: 35%, 2024: 30%\nBonus Issue (Stock Dividend): N/A\nRight Issue: N/A\n"),
        "get_income_statement": _Tool("Most Recent Disclosed EPS: Tk. 3.2 for July-September 2026\nNet Income From Continuing (estimated, EPS x shares): 1280000000\n"
                                      "P/E Ratio (rough, annualized): 9.4\nGross Profit: N/A\nOperating Income: N/A\nNormalized EBITDA: N/A\n"),
    }
    monkeypatch.setattr(agents, "_ta_utils", lambda ticker="": tools)
    llm = _LLM()
    out = agents.create_fundamentals_analyst(llm, log=lambda *a: None)(
        {"trade_date": "2026-10-27", "company_of_interest": "AAA", "messages": []})
    fm = out["fund_metrics"]
    assert fm["pe_ratio"] == "9.4" and fm["eps_ttm"] == "3.2" and "July-September 2026" in fm["eps_period"]
    assert fm["market_cap"] == "৳52.00B" and fm["cash_dividend"] == "2025: 35%, 2024: 30%" and fm["paidup_capital_bs"] == "৳4.00B"
    # the others get the numbers block; the analyst's own call stays in its own fields
    assert out["fundamentals_report"].startswith("FUNDAMENTALS - NUMBERS ONLY") and "FINAL TRANSACTION" not in out["fundamentals_report"]
    assert out["fund_verdict"] == "FINAL TRANSACTION PROPOSAL: BUY" and out["fundamentals_report_llm"].endswith("**BUY**")
    assert "NOT trailing-12-month" in llm.prompts[0] and "Never write 0%" in llm.prompts[0]
    monkeypatch.setattr(agents, "FUND_NUMBERS_ONLY", False)
    out2 = agents.create_fundamentals_analyst(_LLM(), log=lambda *a: None)({"trade_date": "2026-10-27", "company_of_interest": "AAA", "messages": []})
    assert "FINAL TRANSACTION" in out2["fundamentals_report"]


def test_shared_fundamentals_gives_numbers_first_then_the_report(monkeypatch):
    pytest.importorskip("langchain_core")
    from app.ai_pipeline import agents
    facts = "FUNDAMENTALS - NUMBERS ONLY (code) -- AAA\nP/E: 9.4"
    report = "Long narrative...\nFINAL TRANSACTION PROPOSAL: **BUY**"
    monkeypatch.setattr(agents, "FUND_SHARE_BOTH", True)
    both = agents.shared_fundamentals(facts, report)
    assert both.startswith(facts) and both.endswith(report) and agents.FUND_REPORT_MARK in both
    assert both.index("P/E: 9.4") < both.index("FINAL TRANSACTION")      # numbers survive any later cut
    monkeypatch.setattr(agents, "FUND_SHARE_BOTH", False)
    monkeypatch.setattr(agents, "FUND_NUMBERS_ONLY", True)
    assert agents.shared_fundamentals(facts, report) == facts
    monkeypatch.setattr(agents, "FUND_NUMBERS_ONLY", False)
    assert agents.shared_fundamentals(facts, report) == report


def test_news_analyst_reads_web_coverage_and_returns_the_full_texts(monkeypatch):
    pytest.importorskip("langchain_core")
    from app.ai_pipeline import agents, config
    config.ensure_tradingagents_on_path()
    import tradingagents.dataflows.symbol_utils as su
    monkeypatch.setattr(su, "is_dse_ticker", lambda s: True)
    long_text = "- AAA declared a cash dividend for the year.\n" * 200
    tools = {"build_instrument_context": lambda c, *a: "ctx", "get_language_instruction": lambda: "",
             "get_news": _Tool(long_text), "get_global_news": _Tool("- Exchange-wide notice about turnover today.")}
    monkeypatch.setattr(agents, "_ta_utils", lambda ticker="": tools)
    monkeypatch.setattr(news_rag, "fetch_web_news", lambda *a, **k: "- [2 days ago] AAA signs a new contract. (press.com)")
    monkeypatch.setattr(news_rag, "fetch_sector_news", lambda *a, **k: "- [2026-10-20] Sector duty change. (press.com)")
    llm = _LLM("Summary.\n| Category | Headline | Sentiment | Impact | Source |\n|---|---|---|---|---|\n| Company | Dividend | POSITIVE | High | DSE |\n")
    out = agents.create_news_analyst(llm, log=lambda *a: None)({"trade_date": "2026-10-27", "company_of_interest": "AAA", "messages": []})
    assert out["news_metrics"]["positive_count"] == 1 and out["news_metrics"]["overall_sentiment"] == "POSITIVE"
    assert len(out["news_raw"]["company_news"]) == 3000 and len(out["news_raw_full"]["company_news"]) > 3000   # prompt copy capped, corpus copy whole
    assert out["sector_news_full"].startswith("- [2026-10-20]") and "WEB-SOURCED COMPANY COVERAGE" in llm.prompts[0]
    assert "DSE MARKET-WIDE NOTICES" in llm.prompts[0] and "[2 days ago] AAA signs" in llm.prompts[0]


# ---- every agent reads the reports of the agents before it ----
def _state_with_reports():
    return {"trade_date": "2026-11-25", "company_of_interest": "AAA", "asset_type": "stock", "messages": [],
            "fundamentals_report": "FUND-REPORT-TEXT", "market_report": "MARKET-REPORT-TEXT",
            "news_report": "NEWS-REPORT-TEXT", "sentiment_report": "SENT-REPORT-TEXT",
            "macro_report": "MACRO-REPORT-TEXT", "graph_report": "GRAPH-REPORT-TEXT"}


def test_prior_reports_before_only_off_and_cap(monkeypatch):
    from app.ai_pipeline import debate_data as dd
    st = _state_with_reports()
    monkeypatch.delenv("RAEM_SHARE_ALL_REPORTS", raising=False)
    monkeypatch.delenv("RAEM_REPORT_CAP_SCALE", raising=False)
    blk = dd.format_prior_reports(st, before="news")                      # stages that ran before the News analyst
    assert "FUND-REPORT-TEXT" in blk and "MARKET-REPORT-TEXT" in blk and "NEWS-REPORT-TEXT" not in blk
    assert "SENT-REPORT-TEXT" not in blk and "MACRO-REPORT-TEXT" not in blk
    only = dd.format_prior_reports(st, only=("macro", "graph"))
    assert "MACRO-REPORT-TEXT" in only and "GRAPH-REPORT-TEXT" in only and "FUND-REPORT-TEXT" not in only
    full = dd.format_prior_reports(st)
    assert all(t in full for t in ("FUND-REPORT-TEXT", "MARKET-REPORT-TEXT", "NEWS-REPORT-TEXT",
                                   "SENT-REPORT-TEXT", "MACRO-REPORT-TEXT", "GRAPH-REPORT-TEXT"))
    assert full.index("FUND-REPORT") < full.index("MARKET-REPORT") < full.index("GRAPH-REPORT")
    assert dd.format_prior_reports({"messages": []}) == ""                # nothing written yet
    st["market_report"] = "M" * 5000
    assert len(dd.format_prior_reports(st, only=("market",))) < 1800     # a report is cut at its own limit
    monkeypatch.setenv("RAEM_SHARE_ALL_REPORTS", "0")
    assert dd.format_prior_reports(st) == ""


class _Recorder:
    """A chat model that records what it is asked and answers with a fixed verdict."""
    def __init__(self):
        self.prompts = []

    def invoke(self, msgs):
        self.prompts.append(msgs if isinstance(msgs, str) else "\n".join(str(getattr(m, "content", m)) for m in msgs))
        return type("R", (), {"content": "ok BUY"})()


def test_debaters_facilitators_and_graph_agent_get_the_other_reports(monkeypatch):
    pytest.importorskip("langchain_core")
    from app.ai_pipeline import agents, config, fact_guard
    config.ensure_tradingagents_on_path()
    monkeypatch.setenv("RAEM_FACT_GUARD", "0")
    monkeypatch.delenv("RAEM_SHARE_ALL_REPORTS", raising=False)
    monkeypatch.setattr(agents, "_ta_utils", lambda *a, **k: {"get_language_instruction": lambda: "", "build_instrument_context": lambda *a, **k: "ctx"})
    st = _state_with_reports()
    st.update({"investment_debate_state": {"history": "", "bull_history": "", "bear_history": "", "current_response": "", "count": 0},
               "risk_debate_state": {"history": "", "aggressive_history": "", "conservative_history": "", "neutral_history": "",
                                     "latest_speaker": "", "current_aggressive_response": "", "current_conservative_response": "",
                                     "current_neutral_response": "", "count": 0},
               "trader_investment_plan": "plan", "investment_facilitator_decision": "x", "risk_facilitator_decision": "y",
               "graph_context": "peer graph says X", "graph_metrics": {"status": "OK"}})
    for factory in (agents.create_bull_researcher, agents.create_bear_researcher, agents.create_investment_facilitator,
                    agents.create_aggressive_debator, agents.create_conservative_debator, agents.create_neutral_debator,
                    agents.create_risk_facilitator):
        rec = _Recorder()
        factory(rec)(dict(st))
        p = rec.prompts[0]
        assert "MACRO-REPORT-TEXT" in p and "GRAPH-REPORT-TEXT" in p, factory.__name__
    for factory in (agents.create_investment_facilitator, agents.create_risk_facilitator):   # these two carry no reports of their own
        rec = _Recorder()
        factory(rec)(dict(st))
        assert all(t in rec.prompts[0] for t in ("FUND-REPORT-TEXT", "MARKET-REPORT-TEXT", "NEWS-REPORT-TEXT", "SENT-REPORT-TEXT")), factory.__name__
    rec = _Recorder()                                                   # the graph analyst reads the five reports before it
    out = agents.create_graph_report_agent(rec, log=lambda *a: None)(dict(st))
    assert out == {"graph_report": "ok BUY"} and all(t in rec.prompts[0] for t in ("FUND-REPORT-TEXT", "MACRO-REPORT-TEXT"))
    assert "GRAPH-REPORT-TEXT" not in rec.prompts[0]                    # it does not read its own old report
    skipped = dict(st, graph_metrics={"status": "SKIPPED"})             # non-DSE ticker: no graph, no report
    assert agents.create_graph_report_agent(_Recorder(), log=lambda *a: None)(skipped) == {}
    monkeypatch.setenv("RAEM_SHARE_ALL_REPORTS", "0")
    rec = _Recorder()
    agents.create_bull_researcher(rec)(dict(st))
    assert "MACRO-REPORT-TEXT" not in rec.prompts[0]


def test_trader_and_portfolio_manager_get_all_reports(monkeypatch):
    pytest.importorskip("langchain_core")
    from app.ai_pipeline import agents, config
    config.ensure_tradingagents_on_path()
    monkeypatch.setenv("RAEM_FACT_GUARD", "0")
    monkeypatch.delenv("RAEM_SHARE_ALL_REPORTS", raising=False)
    seen = []

    def fake_invoke(structured_llm, llm, messages, render, who):
        seen.append((who, "\n".join(str(m["content"]) for m in messages)))
        return "FINAL TRANSACTION PROPOSAL: **HOLD**"

    import tradingagents.agents.utils.structured as structured
    monkeypatch.setattr(structured, "invoke_structured_or_freetext", fake_invoke)
    monkeypatch.setattr(structured, "bind_structured", lambda llm, schema, who: None)
    st = _state_with_reports()
    st.update({"investment_plan": "plan", "trader_investment_plan": "t", "risk_facilitator_decision": "r",
               "risk_debate_state": {"history": "h"}, "investor_profile": "Aggressive", "indicators_parsed": {}, "fund_metrics": {}})
    agents.create_trader(object())(dict(st))
    agents.create_portfolio_manager(object())(dict(st))
    assert [w for w, _ in seen] == ["Trader", "PortfolioManager"]
    for who, text in seen:
        assert all(t in text for t in ("FUND-REPORT-TEXT", "MARKET-REPORT-TEXT", "NEWS-REPORT-TEXT", "SENT-REPORT-TEXT",
                                       "MACRO-REPORT-TEXT", "GRAPH-REPORT-TEXT")), who


def test_later_analysts_read_the_earlier_analysts_reports(monkeypatch):
    pytest.importorskip("langchain_core")
    from app.ai_pipeline import agents, config
    config.ensure_tradingagents_on_path()
    import tradingagents.dataflows.symbol_utils as su
    monkeypatch.setattr(su, "is_dse_ticker", lambda s: False)
    monkeypatch.delenv("RAEM_SHARE_ALL_REPORTS", raising=False)
    prompts = {}

    def fake_analyst_invoke(kind, llm, messages, company, date, log):
        prompts[kind] = "\n".join(str(getattr(m, "content", m)) for m in messages)
        return type("R", (), {"content": "report text"})()

    monkeypatch.setattr(agents, "analyst_invoke", fake_analyst_invoke)

    class Tool:
        def __init__(self, text): self.text = text
        def invoke(self, *a, **k): return self.text

    tools = {"build_instrument_context": lambda c, *a: "ctx", "get_language_instruction": lambda: "",
             "get_stock_data": Tool("Date,Open,High,Low,Close,Volume\n2026-11-24,1,2,1,2,100\n"),
             "get_indicators": Tool("2026-11-24: 50.0\n"), "get_news": Tool("- AAA news item about earnings\n"),
             "get_global_news": Tool("- global item\n"), "get_fundamentals": Tool(""), "get_balance_sheet": Tool(""),
             "get_cashflow": Tool(""), "get_income_statement": Tool("")}
    monkeypatch.setattr(agents, "_ta_utils", lambda *a, **k: tools)
    st = _state_with_reports()
    st["news_raw"] = {}
    try:
        agents.create_market_analyst(object(), ["rsi"], log=lambda *a: None)(dict(st))
    except Exception:
        pass   # only the prompt matters here
    assert "market" in prompts and "FUND-REPORT-TEXT" in prompts["market"]
    assert "MARKET-REPORT-TEXT" not in prompts["market"]                # it does not read its own old report
