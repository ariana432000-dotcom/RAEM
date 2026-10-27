# raem_analysts/dse/dse_data_source_layer.py -- shared by abbank-dse, batbc-dse, bpml-dse, waltonhil-dse
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# DSE (Dhaka Stock Exchange) DATA SOURCE LAYER  -- v2 (bug-fixed)
# ================================================================
# v2 fixes, found by actually testing dsebd.org's real responses:
#   1) day_end_archive.php was being called ONCE PER TRADING DAY (a 120-day
#      lookback meant ~85 requests, x5 for each indicator = 400+ requests
#      per Market Analyst run). DSE's server was rate-limiting/soft-blocking
#      that volume, which looked like a "rejected" request. The endpoint
#      actually accepts a full [start_date, end_date] range in ONE request
#      -- fixed to fetch once per (symbol, start, end) and cache it.
#   2) Column names were wrong: the real table uses "OPENP*" / "CLOSEP*",
#      not "OPENING PRICE*" / "CLOSING PRICE*" -- silently producing 0.00
#      for every Open/Close price.
#   3) get_news / get_global_news pointed at a URL that does not exist
#      (latest_news.php -> 404). The real endpoint is old_news.php with
#      startDate/endDate/inst/criteria=4/archive=data -- fixed, and now
#      parsed with BeautifulSoup instead of guessing at pandas.read_html.
#   4) get_fundamentals pointed at the right page (displayCompany.php) but
#      the two-column "key: value" parsing assumption was wrong -- DSE's
#      table has TWO label/value pairs per row (4 cells), so half the
#      fields were silently dropped. Rewritten with BeautifulSoup th/td
#      pairing. Note: DSE's public company page genuinely does not publish
#      P/E, EPS, revenue, beta or dividend yield in structured form -- those
#      stay "N/A" because the data isn't there, not because of a bug.

%pip install beautifulsoup4 lxml -q

import re as _dse_re
import time as _dse_time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from langchain_core.tools import tool

DSE_BASE_URL = "https://old.dsebd.org"
DSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    # browser-like headers: some servers answer bare scripted requests with 4xx
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.dsebd.org/",
    "Upgrade-Insecure-Requests": "1",
}
DSE_MIN_REQUEST_INTERVAL = 1.0  # seconds between requests -- be polite, avoid rate-limiting
DSE_MAX_RETRIES = 3

import urllib3 as _dse_urllib3
_dse_urllib3.disable_warnings(_dse_urllib3.exceptions.InsecureRequestWarning)

_dse_session = requests.Session()
_dse_session.headers.update(DSE_HEADERS)
_dse_last_request_at = [0.0]
# dsebd.org's server doesn't send its intermediate certificate. Browsers
# paper over this (they fetch missing intermediates via AIA); Python's
# requests/certifi does not, so verify=True fails with
# SSLCertVerificationError on every single attempt -- retrying the same
# secure request with backoff never helps. Sticky flag: flips to False the
# first time we hit that specific error, then stays False for the rest of
# this run instead of re-discovering it on every call.
_dse_ssl_verify = [True]


class DSEEndpointGone(RuntimeError):
    """HTTP 410 from DSE: the endpoint is permanently gone for this client. Not retried."""

_dse_dead_paths = {}          # path -> message; call _dse_dead_paths.clear() to retry after a fix
_dse_warmed = [False]
_dse_snapshot_cache = {}      # symbol -> parsed company snapshot (successes only; 4 tools share one fetch)


def _dse_warm_up():
    """Best-effort homepage hit once per run so the session carries normal cookies."""
    if _dse_warmed[0]:
        return
    _dse_warmed[0] = True
    try:
        _dse_session.get(DSE_BASE_URL + "/", timeout=15, verify=_dse_ssl_verify[0])
    except Exception:
        pass


