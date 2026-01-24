"""
Candle Pattern Detection using Polars

High-performance candle pattern detection for OHLCV data
"""

import polars as pl
from typing import Optional


def detect_engulfing_bullish(df: pl.DataFrame) -> pl.DataFrame:
    """
    Detect Bullish Engulfing pattern
    
    Pattern: Current candle completely engulfs previous bearish candle
    - Previous candle is bearish (close < open)
    - Current candle is bullish (close > open)
    - Current open < previous close
    - Current close > previous open
    
    Args:
        df: DataFrame with OHLCV data (must have 'open', 'high', 'low', 'close')
        
    Returns:
        DataFrame with 'engulfing_bullish' boolean column added
    """
    prev_open = pl.col('open').shift(1)
    prev_close = pl.col('close').shift(1)
    
    prev_bearish = prev_close < prev_open
    curr_bullish = pl.col('close') > pl.col('open')
    engulfs_open = pl.col('open') < prev_close
    engulfs_close = pl.col('close') > prev_open
    
    engulfing = prev_bearish & curr_bullish & engulfs_open & engulfs_close
    
    return df.with_columns(engulfing.alias('engulfing_bullish'))


def detect_engulfing_bearish(df: pl.DataFrame) -> pl.DataFrame:
    """
    Detect Bearish Engulfing pattern
    
    Pattern: Current candle completely engulfs previous bullish candle
    - Previous candle is bullish (close > open)
    - Current candle is bearish (close < open)
    - Current open > previous close
    - Current close < previous open
    
    Args:
        df: DataFrame with OHLCV data
        
    Returns:
        DataFrame with 'engulfing_bearish' boolean column added
    """
    prev_open = pl.col('open').shift(1)
    prev_close = pl.col('close').shift(1)
    
    prev_bullish = prev_close > prev_open
    curr_bearish = pl.col('close') < pl.col('open')
    engulfs_open = pl.col('open') > prev_close
    engulfs_close = pl.col('close') < prev_open
    
    engulfing = prev_bullish & curr_bearish & engulfs_open & engulfs_close
    
    return df.with_columns(engulfing.alias('engulfing_bearish'))


def detect_hammer(df: pl.DataFrame) -> pl.DataFrame:
    """
    Detect Hammer pattern (bullish reversal)
    
    Pattern:
    - Small body at upper end of range
    - Long lower shadow (at least 2x body)
    - Little or no upper shadow
    
    Args:
        df: DataFrame with OHLCV data
        
    Returns:
        DataFrame with 'hammer' boolean column added
    """
    body = (pl.col('close') - pl.col('open')).abs()
    lower_shadow = pl.min_horizontal([pl.col('open'), pl.col('close')]) - pl.col('low')
    upper_shadow = pl.col('high') - pl.max_horizontal([pl.col('open'), pl.col('close')])
    range_size = pl.col('high') - pl.col('low')
    
    # Body should be small relative to range
    small_body = body <= (range_size * 0.3)
    # Lower shadow should be at least 2x body
    long_lower = lower_shadow >= (body * 2)
    # Upper shadow should be small
    small_upper = upper_shadow <= (body * 0.5)
    
    hammer = small_body & long_lower & small_upper
    
    return df.with_columns(hammer.alias('hammer'))


def detect_shooting_star(df: pl.DataFrame) -> pl.DataFrame:
    """
    Detect Shooting Star pattern (bearish reversal)
    
    Pattern:
    - Small body at lower end of range
    - Long upper shadow (at least 2x body)
    - Little or no lower shadow
    
    Args:
        df: DataFrame with OHLCV data
        
    Returns:
        DataFrame with 'shooting_star' boolean column added
    """
    body = (pl.col('close') - pl.col('open')).abs()
    upper_shadow = pl.col('high') - pl.max_horizontal([pl.col('open'), pl.col('close')])
    lower_shadow = pl.min_horizontal([pl.col('open'), pl.col('close')]) - pl.col('low')
    range_size = pl.col('high') - pl.col('low')
    
    # Body should be small relative to range
    small_body = body <= (range_size * 0.3)
    # Upper shadow should be at least 2x body
    long_upper = upper_shadow >= (body * 2)
    # Lower shadow should be small
    small_lower = lower_shadow <= (body * 0.5)
    
    shooting_star = small_body & long_upper & small_lower
    
    return df.with_columns(shooting_star.alias('shooting_star'))


