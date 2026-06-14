"""Unified PostgreSQL / RDS connection layer.

This module is the single place that knows how to reach the database. It merges
three previously separate code paths:

1. ``serving_api.db``                 - pooled connection + ``execute_query`` /
                                         ``execute_one`` for the serving API.
2. ``shared.clients.rds_connection``  - the ``postgresql://`` URI builder used by
                                         the scanner / snapshot / backtester.
3. ``RDSTimescaleClient`` credential   - Secrets Manager loading with an env
   loading                              credential fallback for VPC Lambdas.

Credential resolution (in order):

* If ``RDS_USE_ENV_CREDENTIALS`` is truthy OR ``RDS_SECRET_ARN`` is unset, read
  ``RDS_ENDPOINT`` / ``RDS_USERNAME`` / ``RDS_PASSWORD`` / ``RDS_DATABASE`` (with
  ``DB_HOST`` / ``DB_USER`` / ``DB_PASSWORD`` / ``DB_NAME`` accepted as aliases).
* Otherwise fetch the secret JSON from Secrets Manager (``host`` / ``port`` /
  ``username`` / ``password`` / ``database``|``dbname``). ``SECRETS_MANAGER_ENDPOINT_URL``
  is honoured for VPC interface endpoints without private DNS.

SSL: ``disable`` for local hosts (localhost / docker), otherwise ``require``
(override with ``RDS_SSLMODE`` or the legacy ``RDS_SSL_MODE``).
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Union

import boto3
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# Hosts that should connect without TLS (local dev / docker compose).
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "timescaledb", "host.docker.internal"}

# Params may be a mapping (named ``%(x)s``) or a sequence (positional ``%s``).
Params = Union[Dict[str, Any], Sequence[Any], None]

# Module-level pooled connection for the serving API (warm Lambda reuse).
_serving_conn: Optional["psycopg2.extensions.connection"] = None


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _region() -> str:
    return os.environ.get("AWS_REGION", "ca-west-1")


def _sslmode(host: str) -> str:
    host_l = (host or "").lower()
    if host in _LOCAL_HOSTS or "local" in host_l:
        return "disable"
    return os.environ.get("RDS_SSLMODE") or os.environ.get("RDS_SSL_MODE") or "require"


def _normalize_secret(secret: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Secrets Manager payload to canonical credential keys."""
    return {
        "host": secret["host"],
        "port": int(secret.get("port", 5432)),
        "database": secret.get("database") or secret.get("dbname") or "postgres",
        "username": secret["username"],
        "password": secret["password"],
    }


def _fetch_secret(secret_arn: str, region: Optional[str] = None) -> Dict[str, Any]:
    endpoint_url = (os.environ.get("SECRETS_MANAGER_ENDPOINT_URL") or "").strip() or None
    client = boto3.client(
        "secretsmanager",
        region_name=region or _region(),
        **({"endpoint_url": endpoint_url} if endpoint_url else {}),
    )
    try:
        response = client.get_secret_value(SecretId=secret_arn)
    except Exception as exc:  # noqa: BLE001 - re-raised with operator guidance
        if type(exc).__name__ == "ConnectTimeoutError" or "connect timeout" in str(exc).lower():
            raise RuntimeError(
                "Secrets Manager HTTPS timed out (VPC cannot reach the API). Options: "
                "(1) NAT Gateway, (2) interface VPC endpoint for secretsmanager, "
                "(3) set SECRETS_MANAGER_ENDPOINT_URL, or "
                "(4) set RDS_USE_ENV_CREDENTIALS=true with RDS_ENDPOINT/RDS_USERNAME/"
                "RDS_PASSWORD/RDS_DATABASE."
            ) from exc
        raise
    return _normalize_secret(json.loads(response["SecretString"]))


