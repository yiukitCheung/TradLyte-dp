"""Unit tests for serving layer screener routes."""

from unittest.mock import patch

from serving_api.cache import SCREENER_CACHE
from serving_api.routers.screener import _normalize_sort


def setup_function():
    SCREENER_CACHE.clear()


def teardown_function():
    SCREENER_CACHE.clear()


def test_normalize_sort_default_marketcap_desc():
    assert _normalize_sort("") == "m.marketcap DESC NULLS LAST"


def test_normalize_sort_symbol_asc():
    assert _normalize_sort("symbol:asc") == "m.symbol ASC NULLS FIRST"


def test_normalize_sort_close_desc():
    assert _normalize_sort("close:desc") == "o.close DESC NULLS LAST"


def test_normalize_sort_unknown_field_falls_back_to_marketcap():
    assert _normalize_sort("unknown:asc") == "m.marketcap ASC NULLS FIRST"


@patch("serving_api.routers.screener.execute_one")
@patch("serving_api.routers.screener.execute_query")
def test_get_screener_quotes_returns_payload(mock_execute_query, mock_execute_one, serving_client):
    mock_execute_query.return_value = [
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "industry": "Technology",
            "market_cap": 2_000_000_000_000,
            "type": "CS",
            "primary_exchange": "XNAS",
            "as_of_date": "2026-06-05",
            "open": 200.0,
            "high": 205.0,
            "low": 199.0,
            "close": 204.0,
            "volume": 50_000_000,
        }
    ]
    mock_execute_one.return_value = {"as_of_date": "2026-06-05"}

    response = serving_client.get("/screener/quotes?limit=1&sort=marketcap:desc")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["count"] == 1
    assert body["meta"]["sort"] == "marketcap:desc"
    assert body["meta"]["as_of_date"] == "2026-06-05"
    assert body["data"][0]["symbol"] == "AAPL"

    query = mock_execute_query.call_args.args[0]
    assert "ORDER BY m.marketcap DESC NULLS LAST" in query