def _dse_get(path: str, params: dict = None, timeout: int = 20) -> str:
    """GET a DSE page through a shared Session (keeps the PHPSESSID cookie
    across calls), with a minimum delay between requests and retry-with-
    backoff on transient failures. Raises after DSE_MAX_RETRIES attempts.
    An SSL cert-chain failure gets ONE free retry with verify=False for
    this host (see _dse_ssl_verify above) before counting against the
    normal retry budget."""
    if path in _dse_dead_paths:
        raise DSEEndpointGone(_dse_dead_paths[path])
    _dse_warm_up()
    last_err = None
    attempt = 0
    while attempt < DSE_MAX_RETRIES:
        elapsed = _dse_time.time() - _dse_last_request_at[0]
        if elapsed < DSE_MIN_REQUEST_INTERVAL:
            _dse_time.sleep(DSE_MIN_REQUEST_INTERVAL - elapsed)
        try:
            resp = _dse_session.get(
                f"{DSE_BASE_URL}/{path}", params=params, timeout=timeout,
                verify=_dse_ssl_verify[0],
            )
            _dse_last_request_at[0] = _dse_time.time()
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.SSLError as e:
            _dse_last_request_at[0] = _dse_time.time()
            last_err = e
            if _dse_ssl_verify[0]:
                print(f"   [WARN] SSL verification failed for dsebd.org ({e}). "
                      f"Its server doesn't send an intermediate cert -- disabling "
                      f"verification for this host only and retrying now.")
                _dse_ssl_verify[0] = False
                continue  # free retry, doesn't count against attempt budget
            attempt += 1
            if attempt < DSE_MAX_RETRIES:
                wait = 2 ** attempt
                print(f"   [WARN] DSE request still failing ({e}); retrying in {wait}s...")
                _dse_time.sleep(wait)
        except requests.exceptions.HTTPError as e:
            _dse_last_request_at[0] = _dse_time.time()
            last_err = e
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):          # permanent: retrying with backoff only wastes time
                msg = (f"HTTP {code} for {path} -- DSE does not serve this endpoint to this client "
                       f"(not retried). Run the 'DSE ENDPOINT PROBE' cell to see what DSE answers now.")
                if code == 410:
                    _dse_dead_paths[path] = msg
                raise DSEEndpointGone(msg) from e
            attempt += 1
            if attempt < DSE_MAX_RETRIES:
                wait = 2 ** attempt
                print(f"   [WARN] DSE request failed ({e}); retrying in {wait}s...")
                _dse_time.sleep(wait)
        except requests.exceptions.RequestException as e:
            _dse_last_request_at[0] = _dse_time.time()
            last_err = e
            attempt += 1
            if attempt < DSE_MAX_RETRIES:
                wait = 2 ** attempt
                print(f"   [WARN] DSE request failed ({e}); retrying in {wait}s...")
                _dse_time.sleep(wait)
    raise last_err


# ----------------------------------------------------------------
# OHLCV price history + technical indicators
# ----------------------------------------------------------------
_DSE_OHLCV_CACHE = {}  # (symbol, start_date, end_date) -> DataFrame, avoids re-fetching


