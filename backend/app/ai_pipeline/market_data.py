"""
Real market data — keyless, no account required.

Fetches daily OHLCV from Stooq (primary) or yfinance (if installed) and computes
the technical indicators the pipeline uses (RSI, MACD, 50-day SMA, Bollinger
bands). This lets the app pull genuine finance data out of the box, without the
TradingAgents package configured.

Pure-Python indicator math (below) is offline-testable; only the fetch needs the
network, and it fails soft so a run never crashes on a bad symbol.
"""

from __future__ import annotations

import io
import logging
import re
import time as _time
import urllib.request
from datetime import datetime, timedelta

_log = logging.getLogger(__name__)

# Watchlist polling can fetch several DSE tickers concurrently (each via
# asyncio.to_thread -> a real OS thread), and every one of them calls
# bdshare -> dsebd.org. urllib3's default connection pool (size 10) can't
# hold that many at once, producing repeated "Connection pool is full,
# discarding connection: dsebd.org" warnings. This throttles our own calls
# so we never have more than a few in flight at once — cheaper and more
# reliable than trying to reconfigure bdshare's internal session, which we
# don't control.
import threading
_DSEBD_CONCURRENCY = threading.Semaphore(3)

# ✅ FIXED (see conversation notes): the previous sharenews24 fetch only ever
# looked at <a> headline text on the site's homepage. Two separate problems
# with that, confirmed live:
#   1. The homepage mixes share-market news with politics/sports/celebrity
#      gossip -- finance content is a small fraction of the links. The site
#      has dedicated category pages that are almost entirely on-topic.
#   2. Headline text alone is structurally insufficient. A large share of
#      this outlet's finance coverage is "roundup" style ("Top 10 gainers",
#      "Four companies gave the index confidence today") where the headline
#      names NO company at all -- the companies are only named in the body
#      paragraphs. Worse, even the per-article meta-description/teaser
#      (~250 chars) routinely truncates mid-list: a real example headlined
#      "চার কোম্পানি..." ("Four companies...") named Square Pharmaceuticals,
#      Pragati Life Insurance, Walton Hi-Tech Industries, and National Bank
#      Limited -- all in ENGLISH on first mention -- but only the word
#      "Square" survives before the teaser cuts off; the other three are
#      well into the body. Only fetching each candidate article's full body
#      text finds these.
#
# Since English company names are used on first mention in body text (this
# outlet only switches to a Bengali short form -- "স্কয়ার ফার্মা" -- in
# later sentences of the *same* article), matching the full body against the
# ENGLISH name variants we already resolve via _load_dse_company_names() is
# the primary, reliable signal here -- the small hand-typed Bengali dict is
# now a secondary/backup layer, not the main mechanism.
#
# This is cached (not per-ticker) because many different tickers get looked
# up against the same batch of recent articles -- fetching each candidate
# article's full body is too expensive to repeat per-ticker (category page +
# up to _SHARENEWS24_MAX_ARTICLES article bodies, throttled 2s apart, is
# ~40s on a cold cache). A real production deployment should warm this via a
# periodic background job rather than paying that ~40s on whichever request
# happens to find a stale cache -- there's no scheduler wired in here, so
# this refreshes synchronously (refresh-if-stale, same pattern as
# _load_dse_company_names below) on whichever request finds it stale.
_SHARENEWS24_CATEGORY_URLS = [
    "https://sharenews24.com/group/1/index.html",   # শেয়ারবাজার (Share Market)
    "https://sharenews24.com/group/17/index.html",  # প্রাইস সেনসেটিভ (Price Sensitive)
    "https://sharenews24.com/group/18/index.html",  # টেকনিক্যাল অ্যানালাইসিস (Technical Analysis)
    "https://sharenews24.com/group/9/index.html",   # বিনিয়োগকারীর কথা (Investor's Word)
]
# ✅ CHANGED (GP-vs-BEXIMCO gap): the old version fetched at most 20 NEW
# article bodies per refresh and then REPLACED the whole cache with just
# that batch every 30 min. That means the pool a ticker gets matched
# against is always "whatever's most recent right now" -- a heavily-traded,
# high-news-volume ticker (BEXIMCO: Sukuk conversion, top-10-traded lists)
# dominates that small recent batch essentially every cycle, while a
# quieter blue-chip (GP) can go many refresh cycles with zero coverage in
# the window and simply never gets a chance to match, even though sharenews24
# does cover it sometimes -- that coverage just isn't in *this* snapshot.
#
# Fix: the cache now ACCUMULATES across refreshes instead of being replaced.
# Each refresh only fetches bodies for articles not already cached (cheap,
# and means the 2s-throttled fetch loop does less repeat work over time),
# then merges them into a rolling window capped at _SHARENEWS24_CACHE_MAX_SIZE
# (newest first, oldest evicted past the cap). A ticker's odds of a match now
# depend on total coverage accumulated over hours/days, not on whether it
# happened to be in the news in the last 30 minutes.
_SHARENEWS24_MAX_NEW_PER_REFRESH = 20
_SHARENEWS24_CACHE_MAX_SIZE = 300
_SHARENEWS24_CACHE_TTL_SECONDS = 30 * 60  # news moves faster than the 6h company-list cache
_SHARENEWS24_REQUEST_DELAY_SECONDS = 2.0  # be polite -- there's no public API
_sharenews24_cache: dict = {"ts": 0.0, "articles": []}
_sharenews24_lock = threading.Lock()


# 🔴 FIXED (was still broken -- confirmed live): the note above said this
# "refreshes synchronously... on whichever request finds it stale," and
# flagged that as a real risk rather than a hidden one. It then actually
# happened: "Request to /stocks/SQURPHARMA/news?limit=12 timed out after
# 20000ms" -- a cold refresh (2 category fetches + up to
# _SHARENEWS24_MAX_ARTICLES article fetches, throttled 2s apart, ~40s worst
# case) blew straight through the request's 20s budget. There's still no
# job scheduler wired into this app to warm the cache out-of-band, so this
# now does the next best thing itself: the request path NEVER blocks on the
# fetch loop. If the cache is stale, a background daemon thread is kicked
# off (at most one at a time) to do the ~40s refresh, and the request
# returns immediately with whatever's currently cached -- which may be
# genuinely empty on the very first request after a cold deploy, but will
# be populated for every request after that first ~40s window closes.
_sharenews24_refreshing = False


