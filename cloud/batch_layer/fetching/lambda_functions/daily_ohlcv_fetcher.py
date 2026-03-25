"""
AWS Lambda: OHLCV fetch from Polygon → S3 bronze (no RDS).

RDS watermarking and inserts run in VPC via daily_ohlcv_ingest_handler (S3 trigger).

Typical wiring:
- EventBridge → daily_ohlcv_planner (VPC) → async invoke this function with `dates` / `symbols`
- Or invoke directly for single-day / explicit payloads (no RDS on this function).
"""

import json
import boto3
import logging
import asyncio
from datetime import datetime, date, timedelta
from typing import Dict, Any, List
import os
import pyarrow as pa
import pyarrow.parquet as pq
from io import BytesIO

from shared.clients.polygon_client import PolygonClient
from shared.models.data_models import OHLCVData

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ.get("S3_DATALAKE_BUCKET", "dev-condvest-datalake")
S3_BRONZE_PREFIX = os.environ.get("S3_BRONZE_PREFIX", "bronze/raw_ohlcv")


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Event fields:
    - symbols: optional list; if omitted, uses Polygon active symbol list (bootstrap).
    - date: optional ISO date (single day).
    - dates: optional list of ISO dates (from daily_ohlcv_planner for backfill).
    - backfill_missing: if true, you must supply `dates` (planner computes from RDS).
    - skip_market_check: bool
    - max_backfill_days: informational for logging when using planner-driven dates
    - historical_backfill, years_back, new_symbols_only: range mode; `symbols` required
      (planner fills this from RDS when new_symbols_only is true).
    """
    job_id = f"daily-ohlcv-fetch-{int(datetime.utcnow().timestamp())}"
    start_time = datetime.utcnow()

    try:
        skip_market_check = event.get("skip_market_check", False)

        secrets_client = boto3.client("secretsmanager")
        polygon_secret = secrets_client.get_secret_value(
            SecretId=os.environ["POLYGON_API_KEY_SECRET_ARN"]
        )
        polygon_api_key = json.loads(polygon_secret["SecretString"])["POLYGON_API_KEY"]
        polygon_client = PolygonClient(api_key=polygon_api_key)

        if not skip_market_check:
            market_status = polygon_client.get_market_status()
            if market_status["market"] == "closed":
                logger.info("Skipping execution - market is closed")
                return {
                    "statusCode": 200,
                    "body": json.dumps({"message": "Skipping execution - market is closed"}),
                }
        else:
            logger.info("TESTING MODE: Skipping market status check")

        symbols = event.get("symbols", None)
        target_date = event.get("date", None)
        # Default False: smart RDS backfill is handled by daily_ohlcv_planner (VPC).
        backfill_missing = event.get("backfill_missing", False)
        max_backfill_days = int(event.get("max_backfill_days", 30))

        historical_backfill = event.get("historical_backfill", False)
        years_back = int(event.get("years_back", 5))
        new_symbols_only = event.get("new_symbols_only", True)

        if historical_backfill:
            return handle_historical_backfill(
                polygon_client=polygon_client,
                symbols=symbols,
                years_back=years_back,
                new_symbols_only=new_symbols_only,
                job_id=job_id,
                start_time=start_time,
            )

        execution_mode = "UNKNOWN"
        if target_date:
            execution_mode = "SPECIFIC_DATE"
        elif event.get("dates"):
            execution_mode = "EXPLICIT_DATES"
        elif backfill_missing and max_backfill_days > 30:
            execution_mode = "LARGE_BACKFILL"
        elif backfill_missing:
            execution_mode = "PRODUCTION"
        else:
            execution_mode = "SINGLE_DAY"

        logger.info("EXECUTION MODE: %s", execution_mode)

        dates_to_fetch: List[date] = []
        if event.get("dates"):
            raw_dates = event["dates"]
            if not isinstance(raw_dates, list) or not raw_dates:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": "dates must be a non-empty list of ISO dates"}),
                }
            dates_to_fetch = [datetime.fromisoformat(str(d)).date() for d in raw_dates]
            logger.info("Explicit dates mode: %s day(s)", len(dates_to_fetch))
        elif target_date:
            dates_to_fetch = [datetime.fromisoformat(str(target_date)).date()]
            logger.info("Specific date mode: %s", dates_to_fetch[0])
        elif backfill_missing:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "error": "backfill_missing requires `dates` on this Lambda; use daily_ohlcv_planner (VPC) to compute them from RDS",
                        "job_id": job_id,
                    }
                ),
            }
        else:
            dates_to_fetch = [polygon_client.get_previous_trading_day()]
            logger.info("Single day mode: %s", dates_to_fetch[0])

        logger.info("Starting OHLCV fetch for %s date(s), job_id: %s", len(dates_to_fetch), job_id)

        if symbols is None:
            symbols = polygon_client.get_active_symbols()
            logger.info("Using %s symbols from Polygon (no symbols in event)", len(symbols))

        batch_size = int(os.environ.get("BATCH_SIZE", "50"))
        total_records = 0

        for date_idx, fetch_date in enumerate(dates_to_fetch, 1):
            logger.info(
                "Processing date %s/%s: %s",
                date_idx,
                len(dates_to_fetch),
                fetch_date.isoformat(),
            )
            date_records = 0

            for i in range(0, len(symbols), batch_size):
                batch_symbols = symbols[i : i + batch_size]
                logger.info("  Batch %s: %s symbols (async)", i // batch_size + 1, len(batch_symbols))

                try:
                    ohlcv_data = asyncio.run(
                        polygon_client.fetch_batch_ohlcv_data_async(
                            batch_symbols,
                            fetch_date,
                            max_concurrent=10,
                        )
                    )

                    if ohlcv_data:
                        s3_records = write_to_s3_bronze(ohlcv_data, fetch_date)
                        logger.info("  Wrote %s records to S3 bronze", s3_records)
                        date_records += s3_records
                        total_records += s3_records
                    else:
                        logger.warning("  No data returned for batch: %s...", batch_symbols[:5])
                except Exception as e:
                    logger.error("  Error processing batch: %s", e)
                    continue

            logger.info("Completed %s: %s records to S3", fetch_date.isoformat(), date_records)

        end_time = datetime.utcnow()
        execution_time = (end_time - start_time).total_seconds()

        logger.info("JOB COMPLETE job_id=%s records_s3=%s time=%.1fs", job_id, total_records, execution_time)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"OHLCV {execution_mode} fetch to S3 completed",
                    "execution_mode": execution_mode,
                    "job_id": job_id,
                    "dates_processed": [d.isoformat() for d in dates_to_fetch],
                    "num_dates": len(dates_to_fetch),
                    "symbols_processed": len(symbols),
                    "records_written_s3": total_records,
                    "records_inserted": total_records,
                    "execution_time_seconds": execution_time,
                }
            ),
        }

    except Exception as e:
        logger.error("Fatal error in daily OHLCV fetcher: %s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Daily OHLCV fetch failed", "message": str(e), "job_id": job_id}),
        }


def handle_historical_backfill(
    polygon_client: PolygonClient,
    symbols: List[str] = None,
    years_back: int = 5,
    new_symbols_only: bool = True,
    job_id: str = None,
    start_time: datetime = None,
) -> Dict[str, Any]:
    """Multi-year OHLCV from Polygon → S3 per day/symbol (ingest Lambda loads RDS)."""
    start_time = start_time or datetime.utcnow()
    job_id = job_id or f"historical-backfill-{int(start_time.timestamp())}"

    logger.info("HISTORICAL BACKFILL MODE: %s years", years_back)

    try:
        if new_symbols_only and not symbols:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "error": "historical_backfill with new_symbols_only requires `symbols` "
                        "(supply from daily_ohlcv_planner)",
                        "job_id": job_id,
                    }
                ),
            }
        if not symbols:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "error": "historical_backfill requires `symbols`",
                        "job_id": job_id,
                    }
                ),
            }

        symbols_to_backfill = symbols
        to_date = date.today()
        from_date = to_date - timedelta(days=365 * years_back)
        logger.info("Date range: %s to %s", from_date, to_date)

        batch_size = 50
        total_records = 0
        symbols_processed = 0

        for i in range(0, len(symbols_to_backfill), batch_size):
            batch_symbols = symbols_to_backfill[i : i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(symbols_to_backfill) + batch_size - 1) // batch_size
            logger.info("Batch %s/%s: %s symbols", batch_num, total_batches, len(batch_symbols))

            symbol_data = asyncio.run(
                polygon_client.fetch_batch_historical_ohlcv_async(
                    symbols=batch_symbols,
                    from_date=from_date,
                    to_date=to_date,
                    max_concurrent=5,
                )
            )

            for symbol, ohlcv_list in symbol_data.items():
                if not ohlcv_list:
                    logger.warning("No data returned for %s", symbol)
                    continue
                write_historical_to_s3(ohlcv_list, symbol)
                total_records += len(ohlcv_list)
                symbols_processed += 1
                if symbols_processed % 10 == 0:
                    logger.info("Processed %s/%s symbols, %s records", symbols_processed, len(symbols_to_backfill), total_records)

        end_time = datetime.utcnow()
        execution_time = (end_time - start_time).total_seconds()
        logger.info("HISTORICAL BACKFILL COMPLETE symbols=%s records=%s", symbols_processed, total_records)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Historical backfill wrote {symbols_processed} symbols to S3",
                    "job_id": job_id,
                    "symbols_processed": symbols_processed,
                    "total_records": total_records,
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                    "execution_time_seconds": execution_time,
                }
            ),
        }

    except Exception as e:
        logger.error("Historical backfill failed: %s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Historical backfill failed", "message": str(e), "job_id": job_id}),
        }


def write_to_s3_bronze(ohlcv_data: List[OHLCVData], fetch_date: date) -> int:
    """Partition: bronze/raw_ohlcv/symbol=TICKER/date=YYYY-MM-DD.parquet"""
    if not ohlcv_data:
        return 0

    s3_client = boto3.client("s3")
    symbol_groups: Dict[str, List[OHLCVData]] = {}
    for ohlcv in ohlcv_data:
        symbol_groups.setdefault(ohlcv.symbol, []).append(ohlcv)

    records_written = 0
    for symbol, symbol_data in symbol_groups.items():
        s3_key = f"{S3_BRONZE_PREFIX}/symbol={symbol}/date={fetch_date.isoformat()}.parquet"
        table = pa.table(
            {
                "symbol": [o.symbol for o in symbol_data],
                "open": [float(o.open) for o in symbol_data],
                "high": [float(o.high) for o in symbol_data],
                "low": [float(o.low) for o in symbol_data],
                "close": [float(o.close) for o in symbol_data],
                "volume": [int(o.volume) for o in symbol_data],
                "timestamp": [o.timestamp for o in symbol_data],
                "interval": [o.interval for o in symbol_data],
            }
        )
        parquet_buffer = BytesIO()
        pq.write_table(table, parquet_buffer, compression="snappy")
        parquet_buffer.seek(0)
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=parquet_buffer.getvalue(),
            ContentType="application/x-parquet",
        )
        records_written += len(symbol_data)

    return records_written


def write_historical_to_s3(ohlcv_list: List[OHLCVData], symbol: str) -> int:
    """One parquet per calendar day under the symbol prefix."""
    if not ohlcv_list:
        return 0

    s3_client = boto3.client("s3")
    date_groups: Dict[date, List[OHLCVData]] = {}
    for ohlcv in ohlcv_list:
        d = ohlcv.timestamp.date()
        date_groups.setdefault(d, []).append(ohlcv)

    records_written = 0
    for ohlcv_date, day_data in date_groups.items():
        s3_key = f"{S3_BRONZE_PREFIX}/symbol={symbol}/date={ohlcv_date.isoformat()}.parquet"
        table = pa.table(
            {
                "symbol": [o.symbol for o in day_data],
                "open": [float(o.open) for o in day_data],
                "high": [float(o.high) for o in day_data],
                "low": [float(o.low) for o in day_data],
                "close": [float(o.close) for o in day_data],
                "volume": [int(o.volume) for o in day_data],
                "timestamp": [o.timestamp for o in day_data],
                "interval": [o.interval for o in day_data],
            }
        )
        parquet_buffer = BytesIO()
        pq.write_table(table, parquet_buffer, compression="snappy")
        parquet_buffer.seek(0)
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=parquet_buffer.getvalue(),
            ContentType="application/x-parquet",
        )
        records_written += len(day_data)

    return records_written