def _env_credentials() -> Dict[str, Any]:
    host = os.environ.get("RDS_ENDPOINT") or os.environ.get("DB_HOST")
    username = os.environ.get("RDS_USERNAME") or os.environ.get("DB_USER")
    password = os.environ.get("RDS_PASSWORD") or os.environ.get("DB_PASSWORD")
    database = os.environ.get("RDS_DATABASE") or os.environ.get("DB_NAME") or "postgres"
    port = os.environ.get("RDS_PORT") or os.environ.get("DB_PORT") or "5432"
    missing = [
        name
        for name, value in (
            ("RDS_ENDPOINT/DB_HOST", host),
            ("RDS_USERNAME/DB_USER", username),
            ("RDS_PASSWORD/DB_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "Env-credential mode requires: " + ", ".join(missing)
        )
    return {
        "host": host,
        "port": int(port),
        "database": database,
        "username": username,
        "password": password,
    }


@lru_cache(maxsize=1)
def load_db_credentials() -> Dict[str, Any]:
    """Resolve DB credentials from env or Secrets Manager (cached per process)."""
    secret_arn = (os.environ.get("RDS_SECRET_ARN") or "").strip()
    if _truthy(os.environ.get("RDS_USE_ENV_CREDENTIALS")) or not secret_arn:
        logger.info("DB credentials: environment variables")
        return _env_credentials()
    logger.info("DB credentials: Secrets Manager (%s)", secret_arn)
    return _fetch_secret(secret_arn)


def _resolve_credentials(secret_arn: Optional[str], region: Optional[str]) -> Dict[str, Any]:
    if secret_arn:
        # Explicit ARN bypasses the cached default resolution.
        if secret_arn == (os.environ.get("RDS_SECRET_ARN") or "").strip() and not _truthy(
            os.environ.get("RDS_USE_ENV_CREDENTIALS")
        ):
            return load_db_credentials()
        return _fetch_secret(secret_arn, region)
    return load_db_credentials()


def connection_string(*, secret_arn: Optional[str] = None, region: Optional[str] = None) -> str:
    """Build a ``postgresql://`` URI (drop-in for the legacy helper)."""
    creds = _resolve_credentials(secret_arn, region)
    sslmode = _sslmode(creds["host"])
    return (
        f"postgresql://{creds['username']}:{creds['password']}"
        f"@{creds['host']}:{creds['port']}/{creds['database']}?sslmode={sslmode}"
    )


def connect(
    *,
    secret_arn: Optional[str] = None,
    region: Optional[str] = None,
    autocommit: bool = True,
) -> "psycopg2.extensions.connection":
    """Open a new psycopg2 connection using resolved credentials."""
    creds = _resolve_credentials(secret_arn, region)
    conn = psycopg2.connect(
        host=creds["host"],
        port=creds["port"],
        dbname=creds["database"],
        user=creds["username"],
        password=creds["password"],
        sslmode=_sslmode(creds["host"]),
        connect_timeout=int(os.environ.get("RDS_CONNECT_TIMEOUT", "10")),
        application_name=os.environ.get("DB_APP_NAME", "tradlyte"),
    )
    conn.autocommit = autocommit
    return conn


def get_connection() -> "psycopg2.extensions.connection":
    """Return the pooled serving connection, reconnecting if it was dropped."""
    global _serving_conn
    if _serving_conn is not None and _serving_conn.closed == 0:
        return _serving_conn
    _serving_conn = connect(autocommit=True)
    logger.info("Established pooled PostgreSQL connection")
    return _serving_conn


def reset_connection() -> None:
    """Drop the pooled serving connection (used after operational errors)."""
    global _serving_conn
    if _serving_conn is not None:
        try:
            _serving_conn.close()
        except Exception:  # noqa: BLE001 - best effort
            pass
    _serving_conn = None


def _run(conn, sql: str, params: Params) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(sql, params if params is not None else {})
        if cursor.description is None:
            return []
        return [dict(row) for row in cursor.fetchall()]


def execute_query(sql: str, params: Params = None) -> List[Dict[str, Any]]:
    """Run a query on the pooled connection, retrying once on a stale socket."""
    conn = get_connection()
    try:
        return _run(conn, sql, params)
    except psycopg2.OperationalError:
        reset_connection()
        return _run(get_connection(), sql, params)


def execute_one(sql: str, params: Params = None) -> Optional[Dict[str, Any]]:
    rows = execute_query(sql, params=params)
    return rows[0] if rows else None
