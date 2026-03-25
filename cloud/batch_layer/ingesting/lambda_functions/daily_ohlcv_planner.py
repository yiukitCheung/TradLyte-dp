"""
VPC Lambda: reads RDS for symbols / missing dates, then async-invokes daily_ohlcv_fetcher (no VPC).

Wire EventBridge (or manual invoke) to this function for production backfill modes that need
watermarks. Simple single-day runs can invoke the fetcher directly with an empty event.
"""

import json
import logging
import os
from typing import Any, Dict

import boto3

from shared.clients.rds_timescale_client import RDSTimescaleClient
from shared.utils.pipeline import get_missing_dates, get_new_symbols

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    inner: Dict[str, Any] = dict(event) if isinstance(event, dict) else {}

    rds_client = RDSTimescaleClient(secret_arn=os.environ["RDS_SECRET_ARN"])
    fetcher_name = os.environ.get("OHLCV_FETCHER_FUNCTION_NAME", "")
    if not fetcher_name:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "OHLCV_FETCHER_FUNCTION_NAME env var is required"}),
        }

    lambda_client = boto3.client("lambda")

    if inner.get("historical_backfill"):
        if inner.get("new_symbols_only", True) and not inner.get("symbols"):
            symbols = get_new_symbols(rds_client)
            if not symbols:
                return {
                    "statusCode": 200,
                    "body": json.dumps({"message": "No new symbols to backfill"}),
                }
            inner["symbols"] = symbols
        elif not inner.get("symbols"):
            inner["symbols"] = rds_client.get_active_symbols()
            if not inner["symbols"]:
                return {
                    "statusCode": 200,
                    "body": json.dumps({"message": "No active symbols for historical backfill"}),
                }
    else:
        if inner.get("symbols") is None:
            syms = rds_client.get_active_symbols()
            if syms:
                inner["symbols"] = syms

        has_explicit_day = bool(inner.get("date") or inner.get("dates"))
        backfill_missing = inner.get("backfill_missing", True)
        max_backfill_days = int(inner.get("max_backfill_days", 30))

        if not has_explicit_day and backfill_missing:
            missing = get_missing_dates(rds_client, max_backfill_days)
            if not missing:
                return {
                    "statusCode": 200,
                    "body": json.dumps({"message": "No missing dates — database is up to date"}),
                }
            inner["dates"] = [d.isoformat() for d in missing]

    payload_bytes = json.dumps(inner).encode("utf-8")
    lambda_client.invoke(
        FunctionName=fetcher_name,
        InvocationType="Event",
        Payload=payload_bytes,
    )

    logger.info("Invoked fetcher %s async (payload bytes=%s)", fetcher_name, len(payload_bytes))

    return {
        "statusCode": 202,
        "body": json.dumps(
            {
                "message": "OHLCV fetcher invoked asynchronously",
                "fetcher": fetcher_name,
            }
        ),
    }
