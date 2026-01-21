"""
Momentum Strategies

Pre-built strategies focusing on trend following and momentum
"""

import polars as pl
from ..base import BaseStrategy


class GoldenCrossStrategy(BaseStrategy):
    """
    Golden Cross Strategy
    
    Setup: SMA 50 > SMA 200 (uptrend)
    Trigger: SMA 50 crosses above SMA 200
    Exit: Stop loss 5% or SMA 50 crosses below SMA 200
    """
    
    def __init__(self):
        super().__init__(
            name="Golden Cross",
            description="SMA 50/200 crossover strategy"
        )
    
    def setup(self, df: pl.DataFrame) -> pl.DataFrame:
        """Setup: Uptrend (SMA 50 > SMA 200)"""
        return df.with_columns(
            (pl.col('sma_50') > pl.col('sma_200')).alias('setup_valid')
        )
    
    def trigger(self, df: pl.DataFrame) -> pl.DataFrame:
        """Trigger: Golden Cross (SMA 50 crosses above SMA 200)"""
        golden_cross = (
            (pl.col('sma_50') > pl.col('sma_200')) &
            (pl.col('sma_50').shift(1) <= pl.col('sma_200').shift(1))
        )
        
        return df.with_columns(
            pl.when(pl.col('setup_valid') & golden_cross)
            .then(pl.lit('BUY'))
            .otherwise(pl.lit('HOLD'))
            .alias('signal')
        )
    
    def exit(self, df: pl.DataFrame) -> pl.DataFrame:
        """Exit: Stop loss 5% or Death Cross"""
        # Stop loss
        stop_loss = pl.col('close') * 0.95
        
        # Death cross
        death_cross = (
            (pl.col('sma_50') < pl.col('sma_200')) &
            (pl.col('sma_50').shift(1) >= pl.col('sma_200').shift(1))
        )
        
        return df.with_columns([
            pl.when(death_cross)
            .then(pl.lit('SELL'))
            .otherwise(None)
            .alias('exit_signal'),
            stop_loss.alias('stop_loss_price'),
        ])


class RSIMomentumStrategy(BaseStrategy):
    """
    RSI Momentum Strategy
    
    Setup: RSI > 50 (bullish momentum)
    Trigger: RSI crosses above 50 from below
    Exit: RSI < 50 or stop loss 5%
    """
    
    def __init__(self, rsi_period: int = 14, oversold: float = 30, overbought: float = 70):
        super().__init__(
            name="RSI Momentum",
            description=f"RSI {rsi_period} momentum strategy"
        )
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
    
    def setup(self, df: pl.DataFrame) -> pl.DataFrame:
        """Setup: RSI > 50 (bullish momentum)"""
        return df.with_columns(
            (pl.col('rsi') > 50).alias('setup_valid')
        )
    
    def trigger(self, df: pl.DataFrame) -> pl.DataFrame:
        """Trigger: RSI crosses above 50"""
        rsi_cross = (
            (pl.col('rsi') > 50) &
            (pl.col('rsi').shift(1) <= 50)
        )
        
        return df.with_columns(
            pl.when(pl.col('setup_valid') & rsi_cross)
            .then(pl.lit('BUY'))
            .otherwise(pl.lit('HOLD'))
            .alias('signal')
        )
    
    def exit(self, df: pl.DataFrame) -> pl.DataFrame:
        """Exit: RSI < 50 or stop loss"""
        rsi_exit = pl.col('rsi') < 50
        stop_loss = pl.col('close') * 0.95
        
        return df.with_columns([
            pl.when(rsi_exit)
            .then(pl.lit('SELL'))
            .otherwise(None)
            .alias('exit_signal'),
            stop_loss.alias('stop_loss_price'),
        ])


class MACDCrossoverStrategy(BaseStrategy):
    """
    MACD Crossover Strategy
    
    Setup: MACD > Signal (bullish)
    Trigger: MACD crosses above Signal line
    Exit: MACD crosses below Signal or stop loss
    """
    
    def __init__(self):
        super().__init__(
            name="MACD Crossover",
            description="MACD signal line crossover strategy"
        )
    
    def setup(self, df: pl.DataFrame) -> pl.DataFrame:
        """Setup: MACD > Signal (bullish)"""
        return df.with_columns(
            (pl.col('macd') > pl.col('macd_signal')).alias('setup_valid')
        )
    
    def trigger(self, df: pl.DataFrame) -> pl.DataFrame:
        """Trigger: MACD crosses above Signal"""
        macd_cross = (
            (pl.col('macd') > pl.col('macd_signal')) &
            (pl.col('macd').shift(1) <= pl.col('macd_signal').shift(1))
        )
        
        return df.with_columns(
            pl.when(pl.col('setup_valid') & macd_cross)
            .then(pl.lit('BUY'))
            .otherwise(pl.lit('HOLD'))
            .alias('signal')
        )
    
    def exit(self, df: pl.DataFrame) -> pl.DataFrame:
        """Exit: MACD crosses below Signal or stop loss"""
        macd_exit = (
            (pl.col('macd') < pl.col('macd_signal')) &
            (pl.col('macd').shift(1) >= pl.col('macd_signal').shift(1))
        )
        stop_loss = pl.col('close') * 0.95
        
        return df.with_columns([
            pl.when(macd_exit)
            .then(pl.lit('SELL'))
            .otherwise(None)
            .alias('exit_signal'),
            stop_loss.alias('stop_loss_price'),
        ])
