import polars as pl
from typing import List

class TrendAlertProcessor:
    """
    TrendAlertProcessor using Polars for efficient processing of financial time series data.
    Incorporates advanced trend detection algorithms from the polars-based implementation.
    """
    def __init__(self, df: pl.DataFrame, rolling_window: int = 50):
        self.df = df
        self.intervals = self.df["interval"].unique().to_list()
        self.rolling_window = rolling_window
    
    def _add_velocity_alert(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Add velocity alerts based on the relationship between price and various EMAs.
        Similar to velocity_alert_dict in the original implementation.
        """
        # Add velocity status
        df = df.with_columns([
            pl.when(
                (pl.col("close") > pl.col("open")) & 
                (pl.col("close") > pl.max_horizontal("EMA_8", "EMA_13")) & 
                (pl.col("close") > pl.max_horizontal("EMA_144", "EMA_169")) &
                (pl.min_horizontal("EMA_8", "EMA_13") > pl.max_horizontal("EMA_144", "EMA_169"))
            ).then(pl.lit("velocity_maintained"))
            .when(
                (pl.col("close") < pl.col("EMA_13")) & 
                (pl.col("close") > pl.col("EMA_169"))
            ).then(pl.lit("velocity_weak"))
            .when(
                (pl.col("close") < pl.col("EMA_13")) & 
                (pl.col("close") < pl.col("EMA_169"))
            ).then(pl.lit("velocity_loss"))
            .otherwise(pl.lit("velocity_negotiating"))
            .alias("velocity_status")
        ])
        
        return df
    
    def _add_accel_decel_alert(self, df: pl.DataFrame, interval: int) -> pl.DataFrame:
        """
        Add acceleration/deceleration alerts based on EMA relationships and velocity status history.
        """
        window_dict = {
            1: 28, 3: 20, 5: 20, 8: 14, 13: 14
        }
        obs_window = window_dict.get(interval, 7)
        
        # First get velocity status
        df = self._add_velocity_alert(df)
        
        # Count velocity statuses in the observation window
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
        df = df.with_columns([
            pl.when(
                (pl.max_horizontal("EMA_144", "EMA_169") <= pl.max_horizontal("EMA_8", "EMA_13")) &
                (pl.col("open") < pl.col("close")) &
                (pl.col("count_velocity_loss") > pl.col("count_velocity_maintained"))
            ).then(pl.lit("accelerated"))
            .when(
                (pl.col("close") < pl.min_horizontal("EMA_8", "EMA_13")) &
                (pl.col("count_velocity_maintained") < pl.col("count_velocity_loss"))
            ).then(pl.lit("decelerated"))
            .otherwise(None).alias("momentum_signal")
        ])
        
        # Create alert
        momentum_alerts = df.filter(pl.col("momentum_signal").is_not_null())
        momentum_alerts = momentum_alerts.with_columns([
            pl.lit("momentum_alert").alias("alert_type"),
            pl.col("momentum_signal").alias("signal"),
            pl.lit(interval).alias("interval")
        ])
        
        return momentum_alerts.select("symbol", "date", "interval", "alert_type", "signal")
    
    def _add_ema_touch_alert(self, df: pl.DataFrame, interval: int) -> pl.DataFrame:
        """
        Add alerts for when price touches or comes close to important EMAs.
        """
        tolerance_dict = {
            1: 0.002, 3: 0.02, 5: 0.05, 8: 0.07, 13: 0.1
        }
        tolerance = tolerance_dict.get(interval, 0.02)
        
        # Calculate tolerance bands around EMAs
        df = df.with_columns([
            pl.min_horizontal(
                pl.col("EMA_144"), pl.col("EMA_169")
            ).fill_null(pl.col("EMA_13")).alias("long_term_min"),
            
            pl.max_horizontal(
                pl.col("EMA_144"), pl.col("EMA_169")
            ).fill_null(pl.col("EMA_13")).alias("long_term_max"),
            
            pl.min_horizontal(
                pl.col("EMA_8"), pl.col("EMA_13")
            ).alias("short_term_min"),
            
            pl.max_horizontal(
                pl.col("EMA_8"), pl.col("EMA_13")
            ).alias("short_term_max")
        ])
        
        # Calculate tolerance bands
        df = df.with_columns([
            (pl.col("long_term_min") * (1 - tolerance)).alias("lower_bound"),
            (pl.col("long_term_max") * (1 + tolerance)).alias("upper_bound")
        ])
        
        # Detect touches
        df = df.with_columns([
            pl.when(
                ((pl.col("low") <= pl.col("upper_bound")) & (pl.col("low") >= pl.col("lower_bound"))) |
                ((pl.col("EMA_13") <= pl.col("upper_bound")) & (pl.col("EMA_13") >= pl.col("lower_bound"))) |
                ((pl.col("EMA_8") <= pl.col("upper_bound")) & (pl.col("EMA_8") >= pl.col("lower_bound")))
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
            ).otherwise(None).alias("ema_touch_type")
        ])
        
        # Filter for touches and create alert
        ema_touch_alerts = df.filter(pl.col("ema_touch_type").is_not_null())
        ema_touch_alerts = ema_touch_alerts.with_columns([
            pl.lit("ema_touch").alias("alert_type"),
            pl.col("ema_touch_type").alias("signal"),
            pl.lit(interval).alias("interval")
        ])
        
        return ema_touch_alerts.select("symbol", "date", "interval", "alert_type", "signal")
    
    def apply(self) -> pl.DataFrame:
        """
        Apply all alert detection algorithms and return a combined DataFrame of alerts.
        """
        all_alerts = []
        
        for interval in self.intervals:
            # Filter the dataframe for the current interval and last 5 years data
            temp_df = self.df.filter(pl.col("interval") == interval).filter(
                pl.col("date") > pl.col("date").max() - pl.duration(days=5 * 365)
            )
            
            # No empty DataFrames
            if temp_df.height == 0:
                continue
                
            # Add velocity alerts
            velocity_df = self._add_velocity_alert(temp_df)
            velocity_alerts = velocity_df.with_columns([
                pl.lit("velocity_alert").alias("alert_type"),
                pl.col("velocity_status").alias("signal"),
                pl.lit(interval).alias("interval")
            ]).select("symbol", "date", "interval", "alert_type", "signal")
            
            # Add momentum alerts
            momentum_alerts = self._add_accel_decel_alert(temp_df, interval)
            
            # Add EMA touch alerts
            ema_touch_alerts = self._add_ema_touch_alert(temp_df, interval)
            
            # Combine all alerts for this interval
            all_alerts.extend([
                velocity_alerts,
                momentum_alerts,
                ema_touch_alerts
            ])
        
        # Combine all alerts into a single DataFrame
        if all_alerts:
            return pl.concat(all_alerts).sort(["symbol", "interval", "date"])
        else:
            # Return empty DataFrame with correct schema if no alerts
            return pl.DataFrame({
                "symbol": [],
                "date": [],
                "interval": [],
                "alert_type": [],
                "signal": []
            })