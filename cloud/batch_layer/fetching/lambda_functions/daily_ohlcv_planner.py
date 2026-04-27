"""
VPC Lambda: reads RDS for symbols / missing dates, then async-invokes daily_ohlcv_fetcher (no VPC).

Lives under fetching/lambda_functions/ (orchestrates fetch; built via infrastructure/fetching/deploy_lambda.sh).

Wire Step Functions, EventBridge, or manual invoke. For production gap fill, pass backfill_missing
and optional skip_market_check (forwarded to the fetcher payload).

Default mode is fan-out: one async (InvocationType=Event) fetcher invoke per missing date. The
planner returns in seconds and never waits. RDS hydration is handled out-of-band by
S3 → SQS → daily-ohlcv-ingest-handler, so Step Functions only needs to Wait long enough for the
async fetchers + ingest path to land before the scanner runs.

The legacy synchronous_ohlcv_fetch flag is kept for ad-hoc manual invocations only (e.g. backfilling
a single explicit date from a notebook). Step Functions must NOT set it: the planner Lambda
shares its 900s budget with the fetcher, so a synchronous wait inevitably hits Sandbox.Timedout
on real-volume runs.
"""

import json
import logging
import os
import time
from typing import Any, Dict

import boto3
from botocore.config import Config

from shared.clients.rds_timescale_client import RDSTimescaleClient
from shared.utils.pipeline import get_missing_dates, get_new_symbols

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _invoke_fetcher(
    lambda_client,
    fetcher_name: str,
    payload_bytes: bytes,
    synchronous: bool,
    context=None,
) -> Dict[str, Any]:
    if synchronous:
        logger.info(
            "Invoking fetcher synchronously: %s payload_bytes=%s",
            fetcher_name,
            len(payload_bytes),
        )
        if context is not None and getattr(context, "get_remaining_time_in_millis", None):
            logger.info(
                "Lambda time remaining before fetcher invoke: %sms",
                context.get_remaining_time_in_millis(),
            )
        t0 = time.perf_counter()
        resp = lambda_client.invoke(
            FunctionName=fetcher_name,
            InvocationType="RequestResponse",
            Payload=payload_bytes,
        )
        raw = resp["Payload"].read()
        fetcher_out = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        body_raw = fetcher_out.get("body", "{}")
        inner_body = json.loads(body_raw) if isinstance(body_raw, str) else body_raw
        sc = int(fetcher_out.get("statusCode", 500))
        elapsed = time.perf_counter() - t0
        logger.info("Fetcher invoke finished in %.2fs (sync)", elapsed)
        if sc >= 400:
            return {
                "ok": False,
                "statusCode": sc,
                "fetcher_body": inner_body,
            }
        return {
            "ok": True,
            "statusCode": 200,
            "dates_processed": inner_body.get("dates_processed", []),
            "num_dates": inner_body.get(
                "num_dates", len(inner_body.get("dates_processed", []))
            ),
            "fetcher_body": inner_body,
        }

    logger.info(
        "Invoking fetcher asynchronously: %s payload_bytes=%s",
        fetcher_name,
        len(payload_bytes),
    )
    lambda_client.invoke(
        FunctionName=fetcher_name,
        InvocationType="Event",
        Payload=payload_bytes,
    )
    return {"ok": True, "statusCode": 202, "async": True}


