"""
Daily Scanner — AWS Batch Job (two-phase Array Job)

Phase 1  scanner_worker  (Array Job, N children)
  Each child slices the global symbol list by its AWS_BATCH_JOB_ARRAY_INDEX,
  runs all strategies on that slice, and writes raw signals to
  daily_scan_signals (staging table).

Phase 2  scanner_aggregator  (Single Job, runs after all workers finish)
  Reads every signal for today from daily_scan_signals, runs global ranking
  across the full universe, writes final ranked picks to stock_picks, then
  cleans up daily_scan_signals for today.

Step Functions triggers Phase 1 as an Array Job, waits for all children,
then triggers Phase 2 as a normal single job.
"""

import os
import sys
import logging
import json
from datetime import date, datetime
from typing import List, Optional

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../..'))

from shared.analytics_core.scanner import DailyScanner
from shared.analytics_core.models import SignalResult
from shared.clients.rds_timescale_client import RDSTimescaleClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How many parallel worker children the Step Functions array job uses.
# Must match the ArrayProperties.Size value in the state machine.
DEFAULT_ARRAY_SIZE = 10


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def get_rds_connection_string() -> str:
    """Resolve the RDS DSN from Secrets Manager."""
    secret_arn = os.environ.get('RDS_SECRET_ARN')
    if not secret_arn:
        raise ValueError("RDS_SECRET_ARN environment variable not set")

    client = boto3.client('secretsmanager', region_name=os.environ.get('AWS_REGION', 'ca-west-1'))
    secret = json.loads(client.get_secret_value(SecretId=secret_arn)['SecretString'])

    host = secret['host']
    port = secret.get('port', 5432)
    db = secret.get('database', secret.get('dbname', 'postgres'))
    user = secret['username']
    pwd = secret['password']
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


# ---------------------------------------------------------------------------
# Phase 1 — scanner_worker
# ---------------------------------------------------------------------------

def _load_symbols_from_s3(scan_date: date, array_index: int) -> List[str]:
    """
    Download the pre-computed symbol chunk from S3.

    The partitioner Lambda writes files to:
      s3://{CHUNKS_BUCKET}/scanner-chunks/{scan_date}/chunk_{i}.json

    Each file contains a JSON object with a 'symbols' key.
    """
    bucket = os.environ.get('S3_BUCKET_NAME', 'dev-condvest-datalake')
    key    = f"scanner-chunks/{scan_date.isoformat()}/chunk_{array_index}.json"

    logger.info(f"Downloading symbol chunk from s3://{bucket}/{key}")
    s3  = boto3.client('s3')
    obj = s3.get_object(Bucket=bucket, Key=key)
    payload = json.loads(obj['Body'].read())
    symbols = payload.get('symbols', [])
    logger.info(f"Loaded {len(symbols)} symbols from chunk file")
    return symbols


