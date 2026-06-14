"""Route-level tests for serving layer market endpoints."""

from unittest.mock import patch

import pytest

from serving_api.cache import MARKET_CACHE


@pytest.fixture(autouse=True)
def clear_market_cache():
    MARKET_CACHE.clear()
    yield
    MARKET_CACHE.clear()


@patch("serving_api.routers.market.MarketRepository")
def test_get_latest_quote_returns_payload(mock_repo, serving_client):
    mock_repo.return_value.latest_quote.return_value = {
        "symbol": "AAPL",
        "close": 204.0,
        "as_of_date": "2026-06-05",
    }

    response = serving_client.get("/market/quote/aapl")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["symbol"] == "AAPL"
    assert body["meta"]["cache_hit"] is False
    assert body["data"]["symbol"] == "AAPL"
    mock_repo.return_value.latest_quote.assert_called_once_with("AAPL")


@patch("serving_api.routers.market.MarketRepository")
def test_get_latest_quote_404_when_missing(mock_repo, serving_client):
    mock_repo.return_value.latest_quote.return_value = None

    response = serving_client.get("/market/quote/ZZZZ")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"


@patch("serving_api.routers.market.MarketRepository")
def test_get_latest_quote_uses_cache_on_repeat(mock_repo, serving_client):
    mock_repo.return_value.latest_quote.return_value = {"symbol": "AAPL", "close": 204.0}

    first = serving_client.get("/market/quote/AAPL")
    second = serving_client.get("/market/quote/AAPL")

    assert first.json()["meta"]["cache_hit"] is False
    assert second.json()["meta"]["cache_hit"] is True
    mock_repo.return_value.latest_quote.assert_called_once()


@patch("serving_api.routers.market.MarketRepository")
def test_get_ohlcv_history_returns_payload(mock_repo, serving_client):
    mock_repo.return_value.ohlcv_history.return_value = [
        {"as_of_date": "2026-06-05", "close": 204.0},
        {"as_of_date": "2026-06-04", "close": 201.0},
    ]

    response = serving_client.get("/market/ohlcv/aapl?interval=1d&limit=2&sort=desc")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["symbol"] == "AAPL"
    assert body["meta"]["interval"] == "1d"
    assert body["meta"]["count"] == 2
    assert body["meta"]["sort"] == "desc"
    mock_repo.return_value.ohlcv_history.assert_called_once_with(
        symbol="AAPL",
        interval="1d",
        limit=2,
        sort="desc",
        start_date=None,
        end_date=None,
    )


def test_get_ohlcv_history_rejects_unsupported_interval(serving_client):
    response = serving_client.get("/market/ohlcv/AAPL?interval=2d")

    assert response.status_code == 400
    assert "Unsupported interval" in response.json()["error"]["message"]


def test_get_ohlcv_history_rejects_unsupported_sort(serving_client):
    response = serving_client.get("/market/ohlcv/AAPL?sort=sideways")

    assert response.status_code == 400
    assert "Unsupported sort" in response.json()["error"]["message"]


@patch("serving_api.routers.market._fetch_polygon_news")
def test_get_symbol_news_strips_api_key_from_next_url(mock_fetch, serving_client):
    mock_fetch.return_value = {
        "results": [{"id": "n1", "title": "Headline"}],
        "next_url": "https://api.polygon.io/v2/reference/news?cursor=abc&apiKey=secret",
    }

    response = serving_client.get("/market/news/aapl?limit=5&order=desc")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["symbol"] == "AAPL"
    assert body["meta"]["count"] == 1
    assert "apiKey" not in body["meta"]["next_url"]
    assert "cursor=abc" in body["meta"]["next_url"]
    mock_fetch.assert_called_once()


def test_get_symbol_news_rejects_unsupported_order(serving_client):
    response = serving_client.get("/market/news/AAPL?order=upwards")

    assert response.status_code == 400
    assert "Unsupported order" in response.json()["error"]["message"]


def test_get_symbol_news_rejects_inverted_date_range(serving_client):
    response = serving_client.get(
        "/market/news/AAPL?published_utc_gte=2026-06-10&published_utc_lte=2026-06-01"
    )

    assert response.status_code == 400
    assert "on or before" in response.json()["error"]["message"]


@patch("serving_api.routers.market.MarketRepository")
def test_get_symbol_returns_computes_horizons(mock_repo, serving_client):
    mock_repo.return_value.symbol_returns.return_value = {
        "as_of_date": "2026-06-05",
        "close_now": 110.0,
        "close_1d_ago": 100.0,
        "close_5d_ago": 100.0,
        "close_21d_ago": 100.0,
    }

    response = serving_client.get("/market/returns/aapl?horizons=1,5")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["horizons"] == [1, 5]
    assert body["data"]["symbol"] == "AAPL"
    assert body["data"]["returns"]["1d"] == pytest.approx(0.1)
    assert body["data"]["returns"]["5d"] == pytest.approx(0.1)


@patch("serving_api.routers.market.MarketRepository")
def test_get_symbol_returns_404_when_no_data(mock_repo, serving_client):
    mock_repo.return_value.symbol_returns.return_value = None

    response = serving_client.get("/market/returns/ZZZZ")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"
