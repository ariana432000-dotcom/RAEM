# DSE (Dhaka Stock Exchange) data vendor -- for TradingAgents / RAEM

Drop-in vendor modules that follow the same interface as your existing
alpha_vantage_fundamentals.py / yfinance_news.py, so they plug into
route_to_vendor() with minimal changes.

## Files

dataflows/dse_fundamentals.py
    get_fundamentals()      -- headline ratios scraped from dsebd.org
                                (EPS, NAV, P/E, sponsor holding, dividend)
    get_balance_sheet()
    get_cashflow()
    get_income_statement()  -- these three call dse_statement_extractor
                                to pull full statements out of annual
                                report PDFs via pdfplumber + an LLM call

dataflows/dse_statement_extractor.py
    The PDF -> structured JSON pipeline behind the three functions above.
    Reads PDFs from data/dse_reports/<TICKER>/<FISCAL_YEAR>.pdf (you
    place these yourself), extracts statement pages, asks Claude to
    return Alpha-Vantage-shaped JSON, and caches the result to
    data/dse_reports_cache/<TICKER>.json so the slow step only runs once
    per ticker/fiscal year.

dataflows/dse_news.py
    get_news(ticker, start_date, end_date)
    get_global_news(curr_date, look_back_days, limit)
    Combines dsebd.org's Extended News Search (news_archive.php) with
    sharenews24.com headlines.

tools/discover_stocknow_api.py
    Standalone Playwright script -- run it locally (not part of the
    pipeline) to find stocknow.com.bd's internal JSON API without needing
    browser DevTools. See its docstring for usage.

## Install

    pip install requests beautifulsoup4 pdfplumber anthropic playwright --break-system-packages
    playwright install chromium   # only needed for tools/discover_stocknow_api.py

Set ANTHROPIC_API_KEY in your environment for dse_statement_extractor.py.

## Wiring into interface.py / config.py

In interface.py, alongside the existing alpha_vantage / yfinance imports:

    from .dse_fundamentals import (
        get_balance_sheet as get_dse_balance_sheet,
        get_cashflow as get_dse_cashflow,
        get_fundamentals as get_dse_fundamentals,
        get_income_statement as get_dse_income_statement,
    )
    from .dse_news import (
        get_news as get_dse_news,
        get_global_news as get_dse_global_news,
    )

Then register "dse" as a fundamental_data / news_data vendor option
wherever route_to_vendor dispatches on
config["data_vendors"][...], and pick it automatically for BD tickers
(e.g. branch in symbol_utils.py -- DSE trading codes like "SQURPHARMA" or
"GP" won't match a US ticker pattern).

## Known gaps / things to verify before trusting this at scale

1. dsebd.org's robots.txt disallows automated crawling. Both
   dse_fundamentals.py and dse_news.py throttle requests
   (DSE_REQUEST_DELAY_SECONDS) and identify their User-Agent honestly --
   this is meant for a personal/academic project, not a high-frequency or
   commercial scraper. If you need reliable high-volume access, that's
   worth raising with DSE/BSEC directly.

2. I could not fetch dsebd.org's live pages myself (robots block), so
   the HTML parsing in both dse_fundamentals.py and dse_news.py
   (_parse_label_value_tables, the news_archive.php param names, the
   news row detection) is a best-effort guess at the page structure, not
   verified against the live markup. Inspect the real pages
   (view-source / browser Inspect) and adjust selectors/param names if
   results come back empty or noisy.

3. sharenews24.com scraping in dse_news.py grabs every <a> tag on the
   homepage and filters by length/keyword -- functional but noisy. Worth
   tightening once you see real output (e.g. scope to a specific
   container element).

4. Full statement extraction (dse_statement_extractor.py) depends on you
   sourcing the annual report PDFs yourself -- there's no bulk download
   endpoint. BD companies often use a July-June fiscal year and report
   figures in thousands/lakhs/crores inconsistently; spot-check the
   normalized numbers against the source PDF before trusting them at
   scale.

5. stocknow.com.bd is JS-rendered (no plain-HTML scraping possible).
   Use tools/discover_stocknow_api.py to find its internal API, or fall
   back to rendering the page with Playwright directly.
