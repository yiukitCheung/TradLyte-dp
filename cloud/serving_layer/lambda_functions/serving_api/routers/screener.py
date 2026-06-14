"""Screener API routes.

SQL lives in the shared query catalog (``db/sql/screener/*.sql``) reached
through ``ScreenerRepository``; the metadata x daily-bar join is the
``vw_screener_quotes`` database view.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from serving_api.cache import SCREENER_CACHE, make_cache_key

try:  # deployed layout copies shared/db -> db/ at the zip root
    from db.repositories import ScreenerRepository
except ImportError:  # pragma: no cover - local/test fallback
    from shared.db.repositories import ScreenerRepository

router = APIRouter(prefix="/screener", tags=["screener"])


@router.get("/quotes")
def get_screener_quotes(
    industry: Optional[str] = Query(default=None),
    type: Optional[str] = Query(default=None),
    min_market_cap: Optional[int] = Query(default=None, ge=0),
    max_market_cap: Optional[int] = Query(default=None, ge=0),
    sort: str = Query(default="marketcap:desc"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    cache_params = {
        "industry": industry,
        "type": type,
        "min_mc": min_market_cap,
        "max_mc": max_market_cap,
        "limit": limit,
        "offset": offset,
        "sort": sort,
    }

    cache_key = make_cache_key("screener", cache_params)
    cached = SCREENER_CACHE.get(cache_key)
    if cached is not None:
        cached["meta"]["cache_hit"] = True
        return cached

    repo = ScreenerRepository()
    rows = repo.quotes(
        sort=sort,
        limit=limit,
        offset=offset,
        industry=industry,
        type=type,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
    )
    as_of_row = repo.as_of_date()
    response = {
        "data": rows,
        "meta": {
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "as_of_date": as_of_row["as_of_date"] if as_of_row else None,
            "cache_hit": False,
        },
    }
    SCREENER_CACHE[cache_key] = response
    return response