def run_worker(
    scan_date: date,
    array_index: int,
    array_size: int,
    strategy_names: Optional[List[str]],
) -> int:
    """
    Scan one symbol slice and write raw signals to daily_scan_signals.

    Symbol list comes from the S3 chunk file written by the partitioner Lambda —
    no full-universe RDS query here. Each container only touches its own ~500
    symbols when it queries raw_ohlcv.

    Returns:
        Number of signal rows written.
    """
    logger.info("=" * 70)
    logger.info(f"SCANNER WORKER  index={array_index}/{array_size}  date={scan_date}")
    logger.info("=" * 70)

    rds_conn_str = get_rds_connection_string()
    rds_client   = RDSTimescaleClient(secret_arn=os.environ.get('RDS_SECRET_ARN'))

    # ----- symbol slice comes from S3, not from a full RDS query -----
    symbol_slice = _load_symbols_from_s3(scan_date, array_index)

    logger.info(f"Assigned {len(symbol_slice)} symbols (chunk {array_index} of {array_size})")

    if not symbol_slice:
        logger.warning("Empty symbol slice — nothing to do.")
        return 0

    # ----- scan -----
    scanner = DailyScanner(rds_connection_string=rds_conn_str)
    strategy_metadata = scanner.get_strategy_metadata(include_strategy_names=['vegas_channel_short_term'])
    available_names   = [s['strategy_name'] for s in strategy_metadata]

    if strategy_names:
        unknown = [n for n in strategy_names if n not in available_names]
        if unknown:
            raise ValueError(f"Unknown strategies: {unknown}. Available: {available_names}")
        include = strategy_names
    else:
        include = None

    signals: List[SignalResult] = scanner.run(
        symbols=symbol_slice,
        strategy_metadata=strategy_metadata,
        scan_date=scan_date,
        include_strategy_names=include,
    )
    logger.info(f"Generated {len(signals)} signals from {len(symbol_slice)} symbols")

    if not signals:
        return 0

    # ----- write raw signals to staging table -----
    rows = [
        (
            scan_date.isoformat(),
            array_index,
            s.symbol,
            (s.metadata or {}).get('strategy_name', 'unknown'),
            s.signal,
            float(s.price),
            float(s.confidence) if s.confidence is not None else None,
            json.dumps(s.metadata or {}),
        )
        for s in signals
    ]

    conn = rds_client.connection
    old_autocommit  = conn.autocommit
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO daily_scan_signals
                    (scan_date, worker_idx, symbol, strategy_name,
                     signal, price, confidence, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (scan_date, symbol, strategy_name)
                DO UPDATE SET
                    signal      = EXCLUDED.signal,
                    price       = EXCLUDED.price,
                    confidence  = EXCLUDED.confidence,
                    metadata    = EXCLUDED.metadata,
                    worker_idx  = EXCLUDED.worker_idx
                """,
                rows,
            )
        conn.commit()
        logger.info(f"Wrote {len(rows)} signal rows to daily_scan_signals")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = old_autocommit
        rds_client.close()

    return len(rows)


# ---------------------------------------------------------------------------
# Phase 2 — scanner_aggregator
# ---------------------------------------------------------------------------

def run_aggregator(
    scan_date: date,
    strategy_names: Optional[List[str]],
) -> int:
    """
    Read all signals for scan_date from daily_scan_signals, rank globally,
    write final picks to stock_picks, then clean up the staging table.

    Returns:
        Number of top-pick rows written.
    """
    logger.info("=" * 70)
    logger.info(f"SCANNER AGGREGATOR  date={scan_date}")
    logger.info("=" * 70)

    rds_conn_str = get_rds_connection_string()
    rds_client   = RDSTimescaleClient(secret_arn=os.environ.get('RDS_SECRET_ARN'))

    # ----- read raw signals -----
    filter_clause = ""
    params: tuple = (scan_date.isoformat(),)
    if strategy_names:
        placeholders  = ", ".join(["%s"] * len(strategy_names))
        filter_clause = f"AND strategy_name IN ({placeholders})"
        params        = (scan_date.isoformat(), *strategy_names)

    rows = rds_client.execute_query(
        f"""
        SELECT symbol, scan_date::text AS date, strategy_name,
               signal, price, confidence, metadata
        FROM   daily_scan_signals
        WHERE  scan_date = %s {filter_clause}
        """,
        params,
    )

    if not rows:
        logger.warning(f"No signals found in daily_scan_signals for {scan_date}. "
                       "Did all workers complete?")
        return 0

    logger.info(f"Read {len(rows)} raw signals from staging table")

    # Reconstruct SignalResult objects so scanner.rank() can process them
    signals: List[SignalResult] = [
        SignalResult(
            symbol       = r['symbol'],
            date         = r['date'],
            signal       = r['signal'],
            price        = float(r['price']),
            setup_valid  = True,
            trigger_met  = True,
            confidence   = float(r['confidence']) if r['confidence'] is not None else None,
            metadata     = r['metadata'] if isinstance(r['metadata'], dict)
                           else json.loads(r['metadata'] or '{}'),
        )
        for r in rows
    ]

    # ----- global rank -----
    scanner = DailyScanner(rds_connection_string=rds_conn_str)
    ranked  = scanner.rank(signals, by_pick_type=True, top_k=10, unique_symbol=True)

    total = (
        sum(len(picks) for picks in ranked.values())
        if isinstance(ranked, dict) else len(ranked)
    )
    logger.info(f"Ranked {total} top picks across {len(ranked)} strategy group(s)")

    # ----- write stock_picks -----
    picks_written = scanner.write(ranked, rds_client, scan_date)
    logger.info(f"Wrote {picks_written} rows to stock_picks")

    # ----- clean up staging rows for today -----
    try:
        conn = rds_client.connection
        old_autocommit  = conn.autocommit
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM daily_scan_signals WHERE scan_date = %s",
                (scan_date.isoformat(),),
            )
            deleted = cur.rowcount
        conn.commit()
        conn.autocommit = old_autocommit
        logger.info(f"Cleaned up {deleted} staging rows from daily_scan_signals")
    except Exception as e:
        logger.warning(f"Staging cleanup failed (non-fatal): {e}")

    rds_client.close()

    logger.info("=" * 70)
    logger.info("AGGREGATOR COMPLETE")
    logger.info("=" * 70)
    return picks_written


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """
    AWS Batch entry point — routes to worker or aggregator based on JOB_TYPE.

    Environment variables
    ─────────────────────
    JOB_TYPE          scanner_worker | scanner_aggregator  (default: scanner_worker)
    SCAN_DATE         YYYY-MM-DD   (default: today)
    STRATEGY_NAME     comma-separated list or empty for ALL
    ARRAY_SIZE        number of parallel worker children   (default: 10)

    Injected by AWS Batch for array jobs (read-only):
    AWS_BATCH_JOB_ARRAY_INDEX   0-based child index
    AWS_BATCH_JOB_ARRAY_SIZE    total children
    """
    logger.info("=" * 70)
    logger.info("AWS BATCH SCANNER STARTUP")
    logger.info("=" * 70)

    job_type   = os.environ.get('JOB_TYPE', 'scanner_worker')
    aws_region = os.environ.get('AWS_REGION', 'ca-west-1')

    # SCAN_DATE injected by Step Functions as the execution-start date (YYYY-MM-DD)
    scan_date_str = os.environ.get('SCAN_DATE', '').strip()
    scan_date: date = date.today()
    if scan_date_str:
        try:
            scan_date = datetime.strptime(scan_date_str, '%Y-%m-%d').date()
        except ValueError:
            logger.warning(f"Invalid SCAN_DATE '{scan_date_str}' — using today.")

    # Optional strategy filter (comma-separated or empty → ALL)
    strategy_names_env = os.environ.get('STRATEGY_NAME', '').strip()
    strategy_names: Optional[List[str]] = (
        [s.strip() for s in strategy_names_env.split(',') if s.strip()]
        if strategy_names_env else None
    )

    logger.info(f"JOB_TYPE:      {job_type}")
    logger.info(f"AWS_REGION:    {aws_region}")
    logger.info(f"SCAN_DATE:     {scan_date}")
    logger.info(f"STRATEGIES:    {strategy_names or 'ALL'}")

    try:
        if job_type == 'scanner_aggregator':
            result = run_aggregator(scan_date, strategy_names)
            logger.info(f"Aggregator done — {result} top picks written.")

        else:  # scanner_worker (default)
            # AWS Batch injects these automatically for array jobs
            array_index = int(os.environ.get('AWS_BATCH_JOB_ARRAY_INDEX', '0'))
            array_size  = int(os.environ.get('AWS_BATCH_JOB_ARRAY_SIZE',
                                              os.environ.get('ARRAY_SIZE', str(DEFAULT_ARRAY_SIZE))))
            logger.info(f"ARRAY_INDEX:   {array_index}")
            logger.info(f"ARRAY_SIZE:    {array_size}")

            result = run_worker(scan_date, array_index, array_size, strategy_names)
            logger.info(f"Worker done — {result} signal rows written.")

        sys.exit(0)

    except Exception as e:
        logger.error(f"FATAL: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