def _sharenews24_refresh_worker() -> None:
    global _sharenews24_refreshing
    from bs4 import BeautifulSoup

    try:
        with _sharenews24_lock:
            already_cached_urls = {a["url"] for a in _sharenews24_cache["articles"]}

        candidates: dict[str, str] = {}  # url -> title
        for cat_url in _SHARENEWS24_CATEGORY_URLS:
            try:
                html = _sharenews24_get(cat_url)
            except Exception as e:  # noqa: BLE001
                _log.warning("sharenews24 category fetch failed (%s): %s", cat_url, e)
                continue
            soup = BeautifulSoup(html, "html.parser")
            for link in soup.find_all("a", href=True):
                title = link.get_text(strip=True)
                if len(title) < 15:
                    continue
                href = link["href"]
                # ✅ FIXED: confirmed live -- some of the 20 "articles" we
                # fetched turned out to be the site's own nav-menu category
                # labels ("বিনিয়োগকারীর কথা", "প্রাইস সেনসেটিভ", etc, which
                # link to /group/N/index.html), not real articles. Their
                # Bengali text is long enough to clear the len>=15 filter
                # above, so they slipped through as candidates and wasted a
                # scrape slot on a category listing page. Every real
                # article on this site lives under /article/<id>/index.html
                # (confirmed across every article link seen so far) --
                # restrict candidates to that path.
                if "/article/" not in href:
                    continue
                full_link = href if href.startswith("http") else f"https://sharenews24.com{href}"
                candidates.setdefault(full_link, title)

        # ✅ CHANGED: skip anything already in the accumulated cache -- we
        # already have its body, no need to re-fetch it every 30 min. This
        # also means the _SHARENEWS24_MAX_NEW_PER_REFRESH budget is spent
        # entirely on genuinely new articles instead of being partly wasted
        # re-downloading ones we already saw last cycle.
        new_candidates = {u: t for u, t in candidates.items() if u not in already_cached_urls}

        new_articles = []
        for url, title in list(new_candidates.items())[:_SHARENEWS24_MAX_NEW_PER_REFRESH]:
            try:
                html = _sharenews24_get(url)
            except Exception as e:  # noqa: BLE001
                _log.warning("sharenews24 article body fetch failed (%s): %s", url, e)
                continue
            try:
                article_soup = BeautifulSoup(html, "html.parser")
                # 🔴 FIXED (still broken -- confirmed with a worst-case test):
                # the previous version restricted extraction to <p> tags,
                # reasoning that sidebar/ticker/nav items are <a> links
                # instead of paragraphs. That's true for the "সর্বশেষ"/
                # "সর্বোচ্চ পঠিত" sidebar lists (<li><a>...) but NOT
                # necessarily for the "শিরোনাম" ticker at the top of every
                # page, which renders as flowing inline links rather than a
                # bulleted list -- a strong sign its markup is closer to
                # <p><a href="...">Headline</a></p> repeated per item. If
                # so, <p>-only extraction still keeps that anchor text,
                # which is exactly the same contamination as before (a
                # ticker item mentioning some company leaks into every
                # article's body_text, matching unrelated stories against
                # that company). Since we can't fetch this site's raw HTML
                # from this environment to confirm the ticker's exact tag,
                # the robust fix doesn't depend on guessing right: strip
                # EVERY <a> tag (and its text) out of the tree first, THEN
                # extract <p> text. This removes anchor-wrapped boilerplate
                # regardless of which container tag wraps it (<p>, <li>,
                # <div>, ...), while genuine article prose -- which isn't
                # itself a giant clickable link -- survives untouched.
                for a_tag in article_soup.find_all("a"):
                    a_tag.decompose()
                paragraphs = article_soup.find_all("p")
                if paragraphs:
                    body_text = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
                else:
                    body_text = article_soup.get_text(separator=" ", strip=True)
            except Exception as e:  # noqa: BLE001
                _log.warning("sharenews24 article body parse failed (%s): %s", url, e)
                continue
            new_articles.append({"title": title, "url": url, "text": body_text})

        _log.info(
            "sharenews24: background refresh done, %d candidate headlines (%d new) -> %d new article bodies fetched",
            len(candidates), len(new_candidates), len(new_articles),
        )

        with _sharenews24_lock:
            # Newest first, then whatever was already cached, deduped by
            # url, trimmed to the rolling cap -- this is the accumulation
            # step: a ticker's matched article from 3 refresh-cycles ago is
            # still here today, not silently dropped the moment it's no
            # longer "recent."
            merged: dict[str, dict] = {}
            for art in new_articles + _sharenews24_cache["articles"]:
                merged.setdefault(art["url"], art)
            _sharenews24_cache["ts"] = _time.time()
            _sharenews24_cache["articles"] = list(merged.values())[:_SHARENEWS24_CACHE_MAX_SIZE]
            _log.info("sharenews24: accumulated cache now holds %d articles", len(_sharenews24_cache["articles"]))
    except Exception:  # noqa: BLE001
        _log.exception("sharenews24: background refresh crashed")
    finally:
        with _sharenews24_lock:
            _sharenews24_refreshing = False


def _sharenews24_get(url: str) -> str:
    """Throttled fetch -- shared by the category-page pass and every
    per-article body fetch, so we never hammer sharenews24.com faster than
    one request per _SHARENEWS24_REQUEST_DELAY_SECONDS regardless of how
    many articles we're pulling. Only ever called from the background
    refresh thread (see _sharenews24_refresh_worker), never from a request
    thread -- that's the fix."""
    global _last_sharenews24_ts
    elapsed = _time.time() - _last_sharenews24_ts
    if elapsed < _SHARENEWS24_REQUEST_DELAY_SECONDS:
        _time.sleep(_SHARENEWS24_REQUEST_DELAY_SECONDS - elapsed)
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; personal-research-bot/1.0)"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8", "replace")
    _last_sharenews24_ts = _time.time()
    return html


_last_sharenews24_ts = 0.0


