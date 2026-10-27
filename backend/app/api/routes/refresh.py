from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException, Query

from app.api import deps
from app.core import config
from app.workers import jobs

router = APIRouter()


@router.post("/api/refresh", status_code=202)
def run_refresh(tickers: str | None = Query(None, description="Comma-separated tickers; all when empty"),
                extra: bool = True, top_up: bool = True, authorization: str | None = Header(None)):
    """Re-run the default forecasts for all (or some) companies and rebuild the page."""
    if not config.REFRESH_TOKEN:
        raise HTTPException(403, "Refresh is turned off. Set RAEM_REFRESH_TOKEN on the server to turn it on.")
    deps.check_token(config.REFRESH_TOKEN, authorization or "", "Refresh")
    tks = [t.strip() for t in tickers.split(",") if t.strip()] if tickers else None
    if tks:
        for t in tks:
            deps.known(t)
    run, reused = jobs.submit("refresh", "*", json.dumps({"t": tks, "e": extra, "u": top_up}),
                              {"tickers": tks, "extra": extra, "top_up": top_up}, force=True)
    return run
