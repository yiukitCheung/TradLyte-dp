"""
Data Input Utilities

Load OHLCV from RDS (raw 1d) and resample at use for multi-timeframe (3d, 5d, etc.).
No S3 loading; resampling is done in memory from 1d data.
"""

import psycopg2
import polars as pl
from typing import Optional, Union, List, Dict
from datetime import date, timedelta


def load_ohlcv(
    symbols: Union[str, List[str]],
    connection_string: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    table_name: str = "raw_ohlcv",
) -> pl.DataFrame:
    """
    Load OHLCV (1d) for multiple symbols in one query.

    Returns a single DataFrame with columns: symbol, timestamp (and date if added),
    open, high, low, close, volume. Used by the scanner to batch-load then split
    by symbol and resample in memory.

    Args:
        symbols: List of ticker symbols (e.g. ['AAPL', 'MSFT'])
        connection_string: PostgreSQL connection string (URI format, e.g.
                           postgresql://user:pwd@host:5432/db?sslmode=require)
        start_date: Filter start date (inclusive)
        end_date: Filter end date (inclusive)
        table_name: Table name (default: raw_ohlcv)

    Returns:
        Polars DataFrame with all 1d bars for the given symbols and date range.
    """
    if isinstance(symbols, str):
        symbols = [symbols]
    if not symbols:
        return pl.DataFrame()

    # Use exclusive end bound to reliably include all rows on end_date when
    # source column is a timestamp (date params are often interpreted as 00:00:00).
    end_exclusive = (end_date + timedelta(days=1)) if end_date is not None else None

    if len(symbols) > 1:
        query = (
            f"SELECT * FROM {table_name} "
            "WHERE symbol = ANY(%s) AND timestamp >= %s AND timestamp < %s "
            "ORDER BY symbol, timestamp ASC"
        )
        params = (symbols, start_date, end_exclusive)
    else:
        query = (
            f"SELECT * FROM {table_name} "
            "WHERE symbol = %s AND timestamp >= %s AND timestamp < %s "
            "ORDER BY timestamp ASC"
        )
        params = (symbols[0], start_date, end_exclusive)

    try:
        # psycopg2 accepts the full URI directly, including ?sslmode=require
        conn = psycopg2.connect(connection_string)
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
                col_names = [desc[0] for desc in cur.description]
        finally:
            conn.close()
    except Exception as e:
        raise ValueError(f"Error batch-loading OHLCV from RDS: {str(e)}")

    if not rows:
        return pl.DataFrame()

    df = pl.DataFrame([dict(zip(col_names, row)) for row in rows])
    if "date" not in df.columns and "timestamp" in df.columns:
        df = df.with_columns(pl.col("timestamp").dt.date().alias("date"))
    if "symbol" not in df.columns:
        raise ValueError("Batch load must return symbol column")
    df = df.sort("symbol", "date")
    return df

def build_multi_timeframe_from_batch_1d(
    batch_1d: pl.DataFrame,
    timeframes: List[str],
) -> Dict[str, Dict[str, pl.DataFrame]]:
    """
    Split batch 1d DataFrame by symbol and resample each to requested timeframes.

    Args:
        batch_1d: DataFrame with columns symbol, date (or timestamp), open, high, low, close, volume
        timeframes: e.g. ['1d', '3d', '5d', '8d', '13d', '21d', '34d']

    Returns:
        Nested dict: result[symbol][timeframe] -> DataFrame with canonical columns.
    """
    if batch_1d.is_empty():
        return {}
    timeframes = [t.strip().lower() if isinstance(t, str) else str(t).strip().lower() for t in timeframes]
    result: Dict[str, Dict[str, pl.DataFrame]] = {}
    symbols = batch_1d["symbol"].unique().to_list()
    for sym in symbols:
        df_1d = batch_1d.filter(pl.col("symbol") == sym)
        if df_1d.is_empty():
            continue
        result[sym] = {}
        for tf in timeframes:
            if tf == "1d":
                result[sym][tf] = _normalize_ohlcv_schema(df_1d.clone(), sym)
                continue
            if tf not in RESAMPLED_TIMEFRAMES or tf == "1d":
                continue
            interval_days = int(tf.rstrip("d").strip())
            if interval_days < 2:
                continue
            resampled = resample_ohlcv(df_1d.clone(), interval_days)
            if not resampled.is_empty():
                result[sym][tf] = _normalize_ohlcv_schema(resampled, sym)
    return result