def fetch_dse_ohlcv_range(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """ONE request for the whole [start_date, end_date] range (the site
    supports multi-day ranges natively -- no need to loop day by day).
    Returns a DataFrame (Date index, Open/High/Low/Close/Volume), ascending.

    Parsed directly with BeautifulSoup instead of pd.read_html(html): a raw
    HTML string handed to pd.read_html() gets routed through
    lxml.html.parse(), which expects a FILE PATH or URL, not markup -- on
    a long HTML string that raises FileNotFoundError ("No such file or
    directory: <!DOCTYPE html>...") instead of ever parsing the table,
    even though the page itself has real data. Same class of gotcha as
    fetch_dse_company_snapshot's th/td pairing issue above."""
    key = (symbol.upper(), start_date, end_date)
    if key in _DSE_OHLCV_CACHE:
        return _DSE_OHLCV_CACHE[key]

    try:
        html = _dse_get(
            "day_end_archive.php",
            params={"startDate": start_date, "endDate": end_date, "inst": symbol, "archive": "data"},
        )
    except Exception as e:
        print(f"   [WARN] DSE OHLCV fetch failed for {symbol}: {e}")
        _DSE_OHLCV_CACHE[key] = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return _DSE_OHLCV_CACHE[key]

    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="shares-table")

    rows = []
    if table is not None:
        thead = table.find("thead")
        tbody = table.find("tbody")
        header_cells = [th.get_text(strip=True).upper() for th in thead.find_all("th")] if thead else []
        for tr in (tbody.find_all("tr") if tbody else []):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not header_cells or len(cells) != len(header_cells):
                continue
            row = dict(zip(header_cells, cells))
            trading_code = row.get("TRADING CODE", "").strip()
            if trading_code.upper() != symbol.upper():
                continue
            date_val = row.get("DATE", "").strip()
            try:
                rows.append({
                    "Date": date_val,
                    "Open": float((row.get("OPENP*", "0") or "0").replace(",", "") or 0),
                    "High": float((row.get("HIGH", "0") or "0").replace(",", "") or 0),
                    "Low": float((row.get("LOW", "0") or "0").replace(",", "") or 0),
                    "Close": float((row.get("CLOSEP*", "0") or "0").replace(",", "") or 0),
                    "Volume": float((row.get("VOLUME", "0") or "0").replace(",", "") or 0),
                })
            except (ValueError, TypeError):
                continue

    if not rows:
        df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    else:
        df = pd.DataFrame(rows).drop_duplicates(subset="Date").set_index("Date")
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

    _DSE_OHLCV_CACHE[key] = df
    return df


@tool
def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Fetch daily OHLCV price data for a DSE-listed symbol as a CSV string
    (Date,Open,High,Low,Close,Volume) -- matches the format the rest of
    this notebook's parsing (log_step, backfill, backtest) expects."""
    df = fetch_dse_ohlcv_range(symbol, start_date, end_date)
    if df.empty:
        return f"# No DSE data found for {symbol} between {start_date} and {end_date}"
    lines = ["Date,Open,High,Low,Close,Volume"]
    for idx, r in df.iterrows():
        lines.append(
            f"{idx.strftime('%Y-%m-%d')},{r['Open']:.2f},{r['High']:.2f},"
            f"{r['Low']:.2f},{r['Close']:.2f},{int(r['Volume'])}"
        )
    return "\n".join(lines)


def _dse_indicator_series(df: pd.DataFrame, indicator: str) -> pd.Series:
    close = df["Close"]
    if indicator == "close_50_sma":
        return close.rolling(window=50, min_periods=1).mean()
    if indicator == "rsi":
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(window=14, min_periods=1).mean()
        loss = (-delta.clip(upper=0)).rolling(window=14, min_periods=1).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))
    if indicator == "macd":
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        return ema12 - ema26
    if indicator in ("boll_ub", "boll_lb"):
        mid = close.rolling(window=20, min_periods=1).mean()
        std = close.rolling(window=20, min_periods=1).std().fillna(0)
        return mid + 2 * std if indicator == "boll_ub" else mid - 2 * std
    raise ValueError(f"Unknown indicator: {indicator}")


@tool
def get_indicators(symbol: str, indicator: str, curr_date: str) -> str:
    """Compute a technical indicator (rsi / macd / close_50_sma / boll_ub /
    boll_lb) for a DSE symbol as of curr_date. All 5 indicator calls for the
    same (symbol, curr_date) share ONE cached OHLCV fetch (see
    _DSE_OHLCV_CACHE above) instead of hitting DSE 5 separate times. Returns
    a single "YYYY-MM-DD: value" line, matching what this notebook's
    _extract_indicator_value parser expects."""
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=120)  # enough history for 50-SMA / 20-day Bollinger
    df = fetch_dse_ohlcv_range(symbol, start_dt.strftime("%Y-%m-%d"), curr_date)
    if df.empty or len(df) < 2:
        return f"{curr_date}: N/A (insufficient DSE price history)"
    try:
        series = _dse_indicator_series(df, indicator)
        value = series.iloc[-1]
        if pd.isna(value):
            return f"{curr_date}: N/A"
        return f"{df.index[-1].strftime('%Y-%m-%d')}: {value:.4f}"
    except Exception as e:
        return f"{curr_date}: N/A (error: {e})"


