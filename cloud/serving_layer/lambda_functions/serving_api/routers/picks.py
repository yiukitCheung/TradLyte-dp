"""Stock pick API routes.

SQL lives in the shared query catalog (``db/sql/picks/*.sql``) and is reached
through ``PicksRepository``; the join to ``symbol_metadata`` and market-cap
exposure are defined once in the ``vw_picks`` database view.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException, Query

from serving_api.cache import PICKS_CACHE, RETURNS_CACHE, make_cache_key

try:  # deployed layout copies shared/db -> db/ at the zip root
    from db.repositories import PicksRepository
except ImportError:  # pragma: no cover - local/test fallback
    from shared.db.repositories import PicksRepository

router = APIRouter(prefix="/picks", tags=["picks"])

# Canonical serving sort, surfaced in responses. The ORDER BY itself lives in
# the catalog SQL (sql/picks/*.sql).
PICKS_SORT = "marketcap:desc"


def _to_return(pick_price: Optional[Decimal], close_price: Optional[Decimal]) -> Optional[float]:
    if pick_price is None or close_price is None:
        return None
    if float(pick_price) == 0.0:
        return None
    return float((close_price - pick_price) / pick_price)


def _picks_today_meta_filters(
    industry: Optional[str],
    min_market_cap: Optional[int],
    max_market_cap: Optional[int],
) -> Dict[str, Any]:
    """Normalize optional symbol_metadata filters (matches screener semantics)."""
    ind = industry.strip() if industry else None
    if ind == "":
        ind = None
    return {
        "industry": ind,
        "min_mc": min_market_cap,
        "max_mc": max_market_cap,
    }


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
def get_picks_today(
    limit: int = Query(default=25, ge=1, le=200),
    industry: Optional[str] = Query(default=None, description="Exact match on symbol_metadata.industry"),
    min_market_cap: Optional[int] = Query(default=None, ge=0, description="Minimum marketcap (symbol_metadata)"),
    max_market_cap: Optional[int] = Query(default=None, ge=0, description="Maximum marketcap (symbol_metadata)"),
) -> Dict[str, Any]:
    filt = _picks_today_meta_filters(industry, min_market_cap, max_market_cap)
    cache_key = make_cache_key(
        "picks_today",
        {"limit": limit, **filt},
    )
    cached = PICKS_CACHE.get(cache_key)
    if cached is not None:
        cached["meta"]["cache_hit"] = True
        return cached

    rows = PicksRepository().today(
        limit=limit,
        industry=filt["industry"],
        min_market_cap=filt["min_mc"],
        max_market_cap=filt["max_mc"],
    )
    scan_date = rows[0]["scan_date"] if rows else None
    response = {
        "data": rows,
        "meta": {
            "count": len(rows),
            "limit": limit,
            "scan_date": scan_date,
            "sort": PICKS_SORT,
            "filters": {
                "industry": filt["industry"],
                "min_market_cap": filt["min_mc"],
                "max_market_cap": filt["max_mc"],
            },
            "cache_hit": False,
        },
    }
    PICKS_CACHE[cache_key] = response
    return response


@router.get("/today/metadata")
def get_picks_today_metadata(
    limit: int = Query(default=25, ge=1, le=200),
    industry: Optional[str] = Query(default=None, description="Exact match on symbol_metadata.industry"),
    min_market_cap: Optional[int] = Query(default=None, ge=0, description="Minimum marketcap (symbol_metadata)"),
    max_market_cap: Optional[int] = Query(default=None, ge=0, description="Maximum marketcap (symbol_metadata)"),
) -> Dict[str, Any]:
    filt = _picks_today_meta_filters(industry, min_market_cap, max_market_cap)
    cache_key = make_cache_key(
        "picks_today_metadata",
        {"limit": limit, **filt},
    )
    cached = PICKS_CACHE.get(cache_key)
    if cached is not None:
        cached["meta"]["cache_hit"] = True
        return cached

    rows = PicksRepository().today_metadata(
        limit=limit,
        industry=filt["industry"],
        min_market_cap=filt["min_mc"],
        max_market_cap=filt["max_mc"],
    )
    scan_date = rows[0]["scan_date"] if rows else None
    response = {
        "data": rows,
        "meta": {
            "count": len(rows),
            "limit": limit,
            "scan_date": scan_date,
            "sort": PICKS_SORT,
            "filters": {
                "industry": filt["industry"],
                "min_market_cap": filt["min_mc"],
                "max_market_cap": filt["max_mc"],
            },
            "cache_hit": False,
        },
    }
    PICKS_CACHE[cache_key] = response
    return response


@router.get("/detail")
def get_pick_detail(
    symbol: str = Query(..., min_length=1, max_length=50, description="Ticker, e.g. AAPL"),
    scan_date: date = Query(..., description="Market scan date (YYYY-MM-DD)"),
    strategy_name: Optional[str] = Query(
        default=None,
        max_length=255,
        description="If set, return at most one row for this strategy",
    ),
) -> Dict[str, Any]:
    """
    Pick row(s) from `stock_picks` for a symbol + scan date, joined with
    `symbol_metadata` for industry, market cap, and basic listing fields.
    """
    sym = symbol.strip().upper()
    cache_key = make_cache_key(
        "pick_detail",
        {
            "symbol": sym,
            "scan_date": scan_date.isoformat(),
            "strategy_name": strategy_name or "",
        },
    )
    cached = PICKS_CACHE.get(cache_key)
    if cached is not None:
        cached["meta"]["cache_hit"] = True
        return cached

    rows = PicksRepository().detail(
        symbol=sym,
        scan_date=scan_date.isoformat(),
        strategy_name=strategy_name.strip() if isinstance(strategy_name, str) else None,
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No stock_picks row for symbol={sym} scan_date={scan_date.isoformat()}"
            + (f" strategy_name={strategy_name}" if strategy_name else ""),
        )

    response = {
        "data": rows,
        "meta": {
            "count": len(rows),
            "symbol": sym,
            "scan_date": scan_date.isoformat(),
            "strategy_filter": strategy_name,
            "cache_hit": False,
        },
    }
    PICKS_CACHE[cache_key] = response
    return response


@router.get("/{scan_date}/returns")
def get_pick_returns(
    scan_date: date,
    horizons: str = Query(default="1,5,21"),
    industry: Optional[str] = Query(default=None, description="Exact match on symbol_metadata.industry"),
    min_market_cap: Optional[int] = Query(default=None, ge=0, description="Minimum marketcap (symbol_metadata)"),
    max_market_cap: Optional[int] = Query(default=None, ge=0, description="Maximum marketcap (symbol_metadata)"),
) -> Dict[str, Any]:
    filt = _picks_today_meta_filters(industry, min_market_cap, max_market_cap)
    selected_horizons = _parse_horizons(horizons)
    cache_key = make_cache_key(
        "pick_returns",
        {
            "scan_date": scan_date.isoformat(),
            "horizons": ",".join(map(str, selected_horizons)),
            **filt,
        },
    )
    cached = RETURNS_CACHE.get(cache_key)
    if cached is not None:
        cached["meta"]["cache_hit"] = True
        return cached

    rows = PicksRepository().returns(
        scan_date=scan_date.isoformat(),
        industry=filt["industry"],
        min_market_cap=filt["min_mc"],
        max_market_cap=filt["max_mc"],
    )

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
                "market_cap": row.get("market_cap"),
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
            "sort": PICKS_SORT,
            "filters": {
                "industry": filt["industry"],
                "min_market_cap": filt["min_mc"],
                "max_market_cap": filt["max_mc"],
            },
            "cache_hit": False,
        },
    }
    RETURNS_CACHE[cache_key] = response
    return response
