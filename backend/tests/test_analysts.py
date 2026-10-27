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
