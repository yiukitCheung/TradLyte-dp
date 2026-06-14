"""Backward-compatible shim over the shared DB connection layer.

The real implementation now lives in ``db.connection`` (canonical source
``cloud/shared/db/connection.py``), which the serving Lambda bundles as ``db/``.
This module is kept so any direct importers of ``serving_api.db`` keep working
and so there is a single connection/pool/secret path across the whole project.
"""

try:  # deployed layout copies shared/db -> db/ at the zip root
    from db.connection import (  # noqa: F401
        connect,
        execute_one,
        execute_query,
        get_connection,
        load_db_credentials,
    )
except ImportError:  # pragma: no cover - local/test fallback
    from shared.db.connection import (  # noqa: F401
        connect,
        execute_one,
        execute_query,
        get_connection,
        load_db_credentials,
    )


def load_rds_secret():
    """Deprecated alias retained for compatibility (use ``load_db_credentials``)."""
    return load_db_credentials()


__all__ = [
    "connect",
    "execute_one",
    "execute_query",
    "get_connection",
    "load_db_credentials",
    "load_rds_secret",
]
