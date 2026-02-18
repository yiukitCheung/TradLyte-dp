"""
Data Input Utilities

Load OHLCV from RDS (raw 1d) and resample at use for multi-timeframe (3d, 5d, etc.).
No S3 loading; resampling is done in memory from 1d data.
"""

import polars as pl
from typing import Optional
from datetime import date


def resample_ohlcv_1d_to_nd(df_1d: pl.DataFrame, interval_days: int) -> pl.DataFrame:
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
# Multi-Timeframe Data Loading (RDS only; resample at use)
# ============================================================================

TIMEFRAME_TABLE_MAP = {'1d': 'raw_ohlcv'}
RESAMPLED_TIMEFRAMES = ('3d', '5d', '8d', '13d', '21d', '34d')


def load_ohlcv_by_timeframe(
    symbol: str,
    timeframe: str,
    connection_string: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    days: Optional[int] = None,
) -> pl.DataFrame:
    """
    Load OHLCV by timeframe: 1d from RDS; 3d, 5d, etc. from RDS 1d then resampled at use.

    - 1d: loads from RDS raw_ohlcv.
    - 3d, 5d, 8d, ...: loads 1d from RDS for the range, then resamples in memory.

    Args:
        symbol: Stock symbol (e.g., 'AAPL')
        timeframe: '1d' or '3d', '5d', '8d', '13d', '21d', '34d'
        connection_string: PostgreSQL connection (required)
        start_date: Start date (required for resampled; optional for 1d if days set)
        end_date: End date (required for resampled; optional for 1d if days set)
        days: If set, fetch last N days (1d only; for resampled use start_date/end_date)

    Returns:
        Polars DataFrame with OHLCV and date column
    """
    if not connection_string:
        raise ValueError("connection_string (RDS) is required for all timeframes.")
    if timeframe == "1d":
        df = load_ohlcv_from_rds(
            symbol=symbol,
            connection_string=connection_string,
            start_date=start_date,
            end_date=end_date,
            table_name=TIMEFRAME_TABLE_MAP["1d"],
            days=days,
        )
    else:
        x = timeframe.rstrip("d") if isinstance(timeframe, str) else str(timeframe)
        try:
            interval_days = int(x)
        except ValueError:
            raise ValueError(f"Invalid timeframe: {timeframe}")
        if not start_date or not end_date:
            raise ValueError("Resampled timeframes require start_date and end_date.")
        df = load_ohlcv_from_rds(
            symbol=symbol,
            connection_string=connection_string,
            start_date=start_date,
            end_date=end_date,
            table_name=TIMEFRAME_TABLE_MAP["1d"],
        )
        df = resample_ohlcv_1d_to_nd(df, interval_days)
    if df.height > 0 and "date" not in df.columns and "timestamp" in df.columns:
        df = df.with_columns(pl.col("timestamp").dt.date().alias("date"))
    return df
