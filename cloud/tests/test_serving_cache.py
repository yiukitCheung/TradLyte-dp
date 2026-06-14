"""Unit tests for serving layer in-memory cache helpers."""

from serving_api.cache import make_cache_key


def test_make_cache_key_is_stable_for_same_params():
    params = {"limit": 25, "industry": None, "min_mc": 1000}
    assert make_cache_key("picks_today", params) == make_cache_key("picks_today", params)


def test_make_cache_key_differs_when_params_change():
    base = make_cache_key("picks_today", {"limit": 25})
    changed = make_cache_key("picks_today", {"limit": 50})
    assert base != changed


def test_make_cache_key_ignores_param_order():
    a = make_cache_key("screener", {"limit": 10, "offset": 0, "sort": "marketcap:desc"})
    b = make_cache_key("screener", {"sort": "marketcap:desc", "offset": 0, "limit": 10})
    assert a == b
