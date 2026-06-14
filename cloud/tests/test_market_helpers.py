"""Unit tests for serving layer market route helpers."""

from decimal import Decimal

from serving_api.routers.market import _parse_horizons, _strip_api_key_from_url, _to_return


def test_market_to_return_positive_gain():
    assert _to_return(Decimal("50"), Decimal("55")) == 0.1


def test_market_to_return_none_when_base_zero():
    assert _to_return(Decimal("0"), Decimal("55")) is None


def test_market_parse_horizons_custom_values():
    assert _parse_horizons("3,10") == [3, 10]


def test_strip_api_key_from_url_removes_query_param():
    url = "https://api.polygon.io/v2/reference/news?ticker=AAPL&apiKey=secret&limit=5"
    cleaned = _strip_api_key_from_url(url)
    assert "apiKey" not in cleaned
    assert "ticker=AAPL" in cleaned
    assert "limit=5" in cleaned


def test_strip_api_key_from_url_passthrough_when_missing():
    url = "https://api.polygon.io/v2/reference/news?ticker=AAPL"
    assert _strip_api_key_from_url(url) == url
