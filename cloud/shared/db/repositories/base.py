"""Base repository: shared query/execute plumbing.

A repository can run in two contexts:

* **Pooled (serving)** — constructed with no connection. Reads/writes go through
  the module-level pooled connection in ``connection`` (autocommit, with a
  stale-socket retry). Ideal for the read-only serving API.
* **Connection-bound (batch)** — constructed with an explicit psycopg2
  ``conn``. Reads/writes use that connection so callers control the
  transaction; writes commit automatically unless the connection is autocommit.

All methods return ``list[dict]`` (via ``RealDictCursor``) for reads.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

from psycopg2.extras import RealDictCursor

Params = Union[Dict[str, Any], Sequence[Any], None]


class BaseRepository:
    def __init__(self, conn: Optional["object"] = None) -> None:
        self._conn = conn

    # -- reads ---------------------------------------------------------------
    def _fetch(self, sql: str, params: Params = None) -> List[Dict[str, Any]]:
        if self._conn is None:
            from ..connection import execute_query

            return execute_query(sql, params)
        with self._conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params if params is not None else {})
            if cursor.description is None:
                return []
            return [dict(row) for row in cursor.fetchall()]

    def _fetch_one(self, sql: str, params: Params = None) -> Optional[Dict[str, Any]]:
        rows = self._fetch(sql, params)
        return rows[0] if rows else None

    # -- writes --------------------------------------------------------------
    def _execute(self, sql: str, params: Params = None, commit: bool = True) -> int:
        if self._conn is None:
            from ..connection import execute_query

            execute_query(sql, params)
            return 0
        with self._conn.cursor() as cursor:
            cursor.execute(sql, params if params is not None else {})
            rowcount = cursor.rowcount
        self._maybe_commit(commit)
        return rowcount

    def _executemany(self, sql: str, seq: Sequence[Sequence[Any]], commit: bool = True) -> int:
        if self._conn is None:
            raise RuntimeError("executemany requires a connection-bound repository")
        with self._conn.cursor() as cursor:
            cursor.executemany(sql, seq)
            rowcount = cursor.rowcount
        self._maybe_commit(commit)
        return rowcount

    def _maybe_commit(self, commit: bool) -> None:
        if commit and self._conn is not None and not getattr(self._conn, "autocommit", True):
            self._conn.commit()
