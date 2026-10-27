"""Symbol normalization and market-data error types for vendor calls.

Yahoo Finance (the default vendor) uses specific ticker conventions that
differ from the broker / TradingView / MT5 style symbols users often type:

    user types        Yahoo wants       why
    ---------------   ---------------   -----------------------------------
    XAUUSD, XAUUSD+   GC=F              gold has no forex pair on Yahoo;
                                        it is quoted as a COMEX future
    EURUSD            EURUSD=X          spot forex pairs take a ``=X`` suffix
    BTCUSD            BTC-USD           crypto pairs use a ``-`` separator
    SPX500, US500     ^GSPC             index CFDs map to Yahoo index symbols

Passing the raw broker symbol to Yahoo returns an empty result, which the
agents previously received as free text and could hallucinate a price
around (see issue #781). Centralizing the mapping here means every yfinance
entry point resolves symbols the same way, and new instruments are added by
appending a table row rather than editing call sites.
"""

from __future__ import annotations

import logging
import re

# NoMarketDataError lives in the vendor-error taxonomy (errors.py); re-exported
# here for the many call sites that import it alongside normalize_symbol.
from .errors import NoMarketDataError as NoMarketDataError

logger = logging.getLogger(__name__)


# ISO-4217 codes common enough to appear in retail forex pairs. A bare
# six-letter symbol whose halves are BOTH in this set is treated as a spot
# forex pair and given Yahoo's ``=X`` suffix.
_FOREX_CURRENCIES = frozenset(
    {
        "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
        "CNY", "CNH", "HKD", "SGD", "SEK", "NOK", "DKK", "PLN",
        "MXN", "ZAR", "TRY", "INR", "KRW", "BRL", "RUB", "THB",
    }
)

# Crypto bases that brokers quote against USD without a separator.
_CRYPTO_BASES = frozenset(
    {"BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LTC", "BCH", "DOT", "AVAX", "LINK"}
)

# Explicit aliases for instruments whose broker symbol does not map to a
# Yahoo symbol by rule. Metals/energy resolve to their front-month future;
# index CFD names resolve to the underlying Yahoo index symbol. Extend by
# adding rows — no call site changes required.
_ALIASES = {
    # Precious metals (spot names -> COMEX/NYMEX futures)
    "XAUUSD": "GC=F", "XAU": "GC=F", "GOLD": "GC=F",
    "XAGUSD": "SI=F", "XAG": "SI=F", "SILVER": "SI=F",
    "XPTUSD": "PL=F", "XPDUSD": "PA=F",
    # Energy
    "WTICOUSD": "CL=F", "USOIL": "CL=F", "WTI": "CL=F",
    "BCOUSD": "BZ=F", "UKOIL": "BZ=F", "BRENT": "BZ=F",
    "NATGAS": "NG=F", "XNGUSD": "NG=F",
    "COPPER": "HG=F", "XCUUSD": "HG=F",
    # Index CFDs -> Yahoo index symbols
    "SPX500": "^GSPC", "US500": "^GSPC", "SPX": "^GSPC",
    "NAS100": "^NDX", "US100": "^NDX", "USTEC": "^NDX",
    "US30": "^DJI", "DJI30": "^DJI", "WS30": "^DJI",
    "GER40": "^GDAXI", "GER30": "^GDAXI", "DE40": "^GDAXI",
    "UK100": "^FTSE", "JP225": "^N225", "JPN225": "^N225",
    "FRA40": "^FCHI", "EU50": "^STOXX50E", "HK50": "^HSI",
}

# Yahoo symbols may contain letters, digits, and these structural characters.
_YAHOO_SAFE = re.compile(r"^[A-Za-z0-9._\-\^=]+$")


# Crypto quote currencies that all map to Yahoo's USD pair. Yahoo lists only
# ``<BASE>-USD`` (not the USDT/USDC stablecoin pairs), so a broker symbol quoted
# in any of these resolves to ``-USD`` (#982). Longest first so ``USDT``/``USDC``
# match before the ``USD`` substring.
_CRYPTO_QUOTES = ("USDT", "USDC", "USD")


def _normalize_crypto(s: str) -> str | None:
    """Return ``<BASE>-USD`` if ``s`` is a known crypto quoted in USD/USDT/USDC.

    Accepts dashed or undashed forms: ``BTCUSD``, ``BTCUSDT``, ``BTC-USDT``,
    ``BTC-USDC`` all resolve to ``BTC-USD``. Returns None otherwise.
    """
    compact = s.replace("-", "")
    for quote in _CRYPTO_QUOTES:
        if compact.endswith(quote):
            base = compact[: -len(quote)]
            if base in _CRYPTO_BASES:
                return f"{base}-USD"
            break
    return None


