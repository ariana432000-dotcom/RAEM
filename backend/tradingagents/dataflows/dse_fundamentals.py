"""
DSE (Dhaka Stock Exchange) fundamentals vendor.

Exposes the same function signatures as alpha_vantage_fundamentals.py so it
can be dropped into interface.py / config.py as a new `fundamental_data`
vendor (see the wiring notes at the bottom of this file).

Two tiers of data:
  1. Headline snapshot ratios (EPS, NAV, P/E, dividend, sponsor holding) --
     scraped from the DSE company page (dsebd.org). Works for any listed
     ticker, always available, cheap.
  2. Full statements (balance sheet / income statement / cash flow) -- DSE
     does not publish these in structured form anywhere, so they are
     extracted from the company's audited annual report PDF via
     dse_statement_extractor.py. See that file for how PDFs get onto disk.

IMPORTANT -- dsebd.org's robots.txt disallows automated crawling. This
module is meant for a personal/academic project: keep request volume low
(DSE_REQUEST_DELAY_SECONDS below), identify your bot honestly in the
User-Agent, and don't run it as a high-frequency or commercial scraper.
If you need reliable, high-volume access, that's a conversation to have
with DSE/BSEC directly rather than something to script around quietly.
"""

import logging
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from . import dse_statement_extractor as _stmt

logger = logging.getLogger(__name__)

BASE_URL = "https://www.dsebd.org/displayCompany.php"
ALT_BASE_URL = "https://dse.com.bd/displayCompany.php"
# ✅ CHANGED: no longer overriding a User-Agent here -- _throttled_get now
# goes through bdshare's own safe_get(), which uses bdshare's own session
# headers (already proven working across this site's other endpoints).
DSE_REQUEST_DELAY_SECONDS = 2.0  # be polite -- there's no public API

_last_request_ts = 0.0


def _throttled_get(url, params=None):
    """🔴 FIXED (still 403ing after the User-Agent fix -- confirmed live):
    dropping our own UA override wasn't enough by itself. bdshare ships
    its own get_company_info(), which hits this EXACT same
    displayCompany.php endpoint successfully, via its safe_get() helper --
    which retries up to 3 times with exponential back-off AND tries both
    the primary and alt host on every single attempt (up to 6 tries total
    by default), instead of the one-shot, no-retry fetch this function
    was doing. If dsebd.org's 403 here is a transient rate-limit/WAF
    flake rather than a hard, permanent IP-level block, that retry
    resilience is very plausibly the actual difference between "works"
    (bdshare's OHLCV/news calls, and bdshare's own get_company_info) and
    "doesn't" (this function, one attempt, no retry). Reusing bdshare's
    own proven safe_get() directly -- rather than reimplementing a
    thinner version of the same retry/fallback logic ourselves -- is the
    more robust fix. This only changes HOW the HTML is fetched; our own
    downstream parsing (_parse_label_value_tables) is unchanged, since we
    need label:value pairs, not bdshare's pd.read_html() table shape.
    If this STILL 403s after retries, that's strong evidence the block is
    IP-based (e.g. Railway's datacenter range flagged) rather than
    request-shape-based -- test the same code from a non-datacenter
    connection to confirm."""
    from bdshare.util.helper import BDShareError, safe_get

    global _last_request_ts
    elapsed = time.time() - _last_request_ts
    if elapsed < DSE_REQUEST_DELAY_SECONDS:
        time.sleep(DSE_REQUEST_DELAY_SECONDS - elapsed)

    try:
        resp = safe_get(
            url, params=params,
            alt_url=ALT_BASE_URL if url == BASE_URL else None,
            retries=3, pause=1.0, timeout=15,
        )
        return resp
    except BDShareError as e:
        # Re-raise as requests.RequestException so get_fundamentals()'s
        # existing `except requests.RequestException` still catches this
        # without needing its own change -- BDShareError is a plain
        # Exception subclass, not a RequestException one.
        raise requests.RequestException(str(e)) from e
    finally:
        _last_request_ts = time.time()


