"""
Technical Indicators using Polars

High-performance indicator calculations for OHLCV data
"""

import polars as pl
from typing import Optional


def calculate_rsi(df: pl.DataFrame, period: int = 14, price_col: str = 'close') -> pl.DataFrame:
    """
    Calculate Relative Strength Index (RSI)
    
    Args:
        df: DataFrame with OHLCV data
        period: RSI period (default: 14)
        price_col: Column name for price (default: 'close')
        
    Returns:
        DataFrame with 'rsi' column added
    """
    delta = pl.col(price_col).diff()
    gain = delta.filter(delta > 0).fill_null(0)
    loss = -delta.filter(delta < 0).fill_null(0)
    
    avg_gain = gain.rolling_mean(window_size=period)
    avg_loss = loss.rolling_mean(window_size=period)
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return df.with_columns(rsi.alias('rsi'))


def calculate_sma(df: pl.DataFrame, period: int, price_col: str = 'close') -> pl.DataFrame:
    """
    Calculate Simple Moving Average (SMA)
    
    Args:
        df: DataFrame with OHLCV data
        period: SMA period
        price_col: Column name for price (default: 'close')
        
    Returns:
        DataFrame with 'sma_{period}' column added
    """
    sma = pl.col(price_col).rolling_mean(window_size=period)
    return df.with_columns(sma.alias(f'sma_{period}'))


def calculate_ema(df: pl.DataFrame, period: int, price_col: str = 'close') -> pl.DataFrame:
    """
    Calculate Exponential Moving Average (EMA)
    
    Args:
        df: DataFrame with OHLCV data
        period: EMA period
        price_col: Column name for price (default: 'close')
        
    Returns:
        DataFrame with 'ema_{period}' column added
    """
    alpha = 2.0 / (period + 1)
    ema = pl.col(price_col).ewm_mean(alpha=alpha, adjust=False)
    return df.with_columns(ema.alias(f'ema_{period}'))


def calculate_macd(
    df: pl.DataFrame,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    price_col: str = 'close'
) -> pl.DataFrame:
    """
    Calculate MACD (Moving Average Convergence Divergence)
    
    Args:
        df: DataFrame with OHLCV data
        fast_period: Fast EMA period (default: 12)
        slow_period: Slow EMA period (default: 26)
        signal_period: Signal line period (default: 9)
        price_col: Column name for price (default: 'close')
        
    Returns:
        DataFrame with 'macd', 'macd_signal', 'macd_histogram' columns added
    """
    # Calculate EMAs
    fast_ema = pl.col(price_col).ewm_mean(alpha=2.0/(fast_period+1), adjust=False)
    slow_ema = pl.col(price_col).ewm_mean(alpha=2.0/(slow_period+1), adjust=False)
    
    # MACD line
    macd_line = fast_ema - slow_ema
    
    # Signal line (EMA of MACD)
    signal_line = macd_line.ewm_mean(alpha=2.0/(signal_period+1), adjust=False)
    
    # Histogram
    histogram = macd_line - signal_line
    
    return df.with_columns([
        macd_line.alias('macd'),
        signal_line.alias('macd_signal'),
        histogram.alias('macd_histogram'),
    ])


def calculate_bollinger_bands(
    df: pl.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
    price_col: str = 'close'
) -> pl.DataFrame:
    """
    Calculate Bollinger Bands
    
    Args:
        df: DataFrame with OHLCV data
        period: Moving average period (default: 20)
        std_dev: Standard deviation multiplier (default: 2.0)
        price_col: Column name for price (default: 'close')
        
    Returns:
        DataFrame with 'bb_middle', 'bb_upper', 'bb_lower' columns added
    """
    sma = pl.col(price_col).rolling_mean(window_size=period)
    std = pl.col(price_col).rolling_std(window_size=period)
    
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    
    return df.with_columns([
        sma.alias('bb_middle'),
        upper.alias('bb_upper'),
        lower.alias('bb_lower'),
    ])


def calculate_atr(df: pl.DataFrame, period: int = 14) -> pl.DataFrame:
    """
    Calculate Average True Range (ATR)
    
    Args:
        df: DataFrame with OHLCV data
        period: ATR period (default: 14)
        
    Returns:
        DataFrame with 'atr' column added
    """
    high_low = pl.col('high') - pl.col('low')
    high_close = (pl.col('high') - pl.col('close').shift(1)).abs()
    low_close = (pl.col('low') - pl.col('close').shift(1)).abs()
    
    tr = pl.max_horizontal([high_low, high_close, low_close])
    atr = tr.rolling_mean(window_size=period)
    
    return df.with_columns(atr.alias('atr'))


def calculate_stochastic(
    df: pl.DataFrame,
    k_period: int = 14,
    d_period: int = 3
) -> pl.DataFrame:
    """
    Calculate Stochastic Oscillator
    
    Args:
        df: DataFrame with OHLCV data
        k_period: %K period (default: 14)
        d_period: %D period (default: 3)
        
    Returns:
        DataFrame with 'stoch_k', 'stoch_d' columns added
    """
    lowest_low = pl.col('low').rolling_min(window_size=k_period)
    highest_high = pl.col('high').rolling_max(window_size=k_period)
    
    k_percent = 100 * ((pl.col('close') - lowest_low) / (highest_high - lowest_low))
    d_percent = k_percent.rolling_mean(window_size=d_period)
    
    return df.with_columns([
        k_percent.alias('stoch_k'),
        d_percent.alias('stoch_d'),
    ])


def calculate_all_indicators(df: pl.DataFrame) -> pl.DataFrame:
    """
    Calculate all common technical indicators
    
    Args:
        df: DataFrame with OHLCV data
        
    Returns:
        DataFrame with all indicator columns added
    """
    # RSI
    df = calculate_rsi(df, period=14)
    
    # SMAs (common periods)
    for period in [20, 50, 200]:
        df = calculate_sma(df, period=period)
    
    # EMAs (common periods)
    for period in [12, 26]:
        df = calculate_ema(df, period=period)
    
    # MACD
    df = calculate_macd(df)
    
    # Bollinger Bands
    df = calculate_bollinger_bands(df)
    
    # ATR
    df = calculate_atr(df)
    
    # Stochastic
    df = calculate_stochastic(df)
    
    return df
