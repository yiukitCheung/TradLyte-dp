"""Unit tests for serving layer screener routes."""

from unittest.mock import patch

from db.repositories.screener import normalize_sort
from serving_api.cache import SCREENER_CACHE


def setup_function():
    SCREENER_CACHE.clear()


def teardown_function():
    SCREENER_CACHE.clear()


def test_normalize_sort_default_marketcap_desc():
    assert normalize_sort("") == "market_cap DESC NULLS LAST"


def test_normalize_sort_symbol_asc():
    assert normalize_sort("symbol:asc") == "symbol ASC NULLS FIRST"


def test_normalize_sort_close_desc():
    assert normalize_sort("close:desc") == "close DESC NULLS LAST"


def test_normalize_sort_unknown_field_falls_back_to_marketcap():
    assert normalize_sort("unknown:asc") == "market_cap ASC NULLS FIRST"


@patch("serving_api.routers.screener.ScreenerRepository")
def test_get_screener_quotes_returns_payload(mock_repo, serving_client):
    mock_repo.return_value.quotes.return_value = [
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
    mock_repo.return_value.as_of_date.return_value = {"as_of_date": "2026-06-05"}

    response = serving_client.get("/screener/quotes?limit=1&sort=marketcap:desc")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["count"] == 1
    assert body["meta"]["sort"] == "marketcap:desc"
    assert body["meta"]["as_of_date"] == "2026-06-05"
    assert body["data"][0]["symbol"] == "AAPL"

    mock_repo.return_value.quotes.assert_called_once_with(
        sort="marketcap:desc",
        limit=1,
        offset=0,
        industry=None,
        type=None,
        min_market_cap=None,
        max_market_cap=None,
    )