def _parse_label_value_tables(soup: BeautifulSoup) -> dict:
    """
    🔴 FIXED (confirmed live via the Fundamentals Check debug view):
    dsebd.org's page mixes two different row layouts. Some rows are clean
    "<td>Label</td><td>Value</td>" pairs (handled by the original logic
    below). Others -- confirmed live, e.g. a row whose two cells were
    literally "Trading Code:BEXIMCO" and "Scrip Code:99613" -- pack a
    compact "Label:Value" INSIDE each cell of a 2-column grid, so treating
    cell[0] as the label and cell[1] as the value for the whole row
    produced garbage entries like {"Trading Code:BEXIMCO": "Scrip
    Code:99613"}. This is exactly the section that holds P/E, EPS, and
    Market Capitalization, so those never made it into `fields` under any
    recognizable label. Now: a 2-cell row where NEITHER cell contains a
    colon is still treated as the classic label|value pair; otherwise,
    every cell is checked independently for its own "Label:Value" content
    and split on the first colon.

    🔴 FIXED (confirmed live via a real dsebd.org screenshot, ISLAMIBANK):
    P/E ratio and EPS live in WIDE time-series tables --
    "Particulars | Jul 28, 2026 | Jul 29, 2026 | ... | Aug 04, 2026" --
    not simple 2-cell rows, which is why they never appeared under any
    label at all before (neither branch above matches a 7-cell row).
    For rows with more than 2 cells: take the label from column 0, and
    the RIGHTMOST cell that actually has a reported number as that row's
    "current/latest" value -- skipping "-"/"n/a" placeholder columns for
    periods with no data yet. Two consequences confirmed against the real
    page: (1) a fully-dashed row (e.g. the "un-audited" P/E table, before
    the audited figures exist) contributes nothing rather than a bogus
    entry; (2) a pure-header wide row (e.g. "Earnings per share(EPS) |
    EPS - Continuing Operations | NAV Per Share | ...", where every
    "value" cell is text, not a number) also contributes nothing --
    fixing the earlier "Earnings per share(EPS): EPS - Continuing
    Operations" garbage as a side effect, since no cell there is numeric.
    """
    for tag in soup.find_all(["select", "script", "style"]):
        tag.decompose()

    time_pattern = re.compile(r"^\d{1,2}:\d{2}")
    no_value = {"-", "--", "n/a", "na", ""}

    def _latest_numeric_cell(cells):
        for cell in reversed(cells):
            c = cell.strip()
            if c.lower() in no_value:
                continue
            if any(ch.isdigit() for ch in c):
                return c
        return None

    data = {}
    for row in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        if len(cells) == 2 and ":" not in cells[0] and ":" not in cells[1]:
            data[cells[0].rstrip(":").strip()] = cells[1]
        elif len(cells) == 2:
            for cell in cells:
                # 🔴 FIXED (confirmed live, ISLAMIBANK): a plain time value
                # like "2:40 PM" also "contains a colon", so the per-cell
                # split above was misreading it as label="2", value="40 PM".
                # Skip anything that starts like a clock time.
                if ":" in cell and not time_pattern.match(cell):
                    label, _, value = cell.partition(":")
                    label, value = label.strip(), value.strip()
                    if label and value:
                        data[label] = value
        else:
            # A genuine wide row (P/E ratio, EPS across dates) always has
            # numeric or "-" cells after the label. A "Basic Info" grid
            # row instead packs a SECOND real text label into cell[2]
            # (e.g. "Face/par Value | 10.0 | Market Lot | 1") — use that
            # to tell the two shapes apart before picking a value.
            if len(cells) % 2 == 0 and cells[2] and not any(ch.isdigit() for ch in cells[2]):
                for i in range(0, len(cells), 2):
                    pair_label = cells[i].rstrip(":").strip()
                    pair_value = cells[i + 1].strip()
                    if pair_label and pair_value and pair_value.lower() not in no_value:
                        data[pair_label] = pair_value
                continue

            label = cells[0].rstrip(":").strip()
            value = _latest_numeric_cell(cells[1:])
            if not (label and value):
                continue
            data[label] = value
            low = label.lower()
            if low.startswith("basic"):
                data["EPS (Basic)"] = value
            elif low.startswith("diluted"):
                data["EPS (Diluted)"] = value
    return data


def _fetch_fields(ticker: str) -> tuple[dict | None, str | None]:
    """Fetch + parse the DSE snapshot page into the full, UNFILTERED
    {label: value} dict. Shared by get_fundamentals() (which applies the
    wanted_keywords filter on top) and quick_check() (which needs the
    complete dict, not just the filtered subset, to show what dsebd.org's
    page actually contains when diagnosing a parsing gap). Returns
    (fields, None) on success or (None, error_message) on failure."""
    try:
        resp = _throttled_get(BASE_URL, params={"name": ticker})
    except requests.RequestException as e:
        logger.warning("DSE fundamentals fetch failed for %s: %s", ticker, e)
        return None, f"DSE fundamentals unavailable for {ticker}: {e}"

    soup = BeautifulSoup(resp.text, "html.parser")
    fields = _parse_label_value_tables(soup)
    if not fields:
        return None, (
            f"No fundamentals table found for {ticker} on DSE. The page "
            f"markup may have changed -- inspect {BASE_URL}?name={ticker} "
            f"manually (view-source) and update _parse_label_value_tables."
        )
    return fields, None


def get_fundamentals(ticker: str, curr_date: str = None) -> str:
    """
    Scrape the DSE company snapshot page for headline fundamentals: EPS,
    NAV per share, P/E, sponsor/director/govt/institute/foreign/public
    holding %, market cap, last dividend, etc.
    """
    fields, error = _fetch_fields(ticker)
    if error:
        return error

    wanted_keywords = [
        "last trading price", "closing price", "face", "market category",
        "sector", "pe(x)", "p/e", "price earning", "eps", "nav per share",
        "nav(per share)", "market capitalization", "market cap",
        "dividend", "sponsor", "govt", "institute", "foreign", "public",
        "trading code", "scrip code",
    ]
    lines = [f"DSE fundamentals snapshot -- {ticker} (as of {curr_date or datetime.now().date()})"]
    for key, value in fields.items():
        if any(w in key.lower() for w in wanted_keywords):
            lines.append(f"{key}: {value}")
    if len(lines) == 1:
        # keyword filter matched nothing -- dump everything rather than
        # silently return an empty-looking report
        lines += [f"{k}: {v}" for k, v in fields.items()]
    return "\n".join(lines)