# ----------------------------------------------------------------
# Company fundamentals snapshot (displayCompany.php)
# ----------------------------------------------------------------
def fetch_dse_company_snapshot(symbol: str) -> dict:
    """Scrapes DSE's company profile page. Each info row on that page holds
    TWO label/value pairs (4 cells: th,td,th,td), which pandas.read_html
    can't tell apart from a plain 2-column table -- parsed directly with
    BeautifulSoup th/td pairing instead.

    Fields whose value is a link (e.g. "Details of Financial Statement",
    "Price Sensitive Information") previously lost their href entirely --
    td.get_text() only kept the visible label ("View"), discarding the
    actual URL. Now captures href when the cell contains an <a> tag."""
    if symbol in _dse_snapshot_cache:
        return dict(_dse_snapshot_cache[symbol])
    try:
        html = _dse_get("displayCompany.php", params={"name": symbol})
    except Exception as e:
        return {"error": str(e)}

    soup = BeautifulSoup(html, "lxml")
    data = {}
    for table in soup.find_all("table", id="company"):
        for tr in table.find_all("tr"):
            ths = tr.find_all("th")
            tds = tr.find_all("td")
            for th, td in zip(ths, tds):
                key = th.get_text(strip=True).rstrip(":").strip()
                link = td.find("a")
                text = td.get_text(strip=True)
                if link and link.get("href"):
                    href = link["href"]
                    if href.startswith("/"):
                        href = DSE_BASE_URL + href
                    elif not href.lower().startswith("http"):
                        href = f"{DSE_BASE_URL}/{href}"
                    val = f"{text} ({href})" if text else href
                else:
                    val = text
                if key and val:
                    data[key] = val
    if data:
        _dse_snapshot_cache[symbol] = data
    return dict(data)


def _dse_snapshot_find(snapshot: dict, *keys) -> str:
    for k in keys:
        for sk, sv in snapshot.items():
            if k.lower() in sk.lower():
                return sv
    return None


def _dse_num(v) -> str:
    if not v:
        return "N/A"
    nums = _dse_re.findall(r"-?\d[\d,]*\.?\d*", str(v))
    return nums[0].replace(",", "") if nums else "N/A"


def _dse_mn_to_raw(v: str) -> str:
    """DSE reports capital/reserve figures in millions of BDT. Convert to
    raw BDT units (same scaling get_fundamentals already applies to Market
    Cap below) so cell 12's fmt() -- which expects raw absolute amounts --
    scales it correctly into B/M instead of printing the millions figure
    as if it were the raw amount."""
    if v == "N/A":
        return "N/A"
    try:
        return str(int(float(v) * 1_000_000))
    except ValueError:
        return "N/A"


