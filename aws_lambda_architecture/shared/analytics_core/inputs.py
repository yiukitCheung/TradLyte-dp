"""
Data Input Utilities

Load OHLCV data from various sources (S3, RDS) into Polars DataFrames
"""

import polars as pl
import boto3
from typing import Optional
from datetime import date
import os


def load_ohlcv_from_s3(
    bucket: str,
    symbol: str,
    timeframe: str,
    start_date: date,
    end_date: date,
    s3_prefix_base: Optional[str] = "silver",
    aws_region: Optional[str] = None,
) -> pl.DataFrame:
    """
    Load resampled OHLCV data from S3 using a lazy scan with predicate pushdown.

    Path structure (partition: interval -> symbol -> year -> month):
    silver_{x}d/{symbol}/{year}/{month}/data_{x}d_{YYYYMM}.parquet

    Uses pl.scan_parquet() with s3://bucket/prefix/{symbol}/**/*.parquet so that:
    - Only that symbol's partition is scanned (path-level pruning).
    - Date and symbol filters are pushed down; Polars fetches only relevant row groups.

    Expects parquet columns: ts, symbol, open, high, low, close, volume.
    Returns data filtered to [start_date, end_date] with a "date" column for downstream.

    Args:
        bucket: S3 bucket name
        symbol: Stock symbol (e.g., 'AAPL', 'NVDA')
        timeframe: Resample period (e.g., '3d', '5d', '8d')
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        s3_prefix_base: Top-level S3 prefix (default "silver"; use "" for silver_{x}d at bucket root)
        aws_region: AWS region for S3 (default: boto3 default session or AWS_REGION env)

    Returns:
        Polars DataFrame with OHLCV data
    """
    x = timeframe.rstrip("d") if isinstance(timeframe, str) else str(timeframe)
    prefix = f"{s3_prefix_base}/silver_{x}d".strip("/") if s3_prefix_base else f"silver_{x}d"
    # Lazy scan: only this symbol's partition (/**/*.parquet matches year/month subdirs)
    s3_path = f"s3://{bucket}/{prefix}/{symbol}/**/*.parquet"

    session = boto3.Session()
    region = aws_region or session.region_name or os.environ.get("AWS_REGION", "us-east-1")
    creds = session.get_credentials()
    storage_options: dict = {"aws_region": region}
    if creds:
        frozen = creds.get_frozen_credentials()
        storage_options["aws_access_key_id"] = frozen.access_key
        storage_options["aws_secret_access_key"] = frozen.secret_key
        if frozen.token:
            storage_options["aws_session_token"] = frozen.token

    q = pl.scan_parquet(s3_path, storage_options=storage_options)
    names = q.collect_schema().names()
    if "symbol" in names:
        q = q.filter(pl.col("symbol") == symbol)
    date_col = next((c for c in ("ts", "date", "timestamp") if c in names), None)
    if date_col:
        col = pl.col(date_col)
        if date_col == "ts" or date_col == "timestamp":
            q = q.filter(
                col.dt.date() >= start_date,
                col.dt.date() <= end_date,
            )
        else:
            q = q.filter(col >= start_date, col <= end_date)

    df = q.collect()
    if df.is_empty():
        raise ValueError(
            f"No OHLCV data found in S3 for {symbol} {timeframe} from {start_date} to {end_date}. "
            f"Path pattern: {s3_path}. Ensure bucket={bucket}, prefix={prefix}, and parquet under {prefix}/{symbol}/YYYY/MM/."
        )
    if "date" not in df.columns and "ts" in df.columns:
        if df["ts"].dtype == pl.Datetime:
            df = df.with_columns(pl.col("ts").dt.date().alias("date"))
        else:
            df = df.with_columns(pl.col("ts").alias("date"))
    return df


