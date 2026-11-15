from __future__ import annotations

from fastapi import APIRouter

from app.services import market

router = APIRouter()


@router.get("/api/market")
def get_market(refresh: bool = False):
    """DSE market overview for the Home page (indices, day summary, breadth, top 20 by value, news). Cached 15 minutes;
    `refresh=true` asks the exchange again. Always answers 200: a part that could not be fetched is null or empty."""
    return market.overview(refresh)