def _fan_out_per_date(
    lambda_client,
    fetcher_name: str,
    base_payload: Dict[str, Any],
    dates: list,
) -> Dict[str, Any]:
    """Async-invoke the fetcher once per date (single-day payload each).

    AWS-side concurrency throttling on the fetcher (reserved concurrency)
    handles parallelism. Async invokes auto-queue up to 6h, so it's safe to
    fire all dates back-to-back here even when they exceed the cap.
    """
    dispatched: list = []
    failed: list = []
    for d in dates:
        single_payload = {k: v for k, v in base_payload.items() if k != "dates"}
        single_payload["date"] = d
        payload_bytes = json.dumps(single_payload).encode("utf-8")
        try:
            lambda_client.invoke(
                FunctionName=fetcher_name,
                InvocationType="Event",
                Payload=payload_bytes,
            )
            dispatched.append(d)
            logger.info("Dispatched fetcher for date=%s (async)", d)
        except Exception as e:
            logger.error("Failed to dispatch fetcher for date=%s: %s", d, e)
            failed.append({"date": d, "error": str(e)})
    return {"dispatched": dispatched, "failed": failed}


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    req_id = getattr(context, "aws_request_id", "") if context else ""
    logger.info("daily_ohlcv_planner start request_id=%s", req_id)

    inner: Dict[str, Any] = dict(event) if isinstance(event, dict) else {}
    synchronous = bool(inner.pop("synchronous_ohlcv_fetch", False))
    logger.info(
        "synchronous_ohlcv_fetch=%s historical_backfill=%s",
        synchronous,
        bool(inner.get("historical_backfill")),
    )

    t_rds = time.perf_counter()
    logger.info("Creating RDSTimescaleClient (RDS work follows)...")
    try:
        rds_client = RDSTimescaleClient.from_lambda_environment()
    except ValueError as e:
        logger.error("%s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
    logger.info("RDSTimescaleClient ready in %.2fs", time.perf_counter() - t_rds)

    fetcher_name = os.environ.get("OHLCV_FETCHER_FUNCTION_NAME", "")
    if not fetcher_name:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "OHLCV_FETCHER_FUNCTION_NAME env var is required"}),
        }

    lambda_client = boto3.client(
        "lambda",
        config=Config(
            connect_timeout=10,
            read_timeout=900,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )

    if inner.get("historical_backfill"):
        logger.info("Mode: historical_backfill")
        if inner.get("new_symbols_only", True) and not inner.get("symbols"):
            t0 = time.perf_counter()
            logger.info("Calling get_new_symbols...")
            symbols = get_new_symbols(rds_client)
            logger.info("get_new_symbols done in %.2fs count=%s", time.perf_counter() - t0, len(symbols) if symbols else 0)
            if not symbols:
                return {
                    "statusCode": 200,
                    "body": json.dumps({"message": "No new symbols to backfill", "num_dates": 0}),
                }
            inner["symbols"] = symbols
        elif not inner.get("symbols"):
            t0 = time.perf_counter()
            logger.info("Calling get_active_symbols (historical)...")
            inner["symbols"] = rds_client.get_active_symbols()
            logger.info(
                "get_active_symbols done in %.2fs count=%s",
                time.perf_counter() - t0,
                len(inner["symbols"]) if inner["symbols"] else 0,
            )
            if not inner["symbols"]:
                return {
                    "statusCode": 200,
                    "body": json.dumps({"message": "No active symbols for historical backfill", "num_dates": 0}),
                }
    else:
        logger.info("Mode: standard (gap fill / explicit dates)")
        if inner.get("symbols") is None:
            t0 = time.perf_counter()
            logger.info("Calling get_active_symbols...")
            syms = rds_client.get_active_symbols()
            logger.info(
                "get_active_symbols done in %.2fs count=%s",
                time.perf_counter() - t0,
                len(syms) if syms else 0,
            )
            if syms:
                inner["symbols"] = syms

        has_explicit_day = bool(inner.get("date") or inner.get("dates"))
        backfill_missing = inner.get("backfill_missing", True)
        max_backfill_days = int(inner.get("max_backfill_days", 30))

        if not has_explicit_day and backfill_missing:
            t0 = time.perf_counter()
            logger.info(
                "Calling get_missing_dates max_backfill_days=%s...",
                max_backfill_days,
            )
            missing = get_missing_dates(rds_client, max_backfill_days)
            logger.info(
                "get_missing_dates done in %.2fs missing_days=%s",
                time.perf_counter() - t0,
                len(missing) if missing else 0,
            )
            if not missing:
                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {"message": "No missing dates — database is up to date", "num_dates": 0}
                    ),
                }
            inner["dates"] = [d.isoformat() for d in missing]

    if isinstance(inner.get("dates"), list):
        n_dates = len(inner["dates"])
    elif inner.get("date"):
        n_dates = 1
    else:
        n_dates = 0
    n_symbols = len(inner["symbols"]) if inner.get("symbols") else 0
    logger.info("Prepared fetcher payload: dates=%s symbols=%s", n_dates, n_symbols)

    # Always fan-out for non-historical runs (>=1 date), even single-day. This avoids
    # the planner ever waiting on the fetcher synchronously: planner and fetcher both
    # have a 900s budget, so a synchronous wait deterministically hits Sandbox.Timedout
    # on real-volume runs (~5k symbols/day). The async fetcher writes parquet to S3,
    # which triggers daily-ohlcv-ingest-handler via S3 -> SQS to load RDS out-of-band.
    fan_out = (
        not inner.get("historical_backfill")
        and not synchronous
        and isinstance(inner.get("dates"), list)
        and len(inner["dates"]) >= 1
    )

    if fan_out:
        logger.info(
            "Fan-out mode: %s single-day async invokes (relying on fetcher reserved concurrency)",
            len(inner["dates"]),
        )
        outcome = _fan_out_per_date(
            lambda_client, fetcher_name, inner, inner["dates"]
        )
        return {
            "statusCode": 202,
            "body": json.dumps(
                {
                    "message": "OHLCV fetcher fan-out dispatched (one async invoke per date)",
                    "fetcher": fetcher_name,
                    "num_dates_dispatched": len(outcome["dispatched"]),
                    "dates_dispatched": outcome["dispatched"],
                    "dates_failed": outcome["failed"],
                    "fan_out": True,
                    # num_dates intentionally 0 so Step Functions MaybeIngestOHLCV
                    # branch is skipped: ingestion runs out-of-band via S3 -> SQS ->
                    # daily-ohlcv-ingest-handler. SFN should Wait before the scanner.
                    "num_dates": 0,
                    "dates_processed": [],
                }
            ),
        }

    payload_bytes = json.dumps(inner).encode("utf-8")
    result = _invoke_fetcher(
        lambda_client, fetcher_name, payload_bytes, synchronous, context=context
    )

    if not result.get("ok", True) and synchronous:
        logger.error("Fetcher failed: %s", result.get("fetcher_body"))
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "error": "OHLCV fetcher failed",
                    "fetcher": result.get("fetcher_body"),
                }
            ),
        }

    if synchronous and result.get("statusCode") == 200:
        logger.info(
            "Fetcher finished synchronously: num_dates=%s",
            result.get("num_dates"),
        )
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "OHLCV fetcher completed (synchronous)",
                    "fetcher": fetcher_name,
                    "dates_processed": result.get("dates_processed", []),
                    "num_dates": result.get("num_dates", 0),
                }
            ),
        }

    logger.info("Invoked fetcher %s async (payload bytes=%s)", fetcher_name, len(payload_bytes))

    return {
        "statusCode": 202,
        "body": json.dumps(
            {
                "message": "OHLCV fetcher invoked asynchronously",
                "fetcher": fetcher_name,
                "num_dates": 0,
            }
        ),
    }
