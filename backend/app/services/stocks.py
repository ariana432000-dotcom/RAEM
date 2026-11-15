"""The Stocks page: every DSE company traded on the latest day (name, sector, price, change), and one company's analysis data
(profile numbers from its DSE page and daily candles).

The list reuses the market overview (`market.overview()`): its day-end archive rows hold every traded issue. Names come from
dsebd's company listing (`ai_pipeline.market_data._load_dse_company_names`). A sector is only known from a company's own page,
so it is learned when the page is opened (and saved in storage/dse_sectors_learned.json); `tools/build_dse_sectors.py` fills
the whole list in one polite run. Every part is fail-soft: what cannot be fetched is null.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, timedelta

from app.core import config
from app.services import market

CODE_RE = re.compile(r"^[A-Za-z0-9._&-]{1,20}$")
DETAIL_TTL = 900
CANDLE_DAYS = 400          # calendar days of candles (about 13 months)
_lock = threading.Lock()
_detail: dict[str, tuple[float, dict]] = {}


def _names() -> dict:
    try:
        from app.ai_pipeline import market_data
        return market_data._load_dse_company_names()
    except Exception:
        return {}


def _snapshot(code: str) -> dict:
    """The company's DSE profile page as label -> value (the same scraper the fundamentals analyst uses)."""
    from app.ai_pipeline import dse_layer
    return dse_layer.fetch_dse_company_snapshot(code)


def _ohlcv(code: str, start: str, end: str):
    from app.ai_pipeline import dse_layer
    return dse_layer.fetch_dse_ohlcv_range(code, start, end)


def _page_html(code: str) -> str:
    """The raw company page, for the tables the profile scraper does not read (annual EPS)."""
    from app.ai_pipeline import dse_layer
    return dse_layer._dse_get("displayCompany.php", params={"name": code})


def _eps_news(code: str) -> list[str]:
    """Recent DSE news lines for the company (the same feed the income-statement tool reads), newest first."""
    from app.ai_pipeline import dse_layer
    end = date.today()
    return dse_layer._fetch_dse_news(code, (end - timedelta(days=500)).isoformat(), end.isoformat())


EPS_NEWS_RE = re.compile(r"EPS[^.]*?Tk\.?\s*(\(?-?[\d.]+\)?)\s+for\s+([^;.,\n]+)", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _signed(raw: str) -> float | None:
    """DSE writes a negative EPS in parentheses: "(12.41)" is -12.41."""
    raw = raw.strip()
    try:
        v = float(raw.strip("()"))
    except ValueError:
        return None
    return -abs(v) if raw.startswith("(") and raw.endswith(")") else v


def _period_key(label: str) -> tuple[int, int]:
    """(year, last month) of a period such as "January-March 2025", for sorting newest first."""
    ym = re.findall(r"(?:19|20)\d{2}", label)
    mm = [MONTHS[m[:3].lower()] for m in re.findall(r"[A-Za-z]{3,9}", label) if m[:3].lower() in MONTHS]
    return (int(ym[-1]) if ym else 0, mm[-1] if mm else 0)


def quarterly_eps(lines: list[str], limit: int = 4) -> list[dict]:
    """Quarterly EPS disclosures found in the news lines ("EPS was Tk. (0.38) for January-March 2025"), one per period, newest first.
    A period that names a whole year ("the year ended ...") is an annual figure and is left out."""
    seen: dict[str, float] = {}
    for line in lines:
        for m in EPS_NEWS_RE.finditer(line):
            v, raw_period = _signed(m.group(1)), m.group(2).strip()
            cut = re.match(r"(.*?(?:19|20)\d{2})", raw_period)
            period = cut.group(1).strip() if cut else raw_period       # drop words after the year ("... 2025 as per ...")
            if v is not None and "year" not in raw_period.lower() and period not in seen:
                seen[period] = v
    rows = sorted(seen.items(), key=lambda kv: _period_key(kv[0]), reverse=True)[:limit]
    return [{"period": k, "eps": v} for k, v in rows]


def parse_annual_eps(html: str, limit: int = 6) -> list[dict]:
    """Annual EPS by year from any table on the company page that has years and an EPS row or column (both shapes are read)."""
    from bs4 import BeautifulSoup
    out: dict[int, float] = {}
    for table in BeautifulSoup(html, "lxml").find_all("table"):
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]
        rows = [r for r in rows if r]
        yr = re.compile(r"^((?:19|20)\d{2})(?:\D.*)?$")
        for i, r in enumerate(rows):               # years across the top, an EPS row below
            years = [(j, int(yr.match(c).group(1))) for j, c in enumerate(r) if yr.match(c)]
            if len(years) >= 2:
                for r2 in rows[i + 1:]:
                    if r2 and re.search(r"\beps\b|earnings? per share", r2[0], re.I) and not re.search(r"restated|diluted", r2[0], re.I):
                        for j, y in years:
                            v = _cell(r2[j]) if j < len(r2) else None
                            if v is not None:
                                out.setdefault(y, v)
                        break
        head = rows[0] if rows else []             # one row per year, an EPS column
        ycol = next((j for j, c in enumerate(head) if re.search(r"\byear\b", c, re.I)), None)
        ecol = next((j for j, c in enumerate(head) if re.search(r"\beps\b|earnings? per share", c, re.I) and not re.search(r"restated|diluted", c, re.I)), None)
        if ycol is not None and ecol is not None:
            for r in rows[1:]:
                m = yr.match(r[ycol]) if len(r) > max(ycol, ecol) else None
                v = _cell(r[ecol]) if m else None
                if m and v is not None:
                    out.setdefault(int(m.group(1)), v)
    return [{"year": y, "eps": out[y]} for y in sorted(out)[-limit:]]


