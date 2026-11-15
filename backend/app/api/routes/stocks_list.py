from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services import stocks

router = APIRouter()


@router.get("/api/stocks")
def list_stocks(refresh: bool = False):
    """Every DSE issue traded on the latest day (code, name, sector when known, price, change), for the Stocks page."""
    return stocks.listing(refresh)


@router.get("/api/stocks/{code}")
def stock_detail(code: str, refresh: bool = False):
    """One company's analysis data: profile numbers from its DSE page and about 13 months of daily candles."""
    if not stocks.CODE_RE.match(code):
        raise HTTPException(status_code=422, detail="That is not a valid trading code.")
    return stocks.detail(code, refresh)
