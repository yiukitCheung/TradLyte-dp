"""Read repository for stock picks (serving + batch QA)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..catalog import load_sql
from .base import BaseRepository


class PicksRepository(BaseRepository):
    """Reads from the ``vw_picks`` contract; sorting is market-cap first."""

    def today(
        self,
        limit: int,
        industry: Optional[str] = None,
        min_market_cap: Optional[int] = None,
        max_market_cap: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self._fetch(
            load_sql("picks.today"),
            {
                "limit": limit,
                "industry": industry,
                "min_mc": min_market_cap,
                "max_mc": max_market_cap,
            },
        )

    def today_metadata(
        self,
        limit: int,
        industry: Optional[str] = None,
        min_market_cap: Optional[int] = None,
        max_market_cap: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self._fetch(
            load_sql("picks.today_metadata"),
            {
                "limit": limit,
                "industry": industry,
                "min_mc": min_market_cap,
                "max_mc": max_market_cap,
            },
        )

    def detail(
        self,
        symbol: str,
        scan_date: str,
        strategy_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self._fetch(
            load_sql("picks.detail"),
            {
                "symbol": symbol,
                "scan_date": scan_date,
                "strategy_name": strategy_name or None,
            },
        )

    def returns(
        self,
        scan_date: str,
        industry: Optional[str] = None,
        min_market_cap: Optional[int] = None,
        max_market_cap: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self._fetch(
            load_sql("picks.returns"),
            {
                "scan_date": scan_date,
                "industry": industry,
                "min_mc": min_market_cap,
                "max_mc": max_market_cap,
            },
        )
