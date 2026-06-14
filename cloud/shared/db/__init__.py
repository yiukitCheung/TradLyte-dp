"""Tradlyte database access layer (single source of truth).

This package decouples SQL from application code:

* ``connection``   - the one PostgreSQL/RDS connection layer (Secrets Manager or
                     env credentials, connection-string builder, pooled serving
                     helpers). Replaces the three legacy connection paths
                     (serving_api.db, shared.clients.rds_connection, and the
                     secret-loading inside RDSTimescaleClient).
* ``catalog``      - loads versioned ``.sql`` files from ``sql/`` so queries live
                     as reviewable SQL, not inline strings.
* ``repositories`` - thin classes that wrap the catalog + connection per domain
                     (picks, screener, market, symbols, ohlcv, scan signals,
                     watermark).
* ``migrations``   - ordered, idempotent schema migrations plus a runner that
                     records applied versions in ``schema_migrations``.

Internal modules use *relative* imports so the package works under every
deployment layout this repo uses (``shared.db`` as a subpackage, ``db`` flattened
at a zip root, or on ``PYTHONPATH`` inside a container).
"""

from .catalog import load_sql
from .connection import (
    connect,
    connection_string,
    execute_one,
    execute_query,
    get_connection,
    load_db_credentials,
)

__all__ = [
    "connect",
    "connection_string",
    "execute_one",
    "execute_query",
    "get_connection",
    "load_db_credentials",
    "load_sql",
]
