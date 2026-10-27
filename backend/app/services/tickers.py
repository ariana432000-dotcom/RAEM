"""Saved company data: the exported JSON per ticker (backend/data/tickers/) and the list the page's cards use."""
from __future__ import annotations

import json

from app.core import config


def is_known(t: str) -> bool:
    return t in config.tickers()


def load(t: str) -> dict:
    return json.loads((config.TICKERS_DIR / f"{t}.json").read_text(encoding="utf-8"))


def summaries() -> list[dict]:
    out = []
    for t in config.tickers():
        d = load(t)
        f = d["forecast"]
        ens = f["paths"].get("Ensemble")
        out.append({"ticker": t, "name": d["meta"]["name"], "currency": d["meta"]["currency"],
                    "last_price": f["last_price"], "last_date": f["last_date"],
                    "ensemble_day7": ens[6] if ens else None,
                    "ensemble_return_pct": (ens[6] / f["last_price"] - 1) * 100 if ens else None,
                    "daily_signal": d["daily"]["final_signal"], "daily_data_end": d["daily"].get("data_end_date")})
    return out
