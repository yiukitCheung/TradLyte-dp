"""In-memory TTL cache helpers for read-heavy endpoints."""

import json
import os
from typing import Any, Dict

from cachetools import TTLCache


def _ttl_from_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


SCREENER_CACHE = TTLCache(maxsize=256, ttl=_ttl_from_env("SCREENER_CACHE_TTL_S", 60))
PICKS_CACHE = TTLCache(maxsize=128, ttl=_ttl_from_env("SCREENER_CACHE_TTL_S", 60))
RETURNS_CACHE = TTLCache(maxsize=128, ttl=_ttl_from_env("RETURNS_CACHE_TTL_S", 300))


def make_cache_key(prefix: str, params: Dict[str, Any]) -> str:
    normalized = {k: params[k] for k in sorted(params.keys())}
    return f"{prefix}:{json.dumps(normalized, sort_keys=True, default=str)}"