# ============================================================================
# Multi-Timeframe: single source RDS (1d), map to requested timeframe at use
# ============================================================================

RDS_TABLE_1D = "raw_ohlcv"
RESAMPLED_TIMEFRAMES = ("1d", "3d", "5d", "8d", "13d", "21d", "34d")

# Canonical column order so 1d and resampled outputs can be concatenated safely
CANONICAL_OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume", "symbol"]

def _normalize_ohlcv_schema(df: pl.DataFrame, symbol: str) -> pl.DataFrame:
    """Ensure same columns and order for 1d and resampled paths (avoids vstack errors)."""
    if df.is_empty():
        return df
    if "date" not in df.columns and "timestamp" in df.columns:
        df = df.with_columns(pl.col("timestamp").dt.date().alias("date"))
    if "symbol" not in df.columns:
        df = df.with_columns(pl.lit(symbol).alias("symbol"))
    # Keep only canonical columns in fixed order (drop timestamp if present)
    existing = [c for c in CANONICAL_OHLCV_COLUMNS if c in df.columns]
    return df.select(existing)

def load_ohlcv_multi_timeframe(
    symbol: str,
    timeframe: Union[str, List[str]],
    connection_string: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> pl.DataFrame:
    """
    Load OHLCV for one symbol at one or more timeframes.

    Args:
        symbol: Stock symbol (e.g., 'AAPL')
        timeframe: '1d', '3d', etc. or list like ['1d', '3d']
        connection_string: PostgreSQL connection string
        start_date: Start date (optional if days is set)
        end_date: End date (optional if days is set)
    Returns:
        DataFrame with date, open, high, low, close, volume, symbol [, timeframe if list]
    """
    timeframes: List[str]
    if isinstance(timeframe, (list, tuple)):
        if not timeframe:
            raise ValueError("timeframe list must not be empty")
        timeframes = [t.strip().lower() if isinstance(t, str) else str(t).strip().lower() for t in timeframe]
    else:
        timeframes = [timeframe.strip().lower()]

    batch_1d = load_ohlcv(
        symbols=[symbol],
        connection_string=connection_string,
        start_date=start_date,
        end_date=end_date,
        table_name=RDS_TABLE_1D,
    )
    if batch_1d.is_empty():
        return pl.DataFrame()

    by_symbol = build_multi_timeframe_from_batch_1d(batch_1d, timeframes)
    symbol_data = by_symbol.get(symbol, {})
    if not symbol_data:
        return pl.DataFrame()

    if len(timeframes) == 1:
        return symbol_data.get(timeframes[0], pl.DataFrame())

    parts = []
    for tf in timeframes:
        df = symbol_data.get(tf)
        if df is not None and not df.is_empty():
            parts.append(df.with_columns(pl.lit(tf).alias("timeframe")))
    return pl.concat(parts, how="vertical") if parts else pl.DataFrame()

def resample_ohlcv(df_1d: pl.DataFrame, interval_days: int) -> pl.DataFrame:
    """
    Resample 1d OHLCV to Nd (e.g. 3d, 5d) using Polars group_by_dynamic.

    Expects columns: timestamp or date (datetime), open, high, low, close, volume.
    Optional: symbol. Returns one row per interval with date = period end date.
    """
    if df_1d.is_empty():
        return df_1d
    time_col = "timestamp" if "timestamp" in df_1d.columns else "date"
    if time_col not in df_1d.columns:
        raise ValueError("DataFrame must have 'timestamp' or 'date' column")
    if df_1d[time_col].dtype != pl.Datetime:
        df_1d = df_1d.with_columns(pl.col(time_col).cast(pl.Datetime).alias(time_col))
    df_1d = df_1d.sort(time_col)
    out = df_1d.group_by_dynamic(time_col, every=f"{interval_days}d").agg(
        pl.col("open").first().alias("open"),
        pl.col("high").max().alias("high"),
        pl.col("low").min().alias("low"),
        pl.col("close").last().alias("close"),
        pl.col("volume").sum().alias("volume"),
    )
    out = out.with_columns(pl.col(time_col).dt.date().alias("date"))
    if "symbol" in df_1d.columns:
        out = out.with_columns(pl.lit(df_1d["symbol"][0]).alias("symbol"))
    return out