def detect_doji(df: pl.DataFrame, body_threshold: float = 0.1) -> pl.DataFrame:
    """
    Detect Doji pattern (indecision)
    
    Pattern: Open and close are very close (small body relative to range)
    
    Args:
        df: DataFrame with OHLCV data
        body_threshold: Maximum body size as fraction of range (default: 0.1 = 10%)
        
    Returns:
        DataFrame with 'doji' boolean column added
    """
    body = (pl.col('close') - pl.col('open')).abs()
    range_size = pl.col('high') - pl.col('low')
    
    # Body should be very small relative to range
    doji = body <= (range_size * body_threshold)
    
    return df.with_columns(doji.alias('doji'))


def detect_morning_star(df: pl.DataFrame) -> pl.DataFrame:
    """
    Detect Morning Star pattern (bullish reversal, 3-candle pattern)
    
    Pattern:
    - First candle: Bearish
    - Second candle: Small body (gap down from first)
    - Third candle: Bullish, closes into first candle's body
    
    Args:
        df: DataFrame with OHLCV data
        
    Returns:
        DataFrame with 'morning_star' boolean column added
    """
    # First candle (2 bars ago)
    first_close = pl.col('close').shift(2)
    first_open = pl.col('open').shift(2)
    first_bearish = first_close < first_open
    
    # Second candle (1 bar ago) - small body, gap down
    second_close = pl.col('close').shift(1)
    second_open = pl.col('open').shift(1)
    second_body = (second_close - second_open).abs()
    gap_down = second_open < first_close
    small_body = second_body <= (second_body.mean() * 0.5)  # Relative to average
    
    # Third candle (current) - bullish, closes into first body
    third_bullish = pl.col('close') > pl.col('open')
    closes_into_first = pl.col('close') > ((first_open + first_close) / 2)
    
    morning_star = first_bearish & gap_down & small_body & third_bullish & closes_into_first
    
    return df.with_columns(morning_star.alias('morning_star'))


def detect_evening_star(df: pl.DataFrame) -> pl.DataFrame:
    """
    Detect Evening Star pattern (bearish reversal, 3-candle pattern)
    
    Pattern:
    - First candle: Bullish
    - Second candle: Small body (gap up from first)
    - Third candle: Bearish, closes into first candle's body
    
    Args:
        df: DataFrame with OHLCV data
        
    Returns:
        DataFrame with 'evening_star' boolean column added
    """
    # First candle (2 bars ago)
    first_close = pl.col('close').shift(2)
    first_open = pl.col('open').shift(2)
    first_bullish = first_close > first_open
    
    # Second candle (1 bar ago) - small body, gap up
    second_close = pl.col('close').shift(1)
    second_open = pl.col('open').shift(1)
    second_body = (second_close - second_open).abs()
    gap_up = second_open > first_close
    small_body = second_body <= (second_body.mean() * 0.5)  # Relative to average
    
    # Third candle (current) - bearish, closes into first body
    third_bearish = pl.col('close') < pl.col('open')
    closes_into_first = pl.col('close') < ((first_open + first_close) / 2)
    
    evening_star = first_bullish & gap_up & small_body & third_bearish & closes_into_first
    
    return df.with_columns(evening_star.alias('evening_star'))


def detect_green_candle(df: pl.DataFrame) -> pl.DataFrame:
    """
    Detect simple bullish (green) candle
    
    Pattern: Close > Open
    
    Args:
        df: DataFrame with OHLCV data
        
    Returns:
        DataFrame with 'green_candle' boolean column added
    """
    green = pl.col('close') > pl.col('open')
    return df.with_columns(green.alias('green_candle'))


def detect_red_candle(df: pl.DataFrame) -> pl.DataFrame:
    """
    Detect simple bearish (red) candle
    
    Pattern: Close < Open
    
    Args:
        df: DataFrame with OHLCV data
        
    Returns:
        DataFrame with 'red_candle' boolean column added
    """
    red = pl.col('close') < pl.col('open')
    return df.with_columns(red.alias('red_candle'))


def detect_all_patterns(df: pl.DataFrame) -> pl.DataFrame:
    """
    Detect all candle patterns and add columns
    
    Args:
        df: DataFrame with OHLCV data
        
    Returns:
        DataFrame with all pattern columns added
    """
    df = detect_engulfing_bullish(df)
    df = detect_engulfing_bearish(df)
    df = detect_hammer(df)
    df = detect_shooting_star(df)
    df = detect_doji(df)
    df = detect_morning_star(df)
    df = detect_evening_star(df)
    df = detect_green_candle(df)
    df = detect_red_candle(df)
    
    return df
