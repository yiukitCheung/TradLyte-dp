"""Screener API routes."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from serving_api.cache import SCREENER_CACHE, make_cache_key
from serving_api.db import execute_one, execute_query

router = APIRouter(prefix="/v1/screener", tags=["screener"])

SORT_FIELDS = {
    "marketcap": "m.marketcap",
    "symbol": "m.symbol",
    "close": "o.close",
    "volume": "o.volume",
}


def _normalize_sort(sort: str) -> str:
    field = "marketcap"
    direction = "DESC"
    if sort:
        parts = sort.split(":")
        if parts and parts[0].strip().lower() in SORT_FIELDS:
            field = parts[0].strip().lower()
        if len(parts) > 1 and parts[1].strip().lower() == "asc":
            direction = "ASC"
    order_by = SORT_FIELDS[field]
    nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"
    return f"{order_by} {direction} {nulls}"


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
    order_by_clause = _normalize_sort(sort)
    params = {
        "industry": industry,
        "type": type,
        "min_mc": min_market_cap,
        "max_mc": max_market_cap,
        "limit": limit,
        "offset": offset,
        "sort": sort,
    }

    cache_key = make_cache_key("screener", params)
    cached = SCREENER_CACHE.get(cache_key)
    if cached is not None:
        cached["meta"]["cache_hit"] = True
        return cached

    query = f"""
        WITH md AS (
            SELECT MAX(latest_date) AS as_of
            FROM data_ingestion_watermark
            WHERE is_current
        )
        SELECT m.symbol,
               m.name,
               m.industry,
               m.marketcap AS market_cap,
               m.type,
               m.primary_exchange,
               o.timestamp::date AS as_of_date,
               o.open,
               o.high,
               o.low,
               o.close,
               o.volume
        FROM symbol_metadata m
        JOIN raw_ohlcv o
          ON o.symbol = m.symbol
         AND o.interval = '1d'
         AND o.timestamp::date = (SELECT as_of FROM md)
        WHERE LOWER(COALESCE(m.active::text, 'true')) = 'true'
          AND (%(industry)s::text IS NULL OR m.industry = %(industry)s)
          AND (%(type)s::text IS NULL OR m.type = %(type)s)
          AND (%(min_mc)s::bigint IS NULL OR m.marketcap >= %(min_mc)s)
          AND (%(max_mc)s::bigint IS NULL OR m.marketcap <= %(max_mc)s)
        ORDER BY {order_by_clause}
        LIMIT %(limit)s OFFSET %(offset)s;
    """
    rows = execute_query(query, params=params)
    as_of_row = execute_one(
        """
        SELECT MAX(latest_date) AS as_of_date
        FROM data_ingestion_watermark
        WHERE is_current
        """
    )
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