def _load_sharenews24_articles(force: bool = False) -> list[dict]:
    """Returns whatever's currently cached [{"title", "url", "text"}] and,
    if stale, kicks off a background refresh -- but NEVER blocks the caller
    on that refresh (see the 🔴 FIXED note above; this used to block and
    caused a live request timeout). `text` is the article's own body
    paragraphs with every <a>-tag (nav/ticker/sidebar/related-article link)
    stripped out first -- see the 🔴 FIXED note in _sharenews24_refresh_worker
    for why: matching against the full raw page text let boilerplate
    widgets (shared near-verbatim across every article on the site) falsely
    tag unrelated stories as being about whatever company happened to be in
    that widget.

    ⚠️ Cache accumulates and never re-scrapes an already-cached URL (see the
    "GP-vs-BEXIMCO gap" note above) -- so articles cached under an OLDER
    version of the extraction logic keep their stale `text` until evicted
    past _SHARENEWS24_CACHE_MAX_SIZE. A code change to the extraction logic
    only affects newly-scraped articles; restart the process to force a
    clean re-scrape of everything currently in view.

    First-ever call after a cold deploy returns an empty list (nothing
    cached yet) while the background thread populates it -- callers should
    treat an empty result as "not yet warmed," not "no news.\""""
    global _sharenews24_refreshing
    with _sharenews24_lock:
        stale = force or (_time.time() - _sharenews24_cache["ts"]) >= _SHARENEWS24_CACHE_TTL_SECONDS
        if stale and not _sharenews24_refreshing:
            _sharenews24_refreshing = True
            threading.Thread(target=_sharenews24_refresh_worker, daemon=True, name="sharenews24-refresh").start()
        return _sharenews24_cache["articles"]


def _stooq_symbol(symbol: str, asset_type: str = "stock") -> str:
    s = symbol.strip().lower()
    if asset_type == "crypto":
        # Stooq uses e.g. btc.v / eth.v ; fall back to appending .us otherwise
        return s if "." in s else f"{s}"
    return s if "." in s else f"{s}.us"


def fetch_ohlcv(symbol: str, start: str, end: str, asset_type: str = "stock"):
    """Return a list of dict rows [{date,open,high,low,close,volume}], newest last.

    ✅ CHANGED (DSE): a live DSE trading code routes to bdshare instead of
    yfinance/Stooq, neither of which carry Dhaka Stock Exchange data. Same
    row shape either way, so get_history()/get_quote()/compute_indicators()
    in app/services/market_data.py need no changes to pick this up.

    Tries yfinance first (if installed), then Stooq's keyless CSV endpoint.
    Returns [] on failure rather than raising.
    """
    from tradingagents.dataflows.symbol_utils import is_dse_ticker
    if is_dse_ticker(symbol):
        return _fetch_dse_ohlcv(symbol, start, end)
    rows = _fetch_yfinance(symbol, start, end)
    if rows:
        return rows
    return _fetch_stooq(symbol, start, end, asset_type)


def _fetch_dse_ohlcv(symbol: str, start: str, end: str):
    """DSE OHLCV via bdshare, reshaped to fetch_ohlcv's row contract. Fails
    soft to [] — never raises, matching _fetch_yfinance/_fetch_stooq."""
    try:
        from bdshare import get_historical_data
        with _DSEBD_CONCURRENCY:
            df = get_historical_data(start, end, symbol.upper())
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    df = df.sort_index(ascending=True).reset_index()
    out = []
    for _, r in df.iterrows():
        try:
            out.append({
                "date": str(r["date"])[:10],
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": float(r["volume"]) if r.get("volume") not in (None, "") else None,
            })
        except Exception:  # noqa: BLE001
            continue
    return out


def _fetch_yfinance(symbol, start, end):
    try:
        import yfinance as yf  # optional dependency
    except Exception:  # noqa: BLE001
        return []
    try:
        df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or df.empty:
            return []
        out = []
        for idx, r in df.iterrows():
            def g(col):
                v = r[col]
                try:
                    return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
                except Exception:  # noqa: BLE001
                    return None
            out.append({
                "date": idx.strftime("%Y-%m-%d"),
                "open": g("Open"), "high": g("High"), "low": g("Low"),
                "close": g("Close"), "volume": g("Volume"),
            })
        return [r for r in out if r["close"] is not None]
    except Exception:  # noqa: BLE001
        return []


def _fetch_stooq(symbol, start, end, asset_type):
    s = _stooq_symbol(symbol, asset_type)
    url = (f"https://stooq.com/q/d/l/?s={s}&d1={start.replace('-', '')}"
           f"&d2={end.replace('-', '')}&i=d")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    return parse_stooq_csv(text)


