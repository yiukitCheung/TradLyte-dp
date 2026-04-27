"""
VPC Lambda: S3 bronze parquet → RDS raw_ohlcv + watermark updates.

Trigger: SQS queue subscribed to PutObject on bronze/raw_ohlcv/ (recommended), or direct S3 event.

Optional replay: {"s3_bucket": "...", "s3_key": "..."}

Bulk by session date (Step Functions after fetcher): {"s3_bucket": "...", "ingest_dates": ["2024-01-15", ...]}
lists bronze parquet keys ending with date=YYYY-MM-DD.parquet and ingests each.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any, Dict, List, Tuple

import boto3
import pyarrow.parquet as pq

from shared.clients.rds_timescale_client import RDSTimescaleClient
from shared.models.data_models import OHLCVData
from shared.utils.pipeline import update_watermark, write_to_rds_with_retention

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_DATE_IN_KEY = re.compile(r"date=(\d{4}-\d{2}-\d{2})")


def _parse_s3_from_record(record: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if "s3" in record:
        b = record["s3"]["bucket"]["name"]
        k = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        out.append((b, k))
    return out


def _load_ohlcv_from_parquet(body: bytes) -> List[OHLCVData]:
    table = pq.read_table(BytesIO(body))
    n = table.num_rows
    if n == 0:
        return []
    cols = {name: table.column(name) for name in table.column_names}
    required = ("symbol", "open", "high", "low", "close", "volume", "timestamp", "interval")
    for c in required:
        if c not in cols:
            raise ValueError(f"parquet missing column: {c}")

    rows: List[OHLCVData] = []
    for i in range(n):
        ts = cols["timestamp"][i].as_py()
        if not isinstance(ts, datetime):
            raise ValueError(f"timestamp row {i} is not datetime: {type(ts)}")

        rows.append(
            OHLCVData(
                symbol=str(cols["symbol"][i].as_py()),
                open=Decimal(str(cols["open"][i].as_py())),
                high=Decimal(str(cols["high"][i].as_py())),
                low=Decimal(str(cols["low"][i].as_py())),
                close=Decimal(str(cols["close"][i].as_py())),
                volume=int(cols["volume"][i].as_py()),
                timestamp=ts,
                interval=str(cols["interval"][i].as_py()),
            )
        )
    return rows


def _fetch_date_for_rows(key: str, rows: List[OHLCVData]) -> date:
    m = _DATE_IN_KEY.search(key)
    if m:
        return date.fromisoformat(m.group(1))
    return max(r.timestamp.date() for r in rows)


def _ingest_all_parquet_for_session_dates(
    rds_client: RDSTimescaleClient, bucket: str, session_dates: List[str]
) -> Dict[str, Any]:
    """
    Single list pass under S3_BRONZE_PREFIX; ingest every .parquet whose key matches
    date=<session_date> for any requested session date.
    """
    wanted = {str(d).strip() for d in session_dates if str(d).strip()}
    if not wanted:
        return {"ingested_keys": [], "dates": [], "count": 0}

    prefix = os.environ.get("S3_BRONZE_PREFIX", "bronze/raw_ohlcv").rstrip("/") + "/"
    s3 = boto3.client("s3")
    ingested_keys: List[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            key = obj["Key"]
            if not key.endswith(".parquet"):
                continue
            m = _DATE_IN_KEY.search(key)
            if m and m.group(1) in wanted:
                _ingest_s3_object(rds_client, bucket, key)
                ingested_keys.append(key)
    return {"ingested_keys": ingested_keys, "dates": sorted(wanted), "count": len(ingested_keys)}


def _ingest_s3_object(rds_client: RDSTimescaleClient, bucket: str, key: str) -> None:
    if not key.endswith(".parquet"):
        logger.info("Skip non-parquet key: %s", key)
        return

    prefix = os.environ.get("S3_BRONZE_PREFIX", "bronze/raw_ohlcv")
    if prefix and prefix not in key:
        logger.info("Skip key outside prefix %s: %s", prefix, key)
        return

    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    rows = _load_ohlcv_from_parquet(body)
    if not rows:
        logger.warning("Empty parquet: s3://%s/%s", bucket, key)
        return

    retention_years = int(os.environ.get("OHLCV_RDS_RETENTION_YEARS", "5"))
    inserted = write_to_rds_with_retention(rds_client, rows, retention_years=retention_years)
    logger.info("RDS insert s3://%s/%s rows=%s inserted=%s", bucket, key, len(rows), inserted)

    fetch_date = _fetch_date_for_rows(key, rows)
    symbols = sorted({r.symbol for r in rows})
    update_watermark(rds_client, symbols, fetch_date)


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    rds_client = RDSTimescaleClient.from_lambda_environment()

    ingest_dates = event.get("ingest_dates")
    if ingest_dates and isinstance(ingest_dates, list) and event.get("s3_bucket"):
        summary = _ingest_all_parquet_for_session_dates(
            rds_client, event["s3_bucket"], [str(x) for x in ingest_dates]
        )
        return {"statusCode": 200, "body": json.dumps(summary)}

    if event.get("s3_bucket") and event.get("s3_key"):
        _ingest_s3_object(rds_client, event["s3_bucket"], event["s3_key"])
        return {"statusCode": 200, "body": json.dumps({"ok": True})}

    records = event.get("Records") or []
    if not records:
        return {"statusCode": 400, "body": json.dumps({"error": "No Records and no s3_bucket/s3_key"})}

    batch_failures: List[Dict[str, str]] = []

    for record in records:
        mid = record.get("messageId")
        try:
            if record.get("eventSource") == "aws:s3":
                for bucket, key in _parse_s3_from_record(record):
                    _ingest_s3_object(rds_client, bucket, key)
            elif "body" in record:
                body = json.loads(record["body"])
                inner_records = body.get("Records") or []
                if not inner_records:
                    logger.warning("SQS message with no S3 Records: %s", record.get("messageId"))
                    continue
                for ir in inner_records:
                    for bucket, key in _parse_s3_from_record(ir):
                        _ingest_s3_object(rds_client, bucket, key)
            else:
                logger.warning("Unrecognized record shape: keys=%s", list(record.keys()))
        except Exception:
            logger.exception("Ingest failed for record messageId=%s", mid)
            if mid:
                batch_failures.append({"itemIdentifier": mid})

    if batch_failures:
        return {"batchItemFailures": batch_failures}
    return {}