def _cell(c: str) -> float | None:
    """A table cell as a number; accounting parentheses mean negative, like DSE's EPS figures."""
    c = (c or "").strip()
    v = market._num(c.strip("()"))
    return -abs(v) if v is not None and c.startswith("(") else v


def _pct(v: str | None) -> float | None:
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v or ""))
    return market._num(m.group(0)) if m else None


def shareholding(snapshot: dict) -> list[dict]:
    """Sponsor/Director, Govt, Institute, Foreign and Public holding in percent, as far as the company page lists them."""
    rows = []
    for label, key in (("Sponsor/Director", "Sponsor"), ("Govt", "Govt"), ("Institute", "Institute"), ("Foreign", "Foreign"), ("Public", "Public")):
        v = _pct(_find(snapshot, key))
        if v is not None and v > 0:
            rows.append({"label": label, "pct": v})
    return rows


def last_dividend(raw: str | None) -> dict | None:
    """The newest "7% 2022" pair from a cash-dividend history text."""
    pairs = {int(y): float(v) for v, y in re.findall(r"(\d+(?:\.\d+)?)\s*%\s*\(?\s*((?:19|20)\d{2})", str(raw or ""))}
    return {"pct": pairs[max(pairs)], "year": max(pairs)} if pairs else None


def remember_sector(code: str, sector: str | None) -> None:
    if not sector:
        return
    f = market.LEARNED_SECTORS_FILE
    try:
        cur = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cur = {}
    if cur.get(code.upper()) == sector:
        return
    cur[code.upper()] = sector
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cur, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def listing(refresh: bool = False) -> dict:
    """Every issue traded on the latest day, with name and (when known) sector, plus the sector filter chips."""
    m = market.overview(refresh)
    s = m.get("summary") or {}
    rows = s.get("stocks") or []
    names, sectors = _names(), market._sectors()
    out = []
    for r in rows:
        out.append({"code": r["code"], "name": names.get(r["code"]) or "", "sector": sectors.get(r["code"]) or "",
                    "ltp": r.get("ltp"), "close": r.get("close"), "ycp": r.get("ycp"), "change_pct": r.get("change_pct"),
                    "volume": r.get("volume"), "value_mn": r.get("value_mn"), "trade": r.get("trade")})
    out.sort(key=lambda r: r["code"])
    counts: dict[str, int] = {}
    for r in out:
        if r["sector"]:
            counts[r["sector"]] = counts.get(r["sector"], 0) + 1
    return {"ok": bool(out), "date": m.get("date"), "count": len(out), "stale": bool(m.get("stale")),
            "sectors": [{"sector": k, "count": v} for k, v in sorted(counts.items())],
            "sectors_known": sum(counts.values()), "stocks": out}