@tool
def get_fundamentals(ticker: str, curr_date: str) -> str:
    """Fundamentals snapshot for a DSE symbol, formatted as label: value
    lines matching what this notebook's Fundamentals Analyst cell parses
    with regex. DSE's public site does not publish P/E, EPS, revenue, beta
    or dividend yield in structured form -- those are genuinely "N/A", not
    a scraping bug."""
    snapshot = fetch_dse_company_snapshot(ticker)
    # DEBUG: show exactly what labels DSE's page actually returned, so a
    # label-text mismatch (e.g. "Market Capitalization" vs "Market Cap.(mn)")
    # is visible instead of silently producing N/A.
    print(f"   [DEBUG] Raw DSE snapshot keys for {ticker}: {list(snapshot.keys())}")
    if "error" in snapshot:
        return f"Error fetching DSE fundamentals for {ticker}: {snapshot['error']}"
    if not snapshot:
        return f"No DSE company data found for {ticker} (page returned no parsable fields)."

    sector = _dse_snapshot_find(snapshot, "Sector")
    last_price = _dse_snapshot_find(snapshot, "Last Trading Price")
    mcap_mn = _dse_num(_dse_snapshot_find(snapshot, "Market Capitalization"))
    range_52w = _dse_snapshot_find(snapshot, "52 Weeks")
    paidup_mn = _dse_num(_dse_snapshot_find(snapshot, "Paid-up Capital"))

    # DSE reports Market Cap in "(mn)" units (millions of BDT) -- convert to
    # raw units so this notebook's existing fmt() (in the Fundamentals
    # Analyst cell) scales it correctly into B/M display form.
    mcap_raw = _dse_mn_to_raw(mcap_mn)

    lo52, hi52 = "N/A", "N/A"
    if range_52w and "-" in range_52w:
        parts = [p.strip() for p in range_52w.replace(",", "").split("-")]
        if len(parts) == 2:
            lo52, hi52 = parts[0], parts[1]

    lines = [
        f"COMPANY: {ticker} (DSE) | DATE: {curr_date}",
        f"Sector: {sector or 'N/A'}",
        f"Last Trading Price: {_dse_num(last_price)}",
        "PE Ratio: N/A",
        "EPS (TTM): N/A",
        f"Market Cap: {mcap_raw}",
        "Revenue (TTM): N/A",
        "Beta: N/A",
        "Dividend Yield: N/A",
        f"52 Week High: {hi52}",
        f"52 Week Low: {lo52}",
        f"Paid-up Capital (mn BDT): {paidup_mn}",
        "",
        "NOTE: DSE's public company page does not publish P/E, EPS, revenue,",
        "beta or dividend yield in structured form -- those fields are",
        "genuinely unavailable from this source, not a scraping failure.",
    ]
    return "\n".join(lines)


@tool
def get_balance_sheet(ticker: str, curr_date: str) -> str:
    """Capital-structure / reserves snapshot for a DSE symbol, scraped from
    the same displayCompany.php page as get_fundamentals. DSE's public site
    does NOT publish a full balance sheet (total assets/liabilities, net
    debt, cash & equivalents) -- only annual-report PDFs have that. What IS
    genuinely published there: authorized/paid-up capital, face value,
    outstanding securities, and reserves & surplus (incl. OCI) -- returned
    as real numbers below; fields truly absent from the page stay N/A."""
    snapshot = fetch_dse_company_snapshot(ticker)
    if "error" in snapshot:
        return f"Error fetching DSE balance sheet data for {ticker}: {snapshot['error']}"
    if not snapshot:
        return f"No DSE company data found for {ticker} (page returned no parsable fields)."

    lines = [
        f"COMPANY: {ticker} (DSE) | DATE: {curr_date}",
        f"Authorized Capital: {_dse_mn_to_raw(_dse_num(_dse_snapshot_find(snapshot, 'Authorized Capital')))}",
        f"Paid-up Capital: {_dse_mn_to_raw(_dse_num(_dse_snapshot_find(snapshot, 'Paid-up Capital')))}",
        f"Face/Par Value: {_dse_num(_dse_snapshot_find(snapshot, 'Face'))}",
        f"Total Outstanding Securities: {_dse_num(_dse_snapshot_find(snapshot, 'Outstanding Securities'))}",
        f"Reserve & Surplus: {_dse_mn_to_raw(_dse_num(_dse_snapshot_find(snapshot, 'Reserve & Surplus')))}",
        f"Other Comprehensive Income: {_dse_mn_to_raw(_dse_num(_dse_snapshot_find(snapshot, 'Other Comprehensive Income')))}",
        "Net Debt: N/A",
        "Total Debt: N/A",
        "Cash And Cash Equivalents: N/A",
        f"Full Audited Financial Statement (PDF): {_dse_snapshot_find(snapshot, 'Details of Financial Statement') or 'N/A'}",
        "",
        "NOTE: DSE's public page does not publish a full balance sheet (total",
        "assets/liabilities, debt, cash) -- only annual-report PDFs have that.",
        "The capital-structure and reserves fields above are genuinely what's",
        "public; Net Debt/Total Debt/Cash stay N/A, not a scraping failure.",
    ]
    return "\n".join(lines)


