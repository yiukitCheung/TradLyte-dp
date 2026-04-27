"""
VPC Lambda: S3 bronze metadata manifest -> RDS `symbol_metadata`.

Trigger: SQS fed by S3 object-created notifications (recommended).

Contract written by `daily_meta_fetcher`:
- Part files (optional) under:
  `${S3_META_PREFIX}/run_date=YYYY-MM-DD/run_id=<job_id>/part-*.json`
  Each part file contains a JSON array of metadata objects compatible with
  `RDSTimescaleClient.insert_metadata_batch`.

- A single manifest file:
  `${S3_META_PREFIX}/run_date=YYYY-MM-DD/run_id=<job_id>/_manifest.json`
  Manifest JSON includes:
  - `part_keys`: list of S3 object keys for the part JSON arrays
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from typing import Any, Dict, List, Tuple

import boto3

from shared.clients.rds_timescale_client import RDSTimescaleClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_DEFAULT_META_PREFIX = "bronze/raw_meta"


def _parse_s3_from_record(record: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Extract (bucket, key) from an S3 event record structure.
    """
    if "s3" not in record:
        return []

    s3_obj = record.get("s3", {}).get("object", {})
    s3_bucket = record.get("s3", {}).get("bucket", {})

    bucket = s3_bucket.get("name")
    key = s3_obj.get("key")
    if not bucket or not key:
        return []

    return [(bucket, urllib.parse.unquote_plus(key))]


def _is_manifest_key(key: str, meta_prefix: str) -> bool:
    if meta_prefix and meta_prefix not in key:
        return False
    return key.endswith("_manifest.json")


def _ingest_manifest(
    rds_client: RDSTimescaleClient,
    bucket: str,
    key: str,
    chunk_size: int = 200,
) -> Dict[str, Any]:
    """
    Download manifest + part files, then upsert into `symbol_metadata`.
    """
    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket=bucket, Key=key)
    manifest_body = obj["Body"].read()
    manifest = json.loads(manifest_body)

    part_keys: List[str] = manifest.get("part_keys") or []
    if not part_keys:
        # Fallback: allow a manifest that directly stores metadata objects as `metadata`
        direct = manifest.get("metadata")
        if isinstance(direct, list):
            part_keys = [key]
            manifest["direct_metadata"] = True
        else:
            raise ValueError(f"Manifest missing `part_keys` and no direct `metadata`: {key}")

    total_inserted = 0
    buffer: List[Dict[str, Any]] = []

    # If manifest supports direct metadata (fallback), ingest it as a single "part".
    if manifest.get("direct_metadata") is True:
        direct_metadata = manifest.get("metadata", [])
        for meta in direct_metadata:
            buffer.append(meta)
            if len(buffer) >= chunk_size:
                inserted = rds_client.insert_metadata_batch(buffer)
                total_inserted += inserted
                buffer = []
        if buffer:
            inserted = rds_client.insert_metadata_batch(buffer)
            total_inserted += inserted
        return {
            "manifest_key": key,
            "total_inserted": total_inserted,
            "part_count": 1,
        }

    for part_key in part_keys:
        part_obj = s3.get_object(Bucket=bucket, Key=part_key)
        part_body = part_obj["Body"].read()
        part_metadata = json.loads(part_body)
        if not isinstance(part_metadata, list):
            raise ValueError(f"Part file must contain JSON list: {part_key}")

        for meta in part_metadata:
            buffer.append(meta)
            if len(buffer) >= chunk_size:
                inserted = rds_client.insert_metadata_batch(buffer)
                total_inserted += inserted
                buffer = []

    if buffer:
        inserted = rds_client.insert_metadata_batch(buffer)
        total_inserted += inserted

    return {
        "manifest_key": key,
        "total_inserted": total_inserted,
        "part_count": len(part_keys),
    }


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Supports:
    - SQS event (standard S3-notification via SQS): event['Records'][...]['body'] contains S3 event JSON.
    - Manual replay: {"s3_bucket": "...", "s3_key": "..."}
    """
    rds_client = RDSTimescaleClient.from_lambda_environment()

    meta_prefix = os.environ.get("S3_META_PREFIX", _DEFAULT_META_PREFIX)
    chunk_size = int(os.environ.get("META_RDS_CHUNK_SIZE", "200"))

    # Manual replay
    if event.get("s3_bucket") and event.get("s3_key"):
        res = _ingest_manifest(rds_client, event["s3_bucket"], event["s3_key"], chunk_size=chunk_size)
        return {"statusCode": 200, "body": json.dumps(res)}

    records = event.get("Records") or []
    if not records:
        return {"statusCode": 400, "body": json.dumps({"error": "No Records"})}

    batch_failures: List[Dict[str, str]] = []

    for record in records:
        message_id = record.get("messageId")
        try:
            s3_items: List[Tuple[str, str]] = []

            if record.get("eventSource") == "aws:s3":
                # Rare direct-S3 shape
                s3_items.extend(_parse_s3_from_record(record))
            elif "body" in record:
                # Standard S3-notification delivered via SQS
                body = json.loads(record["body"])
                inner_records = body.get("Records") or []
                for ir in inner_records:
                    s3_items.extend(_parse_s3_from_record(ir))

            # Only ingest when manifest is written.
            for bucket, key in s3_items:
                if _is_manifest_key(key, meta_prefix=meta_prefix):
                    _ingest_manifest(rds_client, bucket, key, chunk_size=chunk_size)
                else:
                    logger.info("Skip non-manifest key: %s", key)

        except Exception:
            logger.exception("Ingest failed for messageId=%s", message_id)
            if message_id:
                batch_failures.append({"itemIdentifier": message_id})

    if batch_failures:
        return {"batchItemFailures": batch_failures}
    return {}