def load_ohlcv_from_rds(
    symbol: str,
    connection_string: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    table_name: str = 'raw_ohlcv',
    days: Optional[int] = None,
) -> pl.DataFrame:
    """
    Load OHLCV data from RDS PostgreSQL.

    When days is set, uses the Fetch_Symbol_Range(symbol, days) procedure
    (last N days from today). Otherwise uses a direct query with start_date/end_date.

    Args:
        symbol: Stock symbol (e.g., 'AAPL')
        connection_string: PostgreSQL connection string
        start_date: Filter start date (optional; ignored if days is set)
        end_date: Filter end date (optional; ignored if days is set)
        table_name: Table name for direct query (default: 'raw_ohlcv')
        days: If set, fetch last N days via Fetch_Symbol_Range (overrides start/end date)

    Returns:
        Polars DataFrame with columns: open, high, low, close, volume, timestamp (and date if from procedure)
    """
    from sqlalchemy import create_engine

    try:
        engine = create_engine(connection_string)
    except Exception as e:
        raise ValueError(f"Error connecting to RDS: {str(e)}")

    try:
        if days is not None:
            query = "SELECT * FROM Fetch_Symbol_Range(%s, %s)"
            params = [symbol, days]
            df = pl.read_database(
                query, engine.connect(), execute_options={"parameters": params}
            )
            # Map procedure output to standard names
            df = df.rename({
                "open_p": "open",
                "high_p": "high",
                "close_p": "close",
                "low_p": "low",
                "volume_p": "volume",
                "ts_p": "timestamp",
            })
            if "timestamp" in df.columns:
                df = df.with_columns(pl.col("ts_p").dt.date().alias("date"))
            return df

        # Direct table query with date range
        query = f"SELECT * FROM {table_name} WHERE symbol = %s"
        params = [symbol]
        if start_date:
            query += " AND timestamp >= %s"
            params.append(start_date)
        if end_date:
            query += " AND timestamp <= %s"
            params.append(end_date)
        query += " ORDER BY timestamp ASC"

        df = pl.read_database(
            query, engine.connect(), execute_options={"parameters": params}
        )
        return df

    except Exception as e:
        raise ValueError(f"Error loading {symbol} from RDS: {str(e)}")

# ============================================================================
# Multi-Timeframe Data Loading
# ============================================================================

# Daily data: RDS only. Resampled data: S3 only (partition: silver_{x}d/{symbol}/{year}/{month}/data_{x}d_{YYYYMM}.parquet).
TIMEFRAME_TABLE_MAP = {
    '1d': 'raw_ohlcv',
}
RESAMPLED_TIMEFRAMES = ('3d', '5d', '8d', '13d', '21d', '34d')


def load_ohlcv_by_timeframe(
    symbol: str,
    timeframe: str,
    connection_string: Optional[str] = None,
    s3_bucket: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> pl.DataFrame:
    """
    Load OHLCV by timeframe: daily (1d) from RDS, resampled (3d, 5d, ...) from S3.

    - 1d: requires connection_string; loads from RDS raw_ohlcv.
    - 3d, 5d, 8d, etc.: requires s3_bucket and start_date/end_date; loads from S3
      path silver_{x}d/{symbol}/{year}/{month}/data_{x}d_{YYYYMM}.parquet.

    Args:
        symbol: Stock symbol (e.g., 'AAPL')
        timeframe: '1d' or resampled ('3d', '5d', '8d', '13d', '21d', '34d')
        connection_string: PostgreSQL connection (required for 1d)
        s3_bucket: S3 bucket (required for resampled)
        start_date: Start date (required for resampled; optional for 1d)
        end_date: End date (required for resampled; optional for 1d)

    Returns:
        Polars DataFrame with OHLCV data
    """
    if timeframe == "1d":
        if not connection_string:
            raise ValueError("Daily (1d) data requires connection_string (RDS).")
        return load_ohlcv_from_rds(
            symbol=symbol,
            connection_string=connection_string,
            start_date=start_date,
            end_date=end_date,
            table_name=TIMEFRAME_TABLE_MAP["1d"],
        )
    # Resampled: load from S3
    if not s3_bucket:
        raise ValueError("Resampled timeframes require s3_bucket.")
    if not start_date or not end_date:
        raise ValueError("Resampled timeframes require start_date and end_date.")
    return load_ohlcv_from_s3(
        bucket=s3_bucket,
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
    )
