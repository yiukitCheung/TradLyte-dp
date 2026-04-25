"""Stock pick API routes."""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Query

from serving_api.cache import PICKS_CACHE, RETURNS_CACHE, make_cache_key
from serving_api.db import execute_query

router = APIRouter(prefix="/v1/picks", tags=["picks"])


def _to_return(pick_price: Optional[Decimal], close_price: Optional[Decimal]) -> Optional[float]:
    if pick_price is None or close_price is None:
        return None
    if float(pick_price) == 0.0:
        return None
    return float((close_price - pick_price) / pick_price)


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


@router.get("/today")
def get_picks_today(limit: int = Query(default=25, ge=1, le=200)) -> Dict[str, Any]:
    cache_key = make_cache_key("picks_today", {"limit": limit})
    cached = PICKS_CACHE.get(cache_key)
    if cached is not None:
        cached["meta"]["cache_hit"] = True
        return cached

    query = """
        WITH latest AS (SELECT MAX(scan_date) AS d FROM stock_picks)
        SELECT scan_date,
               rank,
               symbol,
               strategy_name,
               signal,
               price,
               confidence,
               score,
               metadata
        FROM stock_picks
        WHERE scan_date = (SELECT d FROM latest)
        ORDER BY rank ASC
        LIMIT %(limit)s;
    """
    rows = execute_query(query, params={"limit": limit})
    scan_date = rows[0]["scan_date"] if rows else None
    response = {
        "data": rows,
        "meta": {
            "count": len(rows),
            "limit": limit,
            "scan_date": scan_date,
            "cache_hit": False,
        },
    }
    PICKS_CACHE[cache_key] = response
    return response


@router.get("/{scan_date}/returns")
def get_pick_returns(
    scan_date: date,
    horizons: str = Query(default="1,5,21"),
) -> Dict[str, Any]:
    selected_horizons = _parse_horizons(horizons)
    cache_key = make_cache_key(
        "pick_returns",
        {"scan_date": scan_date.isoformat(), "horizons": ",".join(map(str, selected_horizons))},
    )
    cached = RETURNS_CACHE.get(cache_key)
    if cached is not None:
        cached["meta"]["cache_hit"] = True
        return cached

    query = """
        WITH picks AS (
            SELECT symbol,
                   rank,
                   strategy_name,
                   signal,
                   price AS pick_price,
                   scan_date
            FROM stock_picks
            WHERE scan_date = %(scan_date)s::date
        )
        SELECT p.*,
               (
                    SELECT close
                    FROM raw_ohlcv
                    WHERE symbol = p.symbol
                      AND interval = '1d'
                      AND timestamp::date > p.scan_date
                    ORDER BY timestamp ASC
                    LIMIT 1 OFFSET 0
               ) AS close_1d,
               (
                    SELECT close
                    FROM raw_ohlcv
                    WHERE symbol = p.symbol
                      AND interval = '1d'
                      AND timestamp::date > p.scan_date
                    ORDER BY timestamp ASC
                    LIMIT 1 OFFSET 4
               ) AS close_5d,
               (
                    SELECT close
                    FROM raw_ohlcv
                    WHERE symbol = p.symbol
                      AND interval = '1d'
                      AND timestamp::date > p.scan_date
                    ORDER BY timestamp ASC
                    LIMIT 1 OFFSET 20
               ) AS close_21d,
               (
                    SELECT close
                    FROM raw_ohlcv
                    WHERE symbol = p.symbol
                      AND interval = '1d'
                    ORDER BY timestamp DESC
                    LIMIT 1
               ) AS close_now
        FROM picks p
        ORDER BY p.rank;
    """
    rows = execute_query(query, params={"scan_date": scan_date.isoformat()})

    data = []
    for row in rows:
        return_map: Dict[str, Optional[float]] = {}
        close_map = {
            1: row.get("close_1d"),
            5: row.get("close_5d"),
            21: row.get("close_21d"),
        }
        for horizon in selected_horizons:
            close_value = close_map.get(horizon)
            return_map[f"{horizon}d"] = _to_return(row.get("pick_price"), close_value)
        data.append(
            {
                "scan_date": row["scan_date"],
                "rank": row["rank"],
                "symbol": row["symbol"],
                "strategy_name": row["strategy_name"],
                "signal": row["signal"],
                "pick_price": row["pick_price"],
                "close_now": row.get("close_now"),
                "return_to_date": _to_return(row.get("pick_price"), row.get("close_now")),
                "returns": return_map,
            }
        )

    response = {
        "data": data,
        "meta": {
            "count": len(data),
            "scan_date": scan_date.isoformat(),
            "horizons": selected_horizons,
            "cache_hit": False,
        },
    }
    RETURNS_CACHE[cache_key] = response
    return response
