"""
Data Input Utilities

Load OHLCV data from various sources (S3, RDS) into Polars DataFrames
"""

import polars as pl
import boto3
from typing import Optional, List
from datetime import datetime, date
import os


def load_ohlcv_from_s3(
    bucket: str,
    symbol: str,
    prefix: str = 'bronze/raw_ohlcv',
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> pl.DataFrame:
    """
    Load OHLCV data from S3 Parquet file
    
    Args:
        bucket: S3 bucket name
        symbol: Stock symbol (e.g., 'AAPL')
        prefix: S3 prefix (default: 'bronze/raw_ohlcv')
        start_date: Filter start date (optional)
        end_date: Filter end date (optional)
        
    Returns:
        Polars DataFrame with OHLCV data
    """
    s3_client = boto3.client('s3')
    
    # Construct S3 path: bronze/raw_ohlcv/symbol=AAPL/data.parquet
    s3_key = f"{prefix}/symbol={symbol}/data.parquet"
    
    try:
        # Download parquet file to temporary location
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp_file:
            s3_client.download_fileobj(bucket, s3_key, tmp_file)
            tmp_path = tmp_file.name
        
        # Read parquet into Polars
        df = pl.read_parquet(tmp_path)
        
        # Clean up temp file
        os.unlink(tmp_path)
        
        # Filter by date if provided
        if start_date or end_date:
            if 'date' in df.columns:
                if start_date:
                    df = df.filter(pl.col('date') >= start_date)
                if end_date:
                    df = df.filter(pl.col('date') <= end_date)
        
        return df
        
    except Exception as e:
        raise ValueError(f"Error loading {symbol} from S3: {str(e)}")


def load_ohlcv_from_rds(
    symbol: str,
    connection_string: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    table_name: str = 'raw_ohlcv'
) -> pl.DataFrame:
    """
    Load OHLCV data from RDS PostgreSQL
    
    Args:
        symbol: Stock symbol (e.g., 'AAPL')
        connection_string: PostgreSQL connection string
        start_date: Filter start date (optional)
        end_date: Filter end date (optional)
        table_name: Table name (default: 'raw_ohlcv')
        
    Returns:
        Polars DataFrame with OHLCV data
    """
    import psycopg2
    from sqlalchemy import create_engine
    
    # Build query
    query = f"SELECT * FROM {table_name} WHERE symbol = %s"
    params = [symbol]
    
    if start_date:
        query += " AND date >= %s"
        params.append(start_date)
    
    if end_date:
        query += " AND date <= %s"
        params.append(end_date)
    
    query += " ORDER BY date ASC"
    
    try:
        # Use SQLAlchemy for connection
        engine = create_engine(connection_string)
        
        # Read directly into Polars
        df = pl.read_database(query, engine.connect(), execute_params=params)
        
        return df
        
    except Exception as e:
        raise ValueError(f"Error loading {symbol} from RDS: {str(e)}")


def load_ohlcv_from_parquet_file(file_path: str) -> pl.DataFrame:
    """
    Load OHLCV data from local Parquet file
    
    Args:
        file_path: Path to parquet file
        
    Returns:
        Polars DataFrame with OHLCV data
    """
    return pl.read_parquet(file_path)


# ============================================================================
# Multi-Timeframe Data Loading
# ============================================================================

# Map timeframe strings to table names
TIMEFRAME_TABLE_MAP = {
    '1d': 'raw_ohlcv',
    '3d': 'silver_3d',
    '5d': 'silver_5d',
    '8d': 'silver_8d',
    '13d': 'silver_13d',
    '21d': 'silver_21d',
    '34d': 'silver_34d',
}

# Map timeframe strings to S3 prefixes
TIMEFRAME_S3_PREFIX_MAP = {
    '1d': 'bronze/raw_ohlcv',
    '3d': 'silver/silver_3d',
    '5d': 'silver/silver_5d',
    '8d': 'silver/silver_8d',
    '13d': 'silver/silver_13d',
    '21d': 'silver/silver_21d',
    '34d': 'silver/silver_34d',
}


def load_ohlcv_by_timeframe(
    symbol: str,
    timeframe: str,
    connection_string: Optional[str] = None,
    s3_bucket: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    use_s3: bool = False
) -> pl.DataFrame:
    """
    Load OHLCV data for a specific timeframe
    
    Maps timeframe strings to table names:
    - '1d' -> 'raw_ohlcv'
    - '3d' -> 'silver_3d'
    - '5d' -> 'silver_5d'
    - etc.
    
    Args:
        symbol: Stock symbol (e.g., 'AAPL')
        timeframe: Timeframe string (e.g., '1d', '3d', '5d')
        connection_string: PostgreSQL connection string (for RDS)
        s3_bucket: S3 bucket name (for S3)
        start_date: Filter start date (optional)
        end_date: Filter end date (optional)
        use_s3: If True, load from S3; if False, load from RDS
        
    Returns:
        Polars DataFrame with OHLCV data
    """
    if use_s3 and s3_bucket:
        # Load from S3
        prefix = TIMEFRAME_S3_PREFIX_MAP.get(timeframe, 'bronze/raw_ohlcv')
        return load_ohlcv_from_s3(
            bucket=s3_bucket,
            symbol=symbol,
            prefix=prefix,
            start_date=start_date,
            end_date=end_date
        )
    elif connection_string:
        # Load from RDS
        table_name = TIMEFRAME_TABLE_MAP.get(timeframe, 'raw_ohlcv')
        return load_ohlcv_from_rds(
            symbol=symbol,
            connection_string=connection_string,
            start_date=start_date,
            end_date=end_date,
            table_name=table_name
        )
    else:
        raise ValueError("Either connection_string (for RDS) or s3_bucket (for S3) must be provided")
