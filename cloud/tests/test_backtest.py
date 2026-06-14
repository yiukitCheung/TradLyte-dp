"""Route-level tests for the serving layer backtest proxy endpoint."""

from unittest.mock import patch

import pytest

from serving_api.routers.backtest import _validate_payload
from fastapi import HTTPException


def _valid_payload(**overrides):
    payload = {
        "strategy_name": "golden_cross",
        "symbol": "AAPL",
        "components": {"sma": {"fast": 50, "slow": 200}},
        "start_date": "2026-01-01",
        "end_date": "2026-06-01",
        "initial_capital": 10000.0,
    }
    payload.update(overrides)
    return payload


class TestBacktestValidation:
    def test_accepts_valid_payload(self):
        _validate_payload(_valid_payload())

    def test_rejects_missing_required_fields(self):
        with pytest.raises(HTTPException) as exc:
            _validate_payload({"symbol": "AAPL"})
        assert exc.value.status_code == 400
        assert "Missing required fields" in exc.value.detail

    def test_rejects_empty_components(self):
        with pytest.raises(HTTPException) as exc:
            _validate_payload(_valid_payload(components={}))
        assert exc.value.status_code == 400
        assert "components" in exc.value.detail

    def test_rejects_bad_date_format(self):
        with pytest.raises(HTTPException) as exc:
            _validate_payload(_valid_payload(start_date="06/01/2026"))
        assert exc.value.status_code == 400

    def test_rejects_start_after_end(self):
        with pytest.raises(HTTPException) as exc:
            _validate_payload(_valid_payload(start_date="2026-06-02", end_date="2026-06-01"))
        assert exc.value.status_code == 400
        assert "on or before" in exc.value.detail

    def test_rejects_non_positive_initial_capital(self):
        with pytest.raises(HTTPException) as exc:
            _validate_payload(_valid_payload(initial_capital=0))
        assert exc.value.status_code == 400

    def test_rejects_date_range_over_max_lookback(self):
        with pytest.raises(HTTPException) as exc:
            _validate_payload(_valid_payload(start_date="2010-01-01", end_date="2026-01-01"))
        assert exc.value.status_code == 400
        assert "maximum" in exc.value.detail


def test_run_backtest_returns_400_on_invalid_payload(serving_client):
    response = serving_client.post("/backtest", json={"symbol": "AAPL"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "http_error"


@patch("serving_api.routers.backtest._invoke_backtester_lambda")
def test_run_backtest_proxies_valid_payload(mock_invoke, serving_client):
    mock_invoke.return_value = {
        "data": {"total_return": 0.25, "trades": []},
        "meta": {"symbol": "AAPL", "strategy_name": "golden_cross"},
    }

    response = serving_client.post("/backtest", json=_valid_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["total_return"] == 0.25
    assert body["meta"]["symbol"] == "AAPL"
    mock_invoke.assert_called_once()
