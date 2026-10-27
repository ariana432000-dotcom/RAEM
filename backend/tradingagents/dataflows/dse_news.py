"""
DSE news vendor -- combines two sources:

  1. dsebd.org's news archive, via `bdshare.get_all_news` /
     `bdshare.get_price_sensitive_news` -- per-ticker corporate disclosures
     (EPS, NAV, dividend, AGM, etc.). Uses the maintained `bdshare` library
     (see dse_stock_data.py's docstring for why) instead of the hand-rolled
     scraper this file originally shipped with.

  2. sharenews24.com -- general Bangladeshi share-market news (market
     commentary, macro, IPO news). Plain server-rendered HTML, no JS
     rendering needed, not covered by bdshare.

Matches the get_news / get_global_news contract from news_data_tools.py, so
this registers as a "news_data" vendor exactly like yfinance_news.py.

✅ FIXED: this module used to duplicate (and not fix) several bugs that were
found and fixed in app/pipeline/market_data.py's `_fetch_dse_news` for the
watchlist feature. Since `route_to_vendor` auto-forces every DSE ticker to
THIS module for get_news/get_global_news with no fallback vendor, those bugs
were live in the main analysis pipeline the whole time:
  - sharenews24 was matched against the raw ticker code ("SQURPHARMA"),
    which never appears in a real headline (only "Square Pharmaceuticals"
    does) -- silently matched nothing, ever.
  - relatedly, even headline-only company-name matching is insufficient on
    this site: a large share of its coverage is "roundup" articles (Top 10
    gainers, "Four companies gave the index confidence today") where the
    headline names no company at all, and the company names only appear in
    body paragraphs -- confirmed live, see market_data.py's
    _load_sharenews24_articles docstring for the specific example.
  - get_price_sensitive_news(code=ticker) has no client-side re-check, but
    DSE's server ignores the code= filter for that endpoint (confirmed:
    an ABBANK request returned EXCH/EIL/IPDC items) -- so a ticker's "news"
    silently included other companies' disclosures.
  - no per-call cap -- get_price_sensitive_news() has been observed
    returning ~492 rows in one response, all of which got dumped unbounded
    into the LLM prompt.
  - no throttling around bdshare/dsebd.org calls, risking the same
    "Connection pool is full" issue fixed elsewhere via a semaphore.

Rather than re-fix (and risk re-diverging from) the same logic twice, this
module now reuses the already-fixed helpers directly from
app.ai_pipeline.market_data: company-name resolution (incl. Bengali variants),
the cached full-article-body sharenews24 store, the matching logic, and the
concurrency semaphore. (tradingagents already depends on app.pipeline
elsewhere -- see symbol_utils.py's `_llm_guess_dse_ticker`, which imports
from app.ai_pipeline.llm -- so this follows the same, already established,
layering.)

Install: pip install bdshare beautifulsoup4 requests
"""

import logging
import re

import pandas as pd
from bdshare import BDShareError, get_all_news, get_price_sensitive_news

from app.ai_pipeline.market_data import (
    _DSEBD_CONCURRENCY,
    _company_name_variants,
    _load_dse_company_names,
    _load_sharenews24_articles,
    _title_matches_company,
    _DSE_COMPANY_NAMES_BENGALI_FALLBACK,
)

logger = logging.getLogger(__name__)

DEFAULT_ARTICLE_LIMIT = 20

# ✅ FIXED: matches app/pipeline/market_data.py's PER_WEBSITE_CAP -- caps
# each bdshare-sourced feed so a single 492-row response can't flood the
# whole prompt.
_PER_SOURCE_CAP = 20


def _row_to_line(row: pd.Series) -> str:
    """bdshare's news rows carry varying columns depending on the source
    page (date/symbol/headline/details); print whatever is present rather
    than assuming a fixed schema."""
    parts = [str(v) for v in row.values if pd.notna(v) and str(v).strip()]
    return " | ".join(parts)


def _row_matches_ticker(row: pd.Series, ticker: str) -> bool:
    """✅ FIXED (cross-ticker contamination): same alnum-boundary check used
    in market_data.py's _add_rows, needed because DSE's server ignores the
    code= filter for get_price_sensitive_news."""
    text = " | ".join(str(v) for v in row.values if pd.notna(v) and str(v).strip()).upper()
    pattern = re.compile(rf"(?<![A-Z0-9]){re.escape(ticker.upper())}(?![A-Z0-9])")
    return bool(pattern.search(text))


def _fetch_dsebd_news(ticker: str = None, start_date: str = None, end_date: str = None) -> list[str]:
    try:
        with _DSEBD_CONCURRENCY:
            df = get_all_news(start=start_date, end=end_date, code=ticker)
    except BDShareError as e:
        logger.info("bdshare get_all_news returned nothing for %s: %s", ticker, e)
        return []
    except Exception as e:
        logger.warning("bdshare get_all_news failed for %s: %s", ticker, e)
        return []
    return [_row_to_line(row) for _, row in df.iterrows()][:_PER_SOURCE_CAP]