def normalize_symbol(raw: str) -> str:
    """Map a user/broker symbol to its canonical Yahoo Finance symbol.

    Resolution order (first match wins):
      1. Explicit alias table (metals, energy, index CFDs).
      2. Crypto rule: a known crypto base quoted in USD/USDT/USDC (dashed or
         not) -> ``BASE-USD``.
      3. Forex rule: six letters that are two ISO currency codes -> ``PAIR=X``.
      4. Otherwise the upper-cased symbol is returned unchanged (plain
         equities, ETFs, Yahoo-native symbols like ``GC=F`` or ``^GSPC``).

    A trailing ``+`` (broker CFD marker, e.g. ``XAUUSD+``) is stripped before
    matching. The function is purely syntactic — it performs no network
    calls — so it is safe to apply on every request.
    """
    if not isinstance(raw, str) or not raw.strip():
        return raw

    s = raw.strip().upper()
    # Broker CFD/qualifier suffixes Yahoo never uses.
    s = s.rstrip("+")

    crypto = _normalize_crypto(s)
    if s in _ALIASES:
        canonical = _ALIASES[s]
    elif crypto is not None:
        canonical = crypto
    elif len(s) == 6 and s[:3] in _FOREX_CURRENCIES and s[3:] in _FOREX_CURRENCIES:
        canonical = f"{s}=X"
    else:
        canonical = s

    if canonical != raw.strip().upper():
        logger.info("Resolved symbol %r to Yahoo symbol %r", raw, canonical)
    return canonical


def is_yahoo_safe(symbol: str) -> bool:
    """True when ``symbol`` only contains characters Yahoo symbols use."""
    return bool(symbol) and _YAHOO_SAFE.fullmatch(symbol) is not None


# ---------------------------------------------------------------------------
# DSE (Dhaka Stock Exchange) ticker detection.
#
# There is no reliable *syntactic* way to tell a DSE trading code (e.g.
# "SQURPHARMA", "GP") apart from a US ticker by shape alone — both are
# uppercase letters. So instead of guessing with a regex, this checks
# membership against the real, current list of DSE trading codes (via
# bdshare.get_current_trading_code()), cached in-process so normal requests
# don't hit the network on every call.
#
# Any lookup failure (network blip, bdshare/dsebd unavailable) is treated as
# "not a DSE ticker" rather than raising — a transient failure here should
# fall through to the default (US) vendor chain, not break routing entirely.
# ---------------------------------------------------------------------------

import time as _time

_DSE_TICKER_CACHE: set[str] | None = None
_DSE_TICKER_CACHE_TS: float = 0.0
_DSE_TICKER_CACHE_TTL_SECONDS = 6 * 60 * 60  # refresh twice a day — DSE listings change rarely


def _load_dse_tickers() -> set[str]:
    global _DSE_TICKER_CACHE, _DSE_TICKER_CACHE_TS

    now = _time.time()
    if _DSE_TICKER_CACHE is not None and (now - _DSE_TICKER_CACHE_TS) < _DSE_TICKER_CACHE_TTL_SECONDS:
        return _DSE_TICKER_CACHE

    try:
        from bdshare import get_current_trading_code
        df = get_current_trading_code()
        tickers = {str(s).strip().upper() for s in df["symbol"] if str(s).strip()}
        _DSE_TICKER_CACHE = tickers
        _DSE_TICKER_CACHE_TS = now
        logger.info("Loaded %d DSE trading codes for ticker routing.", len(tickers))
        return tickers
    except Exception as e:
        logger.warning("Could not load DSE trading code list (%s); treating tickers as non-DSE for now.", e)
        # Don't cache a failure with a fresh timestamp — retry on the next call
        # instead of being stuck "not DSE" for the full TTL after one network blip.
        return _DSE_TICKER_CACHE or set()


