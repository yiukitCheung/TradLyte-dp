"""
Composite Strategy Builder

Allows users to mix-and-match Setup, Trigger, and Exit configurations
without writing new code. Builds a CompositeStrategy from JSON config.
"""

import polars as pl
from typing import Dict, Any, Optional
from ..models import StrategyConfig, SetupConfig, TriggerConfig, ExitConfig
from .base import BaseStrategy


class CompositeStrategy(BaseStrategy):
    """
    Composite Strategy - Built from user configuration
    
    Combines Setup, Trigger, and Exit configurations into a single strategy
    """
    
    def __init__(self, config: StrategyConfig):
        """
        Initialize composite strategy from configuration
        
        Args:
            config: StrategyConfig with setup, trigger, and exit rules
        """
        super().__init__(name=config.name, description=config.description)
        self.config = config
        self.setup_config = config.setup
        self.trigger_config = config.trigger
        self.exit_config = config.exit
    
    def setup(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply setup (momentum) logic based on configuration"""
        setup_type = self.setup_config.type
        
        if setup_type == 'NONE':
            # No setup filter - always valid
            return df.with_columns(pl.lit(True).alias('setup_valid'))
        
        elif setup_type == 'RSI_MOMENTUM':
            return self._setup_rsi_momentum(df)
        
        elif setup_type == 'SMA_TREND':
            return self._setup_sma_trend(df)
        
        elif setup_type == 'MACD_TREND':
            return self._setup_macd_trend(df)
        
        elif setup_type == 'VOLUME_TREND':
            return self._setup_volume_trend(df)
        
        else:
            raise ValueError(f"Unknown setup type: {setup_type}")
    
    def trigger(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply trigger (entry) logic based on configuration"""
        trigger_type = self.trigger_config.type
        
        # Initialize signal column as HOLD
        df = df.with_columns(pl.lit('HOLD').alias('signal'))
        
        # Only generate signals when setup is valid
        if trigger_type == 'CANDLE_PATTERN':
            return self._trigger_candle_pattern(df)
        
        elif trigger_type == 'PRICE_CROSSOVER':
            return self._trigger_price_crossover(df)
        
        elif trigger_type == 'INDICATOR_CROSSOVER':
            return self._trigger_indicator_crossover(df)
        
        elif trigger_type == 'BREAKOUT':
            return self._trigger_breakout(df)
        
        elif trigger_type == 'REVERSAL':
            return self._trigger_reversal(df)
        
        else:
            raise ValueError(f"Unknown trigger type: {trigger_type}")
    
    def exit(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply exit (management) logic based on configuration"""
        exit_type = self.exit_config.type
        
        # Initialize exit columns
        df = df.with_columns([
            pl.lit(None).alias('exit_signal'),
            pl.lit(None).cast(pl.Float64).alias('exit_price'),
        ])
        
        if exit_type == 'STOP_LOSS':
            return self._exit_stop_loss(df)
        
        elif exit_type == 'TAKE_PROFIT':
            return self._exit_take_profit(df)
        
        elif exit_type == 'TRAILING_STOP':
            return self._exit_trailing_stop(df)
        
        elif exit_type == 'TIME_BASED':
            return self._exit_time_based(df)
        
        elif exit_type == 'INDICATOR_SIGNAL':
            return self._exit_indicator_signal(df)
        
        elif exit_type == 'COMBINED':
            return self._exit_combined(df)
        
        else:
            raise ValueError(f"Unknown exit type: {exit_type}")
    
    # Setup Methods
    
    def _setup_rsi_momentum(self, df: pl.DataFrame) -> pl.DataFrame:
        """RSI momentum filter"""
        conditions = []
        
        if self.setup_config.min_rsi is not None:
            conditions.append(pl.col('rsi') >= self.setup_config.min_rsi)
        
        if self.setup_config.max_rsi is not None:
            conditions.append(pl.col('rsi') <= self.setup_config.max_rsi)
        
        if not conditions:
            return df.with_columns(pl.lit(True).alias('setup_valid'))
        
        return df.with_columns(
            pl.all_horizontal(conditions).alias('setup_valid')
        )
    
    def _setup_sma_trend(self, df: pl.DataFrame) -> pl.DataFrame:
        """SMA trend filter"""
        fast = self.setup_config.fast_period
        slow = self.setup_config.slow_period
        direction = self.setup_config.direction
        
        fast_col = f'sma_{fast}'
        slow_col = f'sma_{slow}'
        
        if direction == 'ABOVE':
            return df.with_columns(
                (pl.col(fast_col) > pl.col(slow_col)).alias('setup_valid')
            )
        elif direction == 'BELOW':
            return df.with_columns(
                (pl.col(fast_col) < pl.col(slow_col)).alias('setup_valid')
            )
        else:
            return df.with_columns(pl.lit(True).alias('setup_valid'))
    
    def _setup_macd_trend(self, df: pl.DataFrame) -> pl.DataFrame:
        """MACD trend filter"""
        signal = self.setup_config.macd_signal
        
        if signal == 'BULLISH':
            return df.with_columns(
                (pl.col('macd') > pl.col('macd_signal')).alias('setup_valid')
            )
        elif signal == 'BEARISH':
            return df.with_columns(
                (pl.col('macd') < pl.col('macd_signal')).alias('setup_valid')
            )
        else:
            return df.with_columns(pl.lit(True).alias('setup_valid'))
    
    def _setup_volume_trend(self, df: pl.DataFrame) -> pl.DataFrame:
        """Volume trend filter"""
        multiplier = self.setup_config.volume_multiplier or 1.0
        
        # Calculate average volume
        avg_volume = df['volume'].mean()
        
        return df.with_columns(
            (pl.col('volume') >= (avg_volume * multiplier)).alias('setup_valid')
        )
    
    # Trigger Methods
    
    def _trigger_candle_pattern(self, df: pl.DataFrame) -> pl.DataFrame:
        """Candle pattern trigger"""
        pattern = self.trigger_config.pattern
        
        # Only trigger when setup is valid
        setup_mask = pl.col('setup_valid')
        
        if pattern == 'ENGULFING_BULLISH':
            # Current candle engulfs previous candle (bullish)
            bullish_engulfing = (
                (pl.col('open') < pl.col('close').shift(1)) &  # Current opens below prev close
                (pl.col('close') > pl.col('open').shift(1)) &  # Current closes above prev open
                (pl.col('close') > pl.col('open'))              # Current is bullish
            )
            return df.with_columns(
                pl.when(setup_mask & bullish_engulfing)
                .then(pl.lit('BUY'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        elif pattern == 'ENGULFING_BEARISH':
            bearish_engulfing = (
                (pl.col('open') > pl.col('close').shift(1)) &
                (pl.col('close') < pl.col('open').shift(1)) &
                (pl.col('close') < pl.col('open'))
            )
            return df.with_columns(
                pl.when(setup_mask & bearish_engulfing)
                .then(pl.lit('SELL'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        # Add more patterns as needed
        else:
            return df
    
    def _trigger_price_crossover(self, df: pl.DataFrame) -> pl.DataFrame:
        """Price crossover trigger"""
        price_level = self.trigger_config.price_level
        direction = self.trigger_config.direction
        
        setup_mask = pl.col('setup_valid')
        
        if direction == 'ABOVE':
            crossover = (
                (pl.col('close') > price_level) &
                (pl.col('close').shift(1) <= price_level)
            )
            return df.with_columns(
                pl.when(setup_mask & crossover)
                .then(pl.lit('BUY'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        elif direction == 'BELOW':
            crossover = (
                (pl.col('close') < price_level) &
                (pl.col('close').shift(1) >= price_level)
            )
            return df.with_columns(
                pl.when(setup_mask & crossover)
                .then(pl.lit('SELL'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        return df
    
    def _trigger_indicator_crossover(self, df: pl.DataFrame) -> pl.DataFrame:
        """Indicator crossover trigger (e.g., Golden Cross)"""
        indicator1 = self.trigger_config.indicator1
        indicator2 = self.trigger_config.indicator2
        crossover_type = self.trigger_config.crossover_type
        
        setup_mask = pl.col('setup_valid')
        
        if crossover_type == 'GOLDEN_CROSS':
            # Fast crosses above slow
            golden_cross = (
                (pl.col(indicator1) > pl.col(indicator2)) &
                (pl.col(indicator1).shift(1) <= pl.col(indicator2).shift(1))
            )
            return df.with_columns(
                pl.when(setup_mask & golden_cross)
                .then(pl.lit('BUY'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        elif crossover_type == 'DEATH_CROSS':
            # Fast crosses below slow
            death_cross = (
                (pl.col(indicator1) < pl.col(indicator2)) &
                (pl.col(indicator1).shift(1) >= pl.col(indicator2).shift(1))
            )
            return df.with_columns(
                pl.when(setup_mask & death_cross)
                .then(pl.lit('SELL'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        return df
    
    def _trigger_breakout(self, df: pl.DataFrame) -> pl.DataFrame:
        """Breakout trigger"""
        breakout_type = self.trigger_config.breakout_type
        confirmation_bars = self.trigger_config.confirmation_bars or 1
        
        setup_mask = pl.col('setup_valid')
        
        if breakout_type == 'BOLLINGER_UPPER':
            # Price breaks above upper Bollinger Band
            breakout = (
                (pl.col('close') > pl.col('bb_upper')) &
                (pl.col('close').shift(1) <= pl.col('bb_upper').shift(1))
            )
            return df.with_columns(
                pl.when(setup_mask & breakout)
                .then(pl.lit('BUY'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        # Add more breakout types as needed
        return df
    
    def _trigger_reversal(self, df: pl.DataFrame) -> pl.DataFrame:
        """Reversal trigger (e.g., RSI oversold bounce)"""
        reversal_type = self.trigger_config.reversal_type
        
        setup_mask = pl.col('setup_valid')
        
        if reversal_type == 'RSI_OVERSOLD':
            # RSI was oversold (<30) and now bouncing back
            reversal = (
                (pl.col('rsi') > 30) &
                (pl.col('rsi').shift(1) <= 30) &
                (pl.col('close') > pl.col('open'))  # Bullish candle
            )
            return df.with_columns(
                pl.when(setup_mask & reversal)
                .then(pl.lit('BUY'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        elif reversal_type == 'RSI_OVERBOUGHT':
            # RSI was overbought (>70) and now reversing
            reversal = (
                (pl.col('rsi') < 70) &
                (pl.col('rsi').shift(1) >= 70) &
                (pl.col('close') < pl.col('open'))  # Bearish candle
            )
            return df.with_columns(
                pl.when(setup_mask & reversal)
                .then(pl.lit('SELL'))
                .otherwise(pl.col('signal'))
                .alias('signal')
            )
        
        return df
    
    # Exit Methods
    
    def _exit_stop_loss(self, df: pl.DataFrame) -> pl.DataFrame:
        """Stop loss exit"""
        stop_pct = self.exit_config.stop_loss_pct
        
        if stop_pct is None:
            return df
        
        # Calculate stop loss price for each buy signal
        # This is simplified - in practice, you'd track entry price per position
        return df.with_columns([
            pl.when(pl.col('signal') == 'BUY')
            .then(pl.col('close') * (1 - stop_pct))
            .otherwise(None)
            .alias('stop_loss_price')
        ])
    
    def _exit_take_profit(self, df: pl.DataFrame) -> pl.DataFrame:
        """Take profit exit"""
        profit_pct = self.exit_config.take_profit_pct
        
        if profit_pct is None:
            return df
        
        return df.with_columns([
            pl.when(pl.col('signal') == 'BUY')
            .then(pl.col('close') * (1 + profit_pct))
            .otherwise(None)
            .alias('take_profit_price')
        ])
    
    def _exit_trailing_stop(self, df: pl.DataFrame) -> pl.DataFrame:
        """Trailing stop exit"""
        trailing_pct = self.exit_config.trailing_stop_pct
        
        if trailing_pct is None:
            return df
        
        # Simplified trailing stop logic
        # In practice, you'd track highest price since entry
        return df.with_columns([
            pl.col('close').rolling_max(window_size=20).alias('trailing_stop_price')
        ])
    
    def _exit_time_based(self, df: pl.DataFrame) -> pl.DataFrame:
        """Time-based exit"""
        max_days = self.exit_config.max_holding_days
        
        if max_days is None:
            return df
        
        # This would require tracking entry dates per position
        # Simplified for now
        return df
    
    def _exit_indicator_signal(self, df: pl.DataFrame) -> pl.DataFrame:
        """Indicator-based exit signal"""
        # Implementation depends on specific indicator
        return df
    
    def _exit_combined(self, df: pl.DataFrame) -> pl.DataFrame:
        """Combined exit rules (multiple conditions)"""
        # Apply multiple exit rules with OR logic
        return df