def _fetch_price_sensitive_news(ticker: str = None) -> list[str]:
    try:
        with _DSEBD_CONCURRENCY:
            df = get_price_sensitive_news(code=ticker)
    except BDShareError as e:
        logger.info("bdshare get_price_sensitive_news returned nothing for %s: %s", ticker, e)
        return []
    except Exception as e:
        logger.warning("bdshare get_price_sensitive_news failed for %s: %s", ticker, e)
        return []
    # ✅ FIXED: DSE's server ignores code= for this endpoint, so re-check
    # every row client-side before trusting it belongs to `ticker`.
    if ticker:
        df = df[df.apply(lambda row: _row_matches_ticker(row, ticker), axis=1)]
    return [_row_to_line(row) for _, row in df.iterrows()][:_PER_SOURCE_CAP]


def _fetch_sharenews24(ticker: str = None, limit: int = DEFAULT_ARTICLE_LIMIT) -> list[dict]:
    """
    ✅ FIXED (see module docstring): now pulls from the cached, category-page
    -scoped, full-article-body store in market_data.py instead of scraping
    the noisy homepage and matching headline text alone. `ticker` is
    resolved to its company name (+ Bengali variants) the same way
    market_data.py does; when `ticker` is None (get_global_news' front-page
    pull), the most recent cached articles are returned unfiltered.
    """
    try:
        articles = _load_sharenews24_articles()
    except Exception as e:  # noqa: BLE001
        logger.warning("sharenews24 fetch failed: %s", e)
        return []

    if ticker is None:
        return [{"text": a["title"], "link": a["url"]} for a in articles[:limit]]

    company_name = _load_dse_company_names().get(ticker.upper())
    if not company_name:
        logger.info("sharenews24: no company name mapped for %s, skipping ticker filter", ticker)
        return []
    variants = _company_name_variants(company_name) + _DSE_COMPANY_NAMES_BENGALI_FALLBACK.get(ticker.upper(), [])

    matches = []
    for art in articles:
        haystack = f"{art['title']} {art['text']}"
        if not _title_matches_company(haystack, variants):
            continue
        matches.append({"text": art["title"], "link": art["url"]})
        if len(matches) >= limit:
            break
    return matches


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Per-ticker news: bdshare's dsebd disclosures in the date range +
    price-sensitive news + best-effort sharenews24 headline matches."""
    dsebd_lines = _fetch_dsebd_news(ticker=ticker, start_date=start_date, end_date=end_date)
    sensitive_lines = _fetch_price_sensitive_news(ticker=ticker)
    sharenews_articles = _fetch_sharenews24(ticker=ticker)

    if not dsebd_lines and not sensitive_lines and not sharenews_articles:
        return f"No DSE news found for {ticker} between {start_date} and {end_date}."

    lines = [f"DSE news for {ticker} ({start_date} to {end_date}):"]
    lines += [f"[dsebd.org] {line}" for line in dsebd_lines]
    lines += [f"[dsebd.org price-sensitive] {line}" for line in sensitive_lines]
    lines += [f"[sharenews24.com] {a['text']} ({a['link']})" for a in sharenews_articles]
    return "\n".join(lines)


def get_global_news(curr_date: str, look_back_days: int = None, limit: int = None) -> str:
    """General DSE/market news: bdshare's all-market disclosures + recent
    price-sensitive news + sharenews24 front-page headlines, no ticker filter."""
    from datetime import datetime, timedelta

    look_back_days = look_back_days or 7
    limit = limit or DEFAULT_ARTICLE_LIMIT
    start_date = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days)).strftime("%Y-%m-%d")

    dsebd_lines = _fetch_dsebd_news(start_date=start_date, end_date=curr_date)[:limit]
    sensitive_lines = _fetch_price_sensitive_news()[:limit]
    sharenews_articles = _fetch_sharenews24(limit=limit)

    lines = [f"DSE market news (last {look_back_days} days as of {curr_date}):"]
    lines += [f"[dsebd.org] {line}" for line in dsebd_lines]
    lines += [f"[dsebd.org price-sensitive] {line}" for line in sensitive_lines]
    lines += [f"[sharenews24.com] {a['text']} ({a['link']})" for a in sharenews_articles]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Wiring into interface.py (see dse_fundamentals.py's comment for the pattern):
#   from .dse_news import get_news as get_dse_news, get_global_news as get_dse_global_news
# Register "dse" as a news_data vendor option alongside yfinance/alpha_vantage.
# ---------------------------------------------------------------------------
