"""Unit tests for serving API app wiring and auth."""


def test_health_does_not_require_api_key(serving_client):
    response = serving_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "serving-api"
    assert "timestamp" in body


def test_protected_route_rejects_missing_api_key(serving_client_with_key):
    response = serving_client_with_key.get("/picks/today")

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid API key"


def test_protected_route_accepts_valid_api_key(serving_client_with_key):
    from unittest.mock import patch

    with patch("serving_api.routers.picks.PicksRepository") as mock_repo:
        mock_repo.return_value.today.return_value = []
        response = serving_client_with_key.get("/picks/today", headers={"x-api-key": "test-key"})

    assert response.status_code == 200


def test_http_exception_uses_standard_error_shape(serving_client_with_key):
    from unittest.mock import patch

    with patch("serving_api.routers.picks.PicksRepository") as mock_repo:
        mock_repo.return_value.today.return_value = []
        response = serving_client_with_key.get("/picks/today")

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "http_error", "message": "Invalid API key"},
    }
