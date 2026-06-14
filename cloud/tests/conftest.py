"""Pytest path setup and shared fixtures for cloud package imports."""

import sys
from pathlib import Path

import pytest

CLOUD_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = CLOUD_ROOT / "shared"
BATCH_ROOT = CLOUD_ROOT / "batch_layer"
SERVING_ROOT = CLOUD_ROOT / "serving_layer" / "lambda_functions"

for path in (str(CLOUD_ROOT), str(SHARED_ROOT), str(BATCH_ROOT), str(SERVING_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture
def serving_client(monkeypatch):
    """FastAPI TestClient with API-key auth disabled for route unit tests."""
    monkeypatch.delenv("SERVING_API_KEY", raising=False)
    monkeypatch.delenv("SERVING_API_KEY_SECRET_ARN", raising=False)

    from fastapi.testclient import TestClient
    from serving_api.app import _get_expected_api_key, app

    _get_expected_api_key.cache_clear()
    with TestClient(app) as client:
        yield client
    _get_expected_api_key.cache_clear()


@pytest.fixture
def serving_client_with_key(monkeypatch):
    """FastAPI TestClient with a fixed API key enforced."""
    monkeypatch.setenv("SERVING_API_KEY", "test-key")

    from fastapi.testclient import TestClient
    from serving_api.app import _get_expected_api_key, app

    _get_expected_api_key.cache_clear()
    with TestClient(app) as client:
        yield client
    _get_expected_api_key.cache_clear()
