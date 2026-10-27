# raem_analysts/dse/news_analyst.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
%pip install ddgs duckduckgo_search -q

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
# get_news / get_global_news now come from the DSE Data Source Layer cell
# above (global names) -- best-effort scrapes of DSE's public notice pages,
# not Finnhub. DSE's own feed is thin (only exchange-mandated disclosures --
# PSI, corporate actions -- not broader financial-press coverage). Sites
# like amarstock.com and stocknow.com.bd carry that broader coverage but
# aren't indexed by web search, so their real page structure couldn't be
# verified without a live run against them (untestable from this sandbox).
# Instead of guessing blind at one site's HTML, this uses general web
# search (ddgs / DuckDuckGo, no API key) below, which naturally surfaces
# whatever Bangladeshi financial press actually covers the company (The
# Financial Express, The Daily Star, ShareNews24, etc. -- all confirmed
# indexed, unlike DSE/amarstock).

llm_news = ChatOllama(model="qwen2.5:7b", base_url="http://localhost:11434", num_ctx=8192, temperature=0, num_predict=_cap('news') or 2048, repeat_penalty=1.15)


@@SLOT:news_analyst.1@@
    """General web search for real news coverage of a DSE company via
    ddgs (DuckDuckGo) -- a supplement to DSE's own thin notice feed.
    UNVERIFIED from this sandbox (no network to duckduckgo.com here), so
    this is defensive: any failure (import, rate limit, empty results)
    falls back to an explicit not-available string with the raw error
    printed for diagnosis, exactly like the DSE-side tools above."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
    except ImportError as e:
        print(f"   [DEBUG] Neither ddgs nor duckduckgo_search importable: {e}")
        return "Error: web search library not available."

    query = f"{company_hint} {ticker} Bangladesh DSE news"
    try:
        with DDGS() as ddgs:
            # NEWS endpoint first: dated press articles (Daily Star, FE, TBS ...) instead of quote/directory pages.
            results, _seen_href = [], set()
            for _q in (f'"{company_hint}"', f"{ticker} DSE {company_hint.split()[0]} Bangladesh"):
                try:
                    for _r in ddgs.news(_q, timelimit="m", max_results=max_results):
                        _h = _r.get("url") or _r.get("href", "")
                        if _h and _h not in _seen_href:
                            _seen_href.add(_h)
                            results.append({"title": _r.get("title", ""), "body": _r.get("body", ""), "href": _h,
                                            "date": _r.get("date", "")})
                except Exception as _ne:
                    print(f"   [DEBUG] ddgs.news failed for '{_q}': {str(_ne)[:160]}")
            if not results:                       # fall back to the general text search (old behaviour)
                print("   [DEBUG] ddgs.news returned nothing -> falling back to ddgs.text")
                results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        print(f"   [DEBUG] ddgs web search failed for query '{query}': {str(e)[:250]}")
        return f"Error: web search failed ({str(e)[:150]})"

    if not results:
        print(f"   [DEBUG] ddgs web search returned zero results for '{query}'")
        return "No web search results found."

    # Source hygiene (audit of a real run: price/directory pages were summarised as 'news'). Directory / quote /
    # price-tracker pages are dropped; the rest keeps the date WRITTEN IN THE SNIPPET ("2 days ago", "9th Sep 2026")
    # as a [date] prefix, or "[no date]" when the snippet has none.
    _dir_dom = re.compile(r"amarstock|stocknow|stockanalysis|dseanalyst|investing\.com|tradingview|marketscreener|morningstar|"
                          r"companiesmarketcap|dsebd\.org|dse\.com\.bd|wikipedia|linkedin|facebook|youtube", re.I)
    _dir_txt = re.compile(r"stock (?:price|quote|analysis)|share price|live (?:dsex|price|data)|company (?:report|profile|overview)|"
                          r"historical data|price (?:chart|history)|\bscore \d+/100\b|are available (?:on|at)", re.I)
    lines, dropped = [], []
    for r in results:
        title = r.get("title", "")
        body = (r.get("body", "") or "")[:200]
        href = r.get("href", "")
        if _dir_dom.search(href) or _dir_txt.search(title + " " + body):
            dropped.append(f"{title[:70]} ({href[:60]})")
            continue
        dom = re.sub(r"^https?://(?:www\.)?", "", href).split("/")[0]
        _dm = re.search(r"\b(\d+\s+(?:minutes?|hours?|days?|weeks?|months?)\s+ago|\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]{2,8}\.?\s+\d{4}|[A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4})\b", title + " " + (r.get("body", "") or ""))
        _nd = str(r.get("date", "") or "")[:10]            # ISO date from the news endpoint
        _date = _nd if re.match(r"\d{4}-\d{2}-\d{2}$", _nd) else (_dm.group(1) if _dm else "no date")
        lines.append(f"- [{_date}] {title}: {body} ({dom})")
    for d in dropped:
        print(f"   [DEBUG] web item dropped (directory/price page): {d}")
    for ln in lines:
        print(f"   [RAW web snippet kept] {ln[:220]}")
    if not lines:
        return "No dated company-news coverage found (only directory/price pages, which are excluded)."
    return "\n".join(lines)


# ── Sector / regulator news (tobacco tax, NBR, budget): NOT passed to the news analyst prompt ──
# Stored in the global SECTOR_WEB_NEWS and ingested by the news-RAG cell as scope="sector"
# (company tag DSE_SECTOR), where the news graph links it to the company through topics.
SECTOR_NEWS_QUERIES = {
    "BATBC": ["Bangladesh cigarette tobacco tax NBR", "Bangladesh budget supplementary duty cigarette price"],
    "ABBANK": ["Bangladesh Bank policy rate bank interest rate", "Bangladesh banking sector non-performing loan NPL"],
    "BPML": ["Bangladesh paper industry newsprint import duty", "Bangladesh budget duty paper pulp price"],
    "WALTONHIL": ["Bangladesh electronics refrigerator import duty", "Bangladesh budget duty home appliance price"],
}
_fetch_web_news_company_only = fetch_web_news_for_company


def fetch_sector_news(ticker: str, max_results: int = 6) -> str:
    qs = SECTOR_NEWS_QUERIES.get(ticker, [])
    if not qs:
        return ""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
    except ImportError:
        return ""
    _dir = re.compile(r"amarstock|stocknow|stockanalysis|dseanalyst|investing\.com|tradingview|marketscreener|morningstar|"
                      r"companiesmarketcap|dsebd\.org|dse\.com\.bd|wikipedia|linkedin|facebook|youtube", re.I)
    out, seen = [], set()
    try:
        with DDGS() as ddgs:
            for q in qs:
                try:
                    for r in ddgs.news(q, timelimit="m", max_results=max_results):
                        href = r.get("url") or r.get("href", "")
                        if not href or href in seen or _dir.search(href):
                            continue
                        seen.add(href)
                        d = str(r.get("date", "") or "")[:10]
                        if not re.match(r"\d{4}-\d{2}-\d{2}$", d):
                            continue                               # undated sector items cannot be point-in-time filtered
                        dom = re.sub(r"^https?://(?:www\.)?", "", href).split("/")[0]
                        out.append(f"- [{d}] {r.get('title', '')}: {(r.get('body', '') or '')[:200]} ({dom})")
                except Exception as e:
                    print(f"   [DEBUG] sector news query failed '{q}': {str(e)[:140]}")
    except Exception as e:
        print(f"   [DEBUG] sector news search failed: {str(e)[:140]}")
    for ln in out:
        print(f"   [sector news kept] {ln[:200]}")
    return "\n".join(out)


def fetch_web_news_for_company(*args, **kwargs):
    """Company web news (unchanged interface for the news analyst) + side-effect: fills SECTOR_WEB_NEWS."""
    res = _fetch_web_news_company_only(*args, **kwargs)
    try:
        tk = args[0] if args else kwargs.get("ticker", "")
        globals()["SECTOR_WEB_NEWS"] = fetch_sector_news(tk)
    except Exception as e:
        print(f"   [DEBUG] sector news skipped: {str(e)[:120]}")
        globals()["SECTOR_WEB_NEWS"] = ""
    return res


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        company = state["company_of_interest"]
        asset_type = state.get("asset_type", "stock")
        instrument_context = resolve_instrument_context(
            company, build_instrument_context(company, asset_type)
        )

        print(f"📰 Fetching news for {company} on {current_date}...")
        # untruncated copies for the RAG corpus (the prompt copies below stay capped)
        company_news_full = global_news_full = web_news_full = ""

        # ── Step 1: get_news ──
        print("⚙️  Calling: get_news")
        try:
            from datetime import datetime, timedelta
            end_dt = datetime.strptime(current_date, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=7)
            company_news = get_news.invoke({
                "ticker": company,
                "start_date": start_dt.strftime("%Y-%m-%d"),
                "end_date": current_date,
            })
            company_news_full = str(company_news)
            company_news = company_news_full[:3000]
            print(f"   ✅ Got {len(company_news)} chars")
        except Exception as e:
            company_news = f"Error: {str(e)}"
            print(f"   ❌ {e}")

        # ── Step 2: get_global_news ──
        print("⚙️  Calling: get_global_news")
        try:
            global_news = get_global_news.invoke({"curr_date": current_date})
            global_news_full = str(global_news)
            global_news = global_news_full[:2000]
            print(f"   ✅ Got {len(global_news)} chars")
        except Exception as e:
            global_news = f"Error: {str(e)}"
            print(f"   ❌ {e}")

        # ── Step 2b: web-search fallback for broader company coverage ──
        print("⚙️  Calling: fetch_web_news_for_company (web search fallback)")
        try:
            web_news = fetch_web_news_for_company(company)
            web_news_full = str(web_news)
            web_news = web_news_full[:2500]
            print(f"   ✅ Got {len(web_news)} chars")
        except Exception as e:
            web_news = f"Error: {str(e)}"
            print(f"   ❌ {e}")

        # ── Step 3: LLM writes the report WITH enforced table ──
        prompt = f"""You are a news analyst. Today is {current_date}.
{instrument_context}