def _range_52w(text: str | None) -> tuple[float | None, float | None]:
    if not text or "-" not in text:
        return None, None
    parts = [market._num(p) for p in text.replace(",", "").split("-")]
    return (parts[0], parts[1]) if len(parts) == 2 and None not in parts else (None, None)


def _find(snapshot: dict, *keys) -> str | None:
    for k in keys:
        for sk, sv in snapshot.items():
            if k.lower() in sk.lower():
                return sv
    return None


def build_detail(code: str) -> dict:
    code = code.upper()
    out = {"ok": False, "code": code, "name": _names().get(code) or "", "sector": market._sectors().get(code) or "", "category": "",
           "as_of": None, "candles": [], "errors": []}
    try:
        snap = _snapshot(code)
    except Exception as e:
        snap = {"error": str(e)}
    if snap and "error" not in snap:
        out["sector"] = _find(snap, "Sector") or out["sector"]
        out["category"] = _find(snap, "Market Category", "Category") or ""
        out["market_cap_mn"] = market._num(re.sub(r"[^\d.,\-]", "", _find(snap, "Market Capitalization") or "").strip() or None)
        out["paid_up_mn"] = market._num(re.sub(r"[^\d.,\-]", "", _find(snap, "Paid-up Capital") or "").strip() or None)
        out["week52_low"], out["week52_high"] = _range_52w(_find(snap, "52 Weeks"))
        out["shares_out"] = market._num(re.sub(r"[^\d.]", "", _find(snap, "Outstanding Securities") or "") or None)
        out["shareholding"] = shareholding(snap)
        out["dividend"] = last_dividend(_find(snap, "Cash Dividend"))
        remember_sector(code, out["sector"])
    else:
        out["errors"].append("profile: " + str((snap or {}).get("error", "no fields on the company page")))
    try:
        end = date.today()
        df = _ohlcv(code, (end - timedelta(days=CANDLE_DAYS)).isoformat(), end.isoformat())
        for ts, r in df.iterrows():
            if r["Close"] > 0:
                out["candles"].append([ts.date().isoformat(), r["Open"] or r["Close"], r["High"] or r["Close"], r["Low"] or r["Close"], r["Close"], r["Volume"]])
    except Exception as e:
        out["errors"].append(f"candles: {e}")
    try:
        out["annual_eps"] = parse_annual_eps(_page_html(code))
    except Exception as e:
        out["annual_eps"] = []
        out["errors"].append(f"annual EPS: {e}")
    try:
        out["quarterly_eps"] = quarterly_eps(_eps_news(code))
    except Exception as e:
        out["quarterly_eps"] = []
        out["errors"].append(f"quarterly EPS: {e}")
    c = out["candles"]
    if c:
        last, prev = c[-1], (c[-2] if len(c) > 1 else None)
        out.update(as_of=last[0], open=last[1], day_high=last[2], day_low=last[3], last_close=last[4], volume=last[5],
                   prev_close=prev[4] if prev else None,
                   change=last[4] - prev[4] if prev else None, change_pct=(last[4] / prev[4] - 1) * 100 if prev and prev[4] else None)
        yr = c[-252:]
        if out.get("week52_low") is None:
            out["week52_low"], out["week52_high"] = min(r[3] for r in yr), max(r[2] for r in yr)
        out["ok"] = True
    return out


def detail(code: str, refresh: bool = False) -> dict:
    code = code.upper()
    with _lock:
        hit = _detail.get(code)
        if hit and not refresh and time.time() - hit[0] < DETAIL_TTL:
            return hit[1]
        data = build_detail(code)
        if data["ok"]:
            _detail[code] = (time.time(), data)
        return data
