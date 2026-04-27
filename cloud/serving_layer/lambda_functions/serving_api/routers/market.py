"""Market data API routes (quote, OHLCV, returns)."""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException, Query

from serving_api.cache import MARKET_CACHE, make_cache_key
from serving_api.db import execute_one, execute_query

router = APIRouter(prefix="/market", tags=["market"])

SORT_DIRECTIONS = {"asc", "desc"}
INTERVALS = {"1d", "1h", "15m", "5m", "1m"}


def _to_return(base_price: Optional[Decimal], latest_price: Optional[Decimal]) -> Optional[float]:
    if base_price is None or latest_price is None:
        return None
    if float(base_price) == 0.0:
        return None
    return float((latest_price - base_price) / base_price)


def _parse_horizons(raw_horizons: str) -> List[int]:
    horizons: Set[int] = set()
    for token in raw_horizons.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        if value <= 0 or value > 252:
            continue
        horizons.add(value)
    if not horizons:
        horizons = {1, 5, 21}
    return sorted(horizons)


@router.get("/quote/{symbol}")
def get_latest_quote(symbol: str) -> Dict[str, Any]:
    normalized_symbol = symbol.upper().strip()
    cache_key = make_cache_key("market_quote", {"symbol": normalized_symbol})
    cached = MARKET_CACHE.get(cache_key)
    if cached is not None:
        cached["meta"]["cache_hit"] = True
        return cached

    row = execute_one(
        """
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
        JOIN LATERAL (
            SELECT timestamp, open, high, low, close, volume
            FROM raw_ohlcv
            WHERE symbol = m.symbol
              AND interval = '1d'
            ORDER BY timestamp DESC
            LIMIT 1
        ) o ON TRUE
        WHERE m.symbol = %(symbol)s
        LIMIT 1;
        """,
        params={"symbol": normalized_symbol},
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Symbol not found or no OHLCV data: {normalized_symbol}")

    response = {"data": row, "meta": {"symbol": normalized_symbol, "cache_hit": False}}
    MARKET_CACHE[cache_key] = response
    return response


@router.get("/ohlcv/{symbol}")
def get_ohlcv_history(
    symbol: str,
    interval: str = Query(default="1d"),
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    sort: str = Query(default="desc"),
) -> Dict[str, Any]:
    normalized_symbol = symbol.upper().strip()
    normalized_interval = interval.strip().lower()
    normalized_sort = sort.strip().lower()

    if normalized_interval not in INTERVALS:
        raise HTTPException(status_code=400, detail=f"Unsupported interval: {interval}")
    if normalized_sort not in SORT_DIRECTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported sort: {sort}")

    params: Dict[str, Any] = {
        "symbol": normalized_symbol,
        "interval": normalized_interval,
        "limit": limit,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
    }
    cache_key = make_cache_key("market_ohlcv", params | {"sort": normalized_sort})
    cached = MARKET_CACHE.get(cache_key)
    if cached is not None:
        cached["meta"]["cache_hit"] = True
        return cached

    query = f"""
        SELECT symbol,
               interval,
               timestamp,
               timestamp::date AS trading_date,
               open,
               high,
               low,
               close,
               volume
        FROM raw_ohlcv
        WHERE symbol = %(symbol)s
          AND interval = %(interval)s
          AND (%(start_date)s::date IS NULL OR timestamp::date >= %(start_date)s::date)
          AND (%(end_date)s::date IS NULL OR timestamp::date <= %(end_date)s::date)
        ORDER BY timestamp {normalized_sort.upper()}
        LIMIT %(limit)s;
    """
    rows = execute_query(query, params=params)
    response = {
        "data": rows,
        "meta": {
            "symbol": normalized_symbol,
            "interval": normalized_interval,
            "count": len(rows),
            "limit": limit,
            "start_date": params["start_date"],
            "end_date": params["end_date"],
            "sort": normalized_sort,
            "cache_hit": False,
        },
    }
    MARKET_CACHE[cache_key] = response
    return response


@router.get("/returns/{symbol}")
def get_symbol_returns(
    symbol: str,
    horizons: str = Query(default="1,5,21"),
) -> Dict[str, Any]:
    normalized_symbol = symbol.upper().strip()
    selected_horizons = _parse_horizons(horizons)
    cache_key = make_cache_key(
        "market_returns",
        {"symbol": normalized_symbol, "horizons": ",".join(map(str, selected_horizons))},
    )
    cached = MARKET_CACHE.get(cache_key)
    if cached is not None:
        cached["meta"]["cache_hit"] = True
        return cached

    row = execute_one(
        """
        WITH prices AS (
            SELECT timestamp::date AS trade_date,
                   close,
                   ROW_NUMBER() OVER (ORDER BY timestamp DESC) AS rn
            FROM raw_ohlcv
            WHERE symbol = %(symbol)s
              AND interval = '1d'
        )
        SELECT MAX(CASE WHEN rn = 1 THEN trade_date END) AS as_of_date,
               MAX(CASE WHEN rn = 1 THEN close END) AS close_now,
               MAX(CASE WHEN rn = 2 THEN close END) AS close_1d_ago,
               MAX(CASE WHEN rn = 6 THEN close END) AS close_5d_ago,
               MAX(CASE WHEN rn = 22 THEN close END) AS close_21d_ago
        FROM prices;
        """,
        params={"symbol": normalized_symbol},
    )
    if not row or row.get("close_now") is None:
        raise HTTPException(status_code=404, detail=f"No OHLCV data found for symbol: {normalized_symbol}")

    close_map = {1: row.get("close_1d_ago"), 5: row.get("close_5d_ago"), 21: row.get("close_21d_ago")}
    returns_map: Dict[str, Optional[float]] = {}
    for horizon in selected_horizons:
        returns_map[f"{horizon}d"] = _to_return(close_map.get(horizon), row.get("close_now"))

    response = {
        "data": {
            "symbol": normalized_symbol,
            "as_of_date": row.get("as_of_date"),
            "close_now": row.get("close_now"),
            "returns": returns_map,
        },
        "meta": {
            "horizons": selected_horizons,
            "cache_hit": False,
        },
    }
    MARKET_CACHE[cache_key] = response
    return response
