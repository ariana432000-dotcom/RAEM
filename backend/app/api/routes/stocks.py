"""The page and the saved company data."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api import deps
from app.core import config
from app.services import tickers as ticker_data

router = APIRouter()


@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)  # HEAD for hosts' uptime checks
def index():
    if not config.INDEX.exists():
        raise HTTPException(503, "The page is not built yet. Run: python backend/refresh.py --build-only")
    return FileResponse(config.INDEX, media_type="text/html")


@router.get("/api/tickers")
def tickers():
    return ticker_data.summaries()


@router.get("/api/tickers/{ticker}")
def ticker(ticker: str):
    return deps.load_ticker(ticker)


@router.get("/api/extra")
def extra():
    if not config.EXTRA.exists():
        raise HTTPException(503, "Extra data not exported yet. Run: python backend/refresh.py")
    return FileResponse(config.EXTRA, media_type="application/json")
