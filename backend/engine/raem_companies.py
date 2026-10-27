"""Companies added from the website, merged into predictor_lib.TICKERS.

The eight research companies stay in predictor_lib.TICKERS.  A company added on the website is saved in
backend/data/companies.json (override: RAEM_COMPANIES) and registered here at import time, so forecast_lib, the
exporters and the notebooks all see it like a built-in one.  Nothing here needs yfinance or the network.
"""
from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# Exchange presets by ticker suffix: currency, time zone, minutes-after-which today's bar is final, weekend days (Mon=0)
SUFFIX = {
    ".HK": ("HKD", "Asia/Hong_Kong", "16:30", (5, 6)),
    ".SR": ("SAR", "Asia/Riyadh", "15:30", (4, 5)),
    ".L": ("GBP", "Europe/London", "16:45", (5, 6)),
    ".T": ("JPY", "Asia/Tokyo", "15:30", (5, 6)),
    ".NS": ("INR", "Asia/Kolkata", "15:45", (5, 6)),
    ".BO": ("INR", "Asia/Kolkata", "15:45", (5, 6)),
    ".TO": ("CAD", "America/Toronto", "16:15", (5, 6)),
    ".DE": ("EUR", "Europe/Berlin", "17:45", (5, 6)),
    ".PA": ("EUR", "Europe/Paris", "17:45", (5, 6)),
    ".AX": ("AUD", "Australia/Sydney", "16:15", (5, 6)),
    ".SI": ("SGD", "Asia/Singapore", "17:15", (5, 6)),
    ".KS": ("KRW", "Asia/Seoul", "15:45", (5, 6)),
    ".SS": ("CNY", "Asia/Shanghai", "15:15", (5, 6)),
    ".SZ": ("CNY", "Asia/Shanghai", "15:15", (5, 6)),
    ".AE": ("AED", "Asia/Dubai", "15:15", (5, 6)),
}
US = ("USD", "America/New_York", "16:30", (5, 6))
DSE = {"currency": "BDT", "source": "dse", "tz": "Asia/Dhaka", "final_after": "15:00", "weekend": (4, 5)}

_TICKER_OK = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")


def path() -> str:
    return os.environ.get("RAEM_COMPANIES") or os.path.join(os.path.dirname(HERE), "data", "companies.json")


def normalize(raw: str) -> str:
    """Upper-case ticker; raises ValueError when it could not be a ticker (spaces, slashes, too long)."""
    t = (raw or "").strip().upper()
    if not _TICKER_OK.match(t):
        raise ValueError(f"{raw!r} is not a ticker. Use letters, digits, dots and dashes, e.g. TSLA, 7203.T, SQURPHARMA.")
    return t


def load() -> dict:
    try:
        with open(path(), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(companies: dict) -> None:
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(companies, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def meta_for(ticker: str, market: str, name: str | None = None, currency: str | None = None, tz: str | None = None) -> dict:
    """The predictor_lib.TICKERS entry for a new company.  market is 'dse' or 'international'."""
    if market == "dse":
        return {"name": name or ticker, **DSE, "weekend": list(DSE["weekend"]), "group": "dse"}
    suffix = next((s for s in SUFFIX if ticker.endswith(s)), None)
    cur, zone, final, weekend = SUFFIX[suffix] if suffix else US
    return {"name": name or ticker, "currency": currency or cur, "source": "yfinance", "tz": tz or zone,
            "final_after": final, "weekend": list(weekend), "group": "international"}


def add(ticker: str, meta: dict) -> None:
    c = load()
    c[ticker] = meta
    save(c)


def file_slug(name: str) -> str:
    """'Tesla, Inc.' -> 'Tesla_Inc' (the name part of data/<Name>_<TICKER>_2y.csv)."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_")
    return s or "Company"


def register(tickers: dict, fx_pairs: dict | None = None) -> list[str]:
    """Add the saved companies to predictor_lib's tables; the built-in companies are never replaced."""
    added = []
    for t, m in load().items():
        if t in tickers or not isinstance(m, dict):
            continue
        tickers[t] = {k: (tuple(v) if k == "weekend" else v) for k, v in m.items() if k != "group"}
        if fx_pairs is not None and m.get("currency") and m["currency"] not in fx_pairs:
            fx_pairs[m["currency"]] = f"USD{m['currency']}=X"
        added.append(t)
    return added