Using the REAL news data below, write a brief news analysis report for {company}.
Sections: Company News Summary → Web-Sourced Company Coverage → DSE Market-Wide Notices → Sentiment Assessment → Key Risks
{_len_rule('news')}

RULE: Each web item starts with the date written in its snippet, e.g. [2 days ago] or [no date]. Use that date; call a
[no date] item "undated" and never "recent". Copy numbers EXACTLY as written in the snippet; add no detail the snippet does not contain.

RULE: "Web-Sourced Company Coverage" comes from general web search (financial press
articles), separate from DSE's own disclosure feed above it -- treat it as a supplement,
not a replacement, and if it says no results were found, say so plainly rather than
inventing coverage.

RULE: The second section is DSE's own exchange-wide notices, NOT international/global
macro news -- DSE has no general financial news feed, so do not present this as if it
covers global markets, the Fed, or other countries. If it's thin, say so plainly.

RULE: If any section below starts with "Error", that data source was UNAVAILABLE (a failed request) -- say "data unavailable" for it; do NOT treat it as "no news" and do NOT infer a neutral or positive sentiment from it.

RULE: If COMPANY NEWS above says no news/disclosures were found for {company}, say that
plainly in the Company News Summary -- do not invent items or reframe unrelated items as
if they were about {company}.

