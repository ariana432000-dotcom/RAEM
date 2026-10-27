"""Companies added from the search box: look-ups, the saved list, and the bundle the page needs to show one."""
from __future__ import annotations

import json
import sys

from app.core import config


def _registry():
    if str(config.ENGINE) not in sys.path:
        sys.path.insert(0, str(config.ENGINE))
    import raem_companies
    return raem_companies


def normalize(raw: str) -> str:
    return _registry().normalize(raw)


def added() -> list[dict]:
    """Companies added from the website: ticker, name, market group, currency, and whether their forecast is ready."""
    out = []
    for t, m in sorted(_registry().load().items()):
        out.append({"ticker": t, "name": m.get("name", t), "group": m.get("group", "international"), "currency": m.get("currency"),
                    "ready": (config.TICKERS_DIR / f"{t}.json").exists()})
    return out


def search(q: str, limit: int = 8) -> list[dict]:
    """Matches for a search text: companies already on the site first, then Yahoo Finance's suggestions (when it is reachable)."""
    q = (q or "").strip()
    if not q:
        return []
    qu = q.upper()
    out, seen = [], set()
    for t in config.tickers():
        d = json.loads((config.TICKERS_DIR / f"{t}.json").read_text(encoding="utf-8"))
        name = d["meta"]["name"]
        if t.upper().startswith(qu) or qu in name.upper():
            out.append({"symbol": t, "name": name, "exchange": d["meta"].get("source", ""), "type": "", "on_site": True})
            seen.add(t)
    try:
        import yfinance as yf
        for r in (yf.Search(q, max_results=limit, news_count=0).quotes or []):
            sym = r.get("symbol")
            if sym and sym not in seen and r.get("quoteType") in (None, "EQUITY", "ETF", "INDEX"):
                out.append({"symbol": sym, "name": r.get("longname") or r.get("shortname") or sym, "exchange": r.get("exchDisp") or r.get("exchange") or "",
                            "type": r.get("quoteType") or "", "on_site": False})
                seen.add(sym)
    except Exception:  # Yahoo unreachable or its answer changed: the typed text can still be added as a ticker
        pass
    return out[:limit]


def bundle(t: str) -> dict:
    """Everything the page keeps per company besides the forecast: what the page adds to DATA and EXTRA to show it."""
    data = json.loads((config.TICKERS_DIR / f"{t}.json").read_text(encoding="utf-8"))
    extra = {}
    if config.EXTRA.exists():
        extra = json.loads(config.EXTRA.read_text(encoding="utf-8"))
    cur = data["meta"].get("currency")
    return {"ticker": t, "data": data, "dataset": (extra.get("dataset") or {}).get(t) or [],
            "backtest": (extra.get("backtest") or {}).get(t) or {}, "ai": (extra.get("ai") or {}).get(t),
            "fx": {cur: (extra.get("fx") or {}).get(cur)} if cur else {}}