def is_dse_ticker(symbol: str) -> bool:
    """True if ``symbol`` is a currently-listed DSE trading code.

    Used by route_to_vendor (interface.py) to auto-select the "dse" vendor
    for BD tickers without requiring the user to switch config per call.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        return False
    return symbol.strip().upper() in _load_dse_tickers()


# ---------------------------------------------------------------------------
# Company-name resolution (Bengali or English) -> DSE trading code.
#
# bdshare's trading-code list is symbols only, no company names ("SQURPHARMA"
# but not "Square Pharmaceuticals PLC"), and there's no verified bulk
# name<->code mapping to scrape. So instead of building a name database,
# this leans on the LLM's own general knowledge of Bangladeshi listed
# companies to *guess* a code from free text (English or Bengali), then
# validates that guess against the real trading-code list before trusting
# it — the LLM disambiguates, it never gets to invent a code that doesn't
# actually exist. Exact/fuzzy matching is tried first and is free (no LLM
# call) for the common case of the user typing the code itself.
# ---------------------------------------------------------------------------

import difflib as _difflib

_NAME_RESOLUTION_CACHE: dict[str, str | None] = {}


def resolve_dse_ticker(query: str) -> str | None:
    """
    Resolve free-text input to a real DSE trading code, or None.

    Tries, cheapest first:
      1. Exact match (case-insensitive) against the live trading-code list.
      2. Fuzzy match for near-miss typos of a trading code itself.
      3. LLM lookup for anything that looks like a company name rather than
         a bare code (contains a space, or non-ASCII/Bengali characters) --
         the model's guess is only accepted if it's an actual member of the
         real trading-code list, so a wrong or hallucinated guess degrades
         to None rather than routing to a fake ticker.

    Results are cached in-process (including "not found") so a repeated
    query — especially one that needed an LLM call — doesn't redo the work.
    """
    if not isinstance(query, str) or not query.strip():
        return None

    raw = query.strip()
    key = raw.lower()
    if key in _NAME_RESOLUTION_CACHE:
        return _NAME_RESOLUTION_CACHE[key]

    tickers = _load_dse_tickers()
    upper = raw.upper()

    # 1. Exact match.
    if upper in tickers:
        _NAME_RESOLUTION_CACHE[key] = upper
        return upper

    # 2. Fuzzy match — only for single-token queries, so a multi-word company
    # name doesn't get incorrectly snapped to some unrelated short code.
    if " " not in raw and tickers:
        close = _difflib.get_close_matches(upper, tickers, n=1, cutoff=0.8)
        if close:
            logger.info("Fuzzy-matched ticker input %r -> %r", raw, close[0])
            _NAME_RESOLUTION_CACHE[key] = close[0]
            return close[0]

    # 3. LLM disambiguation for company-name-shaped input (has a space, or
    # non-ASCII characters e.g. Bengali script).
    looks_like_name = (" " in raw) or any(ord(c) > 127 for c in raw)
    if not looks_like_name:
        _NAME_RESOLUTION_CACHE[key] = None
        return None

    guess = _llm_guess_dse_ticker(raw)
    resolved = guess if guess and guess.upper() in tickers else None
    if guess and not resolved:
        logger.info("LLM guessed ticker %r for %r but it's not a real DSE code; discarding.", guess, raw)
    _NAME_RESOLUTION_CACHE[key] = resolved
    return resolved


def _llm_guess_dse_ticker(company_query: str) -> str | None:
    try:
        from app.ai_pipeline.llm import invoke_llm_with_retry, make_llm
    except Exception as e:
        logger.warning("LLM not available for ticker-name resolution (%s); skipping.", e)
        return None

    prompt = (
        "You are identifying Dhaka Stock Exchange (DSE, Bangladesh) trading "
        "codes. The company name below may be written in Bengali or English, "
        "full or shortened. Reply with ONLY the DSE trading code in uppercase "
        "(e.g. SQURPHARMA, GP, ACI) and nothing else. If you are not "
        "confident, reply exactly: NONE\n\n"
        f"Company: {company_query}"
    )
    try:
        llm = make_llm(temperature=0)
        response = invoke_llm_with_retry(llm, prompt)
        text = (response.content or "").strip().upper()
    except Exception as e:
        logger.warning("LLM ticker-name resolution failed for %r: %s", company_query, e)
        return None

    if not text or text == "NONE" or " " in text or len(text) > 15:
        return None
    return text


# ---------------------------------------------------------------------------
# International/US company-name resolution ("Apple", "Tesla Motors" -> ticker).
#
# Unlike DSE, Yahoo Finance has its own search endpoint that already maps
# company names to tickers authoritatively (no LLM guessing needed, no
# hallucination risk) -- yfinance exposes it as yf.Search. This is only
# tried for input that doesn't already look like a ticker (see
# _looks_like_company_name), so a normal "AAPL"/"aapl" input never pays for
# an extra network call.
# ---------------------------------------------------------------------------

_GLOBAL_NAME_RESOLUTION_CACHE: dict[str, str | None] = {}


def _looks_like_company_name(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    if " " in s:
        return True
    if not is_yahoo_safe(s.upper()):
        return True
    return len(s) > 10


def resolve_global_ticker(query: str) -> str | None:
    """
    Resolve a free-text international/US company name to its ticker symbol
    via Yahoo Finance's own search (yfinance.Search) -- e.g. "Apple" -> AAPL,
    "Tesla Motors" -> TSLA. Only meant to be tried on input that doesn't
    already look like a ticker (see _looks_like_company_name); results
    (including failures) are cached in-process.
    """
    if not isinstance(query, str) or not query.strip():
        return None
    key = query.strip().lower()
    if key in _GLOBAL_NAME_RESOLUTION_CACHE:
        return _GLOBAL_NAME_RESOLUTION_CACHE[key]

    resolved = None
    try:
        import yfinance as yf
        quotes = yf.Search(query.strip(), max_results=5, news_count=0, lists_count=0).quotes
        for q in quotes:
            if q.get("quoteType") == "EQUITY" and q.get("symbol"):
                resolved = q["symbol"]
                break
        if not resolved and quotes:
            resolved = quotes[0].get("symbol")
    except Exception as e:
        logger.warning("yfinance company-name search failed for %r: %s", query, e)

    _GLOBAL_NAME_RESOLUTION_CACHE[key] = resolved
    return resolved
