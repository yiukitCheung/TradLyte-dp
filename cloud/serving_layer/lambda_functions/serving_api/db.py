"""Shared RDS access helpers for serving endpoints."""

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

import boto3
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

_connection: Optional[psycopg2.extensions.connection] = None
_secrets_client = boto3.client(
    "secretsmanager", region_name=os.environ.get("AWS_REGION", "ca-west-1")
)


@lru_cache(maxsize=1)
def load_rds_secret() -> Dict[str, Any]:
    secret_arn = os.environ.get("RDS_SECRET_ARN")
    if not secret_arn:
        raise RuntimeError("RDS_SECRET_ARN environment variable is required")

    response = _secrets_client.get_secret_value(SecretId=secret_arn)
    secret = json.loads(response["SecretString"])
    return {
        "host": secret["host"],
        "port": int(secret.get("port", 5432)),
        "database": secret.get("database", secret.get("dbname", "postgres")),
        "username": secret["username"],
        "password": secret["password"],
    }

def get_connection() -> psycopg2.extensions.connection:
    global _connection

    if _connection is not None and _connection.closed == 0:
        return _connection

    cfg = load_rds_secret()
    _connection = psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["database"],
        user=cfg["username"],
        password=cfg["password"],
        connect_timeout=5,
        sslmode=os.environ.get("RDS_SSL_MODE", "require"),
        application_name="tradlyte-serving-api",
    )
    _connection.autocommit = True
    logger.info("Established serving API PostgreSQL connection")
    return _connection

def execute_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params or {})
            return [dict(row) for row in cursor.fetchall()]
    except psycopg2.OperationalError:
        # Retry once after dropping a stale connection.
        global _connection
        _connection = None
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params or {})
            return [dict(row) for row in cursor.fetchall()]

def execute_one(sql: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    rows = execute_query(sql, params=params)
    return rows[0] if rows else None
