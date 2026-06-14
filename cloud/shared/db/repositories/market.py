"""Read repository for market data (quote, OHLCV history, returns)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..catalog import load_sql
from .base import BaseRepository

_SORT_DIRECTIONS = {"asc", "desc"}


class MarketRepository(BaseRepository):
    def latest_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._fetch_one(load_sql("market.latest_quote"), {"symbol": symbol})

    def ohlcv_history(
        self,
        symbol: str,
        interval: str,
        limit: int,
        sort: str = "desc",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        direction = sort.strip().lower()
        if direction not in _SORT_DIRECTIONS:
            raise ValueError(f"Unsupported sort direction: {sort}")
        sql = load_sql("market.ohlcv_history").replace("__SORT__", direction.upper())
        return self._fetch(
            sql,
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

    def symbol_returns(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._fetch_one(load_sql("market.symbol_returns"), {"symbol": symbol})
