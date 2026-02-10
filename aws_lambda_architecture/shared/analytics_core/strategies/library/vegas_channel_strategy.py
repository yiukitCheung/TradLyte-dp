"""
Vegas Channel Strategy

A multi-timeframe trend-following strategy based on EMA relationships and velocity.
Adapted from the prefect_medallion implementation to work with the BaseStrategy framework.

Key Concepts:
- Velocity: Price momentum relative to EMAs (maintained/weak/loss)
- Momentum: Acceleration/deceleration patterns
- EMA Touch: Support/resistance levels at key EMAs

Requires at least 169 candles (longest EMA period). Interval-agnostic.
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
    
    def __init__(
        self,
        rolling_window: int = 50,
        obs_window: Optional[int] = None,
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
    ):
        """
        Initialize Vegas Channel Strategy

        Args:
            rolling_window: Rolling window for velocity status calculations (default: 50)
            obs_window: Observation window for maintain/loss counts in momentum signal.
                When set, used for count_velocity_loss and count_velocity_maintained.
                When None, uses 30 (interval-agnostic).
            stop_loss_pct: Stop loss percentage; used by backtester (default: 0.05 = 5%)
            take_profit_pct: Take profit percentage; used by backtester (default: 0.10 = 10%)
        """
        super().__init__(
            name="Vegas Channel",
            description="Multi-timeframe EMA-based trend following strategy"
        )
        self.rolling_window = rolling_window
        self.obs_window = obs_window
        self.required_emas = [8, 13, 144, 169]
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
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
                (pl.col("close") > pl.col("open")) & # Green candle
                (pl.col("close") > pl.max_horizontal("ema_8", "ema_13")) & # Close above EMA8 and EMA13
                (pl.col("close") > pl.max_horizontal("ema_144", "ema_169")) & # Close above EMA144 and EMA169
                (pl.min_horizontal("ema_8", "ema_13") > pl.max_horizontal("ema_144", "ema_169")) # EMA8 and EMA13 above EMA144 and EMA169
            ).then(pl.lit("velocity_maintained"))
            .when(
                (pl.col("close") < pl.col("ema_13")) & # Close below EMA13
                (pl.col("close") > pl.col("ema_169")) # Close above EMA169
            ).then(pl.lit("velocity_weak"))
            .when(
                (pl.col("close") < pl.col("ema_13")) & # Close below EMA13 and EMA169
                (pl.col("close") < pl.col("ema_169")) # Close below EMA169
            ).then(pl.lit("velocity_loss"))
            .otherwise(pl.lit("velocity_negotiating")) # Neutral/transitioning
            .alias("velocity_status")
        ])
    
    def _calculate_momentum_signal(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        First label each candle as maintain, loss, weak, neutral (velocity_status).
        Then count maintain vs loss in the last obs_window (default 30) candles.
        - Accelerated: loss count > maintain count in window AND
          lng_term_max <= short_term_max < open < close (green candle above short-term band).
        - Decelerated (sell): same count (loss > maintain) AND
          close < short_term_min <= lng_term_min (notebook sell logic). Cooldown: no decelerated in previous 30.
        Interval-agnostic: obs_window is self.obs_window or 30.
        """
        obs_window = self.obs_window if self.obs_window is not None else 30

        df = self._calculate_velocity_status(df)

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

        lng_term_max = pl.max_horizontal(pl.col("ema_144"), pl.col("ema_169"))
        lng_term_min = pl.min_horizontal(pl.col("ema_144"), pl.col("ema_169"))
        short_term_max = pl.max_horizontal(pl.col("ema_8"), pl.col("ema_13"))
        short_term_min = pl.min_horizontal(pl.col("ema_8"), pl.col("ema_13"))

        # Accelerated: loss > maintain in last obs_window AND lng_term_max <= short_term_max < open < close
        accel_candle = (lng_term_max <= short_term_max) & (short_term_max < pl.col("open")) & (pl.col("open") < pl.col("close"))
        # Decelerated: same count as accelerated (loss > maintain in window) per notebook;
        # candle: close < short_term_min <= lng_term_min (sell logic)
        decel_candle = (pl.col("close") < short_term_min) & (short_term_min <= lng_term_min)

        df = df.with_columns([
            pl.when(
                (pl.col("count_velocity_loss") > pl.col("count_velocity_maintained")) & accel_candle
            ).then(pl.lit("accelerated"))
            .when(
                (pl.col("count_velocity_loss") > pl.col("count_velocity_maintained")) & decel_candle
            ).then(pl.lit("decelerated"))
            .otherwise(None)
            .alias("momentum_signal")
        ])

        # Cooldown: only keep accelerated if no accelerated in previous 30 candles (alert lasts 30+ days)
        # Same for decelerated. Matches notebook: "accelerated not in previous_window" and allow_new_alert after 30.
        accel_flag = (pl.col("momentum_signal") == "accelerated").cast(pl.Int32)
        decel_flag = (pl.col("momentum_signal") == "decelerated").cast(pl.Int32)
        accel_in_prev_30 = accel_flag.shift(1).rolling_sum(window_size=30)
        decel_in_prev_30 = decel_flag.shift(1).rolling_sum(window_size=30)
        df = df.with_columns([
            pl.when(
                (pl.col("momentum_signal") == "accelerated") & (accel_in_prev_30 > 0)
            ).then(None)
            .when(
                (pl.col("momentum_signal") == "decelerated") & (decel_in_prev_30 > 0)
            ).then(None)
            .otherwise(pl.col("momentum_signal"))
            .alias("momentum_signal")
        ])
        return df

    
    MIN_CANDLES = 169  # Longest EMA period; need at least this many candles.

    def setup(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Setup: Validate trend environment.

        Requires at least 169 candles (longest EMA period). Setup valid when
        momentum is accelerating OR velocity is maintained (interval-agnostic).
        """
        if df.height < self.MIN_CANDLES:
            return df.with_columns(pl.lit(False).alias("setup_valid"))

        df = self._ensure_emas(df)
        df = self._calculate_momentum_signal(df)
        df = self._calculate_velocity_status(df)
        setup_condition = (
            (pl.col("momentum_signal") == "accelerated") |
            (pl.col("velocity_status") == "velocity_maintained")
        )
        return df.with_columns(setup_condition.alias("setup_valid"))
    
    def trigger(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Trigger: Entry signal detection (interval-agnostic).

        BUY when (momentum accelerated + green candle) OR (EMA touch at support + velocity maintained).
        Adds 'signal' column with 'BUY' or 'HOLD'.
        """
        if "momentum_signal" not in df.columns:
            df = self._calculate_momentum_signal(df)
        if "velocity_status" not in df.columns:
            df = self._calculate_velocity_status(df)
        part1 = (pl.col("momentum_signal") == "accelerated") & (pl.col("open") < pl.col("close"))
        part2 = (
            (pl.col("ema_touch_type") == "support") & (pl.col("velocity_status") == "velocity_maintained")
            if "ema_touch_type" in df.columns
            else pl.lit(False)
        )
        trigger_condition = part1 | part2
        return df.with_columns(
            pl.when(pl.col("setup_valid") & trigger_condition)
            .then(pl.lit("BUY"))
            .otherwise(pl.lit("HOLD"))
            .alias("signal")
        )
    
    def exit(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Exit: sell when velocity_loss appears.

        No deceleration logic. Adds 'exit_signal' and 'stop_loss_price' (5% below close).
        """
        if 'velocity_status' not in df.columns:
            df = self._calculate_velocity_status(df)
        exit_condition = pl.col("velocity_status") == "velocity_loss"
        if self.stop_loss_pct is not None:
            stop_loss = pl.col('close') * (1 - self.stop_loss_pct)
        else:
            stop_loss = None
        if self.take_profit_pct is not None:
            take_profit = pl.col('close') * (1 + self.take_profit_pct)
        else:
            take_profit = None
        return df.with_columns([
            pl.when(exit_condition).then(pl.lit('SELL')).otherwise(None).alias('exit_signal'),
            pl.when(stop_loss is not None).then(stop_loss).otherwise(None).alias('stop_loss_price'),
            pl.when(take_profit is not None).then(take_profit).otherwise(None).alias('take_profit_price'),
        ])