@tool
def get_cashflow(ticker: str, curr_date: str) -> str:
    """Dividend / corporate-action history for a DSE symbol -- DSE's public
    site does not publish a company cash flow statement (operating/
    investing/financing activities, free cash flow); only annual-report
    PDFs have that. Returns the closest public proxy: recent cash dividend,
    bonus issue and rights issue history from the same snapshot page."""
    snapshot = fetch_dse_company_snapshot(ticker)
    if "error" in snapshot:
        return f"Error fetching DSE cash flow data for {ticker}: {snapshot['error']}"
    if not snapshot:
        return f"No DSE company data found for {ticker} (page returned no parsable fields)."

    cash_div    = _dse_snapshot_find(snapshot, "Cash Dividend") or "N/A"
    bonus_issue = _dse_snapshot_find(snapshot, "Bonus Issue") or "N/A"
    right_issue = _dse_snapshot_find(snapshot, "Right Issue") or "N/A"

    lines = [
        f"COMPANY: {ticker} (DSE) | DATE: {curr_date}",
        f"Cash Dividend: {cash_div}",
        f"Bonus Issue (Stock Dividend): {bonus_issue}",
        f"Right Issue: {right_issue}",
        "Free Cash Flow: N/A",
        "Repurchase Of Capital Stock: N/A",
        "",
        "NOTE: DSE's public page does not publish a cash flow statement",
        "(operating/investing/financing activities, FCF) -- only annual-report",
        "PDFs have that. The dividend/bonus/rights history above is the",
        "closest public proxy for shareholder cash flow, not a company-level",
        "cash flow statement.",
    ]
    return "\n".join(lines)


# Matches DSE's own "Price Sensitive Information" quarterly EPS disclosure
# wording, e.g. "EPS was Tk. 0.03 for January-March 2024" or
# "EPS of the company increased to Tk 0.27 for October-December, 2017".
# CONFIRMED against a real disclosure: DSE writes negative EPS in
# PARENTHESES per standard accounting convention -- "EPS was Tk. (12.41)
# for April-June 2026" means -12.41, not a literal minus sign. The
# original regex only looked for "-", so it never matched a negative
# print at all (and some DSE tickers' EPS has been negative for several quarters
# running, so that was silently swallowing every real disclosure).
_DSE_EPS_NEWS_RE = _dse_re.compile(
    r"EPS[^.]*?Tk\.?\s*(\(?-?[\d.]+\)?)\s+for\s+([^;.,\n]+)", _dse_re.IGNORECASE
)


def _parse_dse_signed_number(raw: str) -> float:
    """DSE/accounting convention: a value in parentheses is negative,
    e.g. "(12.41)" == -12.41, "7.19" == 7.19. Handles this explicitly
    rather than relying on a literal minus sign being present."""
    raw = raw.strip()
    is_paren_negative = raw.startswith("(") and raw.endswith(")")
    val = float(raw.strip("()"))
    return -abs(val) if is_paren_negative else val


