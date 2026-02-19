"""
Data Input Utilities

Load OHLCV from RDS (raw 1d) and resample at use for multi-timeframe (3d, 5d, etc.).
No S3 loading; resampling is done in memory from 1d data.
"""

import polars as pl
from typing import Optional, Union, List
from datetime import date


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


def load_ohlcv(
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
                df = df.with_columns(pl.col("timestamp").dt.date().alias("date"))
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


def _load_one_timeframe(
    symbol: str,
    timeframe: str,
    connection_string: str,
    start_date: Optional[date],
    end_date: Optional[date],
    days: Optional[int],
) -> pl.DataFrame:
    """Load and return one timeframe as normalized DataFrame (single string only)."""
    timeframe = timeframe.strip().lower()
    if timeframe == "1d":
        df = load_ohlcv(
            symbol=symbol,
            connection_string=connection_string,
            start_date=start_date,
            end_date=end_date,
            table_name=RDS_TABLE_1D,
            days=days,
        )
        return _normalize_ohlcv_schema(df, symbol)
    if timeframe in RESAMPLED_TIMEFRAMES and timeframe != "1d":
        interval_days = int(timeframe.rstrip("d").strip())
        if interval_days < 2:
            raise ValueError(f"Resampled timeframe must be 2d or more; got {timeframe!r}")
        df = load_ohlcv(
            symbol=symbol,
            connection_string=connection_string,
            start_date=start_date,
            end_date=end_date,
            table_name=RDS_TABLE_1D,
            days=days,
        )
        df = resample_ohlcv(df, interval_days)
        return _normalize_ohlcv_schema(df, symbol)
    raise ValueError(f"Invalid timeframe: {timeframe!r}; use '1d', '3d', '5d', etc.")


def load_ohlcv_by_timeframe(
    symbol: str,
    timeframe: Union[str, List[str]],
    connection_string: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    days: Optional[int] = None,
) -> pl.DataFrame:
    """
    Load OHLCV for one or more timeframes. Source is always RDS (raw 1d); we map to the
    requested timeframe(s) at use.

    - timeframe: single string (e.g. '1d', '3d') or list of strings (e.g. ['1d', '3d']).
      When a list is passed, returns one combined DataFrame with an extra "timeframe" column.
    - Data source: RDS only (table raw_ohlcv, daily bars).
    - 1d: return 1d from RDS as-is.
    - 3d, 5d, 8d, ...: load 1d from RDS for the range, then resample in memory.

    Returns a DataFrame with canonical columns: date, open, high, low, close, volume, symbol
    (and "timeframe" when multiple timeframes are requested).

    Args:
        symbol: Stock symbol (e.g., 'AAPL')
        timeframe: '1d', '3d', ... or ['1d', '3d', ...]
        connection_string: PostgreSQL connection (required)
        start_date: Start date (required for non-1d; optional for 1d if days set)
        end_date: End date (required for non-1d; optional for 1d if days set)
        days: Last N days (1d only; ignored for 3d, 5d, etc.)

    Returns:
        Polars DataFrame with columns: date, open, high, low, close, volume, symbol [, timeframe]
    """
    if not connection_string:
        raise ValueError("connection_string (RDS) is required.")
    # Accept single string or list (e.g. notebook passes timeframes = ['1d', '3d'])
    if isinstance(timeframe, (list, tuple)):
        if not timeframe:
            raise ValueError("timeframe list must not be empty")
        parts = []
        for t in timeframe:
            t_str = t.strip().lower() if isinstance(t, str) else str(t).strip().lower()
            df = _load_one_timeframe(
                symbol=symbol,
                timeframe=t_str,
                connection_string=connection_string,
                start_date=start_date,
                end_date=end_date,
                days=days,
            )
            if not df.is_empty():
                df = df.with_columns(pl.lit(t_str).alias("timeframe"))
                parts.append(df)
        if not parts:
            return pl.DataFrame()
        return pl.concat(parts, how="vertical")
    if not isinstance(timeframe, str):
        raise ValueError("timeframe must be a string or list of strings (e.g. '1d' or ['1d', '3d'])")
    return _load_one_timeframe(
        symbol=symbol,
        timeframe=timeframe.strip().lower(),
        connection_string=connection_string,
        start_date=start_date,
        end_date=end_date,
        days=days,
    )
