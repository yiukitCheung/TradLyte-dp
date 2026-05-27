"""
Momentum Strategies

Pre-built strategies focusing on trend following and momentum.

NOTE: A previous version of this file included a second, orphaned MACD
strategy class definition that re-declared ``__init__``/``setup``/``trigger``/
``exit`` after ``GoldenCrossStrategy``. Because both definitions lived in the
same class scope, the second set silently overrode the first, breaking the
Golden Cross logic. It also referenced the legacy fixed-name columns ``macd``
and ``macd_signal`` which no longer exist (indicators are now parametric:
``macd_12_26`` / ``macd_signal_12_26_9``). The orphan block has been removed.
A standalone MACD strategy can be re-introduced as its own class file if
needed.
"""

import polars as pl
from ..base import BaseStrategy
from ...indicators.technicals import calculate_sma, sma_col


class GoldenCrossStrategy(BaseStrategy):
    """
    Golden Cross Strategy

    Setup: SMA 50 > SMA 200 (uptrend)
    Trigger: SMA 50 crosses above SMA 200
    Exit: Stop loss 5% or SMA 50 crosses below SMA 200
    """

    FAST_PERIOD = 50
    SLOW_PERIOD = 200

    def __init__(self):
        super().__init__(
            name="Golden Cross",
            description="SMA 50/200 crossover strategy",
        )

    def _ensure_smas(self, df: pl.DataFrame) -> pl.DataFrame:
        df = calculate_sma(df, period=self.FAST_PERIOD)
        df = calculate_sma(df, period=self.SLOW_PERIOD)
        return df

    def setup(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Setup: Uptrend (SMA 50 > SMA 200).
        Requires at least 200 candles of history before the current candle.
        """
        df = self._ensure_smas(df)
        fast, slow = sma_col(self.FAST_PERIOD), sma_col(self.SLOW_PERIOD)
        return df.filter(pl.col('date').n_unique() >= self.SLOW_PERIOD).with_columns(
            (pl.col(fast) > pl.col(slow)).alias('setup_valid')
        )

    def trigger(self, df: pl.DataFrame) -> pl.DataFrame:
        """Trigger: Golden Cross (SMA 50 crosses above SMA 200)."""
        df = self._ensure_smas(df)
        fast, slow = sma_col(self.FAST_PERIOD), sma_col(self.SLOW_PERIOD)
        golden_cross = (
            (pl.col(fast) > pl.col(slow)) &
            (pl.col(fast).shift(1) <= pl.col(slow).shift(1))
        )
        return df.with_columns(
            pl.when(pl.col('setup_valid') & golden_cross)
            .then(pl.lit('BUY'))
            .otherwise(pl.lit('HOLD'))
            .alias('signal')
        )

    def exit(self, df: pl.DataFrame) -> pl.DataFrame:
        """Exit: Stop loss 5% or Death Cross."""
        df = self._ensure_smas(df)
        fast, slow = sma_col(self.FAST_PERIOD), sma_col(self.SLOW_PERIOD)
        stop_loss = pl.col('close') * 0.95
        death_cross = (
            (pl.col(fast) < pl.col(slow)) &
            (pl.col(fast).shift(1) >= pl.col(slow).shift(1))
        )
        return df.with_columns([
            pl.when(death_cross)
            .then(pl.lit('SELL'))
            .otherwise(None)
            .alias('exit_signal'),
            stop_loss.alias('stop_loss_price'),
        ])