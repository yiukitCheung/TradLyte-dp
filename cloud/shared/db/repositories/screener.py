"""Read repository for the screener universe."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..catalog import load_sql
from .base import BaseRepository

# Whitelisted sort fields -> view columns. User input never reaches SQL except
# through this map, so the ORDER BY clause cannot be injected.
SORT_FIELDS = {
    "marketcap": "market_cap",
    "symbol": "symbol",
    "close": "close",
    "volume": "volume",
}


def normalize_sort(sort: str) -> str:
    """Translate a ``field:dir`` token into a safe ORDER BY clause."""
    field = "marketcap"
    direction = "DESC"
    if sort:
        parts = sort.split(":")
        if parts and parts[0].strip().lower() in SORT_FIELDS:
            field = parts[0].strip().lower()
        if len(parts) > 1 and parts[1].strip().lower() == "asc":
            direction = "ASC"
    column = SORT_FIELDS[field]
    nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"
    return f"{column} {direction} {nulls}"


class ScreenerRepository(BaseRepository):
    def quotes(
        self,
        sort: str = "marketcap:desc",
        limit: int = 50,
        offset: int = 0,
        industry: Optional[str] = None,
        type: Optional[str] = None,
        min_market_cap: Optional[int] = None,
        max_market_cap: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        sql = load_sql("screener.quotes").replace("__ORDER_BY__", normalize_sort(sort))
        return self._fetch(
            sql,
            {
                "industry": industry,
                "type": type,
                "min_mc": min_market_cap,
                "max_mc": max_market_cap,
                "limit": limit,
                "offset": offset,
            },
        )

    def as_of_date(self) -> Optional[Dict[str, Any]]:
        return self._fetch_one(load_sql("screener.as_of_date"))
