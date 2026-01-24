"""
Vegas Channel Strategy

A multi-timeframe trend-following strategy based on EMA relationships and velocity.
Adapted from the prefect_medallion implementation to work with the BaseStrategy framework.

Key Concepts:
- Velocity: Price momentum relative to EMAs (maintained/weak/loss)
- Momentum: Acceleration/deceleration patterns
- EMA Touch: Support/resistance levels at key EMAs

The strategy uses:
- Micro intervals (<=5): Focus on acceleration and accumulation
- Macro intervals (>=8): Focus on velocity maintenance
"""

import polars as pl
from typing import Optional
from ..base import BaseStrategy
from ...indicators.technicals import calculate_ema


class VegasChannelStrategy(BaseStrategy):
    """
    Vegas Channel Strategy
    
    Setup: Velocity maintained (macro) OR momentum accelerating (micro)
    Trigger: EMA touch at support OR momentum acceleration confirmed
    Exit: Velocity loss OR momentum deceleration
    
    Required EMAs: 8, 13, 21, 55, 89, 144, 169
    """
    
    def __init__(self, rolling_window: int = 50):
        """
        Initialize Vegas Channel Strategy
        
        Args:
            rolling_window: Rolling window for velocity status calculations (default: 50)
        """
        super().__init__(
            name="Vegas Channel",
            description="Multi-timeframe EMA-based trend following strategy"
        )
        self.rolling_window = rolling_window
        self.required_emas = [8, 13, 21, 55, 89, 144, 169]
    
    def _ensure_emas(self, df: pl.DataFrame) -> pl.DataFrame:
        """Ensure all required EMAs are calculated"""
        for period in self.required_emas:
            ema_col = f'ema_{period}'
            if ema_col not in df.columns:
                df = calculate_ema(df, period=period)
        return df
    
    def _calculate_velocity_status(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Calculate velocity status based on price and EMA relationships
        
        Returns DataFrame with 'velocity_status' column:
        - 'velocity_maintained': Strong uptrend
        - 'velocity_weak': Weakening trend
        - 'velocity_loss': Trend broken
        - 'velocity_negotiating': Neutral/transitioning
        """
        return df.with_columns([
            pl.when(
                (pl.col("close") > pl.col("open")) & 
                (pl.col("close") > pl.max_horizontal("ema_8", "ema_13")) & 
                (pl.col("close") > pl.max_horizontal("ema_144", "ema_169")) &
                (pl.min_horizontal("ema_8", "ema_13") > pl.max_horizontal("ema_144", "ema_169"))
            ).then(pl.lit("velocity_maintained"))
            .when(
                (pl.col("close") < pl.col("ema_13")) & 
                (pl.col("close") > pl.col("ema_169"))
            ).then(pl.lit("velocity_weak"))
            .when(
                (pl.col("close") < pl.col("ema_13")) & 
                (pl.col("close") < pl.col("ema_169"))
            ).then(pl.lit("velocity_loss"))
            .otherwise(pl.lit("velocity_negotiating"))
            .alias("velocity_status")
        ])
    
    def _calculate_momentum_signal(self, df: pl.DataFrame, interval: Optional[int] = None) -> pl.DataFrame:
        """
        Calculate momentum acceleration/deceleration signals
        
        Args:
            df: DataFrame with velocity_status already calculated
            interval: Optional interval for window sizing (uses default if None)
            
        Returns DataFrame with 'momentum_signal' column:
        - 'accelerated': Momentum is accelerating
        - 'decelerated': Momentum is decelerating
        - None: No clear signal
        """
        # Window size based on interval (if provided)
        window_dict = {
            1: 28, 3: 20, 5: 20, 8: 14, 13: 14
        }
        obs_window = window_dict.get(interval, 20) if interval else 20
        
        # Calculate velocity status first
        df = self._calculate_velocity_status(df)
        
        # Count velocity statuses in observation window
        df = df.with_columns([
            pl.col("velocity_status").map_elements(
                lambda s: 1 if s in ["velocity_loss", "velocity_weak", "velocity_negotiating"] else 0,
                return_dtype=pl.Int32
            ).alias("loss_flag"),
            pl.col("velocity_status").map_elements(
                lambda s: 1 if s == "velocity_maintained" else 0,
                return_dtype=pl.Int32
            ).alias("maintain_flag")
        ])
        
        df = df.with_columns([
            pl.col("loss_flag").rolling_sum(window_size=obs_window).alias("count_velocity_loss"),
            pl.col("maintain_flag").rolling_sum(window_size=obs_window).alias("count_velocity_maintained")
        ])
        
        # Add acceleration/deceleration signals
        return df.with_columns([
            pl.when(
                (pl.max_horizontal("ema_144", "ema_169") <= pl.max_horizontal("ema_8", "ema_13")) &
                (pl.col("open") < pl.col("close")) &
                (pl.col("count_velocity_loss") > pl.col("count_velocity_maintained"))
            ).then(pl.lit("accelerated"))
            .when(
                (pl.col("close") < pl.min_horizontal("ema_8", "ema_13")) &
                (pl.col("count_velocity_maintained") < pl.col("count_velocity_loss"))
            ).then(pl.lit("decelerated"))
            .otherwise(None)
            .alias("momentum_signal")
        ])
    
    def _detect_ema_touch(self, df: pl.DataFrame, interval: Optional[int] = None) -> pl.DataFrame:
        """
        Detect when price touches key EMAs (support/resistance)
        
        Args:
            df: DataFrame with EMAs calculated
            interval: Optional interval for tolerance calculation
            
        Returns DataFrame with 'ema_touch_type' column:
        - 'support': Price touched EMA from above (bullish)
        - 'resistance': Price touched EMA from below (bearish)
        - 'neutral': Touch detected but unclear direction
        - None: No touch detected
        """
        tolerance_dict = {
            1: 0.002, 3: 0.02, 5: 0.05, 8: 0.07, 13: 0.1
        }
        tolerance = tolerance_dict.get(interval, 0.02) if interval else 0.02
        
        # Calculate EMA bands
        df = df.with_columns([
            pl.min_horizontal(
                pl.col("ema_144"), pl.col("ema_169")
            ).fill_null(pl.col("ema_13")).alias("long_term_min"),
            
            pl.max_horizontal(
                pl.col("ema_144"), pl.col("ema_169")
            ).fill_null(pl.col("ema_13")).alias("long_term_max"),
            
            pl.min_horizontal(
                pl.col("ema_8"), pl.col("ema_13")
            ).alias("short_term_min"),
            
            pl.max_horizontal(
                pl.col("ema_8"), pl.col("ema_13")
            ).alias("short_term_max")
        ])
        
        # Calculate tolerance bands
        df = df.with_columns([
            (pl.col("long_term_min") * (1 - tolerance)).alias("lower_bound"),
            (pl.col("long_term_max") * (1 + tolerance)).alias("upper_bound")
        ])
        
        # Detect touches
        return df.with_columns([
            pl.when(
                ((pl.col("low") <= pl.col("upper_bound")) & (pl.col("low") >= pl.col("lower_bound"))) |
                ((pl.col("ema_13") <= pl.col("upper_bound")) & (pl.col("ema_13") >= pl.col("lower_bound"))) |
                ((pl.col("ema_8") <= pl.col("upper_bound")) & (pl.col("ema_8") >= pl.col("lower_bound")))
            ).then(
                pl.when(
                    (pl.col("short_term_min") > pl.col("long_term_max")) &
                    (pl.min_horizontal(pl.col("close"), pl.col("open")) > pl.col("long_term_min"))
                ).then(pl.lit("support"))
                .when(
                    (pl.col("short_term_max") < pl.col("long_term_max")) &
                    (pl.col("close") < pl.col("long_term_max"))
                ).then(pl.lit("resistance"))
                .otherwise(pl.lit("neutral"))
            ).otherwise(None)
            .alias("ema_touch_type")
        ])
    
    def setup(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Setup: Validate trend environment
        
        For micro intervals (<=5): Momentum must be accelerating
        For macro intervals (>=8): Velocity must be maintained
        
        Adds 'setup_valid' boolean column
        """
        # Ensure EMAs are calculated
        df = self._ensure_emas(df)
        
        # Get interval if available (for multi-timeframe support)
        interval = None
        if 'interval' in df.columns:
            # Use the most common interval if multiple exist
            interval_counts = df['interval'].value_counts()
            if interval_counts.height > 0:
                interval = interval_counts['interval'][0]
        
        # Calculate velocity and momentum
        df = self._calculate_momentum_signal(df, interval=interval)
        
        # Determine if interval is micro (<=5) or macro (>=8)
        is_micro = interval is not None and interval <= 5
        is_macro = interval is not None and interval >= 8
        
        # Setup conditions
        if is_micro:
            # Micro: Momentum must be accelerating
            setup_condition = pl.col("momentum_signal") == "accelerated"
        elif is_macro:
            # Macro: Velocity must be maintained
            df = self._calculate_velocity_status(df)
            setup_condition = pl.col("velocity_status") == "velocity_maintained"
        else:
            # Default: Either condition works
            df = self._calculate_velocity_status(df)
            setup_condition = (
                (pl.col("momentum_signal") == "accelerated") |
                (pl.col("velocity_status") == "velocity_maintained")
            )
        
        return df.with_columns(
            setup_condition.alias('setup_valid')
        )
    
    def trigger(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Trigger: Entry signal detection
        
        For micro intervals: Momentum acceleration confirmed
        For macro intervals: EMA touch at support (accumulation)
        
        Adds 'signal' column with 'BUY', 'SELL', or 'HOLD'
        """
        # Get interval if available
        interval = None
        if 'interval' in df.columns:
            interval_counts = df['interval'].value_counts()
            if interval_counts.height > 0:
                interval = interval_counts['interval'][0]
        
        # Detect EMA touches
        df = self._detect_ema_touch(df, interval=interval)
        
        # Ensure momentum signal is calculated
        if 'momentum_signal' not in df.columns:
            df = self._calculate_momentum_signal(df, interval=interval)
        
        is_micro = interval is not None and interval <= 5
        is_macro = interval is not None and interval >= 8
        
        # Trigger conditions
        if is_micro:
            # Micro: Momentum acceleration + green candle
            trigger_condition = (
                (pl.col("momentum_signal") == "accelerated") &
                (pl.col("open") < pl.col("close"))
            )
        elif is_macro:
            # Macro: EMA touch at support (long accumulation)
            trigger_condition = (
                (pl.col("ema_touch_type") == "support") &
                (pl.col("velocity_status") == "velocity_maintained")
            )
        else:
            # Default: Either condition
            if 'velocity_status' not in df.columns:
                df = self._calculate_velocity_status(df)
            
            trigger_condition = (
                ((pl.col("momentum_signal") == "accelerated") & (pl.col("open") < pl.col("close"))) |
                ((pl.col("ema_touch_type") == "support") & (pl.col("velocity_status") == "velocity_maintained"))
            )
        
        return df.with_columns(
            pl.when(pl.col('setup_valid') & trigger_condition)
            .then(pl.lit('BUY'))
            .otherwise(pl.lit('HOLD'))
            .alias('signal')
        )
    
    def exit(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Exit: Position management
        
        Exit on:
        - Velocity loss (macro intervals)
        - Momentum deceleration (micro intervals)
        - Stop loss: 5% below entry
        
        Adds 'exit_signal' and 'stop_loss_price' columns
        """
        # Get interval if available
        interval = None
        if 'interval' in df.columns:
            interval_counts = df['interval'].value_counts()
            if interval_counts.height > 0:
                interval = interval_counts['interval'][0]
        
        # Ensure velocity status is calculated
        if 'velocity_status' not in df.columns:
            df = self._calculate_velocity_status(df)
        
        # Ensure momentum signal is calculated
        if 'momentum_signal' not in df.columns:
            df = self._calculate_momentum_signal(df, interval=interval)
        
        is_micro = interval is not None and interval <= 5
        is_macro = interval is not None and interval >= 8
        
        # Exit conditions
        if is_micro:
            # Micro: Momentum deceleration
            exit_condition = pl.col("momentum_signal") == "decelerated"
        elif is_macro:
            # Macro: Velocity loss
            exit_condition = (
                (pl.col("velocity_status") == "velocity_loss") |
                (pl.col("velocity_status") == "velocity_weak")
            )
        else:
            # Default: Either condition
            exit_condition = (
                (pl.col("momentum_signal") == "decelerated") |
                (pl.col("velocity_status") == "velocity_loss") |
                (pl.col("velocity_status") == "velocity_weak")
            )
        
        # Stop loss: 5% below entry price
        stop_loss = pl.col('close') * 0.95
        
        return df.with_columns([
            pl.when(exit_condition)
            .then(pl.lit('SELL'))
            .otherwise(None)
            .alias('exit_signal'),
            stop_loss.alias('stop_loss_price'),
        ])
