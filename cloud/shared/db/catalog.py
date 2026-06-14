"""SQL query catalog loader.

Queries live as ``.sql`` files under ``sql/`` (grouped by domain) instead of as
inline strings scattered across routers and jobs. Repositories reference them by
dotted name, e.g. ``load_sql("picks.today")`` -> ``sql/picks/today.sql``.

Files are read once and cached. The directory ships with every deployment
artifact because each build copies the whole ``db/`` package.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_SQL_DIR = Path(__file__).resolve().parent / "sql"


@lru_cache(maxsize=None)
def load_sql(name: str) -> str:
    """Return the contents of a catalog query.

    Args:
        name: Dotted or slash path relative to ``sql/`` without the ``.sql``
            suffix (e.g. ``"picks.today"`` or ``"picks/today"``).
    """
    relative = name.replace(".", "/") + ".sql"
    path = _SQL_DIR / relative
    if not path.is_file():
        raise FileNotFoundError(f"SQL catalog entry not found: {name} ({path})")
    return path.read_text()