@tool
def get_income_statement(ticker: str, curr_date: str) -> str:
    """DSE's company-profile page has NO income-statement fields at all --
    that part is genuinely unavailable no matter how it's parsed. But every
    listed company discloses quarterly EPS (and NAV/NOCFPS) as "Price
    Sensitive Information" the moment its board approves it, and that shows
    up in DSE's own news feed (same old_news.php this notebook's get_news
    already scrapes). This looks back up to 400 days for the most recent
    such disclosure, and multiplies declared EPS by Total Outstanding
    Securities (from get_balance_sheet's snapshot) to ESTIMATE Net Income --
    a real, company-disclosed number, but an estimate (EPS x share count),
    not an audited P&L line item. Falls back to not-available if no
    matching disclosure is found in the window."""
    # Pull the link to the actual annual/quarterly financial statement PDF
    # (now capturable since fetch_dse_company_snapshot preserves hrefs).
    # This is printed/surfaced regardless of the EPS-estimate outcome below,
    # since it's the one real path to full audited figures.
    snapshot = fetch_dse_company_snapshot(ticker)
    financial_statement_link = _dse_snapshot_find(snapshot, "Details of Financial Statement") or "N/A"
    print(f"   [DEBUG] 'Details of Financial Statement' field for {ticker}: {financial_statement_link}")

    lookback_start = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
    try:
        news_items = _fetch_dse_news(ticker, lookback_start, curr_date)
    except Exception as e:
        return f"Error fetching DSE news for income-statement estimate for {ticker}: {e}"

    eps_val, quarter_label, matched_line = None, None, None
    for line in news_items:
        m = _DSE_EPS_NEWS_RE.search(line)
        if m:
            try:
                eps_val = _parse_dse_signed_number(m.group(1))
            except ValueError:
                continue  # malformed number in this particular match -- keep looking
            quarter_label = m.group(2).strip()
            matched_line = line
            break

    if eps_val is None:
        # Don't just show a random sample -- check whether ANY item even
        # mentions EPS/earnings, so we can tell "genuinely no disclosure
        # in this window" apart from "disclosure exists but regex missed
        # its exact wording" (and see that wording if so).
        eps_mentions = [l for l in news_items if "eps" in l.lower() or "earning" in l.lower()]
        if eps_mentions:
            print(f"   [DEBUG] Regex found no match, but {len(eps_mentions)} of {len(news_items)} "
                  f"news item(s) mention EPS/earnings -- actual DSE wording (adjust "
                  f"_DSE_EPS_NEWS_RE to match this):")
            for l in eps_mentions[:3]:
                print(f"   [DEBUG]   {l[:300]}")
        else:
            print(f"   [DEBUG] Checked {len(news_items)} DSE news item(s) for {ticker} over the last "
                  f"400 days -- none mention EPS or earnings at all. This looks like a genuine "
                  f"reporting gap in DSE's news feed for this window, not a regex problem.")
        return (f"No quarterly EPS disclosure found via DSE news for {ticker} in the last 400 "
                f"days as of {curr_date}. Full income-statement figures otherwise exist only in "
                f"the company's annual-report PDF: {financial_statement_link}")

    print(f"   [DEBUG] Matched EPS disclosure for {ticker}: {matched_line[:200]}")

    shares_raw = _dse_num(_dse_snapshot_find(snapshot, "Outstanding Securities"))
    net_income_est = "N/A"
    if shares_raw != "N/A":
        try:
            net_income_est = str(int(eps_val * float(shares_raw)))
        except ValueError:
            pass

    # Rough P/E: DSE publishes no EPS(TTM), only per-disclosure EPS for
    # whatever period the company reported. Heuristic: if the disclosed
    # period says "year" (e.g. "the year ended December 31, 2025") it's
    # already an annual figure; otherwise (a "Month-Month YYYY" window)
    # treat it as one quarter and annualize by x4. This is a rough
    # approximation, not a true TTM P/E -- labeled as such below.
    is_annual_period = "year" in quarter_label.lower()
    annualized_eps = eps_val if is_annual_period else eps_val * 4
    last_price_raw = _dse_num(_dse_snapshot_find(snapshot, "Last Trading Price"))
    if annualized_eps <= 0:
        pe_line = "P/E Ratio: N/M (negative or zero annualized earnings -- not meaningful)"
    elif last_price_raw == "N/A":
        pe_line = "P/E Ratio: N/A (Last Trading Price unavailable)"
    else:
        try:
            pe_val = float(last_price_raw) / annualized_eps
            basis = "reported annual EPS" if is_annual_period else "most recent quarterly EPS x4"
            pe_line = f"P/E Ratio (rough, {basis}): {pe_val:.2f}"
        except (ValueError, ZeroDivisionError):
            pe_line = "P/E Ratio: N/A"

    lines = [
        f"COMPANY: {ticker} (DSE) | DATE: {curr_date}",
        f"Most Recent Disclosed EPS: Tk. {eps_val} for {quarter_label}",
        f"Total Outstanding Securities: {shares_raw}",
        f"Net Income From Continuing (estimated, EPS x shares): {net_income_est}",
        pe_line,
        "Gross Profit: N/A (not a meaningful line item for a bank's income statement)",
        "Operating Income: N/A (not a meaningful line item for a bank's income statement)",
        "Normalized EBITDA: N/A (not a meaningful line item for a bank's income statement)",
        f"Full Audited Financial Statement (PDF): {financial_statement_link}",
        "",
        "NOTE: DSE's public pages have no full income statement (revenue,",
        "gross profit, operating income, EBITDA) -- only the company's own",
        "quarterly EPS disclosure (Price Sensitive Information, via DSE news)",
        "is available. Net Income above is an ESTIMATE (declared EPS x",
        "outstanding shares), not an audited P&L line item. P/E Ratio is a",
        "rough approximation (annualized from one disclosed period), not a",
        "true trailing-twelve-month P/E.",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------
# News / disclosures (old_news.php -- NOT latest_news.php, which 404s)
# ----------------------------------------------------------------
def _parse_dse_news_table(html: str) -> list:
    """The news table repeats groups of (Trading Code, News Title, News,
    Post Date) th/td rows, separated by spacer/<hr> rows with no <td>.
    Grouped here since pandas.read_html can't infer that structure."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="table-news")
    if table is None:
        return []

    items = []
    current = {}
    for tr in table.find_all("tr"):
        th = tr.find("th")
        td = tr.find("td")
        if th is None:
            continue
        label = th.get_text(strip=True).rstrip(":").strip()
        if not label or td is None:
            if current:
                items.append(current)
                current = {}
            continue
        if label == "Trading Code" and current:
            items.append(current)
            current = {}
        current[label] = td.get_text(strip=True)
    if current:
        items.append(current)
    return items


def _fetch_dse_news(symbol: str, start_date: str, end_date: str) -> list:
    """symbol="" fetches market-wide news across all companies (used by
    get_global_news); a specific symbol filters to that company."""
    try:
        html = _dse_get(
            "old_news.php",
            params={"startDate": start_date, "endDate": end_date, "inst": symbol or "",
                    "criteria": 4, "archive": "data"},
        )
    except Exception as e:
        # was: print + return [] -> callers reported "no news found" / "genuine reporting gap"
        # for what was really a failed request. Now the failure propagates.
        raise RuntimeError(f"DSE news endpoint unavailable: {e}") from e
    items = _parse_dse_news_table(html)
    lines = []
    for it in items:
        title = it.get("News Title", "")
        body = it.get("News", "")
        date = it.get("Post Date", "")
        if title or body:
            lines.append(f"[{date}] {title}: {body}")
    return lines


@tool
def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Company-specific DSE news/disclosures for `ticker` between start_date
    and end_date."""
    try:
        items = _fetch_dse_news(ticker, start_date, end_date)
    except Exception as e:
        return f"Error fetching DSE news for {ticker}: {e}"
    if not items:
        return f"No DSE news/disclosures found for {ticker} between {start_date} and {end_date}."
    return "\n".join(f"- {i}" for i in items[:30])


@tool
def get_global_news(curr_date: str) -> str:
    """DSE-wide market notices (last 7 days up to curr_date) as a substitute
    for global macro news -- DSE has no general financial news feed."""
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=7)
    try:
        items = _fetch_dse_news("", start_dt.strftime("%Y-%m-%d"), curr_date)
    except Exception as e:
        return f"Error fetching DSE market-wide notices: {e}"
    if not items:
        return "No DSE market-wide notices found."
    return "\n".join(f"- {i}" for i in items[:30])


print("[OK] DSE data source layer v2 ready -- get_stock_data / get_indicators / get_fundamentals / "
      "get_balance_sheet / get_cashflow / get_income_statement / get_news / get_global_news "
      "now pull real data from dsebd.org (single range-request + session + rate limiting).")