RULE (table rows): A DSE Market-Wide Notice about a DIFFERENT specific company or fund
(a director's share transaction, another company's NAV, another company's dividend, etc.)
is NOT news about {company} and must NOT appear in the final table, even labeled NEUTRAL --
including it implies relevance it doesn't have. Only include a market-wide item in the
table if it is genuinely exchange-wide (overall market turnover, a circuit-breaker or
trading-halt policy change, a market-wide holiday) rather than about one specific other
company. Do NOT pad the table to reach 4-6 rows: if fewer than 4 rows are genuinely
relevant to {company}, output only those rows, and if none are relevant, output a table
with a single row stating "No company-specific or market-wide news relevant to {company}"
in the Headline column and NEUTRAL elsewhere -- never substitute other companies' notices
as filler.

At the very end, output a Markdown table with EXACTLY these 5 columns:
| Category | Headline | Sentiment | Impact | Source |
(Sentiment must be POSITIVE / NEGATIVE / NEUTRAL. Row count depends on actual relevance --
see rule above, not a fixed target.)

=== COMPANY NEWS ({company}) ===
{company_news}

=== WEB-SOURCED COMPANY COVERAGE (general web search, see rule above) ===
{web_news}

=== DSE MARKET-WIDE NOTICES (NOT global macro news -- see rule above) ===
{global_news}
""" + get_language_instruction()

        messages = [
            SystemMessage(content="You are a senior news analyst. Write reports based only on provided data. Always end with the required Markdown table."),
            HumanMessage(content=prompt),
        ]

        response = cached_analyst_invoke("news", llm, messages)
        report = response.content
        print("✅ News Report ready!")

        # ── Step 4: Parse sentiment counts ──
        import re
        sentiments = re.findall(r"\|\s*\w.*?\|\s*(POSITIVE|NEGATIVE|NEUTRAL)\s*\|", report, re.IGNORECASE)
        pos = sum(1 for s in sentiments if "POSITIVE" in s.upper())
        neg = sum(1 for s in sentiments if "NEGATIVE" in s.upper())
        neu = sum(1 for s in sentiments if "NEUTRAL" in s.upper())
        overall_m = re.search(r"(POSITIVE|NEGATIVE|NEUTRAL|BULLISH|BEARISH)", report, re.IGNORECASE)
        overall = overall_m.group(1).upper() if overall_m else "NEUTRAL"

        # ── Extract markdown table from report ──
        tbl_m = re.search(
            r"(\|.*?Category.*?\|.*?(?:\n\|[-| ]+\|)(?:\n\|.*?\|)+)",
            report, re.IGNORECASE | re.DOTALL
        )
        news_table_md = tbl_m.group(1) if tbl_m else ""

        # ✅ Structured metrics dict
        news_metrics = {
            "positive_count":     pos,
            "negative_count":     neg,
            "neutral_count":      neu,
            "overall_sentiment":  overall,
            "company_news_chars": len(company_news),
            "web_news_chars":     len(web_news),
            "global_news_chars":  len(global_news),
        }

        all_msgs = list(state.get("messages", [])) + messages + [AIMessage(content=report)]
        return {
            "messages":        all_msgs,
            "news_report":     report,
            "news_metrics":    news_metrics,
            "news_table_md":   news_table_md,
            "news_raw_full": {
                "company_news": company_news_full,
                "web_news":     web_news_full,
                "global_news":  global_news_full,
            },
            "news_raw": {
                "company_news": company_news,
                "web_news":     web_news,
                "global_news":  global_news,
            },
        }

    return news_analyst_node


news_analyst = create_news_analyst(llm_news)

news_state = {
    "trade_date": TRADE_DATE,
    "company_of_interest": "@@TICKER@@",
    "asset_type": "stock",
    "messages": [],
}

result = news_analyst(news_state)

# ✅ FIXED: structured dict log
log_step(
    "News Analyst",
    {
        "company":    news_state["company_of_interest"],
        "date":       news_state["trade_date"],
        "tools_used": "get_news, get_global_news",
    },
    {
        "news_metrics":  result.get("news_metrics", {}),
        "news_table_md": result.get("news_table_md", ""),
        "report":        result.get("news_report", ""),
    }
)

news_result = result
from IPython.display import Markdown
Markdown(result["news_report"])
