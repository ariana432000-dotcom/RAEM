"""Dhaka Stock Exchange market overview for the Home page: indices, day summary, breadth, top 20 by value, news.

Source: the day-end archive of old.dsebd.org (the same page `engine/dse_archive.py` reads for one company, here asked for
"All Instrument"), the old.dsebd.org front page for the DSEX / DSES / DS30 index lines, and bdshare for the news feed.
Every part is fail-soft: a part that cannot be fetched or parsed comes back as null (or an empty list) and the page shows
"not available" for it, the other parts still load.

Market cap is not in the archive, so it is not reported. Sector breadth needs a code -> sector map in
`backend/data/dse_sectors.json` ({"ABBANK": "Bank", ...}) plus the sectors learned when a stock's page is opened
(storage/dse_sectors_learned.json, or all at once with tools/build_dse_sectors.py); without any the sector table is empty.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta

from app.core import config

CACHE_TTL = 900            # seconds a fetched overview is reused; the exchange publishes once a day
LOOKBACK_DAYS = 10         # weekends and holidays: walk back to the latest day that has rows
SECTORS_FILE = config.DATA / "dse_sectors.json"
LEARNED_SECTORS_FILE = config.STORAGE / "dse_sectors_learned.json"   # filled by company pages that were opened (services/stocks.py)
CACHE_FILE = config.STORAGE / "market_cache.json"
INDEX_NAMES = ("DSEX", "DSES", "DS30")

_lock = threading.Lock()
_mem: dict = {"at": 0.0, "data": None}


def _num(s) -> float | None:
    s = str(s if s is not None else "").replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _get(path: str, params: dict | None = None) -> str:
    """Fetch a page of old.dsebd.org with the headers and certificate fallback the price archive already uses."""
    if str(config.ENGINE) not in sys.path:
        sys.path.insert(0, str(config.ENGINE))
    import dse_archive
    return dse_archive._get(path, params or {})


def parse_archive(html: str) -> list[dict]:
    """Rows of the day-end archive table, by header name (so a moved column does not shift the numbers)."""
    from bs4 import BeautifulSoup
    table = BeautifulSoup(html, "lxml").find("table", class_="shares-table")
    if table is None or table.find("thead") is None:
        return []
    head = [th.get_text(strip=True).upper() for th in table.find("thead").find_all("th")]
    rows = []
    for tr in (table.find("tbody") or table).find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) != len(head):
            continue
        r = dict(zip(head, cells))
        code = r.get("TRADING CODE", "").strip().upper()
        if not code:
            continue
        rows.append({"code": code, "ltp": _num(r.get("LTP*")), "high": _num(r.get("HIGH")), "low": _num(r.get("LOW")),
                     "close": _num(r.get("CLOSEP*")), "ycp": _num(r.get("YCP")), "trade": _num(r.get("TRADE")),
                     "value": _num(r.get("VALUE (MN)") or r.get("VALUE")), "volume": _num(r.get("VOLUME"))})
    return rows


def _price(r: dict) -> float | None:
    return r["close"] if r.get("close") else r.get("ltp")


def summarise(rows: list[dict], sectors: dict | None = None, top: int = 20) -> dict:
    """Day totals, advanced / declined / unchanged (price against yesterday's close), sector breadth, the top shares by value and all traded issues."""
    sectors = sectors or {}
    traded = [r for r in rows if (r.get("trade") or 0) > 0]
    adv = dec = unc = 0
    by_sector: dict[str, dict] = {}
    for r in traded:
        p, y = _price(r), r.get("ycp")
        if not p or not y:
            continue
        move = (p > y) - (p < y)
        adv, dec, unc = adv + (move > 0), dec + (move < 0), unc + (move == 0)
        sec = sectors.get(r["code"])
        if sec:
            s = by_sector.setdefault(sec, {"sector": sec, "advanced": 0, "declined": 0, "unchanged": 0})
            s["advanced" if move > 0 else "declined" if move < 0 else "unchanged"] += 1
    stocks = []
    for r in sorted(traded, key=lambda r: r.get("value") or 0, reverse=True):
        p, y = _price(r), r.get("ycp")
        stocks.append({"code": r["code"], "ltp": r.get("ltp"), "close": r.get("close"), "ycp": y,
                       "change_pct": (p / y - 1) * 100 if p and y else None,
                       "trade": r.get("trade"), "value_mn": r.get("value"), "volume": r.get("volume")})
    return {"total_trade": sum(r["trade"] for r in traded), "total_volume": sum(r.get("volume") or 0 for r in traded),
            "value_mn": round(sum(r.get("value") or 0 for r in traded), 2), "traded_issues": len(traded),
            "advanced": adv, "declined": dec, "unchanged": unc,
            "sectors": sorted(({**s, "total": s["advanced"] + s["declined"] + s["unchanged"]} for s in by_sector.values()),
                              key=lambda s: s["sector"]),
            "top": stocks[:top], "stocks": stocks}   # stocks: every issue traded, so the page can rank by value, volume or trade


def parse_indices(html: str) -> list[dict]:
    """DSEX / DSES / DS30 value, point change and percent change from the front page text; an index not found is left out."""
    from bs4 import BeautifulSoup
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" ", strip=True))
    out = []
    for name in INDEX_NAMES:
        m = re.search(rf"\b{name}\b(?:\s*Index)?[^0-9\-+]{{0,30}}([\d,]+\.\d+)\s+([+\-]?[\d,]*\.?\d+)\s+\(?([+\-]?[\d.]+)\s*%?\)?", text, re.I)
        if m:
            out.append({"name": name, "value": _num(m.group(1)), "change": _num(m.group(2)), "change_pct": _num(m.group(3))})
    return out


