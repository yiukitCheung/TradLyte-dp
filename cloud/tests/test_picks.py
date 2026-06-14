"""Unit tests for serving layer pick routes."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from serving_api.cache import PICKS_CACHE, RETURNS_CACHE
from serving_api.routers.picks import (
    _PICKS_ORDER_BY,
    _parse_horizons,
    _picks_today_meta_filters,
    _to_return,
    get_pick_detail,
    get_pick_returns,
    get_picks_today,
)


@pytest.fixture(autouse=True)
def clear_picks_caches():
    PICKS_CACHE.clear()
    RETURNS_CACHE.clear()
    yield
    PICKS_CACHE.clear()
    RETURNS_CACHE.clear()


class TestPickHelpers:
    def test_to_return_positive_gain(self):
        assert _to_return(Decimal("100"), Decimal("110")) == pytest.approx(0.1)

    def test_to_return_none_when_missing_prices(self):
        assert _to_return(None, Decimal("110")) is None
        assert _to_return(Decimal("100"), None) is None

    def test_to_return_none_when_pick_price_zero(self):
        assert _to_return(Decimal("0"), Decimal("110")) is None

    def test_picks_today_meta_filters_strips_blank_industry(self):
        assert _picks_today_meta_filters("  Tech  ", 1_000, 5_000) == {
            "industry": "Tech",
            "min_mc": 1_000,
            "max_mc": 5_000,
        }
        assert _picks_today_meta_filters("", None, None)["industry"] is None
        assert _picks_today_meta_filters(None, None, None)["industry"] is None

    def test_parse_horizons_defaults_when_empty(self):
        assert _parse_horizons("") == [1, 5, 21]
        assert _parse_horizons("bad,0,999") == [1, 5, 21]

    def test_parse_horizons_deduplicates_and_sorts(self):
        assert _parse_horizons("21,1,5,1") == [1, 5, 21]

    def test_picks_order_by_uses_market_cap_then_rank(self):
        assert _PICKS_ORDER_BY == "m.marketcap DESC NULLS LAST, sp.rank ASC"


@patch("serving_api.routers.picks.execute_query")
def test_get_picks_today_returns_sorted_meta(mock_execute_query, serving_client):
    mock_execute_query.return_value = [
        {
            "scan_date": date(2026, 6, 5),
            "rank": 2,
            "symbol": "MSFT",
            "strategy_name": "golden_cross",
            "signal": "BUY",
            "price": Decimal("420.00"),
            "confidence": 0.9,
            "metadata": {},
            "market_cap": 3_000_000_000_000,
        },
        {
            "scan_date": date(2026, 6, 5),
            "rank": 1,
            "symbol": "AAPL",
            "strategy_name": "golden_cross",
            "signal": "BUY",
            "price": Decimal("200.00"),
            "confidence": 0.85,
            "metadata": {},
            "market_cap": 2_000_000_000_000,
        },
    ]

    response = serving_client.get("/picks/today?limit=2")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["sort"] == "marketcap:desc"
    assert body["meta"]["scan_date"] == "2026-06-05"
    assert body["meta"]["count"] == 2
    assert body["data"][0]["symbol"] == "MSFT"
    assert body["data"][0]["market_cap"] == 3_000_000_000_000

    sql = mock_execute_query.call_args.kwargs["params"]
    assert sql["limit"] == 2
    query = mock_execute_query.call_args.args[0]
    assert "ORDER BY m.marketcap DESC NULLS LAST, sp.rank ASC" in query


@patch("serving_api.routers.picks.execute_query")
def test_get_picks_today_uses_cache_on_repeat(mock_execute_query, serving_client):
    mock_execute_query.return_value = []

    first = serving_client.get("/picks/today")
    second = serving_client.get("/picks/today")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["meta"]["cache_hit"] is False
    assert second.json()["meta"]["cache_hit"] is True
    mock_execute_query.assert_called_once()


@patch("serving_api.routers.picks.execute_query")
def test_get_pick_detail_404_when_missing(mock_execute_query):
    mock_execute_query.return_value = []

    with pytest.raises(HTTPException) as exc:
        get_pick_detail(symbol="ZZZZ", scan_date=date(2026, 6, 5))

    assert exc.value.status_code == 404


@patch("serving_api.routers.picks.execute_query")
def test_get_pick_returns_includes_market_cap_and_horizons(mock_execute_query, serving_client):
    mock_execute_query.return_value = [
        {
            "symbol": "AAPL",
            "rank": 1,
            "strategy_name": "golden_cross",
            "signal": "BUY",
            "pick_price": Decimal("100"),
            "scan_date": date(2026, 6, 5),
            "market_cap": 2_000_000_000_000,
            "close_1d": Decimal("105"),
            "close_5d": Decimal("110"),
            "close_21d": Decimal("120"),
            "close_now": Decimal("115"),
        }
    ]

    response = serving_client.get("/picks/2026-06-05/returns?horizons=1,5")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["sort"] == "marketcap:desc"
    assert body["meta"]["horizons"] == [1, 5]
    row = body["data"][0]
    assert row["market_cap"] == 2_000_000_000_000
    assert row["returns"]["1d"] == pytest.approx(0.05)
    assert row["returns"]["5d"] == pytest.approx(0.10)
    assert row["return_to_date"] == pytest.approx(0.15)

    query = mock_execute_query.call_args.args[0]
    assert "ORDER BY p.market_cap DESC NULLS LAST, p.rank ASC" in query