def quick_check(ticker: str, curr_date: str = None) -> dict:
    """Standalone sanity-check for the UI's "Fundamentals Check" page/route
    (see app/api/routes/stocks.py's /fundamentals/check and
    app/services/market_data.py's get_fundamentals_check). Calls
    get_fundamentals() directly -- one HTTP fetch, no LLM call, no other
    analysts, no LangGraph pipeline -- so you can verify a scrape/parsing
    fix in a couple seconds instead of waiting through a full multi-agent
    AI Analysis run.

    Mirrors the keyword-matching g_dse() logic in
    app/pipeline/agents.py's create_fundamentals_analyst. Kept as a small,
    independent copy rather than a shared import: this needs to keep
    working even if that function's internals change shape, and the two
    call sites have different failure-handling needs (this one returns a
    structured status for a UI card; that one silently falls back to
    "N/A" per-field for an LLM prompt).
    """
    raw = get_fundamentals(ticker, curr_date)
    all_fields, _fetch_error = _fetch_fields(ticker)

    def _find(keywords, exclude=()):
        for line in raw.split("\n"):
            low = line.lower()
            if any(k in low for k in keywords) and not any(e in low for e in exclude):
                import re
                m = re.search(r"(-?[\d,]+\.?\d*)", line.split(":", 1)[-1])
                if m:
                    return m.group(1).replace(",", "")
        return None

    parsed = {
        "pe_ratio": _find(["pe(x)", "p/e"]),
        "eps": _find(["eps"], exclude=("change", "p/e", "ratio")),
        "market_cap": _find(["market capitalization", "market cap"]),
        "dividend_yield": _find(["dividend"]),
    }

    if raw.startswith("DSE fundamentals snapshot"):
        ok, status = True, "OK -- live snapshot fetched from dsebd.org successfully."
    elif raw.startswith("DSE fundamentals unavailable"):
        ok, status = False, ("FAILED -- the request to dsebd.org/dse.com.bd itself errored "
                              "(network, cert, or a block like 403). See raw_response below.")
    elif raw.startswith("No fundamentals table found"):
        ok, status = False, ("FAILED -- the page loaded but didn't parse into label:value rows "
                              "(markup may have changed, or dsebd.org served something other "
                              "than the real company page).")
    else:
        ok, status = False, "UNKNOWN -- unexpected response shape, see raw_response below."

    return {
        "ticker": ticker.upper(),
        "ok": ok,
        "status": status,
        "parsed": parsed,
        # ✅ CHANGED: previously only the wanted_keywords-filtered text was
        # exposed, which is exactly the subset that WASN'T matching for
        # P/E ratio and Market Cap -- there was no way to see what
        # dsebd.org actually calls those fields without live access to
        # the page. This is the COMPLETE unfiltered {label: value} dict
        # (every <tr> on the page, not just the keyword-matched ones), so
        # the real label wording is visible directly in the UI.
        "all_fields": all_fields or {},
        "raw_response": raw[:500],
    }


def get_balance_sheet(ticker: str, freq: str = "annual", curr_date: str = None):
    """Full balance sheet, extracted from the audited annual report PDF."""
    return _stmt.get_statement(ticker, "balance_sheet", freq=freq, curr_date=curr_date)


def get_cashflow(ticker: str, freq: str = "annual", curr_date: str = None):
    """Full cash flow statement, extracted from the audited annual report PDF."""
    return _stmt.get_statement(ticker, "cashflow", freq=freq, curr_date=curr_date)


def get_income_statement(ticker: str, freq: str = "annual", curr_date: str = None):
    """Full income statement, extracted from the audited annual report PDF."""
    return _stmt.get_statement(ticker, "income_statement", freq=freq, curr_date=curr_date)


# ---------------------------------------------------------------------------
# Wiring into your existing vendor architecture (interface.py / config.py):
#
#   from .dse_fundamentals import (
#       get_balance_sheet as get_dse_balance_sheet,
#       get_cashflow as get_dse_cashflow,
#       get_fundamentals as get_dse_fundamentals,
#       get_income_statement as get_dse_income_statement,
#   )
#
# Then add "dse" alongside "alpha_vantage" / "yfinance" wherever
# route_to_vendor dispatches on config["data_vendors"]["fundamental_data"],
# and set that config value to "dse" for BD tickers (e.g. via your
# symbol_utils.py ticker normalization -- DSE trading codes like
# "SQURPHARMA" or "GP" won't match a US ticker pattern, so you can branch
# on that to pick the vendor automatically).
# ---------------------------------------------------------------------------