def _sectors() -> dict:
    """Code -> sector: the committed backend/data/dse_sectors.json, then the ones learned at run time (storage/dse_sectors_learned.json)."""
    out: dict = {}
    for f in (SECTORS_FILE, LEARNED_SECTORS_FILE):
        try:
            out.update({k.upper(): v for k, v in json.loads(f.read_text(encoding="utf-8")).items() if v})
        except (OSError, ValueError):
            pass
    return out


def _archive_rows(today: date) -> tuple[date | None, list[dict]]:
    for back in range(LOOKBACK_DAYS):
        day = today - timedelta(days=back)
        if day.weekday() in (4, 5):   # the DSE trades Sunday to Thursday
            continue
        rows = parse_archive(_get("day_end_archive.php", {"startDate": day.isoformat(), "endDate": day.isoformat(),
                                                           "inst": "All Instrument", "archive": "data"}))
        if rows and any((r.get("trade") or 0) > 0 for r in rows):
            return day, rows
        time.sleep(0.5)   # be polite to the exchange's server
    return None, []


def _pick(cols: list[str], *needles: str) -> str | None:
    for c in cols:
        if any(n in str(c).lower() for n in needles):
            return c
    return None


def _news(limit: int = 40) -> list[dict]:
    """Latest exchange notices (bdshare get_all_news), as date / code / headline / text."""
    try:
        from bdshare import get_all_news
        df = get_all_news(start=(date.today() - timedelta(days=7)).isoformat(), end=date.today().isoformat())
    except Exception:
        return []
    if df is None or df.empty:
        return []
    cols = list(df.columns)
    cd, cc, ch, cb = _pick(cols, "date"), _pick(cols, "symbol", "code", "ticker"), _pick(cols, "headline", "title"), _pick(cols, "news", "detail", "description", "body")
    out = []
    for _, r in df.iterrows():
        head = str(r[ch]).strip() if ch else ""
        body = str(r[cb]).strip() if cb and cb != ch else ""
        if not head and not body:
            continue
        out.append({"date": str(r[cd]).strip() if cd else "", "code": str(r[cc]).strip().upper() if cc else "",
                    "headline": head or body[:120], "text": body if head else ""})
    return out[:limit]


def build(today: date | None = None) -> dict:
    """Fetch every part, each one fail-soft."""
    today = today or date.today()
    out = {"ok": False, "date": None, "fetched_at": datetime.now().isoformat(timespec="seconds"), "indices": [], "summary": None, "errors": []}
    try:
        day, rows = _archive_rows(today)
        if rows:
            out["date"] = day.isoformat()
            out["summary"] = summarise(rows, _sectors())
            out["ok"] = True
        else:
            out["errors"].append("archive: no trading day with rows in the last %d days" % LOOKBACK_DAYS)
    except Exception as e:
        out["errors"].append(f"archive: {e}")
    try:
        out["indices"] = parse_indices(_get(""))
    except Exception as e:
        out["errors"].append(f"indices: {e}")
    out["news"] = _news()
    return out


def overview(refresh: bool = False) -> dict:
    """The cached overview. A failed fetch keeps the last good copy (memory, then storage/market_cache.json) with `stale` set."""
    with _lock:
        fresh = _mem["data"] is not None and time.time() - _mem["at"] < CACHE_TTL
        if fresh and not refresh:
            return _mem["data"]
        data = build()
        if not data["ok"]:
            last = _mem["data"]
            if last is None:
                try:
                    last = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    last = None
            if last and last.get("ok"):
                return {**last, "stale": True, "errors": data["errors"]}
            return data
        _mem.update(at=time.time(), data=data)
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return data
