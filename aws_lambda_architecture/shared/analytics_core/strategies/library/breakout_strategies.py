"""
Breakout Strategies

Pre-built strategies focusing on breakouts and volatility
"""

import polars as pl
from ..base import BaseStrategy


class BollingerBreakoutStrategy(BaseStrategy):
    """
    Bollinger Band Breakout Strategy
    
    Setup: Volume > average (confirmation)
    Trigger: Price breaks above upper Bollinger Band
    Exit: Price returns to middle band or stop loss
    """
    
    def __init__(self):
        super().__init__(
            name="Bollinger Breakout",
            description="Bollinger Band breakout strategy"
        )
    
    def setup(self, df: pl.DataFrame) -> pl.DataFrame:
        """Setup: Volume confirmation"""
        avg_volume = pl.col('volume').mean()
        return df.with_columns(
            (pl.col('volume') > avg_volume).alias('setup_valid')
        )
    
    def trigger(self, df: pl.DataFrame) -> pl.DataFrame:
        """Trigger: Price breaks above upper Bollinger Band"""
        breakout = (
            (pl.col('close') > pl.col('bb_upper')) &
            (pl.col('close').shift(1) <= pl.col('bb_upper').shift(1))
        )
        
        return df.with_columns(
            pl.when(pl.col('setup_valid') & breakout)
            .then(pl.lit('BUY'))
            .otherwise(pl.lit('HOLD'))
            .alias('signal')
        )
    
    def exit(self, df: pl.DataFrame) -> pl.DataFrame:
        """Exit: Price returns to middle band or stop loss"""
        return_to_middle = pl.col('close') < pl.col('bb_middle')
        stop_loss = pl.col('close') * 0.95
        
        return df.with_columns([
            pl.when(return_to_middle)
            .then(pl.lit('SELL'))
            .otherwise(None)
            .alias('exit_signal'),
            stop_loss.alias('stop_loss_price'),
        ])


class ATRBreakoutStrategy(BaseStrategy):
    """
    ATR Breakout Strategy
    
    Setup: ATR spike (high volatility)
    Trigger: Price breaks above recent high with ATR confirmation
    Exit: Trailing stop based on ATR
    """
    
    def __init__(self, atr_multiplier: float = 2.0):
        super().__init__(
            name="ATR Breakout",
            description=f"ATR-based breakout with {atr_multiplier}x multiplier"
        )
        self.atr_multiplier = atr_multiplier
    
    def setup(self, df: pl.DataFrame) -> pl.DataFrame:
        """Setup: ATR spike (volatility expansion)"""
        avg_atr = pl.col('atr').mean()
        return df.with_columns(
            (pl.col('atr') > avg_atr * self.atr_multiplier).alias('setup_valid')
        )
    
    def trigger(self, df: pl.DataFrame) -> pl.DataFrame:
        """Trigger: Price breaks above 20-day high"""
        high_20 = pl.col('high').rolling_max(window_size=20)
        breakout = (
            (pl.col('close') > high_20.shift(1)) &
            (pl.col('close').shift(1) <= high_20.shift(1))
        )
        
        return df.with_columns(
            pl.when(pl.col('setup_valid') & breakout)
            .then(pl.lit('BUY'))
            .otherwise(pl.lit('HOLD'))
            .alias('signal')
        )
    
    def exit(self, df: pl.DataFrame) -> pl.DataFrame:
        """Exit: Trailing stop based on ATR"""
        # Trailing stop = highest price - (ATR * multiplier)
        highest_since_entry = pl.col('high').rolling_max(window_size=20)
        trailing_stop = highest_since_entry - (pl.col('atr') * self.atr_multiplier)
        
        return df.with_columns([
            pl.when(pl.col('close') < trailing_stop)
            .then(pl.lit('SELL'))
            .otherwise(None)
            .alias('exit_signal'),
            trailing_stop.alias('trailing_stop_price'),
        ])