def parse_stooq_csv(text: str):
    """Parse Stooq's 'Date,Open,High,Low,Close,Volume' CSV into row dicts."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 2 or not lines[0].lower().startswith("date"):
        return []
    rows = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 5:
            continue
        try:
            rows.append({
                "date": parts[0],
                "open": float(parts[1]), "high": float(parts[2]),
                "low": float(parts[3]), "close": float(parts[4]),
                "volume": float(parts[5]) if len(parts) > 5 and parts[5] not in ("", "N/A") else 0.0,
            })
        except ValueError:
            continue
    return rows


# --------------------------------------------------------------------------
# Indicator math (pure Python — offline testable)
# --------------------------------------------------------------------------
def _sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _ema_series(values, period):
    if not values:
        return []
    k = 2 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow:
        return None
    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    return macd_line[-1]


def _bollinger(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None, None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    std = var ** 0.5
    return mid + mult * std, mid - mult * std


def compute_indicators(rows) -> dict:
    """Compute the five indicators the pipeline expects from OHLCV rows."""
    closes = [r["close"] for r in rows if r.get("close") is not None]
    if not closes:
        return {}
    ub, lb = _bollinger(closes)
    ind = {
        "rsi": _rsi(closes),
        "macd": _macd(closes),
        "close_50_sma": _sma(closes, 50) or _sma(closes, min(len(closes), 20)),
        "boll_ub": ub,
        "boll_lb": lb,
    }
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in ind.items() if v is not None}


def to_csv_string(rows, limit: int = 40) -> str:
    """Render rows back to the CSV shape downstream code parses for entry price."""
    head = "Date,Open,High,Low,Close,Volume"
    body = [f"{r['date']},{r['open']},{r['high']},{r['low']},{r['close']},{int(r.get('volume', 0))}"
            for r in rows[-limit:]]
    return "\n".join([head] + body)


def latest_close(rows):
    for r in reversed(rows):
        if r.get("close") is not None:
            return r["close"]
    return None


def window_dates(end_date: str, days_back: int = 220):
    """Give a (start, end) covering enough history to compute a 50-day SMA."""
    end = datetime.strptime(end_date, "%Y-%m-%d")
    start = end - timedelta(days=days_back)
    return start.strftime("%Y-%m-%d"), end_date


def fetch_recent_news(symbol: str, limit: int = 8):
    """✅ CHANGED (DSE): a live DSE trading code routes to bdshare/
    sharenews24 instead of Google News RSS, which returns nothing
    meaningful for Dhaka-listed tickers. Same {title, source, url} row
    shape either way."""
    from tradingagents.dataflows.symbol_utils import is_dse_ticker
    if is_dse_ticker(symbol):
        return _fetch_dse_news(symbol, limit)

    """Real, keyless headlines via Google News RSS. Fails soft to []."""
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(symbol + ' stock')}&hl=en-US&gl=US&ceid=US:en"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            xml = resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    return _parse_rss_titles(xml, limit)


_BDSHARE_DIAG_LOGGED = False


def _log_bdshare_news_functions_once() -> None:
    """Logs, once per process, which of the 4 bdshare news functions this
    file relies on are actually present in the installed bdshare version --
    so a Railway log immediately shows "bdshare 1.x has get_all_news but NOT
    get_agm_news" instead of leaving that to be inferred from 4 separate
    per-request ImportError lines."""
    global _BDSHARE_DIAG_LOGGED
    if _BDSHARE_DIAG_LOGGED:
        return
    _BDSHARE_DIAG_LOGGED = True
    try:
        import bdshare
        version = getattr(bdshare, "__version__", "unknown")
        wanted = ["get_all_news", "get_price_sensitive_news", "get_corporate_announcements", "get_agm_news"]
        present = {name: hasattr(bdshare, name) for name in wanted}
        _log.info("bdshare version=%s, news functions available: %s", version, present)
    except Exception as e:  # noqa: BLE001
        _log.warning("Could not introspect bdshare for diagnostics: %s", e)


# ✅ CHANGED: sharenews24/stocknow/amarstock were matching headlines against
# the FULL fallback company name only (e.g. "Square Pharmaceuticals") --
# these sites commonly use a shorter form ("Square Pharma") or drop the
# corporate suffix ("... Ltd"/"... PLC"), so an exact full-name substring
# check silently returns 0 matches even when the site has real coverage.
# This builds a small set of looser variants (full name, name minus a
# trailing corporate suffix, first two words, first word) and matches
# against any of them instead of just the one exact string.
#
# ✅ Module-level (not a closure inside _fetch_dse_news) so dse_news.py's
# TradingAgents-pipeline implementation can reuse the exact same matching
# logic instead of re-implementing (and re-diverging from) it.
def _company_name_variants(name: str) -> list[str]:
    name = (name or "").strip()
    if not name:
        return []
    variants = {name}
    lname = name.lower()
    for suffix in (" plc", " limited", " ltd", " ltd.", " co.", " company", " inc"):
        if lname.endswith(suffix):
            variants.add(name[: -len(suffix)].strip())
    words = [w for w in re.split(r"\s+", name) if w.lower() != "the"]
    if len(words) >= 2:
        variants.add(" ".join(words[:2]))
    if words:
        variants.add(words[0])
    # Keep the original full name regardless of length; drop derived
    # variants under 4 chars (too generic, risks matching unrelated
    # headlines).
    return [v for v in variants if v == name or len(v) >= 4]


def _title_matches_company(title: str, variants: list[str]) -> bool:
    t = title.lower()
    return any(v.lower() in t for v in variants)


def _fetch_dse_news(symbol: str, limit: int = 8, debug_sink: list | None = None):
    """DSE headlines from every bdshare feed + sharenews24.com.

    bdshare sources (matched by ticker code — reliable, structured, but no
    per-article URL since these are disclosure rows, not articles):
      - get_all_news                general dsebd.org disclosures
      - get_price_sensitive_news    price-sensitive announcements
      - get_corporate_announcements separate announcement feed (criteria=2)
      - get_agm_news                AGM/dividend declarations — has no
                                     per-ticker filter, so this fetches all
                                     companies and filters by name locally

    sharenews24.com (matched by company NAME, not ticker code — headlines
    reference "Square Pharmaceuticals", never "SQURPHARMA", so matching the
    raw ticker here silently never fires). Only attempted for tickers in
    _DSE_COMPANY_NAMES below; these DO carry real clickable article URLs.

    Fails soft — any single source failing just means fewer results, never
    a crash, and never raises.

    ✅ CHANGED: debug_sink, if given a list, receives every diagnostic line
    this function already logs (via a temporary logging.Handler attached to
    the module logger for the duration of this call) -- lets a debug API
    route return the *same* diagnostics that would otherwise only be
    visible in Railway's Deploy Logs, for when navigating that UI is the
    actual blocker rather than the DSE-fetching logic itself.
    """
    _debug_handler = None
    if debug_sink is not None:
        import logging as _logging

        class _SinkHandler(_logging.Handler):
            def emit(self, record):
                debug_sink.append(f"{record.levelname}: {record.getMessage()}")

        _debug_handler = _SinkHandler()
        _log.addHandler(_debug_handler)
        _log.setLevel(_logging.INFO)

    seen_titles = set()
    sym = symbol.upper()
    # urllib.parse.quote, not an f-string interpolation — some real DSE
    # tickers contain parentheses (e.g. AMCL(PRAN)), which break Markdown
    # link syntax if dropped into a URL unescaped.
    # ✅ CHANGED: news_archive.php?symbol= was the wrong page — confirmed
    # against the live site. bdshare's own DSE_NEWS_URL constant (what
    # get_all_news/get_price_sensitive_news/get_corporate_announcements
    # actually fetch under the hood) is old_news.php with inst/criteria/
    # archive params — this now points users at the same page our own
    # data is sourced from.
    dsebd_archive_url = (
        f"https://www.dsebd.org/old_news.php"
        f"?inst={urllib.parse.quote(sym)}&criteria=3&archive=news"
    )
    import re
    # ✅ FIXED: \b...\b silently never matches for tickers that start/end in
    # a non-word character (e.g. "AMCL(PRAN)" — \b requires a word<->non-word
    # transition, and the character after the escaped trailing ")" in real
    # text is *also* non-word, so no boundary ever exists there). Using an
    # explicit "not adjacent to another alnum char" lookaround instead of \b
    # gives the same anti-false-positive behavior (won't match "GP" inside
    # "GPHISPAT") while actually working for punctuation-containing tickers.
    _sym_pattern = re.compile(rf"(?<![A-Z0-9]){re.escape(sym)}(?![A-Z0-9])")

    # ✅ CHANGED: capped and grouped per *website*, not per bdshare feed.
    # dsebd.org alone has 4 sub-feeds (get_all_news, price-sensitive,
    # corporate, AGM) that could each contribute PER_WEBSITE_CAP items —
    # left uncapped as a group, dsebd.org alone could fill the entire
    # `limit` before sharenews24/stocknow/amarstock ever got a turn. Now
    # all 4 dsebd sub-feeds share one bucket capped at PER_WEBSITE_CAP
    # total, same as each of the other 3 sites — so with the default
    # limit=12 you get up to 3 items from each of the 4 websites, evenly.
    PER_WEBSITE_CAP = 3
    groups: dict[str, list[dict]] = {
        "dsebd.org": [],
        "sharenews24.com": [],
        "stocknow.com.bd": [],
        "amarstock.com": [],
    }

    def _add_rows(df, source_label, group="dsebd.org", require_symbol_match=True):
        """✅ CHANGED: get_price_sensitive_news/get_corporate_announcements
        accept a code= filter but DSE's own server ignores it for these two
        categories (confirmed live: ABBANK's feed returned EXCH/EIL/IPDC
        items unrelated to ABBANK) — so every row is re-checked here for an
        actual ticker match before being kept, regardless of which bdshare
        call it came from. Also dedupes by title text, since price-sensitive
        and corporate turned out to be near-identical unfiltered feeds.
        `group` buckets all 4 dsebd sub-feeds into one shared, capped list
        (see PER_WEBSITE_CAP note above).

        ✅ FIXED: get_agm_news()'s rows have NO ticker/code column at all —
        only company/yearEnd/dividend/agmDate/recordDate/venue/time — so the
        \bTICKER\b re-check below can never match those rows (a raw code
        like "SQURPHARMA" doesn't appear verbatim in a dividend/date value).
        Applying it there silently zeroed out the AGM feed unconditionally,
        even after the caller had already correctly filtered by company
        name. `require_symbol_match=False` lets the AGM call site opt out of
        this second, doomed-to-fail filter since it already did its own
        (correct) company-name filtering before calling in here."""
        bucket = groups[group]
        if df is None or df.empty:
            return 0
        n = 0
        for _, row in df.iterrows():
            if len(bucket) >= PER_WEBSITE_CAP:
                break
            text = " | ".join(str(v) for v in row.values if str(v).strip())
            if not text:
                continue
            if require_symbol_match and not _sym_pattern.search(text.upper()):
                continue
            if text in seen_titles:
                continue
            seen_titles.add(text)
            trimmed = text if len(text) <= 300 else text[:300].rstrip() + "…"
            bucket.append({"title": trimmed, "source": source_label, "url": dsebd_archive_url})
            n += 1
        return n

    _log_bdshare_news_functions_once()

    try:
        from bdshare import get_all_news
        with _DSEBD_CONCURRENCY:
            df_all = get_all_news(code=sym)
        n = _add_rows(df_all, "dsebd.org")
        _log.info("DSE news[%s] get_all_news: %d rows", sym, n)
    except Exception as e:  # noqa: BLE001
        _log.warning("DSE news[%s] get_all_news failed: %s", sym, e)

    try:
        from bdshare import get_price_sensitive_news
        with _DSEBD_CONCURRENCY:
            df_psn = get_price_sensitive_news(code=sym)
        n = _add_rows(df_psn, "dsebd.org (price-sensitive)")
        _log.info("DSE news[%s] get_price_sensitive_news: %d rows", sym, n)
    except ImportError as e:
        _log.warning("DSE news[%s] bdshare has no get_price_sensitive_news in this installed "
                      "version (%s) — not a network issue, check `pip show bdshare`.", sym, e)
    except Exception as e:  # noqa: BLE001
        _log.warning("DSE news[%s] get_price_sensitive_news failed: %s", sym, e)

    try:
        from bdshare import get_corporate_announcements
        with _DSEBD_CONCURRENCY:
            df_corp = get_corporate_announcements(code=sym)
        n = _add_rows(df_corp, "dsebd.org (corporate)")
        _log.info("DSE news[%s] get_corporate_announcements: %d rows", sym, n)
    except ImportError as e:
        _log.warning("DSE news[%s] bdshare has no get_corporate_announcements in this installed "
                      "version (%s) — not a network issue, check `pip show bdshare`.", sym, e)
    except Exception as e:  # noqa: BLE001
        _log.warning("DSE news[%s] get_corporate_announcements failed: %s", sym, e)

    company_name = _load_dse_company_names().get(sym)
    _log.info("DSE news[%s] company_name lookup: %r", sym, company_name)
    # ✅ CHANGED: combine English variants with any known Bengali forms for
    # this ticker (see _DSE_COMPANY_NAMES_BENGALI_FALLBACK note above) --
    # sharenews24.com/amarstock.com write almost exclusively in Bengali, so
    # the English-only variant list never matched their real headlines.
    all_name_variants = (
        _company_name_variants(company_name) + _DSE_COMPANY_NAMES_BENGALI_FALLBACK.get(sym, [])
        if company_name else []
    )

    try:
        from bdshare import get_agm_news
        with _DSEBD_CONCURRENCY:
            df = get_agm_news()
        if df is not None and not df.empty and "company" in df.columns:
            needle = (company_name or sym).lower()
            mask = df["company"].astype(str).str.lower().str.contains(needle, regex=False, na=False)
            n = _add_rows(df[mask], "dsebd.org (AGM/dividend)", require_symbol_match=False)
            _log.info("DSE news[%s] get_agm_news: %d/%d rows matched %r", sym, n, len(df), needle)
        else:
            _log.info("DSE news[%s] get_agm_news: empty or no 'company' column", sym)
    except ImportError as e:
        _log.warning("DSE news[%s] bdshare has no get_agm_news in this installed "
                      "version (%s) — not a network issue, check `pip show bdshare`.", sym, e)
    except Exception as e:  # noqa: BLE001
        _log.warning("DSE news[%s] get_agm_news failed: %s", sym, e)

    if not company_name:
        _log.info("DSE news[%s] skipping sharenews24 — no company name mapped for this ticker", sym)
    else:
        try:
            articles = _load_sharenews24_articles()
            bucket = groups["sharenews24.com"]
            variants = all_name_variants
            matched = 0
            for art in articles:
                # ✅ FIXED: match against the full article body, not just the
                # headline -- see _load_sharenews24_articles' docstring.
                haystack = f"{art['title']} {art['text']}"
                if not _title_matches_company(haystack, variants):
                    continue
                bucket.append({"title": art["title"], "source": "sharenews24.com", "url": art["url"]})
                matched += 1
                if matched >= PER_WEBSITE_CAP:
                    break
            _log.info("DSE news[%s] sharenews24: %d/%d cached articles matched %r (variants tried: %r)",
                      sym, matched, len(articles), company_name, variants)
            if matched == 0 and articles:
                _log.info("DSE news[%s] sharenews24 sample titles (no match found): %r",
                          sym, [a["title"] for a in articles[:10]])
        except Exception as e:  # noqa: BLE001
            _log.warning("DSE news[%s] sharenews24 fetch failed: %s", sym, e)

    # stocknow.com.bd — ⚠️ this site's news page is JS-rendered per
    # DSE_VENDOR_README.md's own notes (a discover_stocknow_api.py tool was
    # written to find the underlying data API but never wired in here). A
    # plain requests+BeautifulSoup fetch below may legitimately come back
    # with 0 matches even though the page "looks" like it loaded — the log
    # line distinguishes a real fetch failure from "page loaded, but the
    # actual headlines are injected by JS after load, so the raw HTML we
    # got has no <a> tags with real article text in them."
    if company_name:
        try:
            req = urllib.request.Request(
                "https://www.stocknow.com.bd/news",
                headers={"User-Agent": "Mozilla/5.0 (compatible; personal-research-bot/1.0)"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", "?")
                html = resp.read().decode("utf-8", "replace")
            _log.info("DSE news[%s] stocknow.com.bd fetched: HTTP %s, %d bytes", sym, status, len(html))
            if len(html) < 10000:
                _log.info("DSE news[%s] stocknow.com.bd raw HTML sample (page is small — "
                          "checking for JS-shell): %r", sym, html[:600])
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            all_links = soup.find_all("a", href=True)
            matched = 0
            bucket = groups["stocknow.com.bd"]
            variants = _company_name_variants(company_name)
            for link in all_links:
                title = link.get_text(strip=True)
                if len(title) < 15 or not _title_matches_company(title, variants):
                    continue
                href = link["href"]
                full_link = href if href.startswith("http") else f"https://www.stocknow.com.bd{href}"
                bucket.append({"title": title, "source": "stocknow.com.bd", "url": full_link})
                matched += 1
                if matched >= PER_WEBSITE_CAP:
                    break
            _log.info("DSE news[%s] stocknow.com.bd: %d/%d links matched %r (0 total links found "
                      "usually means JS-rendered content — see comment above)",
                      sym, matched, len(all_links), company_name)
        except Exception as e:  # noqa: BLE001
            _log.warning("DSE news[%s] stocknow.com.bd fetch failed: %s", sym, e)

    # amarstock.com/dse-news — same best-effort static-HTML approach as
    # sharenews24; unverified against the live site (not reachable from my
    # sandbox), so the log line is the only way to know if this worked.
    if company_name:
        try:
            req = urllib.request.Request(
                "https://www.amarstock.com/dse-last-7-days-news",
                headers={"User-Agent": "Mozilla/5.0 (compatible; personal-research-bot/1.0)"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", "?")
                html = resp.read().decode("utf-8", "replace")
            _log.info("DSE news[%s] amarstock.com fetched: HTTP %s, %d bytes", sym, status, len(html))
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            all_links = soup.find_all("a", href=True)
            matched = 0
            bucket = groups["amarstock.com"]
            variants = all_name_variants
            for link in all_links:
                title = link.get_text(strip=True)
                if len(title) < 15 or not _title_matches_company(title, variants):
                    continue
                href = link["href"]
                full_link = href if href.startswith("http") else f"https://www.amarstock.com{href}"
                bucket.append({"title": title, "source": "amarstock.com", "url": full_link})
                matched += 1
                if matched >= PER_WEBSITE_CAP:
                    break
            # ✅ CHANGED: confirmed live (Railway log) that amarstock.com's
            # <a href> links are all nav-menu chrome ("Online Training",
            # "Compare Sector PE", ...) -- the real news text isn't inside
            # anchor tags at all. Fall back to scanning ALL page text lines
            # (not just links) when the anchor pass found nothing; matched
            # lines get the page URL itself as their link (no per-article
            # URL exists for a plain-text hit, same convention as the dsebd
            # disclosure rows above).
            if matched == 0:
                page_text = soup.get_text("\n")
                for line in page_text.splitlines():
                    line = line.strip()
                    if len(line) < 15 or not _title_matches_company(line, variants):
                        continue
                    if line in seen_titles:
                        continue
                    seen_titles.add(line)
                    trimmed = line if len(line) <= 300 else line[:300].rstrip() + "…"
                    bucket.append({"title": trimmed, "source": "amarstock.com", "url": "https://www.amarstock.com/dse-last-7-days-news"})
                    matched += 1
                    if matched >= PER_WEBSITE_CAP:
                        break
                _log.info("DSE news[%s] amarstock.com: text-line fallback found %d matches "
                          "(anchor-tag pass found 0)", sym, matched)
            _log.info("DSE news[%s] amarstock.com: %d/%d links matched %r (variants tried: %r)",
                      sym, matched, len(all_links), company_name, variants)
            if matched == 0 and all_links:
                qualifying = [l.get_text(strip=True) for l in all_links if len(l.get_text(strip=True)) >= 15]
                step = max(1, len(qualifying) // 15)
                sample = qualifying[::step][:15]
                _log.info("DSE news[%s] amarstock.com sample headlines (spread across page, no match found): %r", sym, sample)
        except Exception as e:  # noqa: BLE001
            _log.warning("DSE news[%s] amarstock.com fetch failed: %s", sym, e)

    # ✅ CHANGED: round-robin across the 4 website buckets (dsebd.org,
    # sharenews24.com, stocknow.com.bd, amarstock.com) instead of
    # concatenating them in collection order — each bucket is already
    # capped at PER_WEBSITE_CAP (3) above, so this just interleaves them
    # (dsebd[0], sharenews24[0], stocknow[0], amarstock[0], dsebd[1], ...)
    # so a page showing `limit` items sees a mix of all 4 sources instead
    # of e.g. 3 dsebd items followed by 3 amarstock items back-to-back.
    # A source that failed/returned nothing simply contributes 0 without
    # breaking the interleave (its slot is skipped, not left blank).
    order = ["dsebd.org", "sharenews24.com", "stocknow.com.bd", "amarstock.com"]
    merged: list[dict] = []
    for i in range(PER_WEBSITE_CAP):
        for src in order:
            bucket = groups[src]
            if i < len(bucket):
                merged.append(bucket[i])
    if _debug_handler is not None:
        _log.removeHandler(_debug_handler)
    return merged[:limit]


# Best-effort ticker -> common company name, needed because sharenews24.com
# (and get_agm_news's company-name matching) reference companies by name,
# never by DSE trading code. Not exhaustive — extend as you cover more
# tickers. A ticker missing here still gets full bdshare coverage above
# (those match by code, not name); it just skips the sharenews24 pass and
# falls back to code-matching against get_agm_news's company column, which
# usually won't hit.
_DSE_COMPANY_NAMES_FALLBACK = {
    "SQURPHARMA": "Square Pharmaceuticals",
    "GP": "Grameenphone",
    "BEXIMCO": "Beximco",
    "RFL": "RFL",
    "BATBC": "British American Tobacco Bangladesh",
    "ROBI": "Robi Axiata",
    "WALTONHIL": "Walton Hi-Tech",
    "BRACBANK": "BRAC Bank",
    "ISLAMIBANK": "Islami Bank",
    "LHBL": "LafargeHolcim Bangladesh",
    "ABBANK": "AB Bank",
    "CITY": "City Bank",
    "RANFOUNDRY": "Rangpur Foundry",
    "AMCL(PRAN)": "Agricultural Marketing Company",
}

# ✅ CHANGED: sharenews24.com (confirmed live, see Railway log sample) and
# amarstock.com write Bengali-language headlines almost exclusively -- the
# English fallback name above ("Square Pharmaceuticals") never appears in
# their text, so matching against it alone silently returns 0 every time,
# regardless of how the English matching logic is tuned. This is a
# best-effort, hand-typed set of the common Bengali forms for the same 11
# tickers -- NOT verified against live headlines from this environment
# (network-restricted sandbox), so treat these as a starting point: check
# the "sample headlines" log lines after deploying and correct any that
# don't actually match real article text.
_DSE_COMPANY_NAMES_BENGALI_FALLBACK = {
    "SQURPHARMA": ["স্কয়ার ফার্মা", "স্কয়ার ফার্মাসিউটিক্যালস"],
    # ✅ CHANGED: added the common alternate spelling with a space
    # ("গ্রামীণ ফোন") alongside the closed-up form -- Bengali outlets aren't
    # consistent about this, and a pure substring match misses one if only
    # the other is listed.
    "GP": ["গ্রামীণফোন", "গ্রামীণ ফোন"],
    "BEXIMCO": ["বেক্সিমকো"],
    "RFL": ["আরএফএল"],
    "BATBC": ["বিএটিবিসি", "ব্রিটিশ আমেরিকান টোব্যাকো"],
    "ROBI": ["রবি"],
    "WALTONHIL": ["ওয়ালটন"],
    "BRACBANK": ["ব্র্যাক ব্যাংক"],
    # ✅ FIXED: bare "ইসলামী ব্যাংক" (= generic "Islami Bank") is a substring
    # of SEVERAL other DSE-listed banks' actual Bengali names -- confirmed
    # live, e.g. sharenews24.com's own homepage carries "আল-আরাফাহ্‌ ইসলামী
    # ব্যাংকের দ্বিতীয় প্রান্তিক প্রকাশ" (Al-Arafah Islami Bank), and the same
    # collision applies to Social Islami Bank / First Security Islami Bank /
    # Shahjalal Islami Bank -- none of which are ISLAMIBANK (Islami Bank
    # Bangladesh PLC). With PER_WEBSITE_CAP=3, those wrong-company matches
    # can fill (or crowd out) the 3 sharenews24 slots before this ticker's
    # own real news is ever reached, which is exactly what was reported as
    # "no sharenews24 news for Islami Bank". "বাংলাদেশ" only appears in this
    # company's actual name among the "ইসলামী ব্যাংক" companies (confirmed
    # against a live sharenews24 article: "ইসলামী ব্যাংক বাংলাদেশ পিএলসি"),
    # so anchoring on the fuller phrase disambiguates it.
    "ISLAMIBANK": ["ইসলামী ব্যাংক বাংলাদেশ"],
    "LHBL": ["লাফার্জহোলসিম"],
    "ABBANK": ["এবি ব্যাংক"],
    "CITY": ["সিটি ব্যাংক"],
    "RANFOUNDRY": ["রংপুর ফাউন্ড্রি"],
    "AMCL(PRAN)": ["প্রাণ"],
}

_DSE_COMPANY_NAMES_LIVE: dict[str, str] | None = None
_DSE_COMPANY_NAMES_TS = 0.0
_DSE_COMPANY_NAMES_TTL = 6 * 60 * 60  # names rarely change; matches the ticker-list cache TTL


def _load_dse_company_names() -> dict[str, str]:
    """✅ CHANGED: full ticker -> company-name map for ALL ~650 DSE-listed
    companies, scraped once (then cached 6h) from dsebd.org's own company
    directory — instead of relying on a small hand-typed dict that only
    covers whichever tickers we happened to test (e.g. RANFOUNDRY had real
    amarstock.com coverage the whole time; we just hadn't typed its name in
    yet). The page lists entries as "TICKER (Full Company Name)" in plain
    text, so this is parsed with one regex rather than depending on exact
    table markup, which makes it more resilient to a page redesign.

    _DSE_COMPANY_NAMES_FALLBACK's hand-verified names always win over the
    parsed ones (applied last via .update()), and the whole fallback dict
    is used outright if the live fetch fails for any reason — so a
    dsebd.org outage degrades to "only the 11 verified tickers work",
    never to a crash.
    """
    global _DSE_COMPANY_NAMES_LIVE, _DSE_COMPANY_NAMES_TS
    now = _time.time()
    if _DSE_COMPANY_NAMES_LIVE is not None and (now - _DSE_COMPANY_NAMES_TS) < _DSE_COMPANY_NAMES_TTL:
        return _DSE_COMPANY_NAMES_LIVE

    try:
        # ✅ FIXED: dsebd.org's cert chain is missing an intermediate
        # (Sectigo DV R36) — that's the actual root cause, and bdshare's own
        # fix is bundling that intermediate into its shared requests.Session
        # (bdshare.util.helper._session, .verify = certifi + the missing
        # intermediate), not primarily "try a different host." The dual-host
        # fallback in bdshare's _request() is a separate, orthogonal retry
        # mechanism. The previous version of this function used a fresh
        # requests.get() (default certifi bundle, no added intermediate) for
        # BOTH hosts — if dse.com.bd shares the same incomplete chain as
        # dsebd.org (unverified, plausible), both attempts fail identically
        # and this silently degrades to the 11-ticker hardcoded fallback
        # every time, with no error surfaced. Reusing bdshare's own patched
        # session fixes the cert problem at its actual source regardless of
        # which host is hit, while the host loop remains as a genuine
        # fallback for real outages (not cert issues).
        from bdshare.util.helper import _session as _bdshare_session
        html = None
        host_errors = {}
        for host in ("https://www.dsebd.org", "https://dse.com.bd"):
            try:
                with _DSEBD_CONCURRENCY:
                    # 🔴 FIXED (confirmed live, 403 Forbidden on the sibling
                    # displayCompany.php endpoint with this exact same
                    # override -- see dse_fundamentals.py's _throttled_get):
                    # overriding bdshare's own working User-Agent
                    # ("bdshare/2.0 (...)") with a custom one was the likely
                    # cause. This endpoint has a fallback (the 11-ticker
                    # hardcoded dict below) so a 403 here wouldn't have
                    # raised a visible error -- it would have just silently
                    # degraded every non-hardcoded ticker to "no sharenews24
                    # coverage" forever. Dropping the override, same as the
                    # fundamentals fix.
                    resp = _bdshare_session.get(
                        f"{host}/company_listing.php",
                        timeout=15,
                    )
                    resp.raise_for_status()
                    html = resp.text
                break
            except Exception as e:  # noqa: BLE001
                # ✅ FIXED: previously only the LAST host's error was kept
                # (last_exc), so a log line like "dse.com.bd: 403" told you
                # nothing about whether www.dsebd.org failed the same way or
                # differently. Logging both is purely diagnostic -- doesn't
                # change what requests get made.
                host_errors[host] = repr(e)
                continue
        if html is None:
            _log.warning("DSE company listing: all hosts failed — %s", host_errors)
            raise RuntimeError(f"both dsebd.org and dse.com.bd failed: {host_errors}")
        from bs4 import BeautifulSoup
        text = BeautifulSoup(html, "html.parser").get_text(" ")
        pairs = re.findall(r"\b([A-Z][A-Z0-9]{1,15})\s*\(([^)]{3,80})\)", text)
        mapping = {}
        for ticker, name in pairs:
            # Some company names contain their own parens, e.g. "RAK
            # Ceramics (Bangladesh) Limited" — the regex above stops at the
            # FIRST ")", so `name` here would be the truncated fragment
            # "RAK Ceramics (Bangladesh" (dangling open paren and all).
            # That fragment is a *worse* match target than just "RAK
            # Ceramics", since a real headline is more likely to use the
            # short name alone — so trim anything from the stray "(" on.
            if "(" in name:
                name = name.split("(")[0].strip()
            if name:
                mapping[ticker.strip()] = name
        if len(mapping) < 100:  # real listing has ~650 entries — a much
            # smaller count means the page structure likely changed and
            # this regex isn't matching it correctly anymore; don't cache
            # a broken partial result, fall through to the fallback dict.
            raise ValueError(f"only parsed {len(mapping)} entries — page structure may have changed")
        mapping.update(_DSE_COMPANY_NAMES_FALLBACK)
        _DSE_COMPANY_NAMES_LIVE = mapping
        _DSE_COMPANY_NAMES_TS = now
        _log.info("Loaded %d DSE company names from dsebd.org's company listing.", len(mapping))
        return mapping
    except Exception as e:  # noqa: BLE001
        _log.warning("Could not load DSE company name listing (%s); falling back to %d hardcoded names.",
                     e, len(_DSE_COMPANY_NAMES_FALLBACK))
        return dict(_DSE_COMPANY_NAMES_FALLBACK)


def _parse_rss_titles(xml: str, limit: int):
    import re
    items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
    out = []
    for it in items[:limit]:
        m = re.search(r"<title>(.*?)</title>", it, re.DOTALL)
        if not m:
            continue
        title = m.group(1)
        title = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", title).strip()
        src = re.search(r"<source[^>]*>(.*?)</source>", it, re.DOTALL)
        link_m = re.search(r"<link>(.*?)</link>", it, re.DOTALL)
        url = link_m.group(1).strip() if link_m else None
        out.append({
            "title": title,
            "source": (src.group(1).strip() if src else "News"),
            "url": url,
        })
    return out


import urllib.parse  # noqa: E402  (used by fetch_recent_news)
