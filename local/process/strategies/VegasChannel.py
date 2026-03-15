import polars as pl 

class VegasChannelStrategy:
    """
    A class to process and analyze stock candidates using Polars for efficient data processing.
    Analyzes stocks based on various criteria and alerts from different time intervals.
    """
    
    def __init__(self, df: pl.DataFrame):
        self.df = df
        self.interval_weights = None
        self._initialize_weights()
        
    def _initialize_weights(self):
        """Initialize interval weights based on unique intervals in the data."""
        distinct_intervals = self.df.get_column("interval").unique().sort()
        self.interval_weights = {interval: weight for weight, interval in enumerate(distinct_intervals, 1)}
        
    def _evaluate_micro_interval_stocks(self, data: pl.DataFrame) -> dict:
        """
        Evaluate stocks based on micro-interval criteria (intervals <= 5).
        Analyzes acceleration and accumulation patterns.
        """
        # Process momentum alerts
        momentum_data = data.filter(pl.col("alert_type") == "momentum_alert")
        momentum_results = momentum_data.group_by(["symbol", "interval"]).agg([
            pl.when(pl.col("signal") == "accelerated").then(1).otherwise(0).sum().alias("momentum_alert_accelerated"),
            pl.when(pl.col("signal") == "decelerated").then(1).otherwise(0).sum().alias("momentum_alert_decelerated")
        ])
        
        # Process EMA touch alerts
        ema_data = data.filter(pl.col("alert_type") == "ema_touch")
        ema_results = ema_data.group_by(["symbol", "interval"]).agg([
            pl.when(pl.col("signal") == "resistance").then(1).otherwise(0).sum().alias("touch_type_resistance"),
            pl.when(pl.col("signal") == "support").then(1).otherwise(0).sum().alias("touch_type_support"),
            pl.len().alias("count")
        ])
        
        # Join the results
        results = momentum_results.join(
            ema_results,
            on=["symbol", "interval"],
            how="full"
        ).fill_null(0)
        
        # Apply interval weighting
        results = results.with_columns([
            pl.col("interval").map_elements(lambda x: self.interval_weights.get(x, 0), return_dtype=pl.Int64).alias("interval_weight")
        ])
        
        # Calculate weighted values
        results = results.with_columns([
            (pl.col("momentum_alert_accelerated") * pl.col("interval_weight")).alias("weighted_momentum_alert_accelerated"),
            (pl.col("momentum_alert_decelerated") * pl.col("interval_weight")).alias("weighted_momentum_alert_decelerated"),
            (pl.col("touch_type_resistance") * pl.col("interval_weight")).alias("weighted_touch_type_resistance"),
            (pl.col("touch_type_support") * pl.col("interval_weight")).alias("weighted_touch_type_support")
        ])
        
        # Filter for accelerating stocks
        short_acc_equ = results.filter(
            (pl.col("weighted_momentum_alert_accelerated") > 1) &
            (pl.col("weighted_momentum_alert_decelerated") < 1) &
            (pl.col("interval") <= 3)
        ).get_column("symbol")
        
        lng_acc_equ = results.filter(
            (pl.col("weighted_momentum_alert_accelerated") > 1) &
            (pl.col("weighted_momentum_alert_decelerated") < 1) &
            (pl.col("interval") == 5)
        ).get_column("symbol")
        
        lng_main_acc_equ = results.filter(
            (pl.col("weighted_touch_type_support") > 1) &
            (pl.col("weighted_touch_type_resistance") < 1) &
            (pl.col("count") >= 1) &
            (pl.col("interval") == 5)
        ).get_column("symbol")
        
        return {
            "accelerating": short_acc_equ.to_list(),
            "long_accelerating": lng_acc_equ.to_list(),
            "long_accumulating": lng_main_acc_equ.to_list()
        }

    def _evaluate_macro_interval_stocks(self, data: pl.DataFrame) -> dict:
        """
        Evaluate stocks based on macro-interval criteria (intervals >= 8).
        Analyzes velocity maintenance patterns.
        """
        # Process velocity alerts
        velocity_data = data.filter(pl.col("alert_type") == "velocity_alert")
        results = velocity_data.group_by(["symbol", "interval"]).agg([
            pl.when(pl.col("signal") == "velocity_maintained").then(1).otherwise(0).sum().alias("velocity_maintained"),
            pl.when(pl.col("signal") == "velocity_weak").then(1).otherwise(0).sum().alias("velocity_weak"),
            pl.when(pl.col("signal") == "velocity_loss").then(1).otherwise(0).sum().alias("velocity_loss")
        ])
        
        # Apply interval weighting
        results = results.with_columns([
        pl.col("interval").map_elements(lambda x: self.interval_weights.get(x, 0), return_dtype=pl.Int64).alias("interval_weight")
        ])
        
        # Calculate weighted values
        results = results.with_columns([
            (pl.col("velocity_maintained") * pl.col("interval_weight")).alias("weighted_velocity_maintained"),
            (pl.col("velocity_weak") * pl.col("interval_weight")).alias("weighted_velocity_weak"),
            (pl.col("velocity_loss") * pl.col("interval_weight")).alias("weighted_velocity_loss")
        ])
        
        # Filter for maintained velocity stocks
        maintained_stocks = results.filter(
            (pl.col("weighted_velocity_maintained") > 0) &
            (pl.col("weighted_velocity_weak") == 0) &
            (pl.col("weighted_velocity_loss") == 0) &
            (pl.col("interval") >= 8)
        ).get_column("symbol")
        
        return {
            "velocity_maintained": maintained_stocks.to_list()
        }

    def process(self) -> pl.DataFrame:
        """
        Process the data and generate stock candidates for each date.
        Returns a DataFrame with the results.
        """
        # Group data by date
        grouped_data = self.df.group_by("date")
        
        # Process candidates for each date
        results = []
        
        for date, group in grouped_data:
            # Process micro-interval data
            micro_data = group.filter(pl.col("interval") <= 5)
            micro_results = self._evaluate_micro_interval_stocks(micro_data)
            
            # Process macro-interval data
            macro_data = group.filter(pl.col("interval") >= 8)
            macro_results = self._evaluate_macro_interval_stocks(macro_data)
            
            # Combine results
            combined_results = {**micro_results, **macro_results}
            combined_results["date"] = date
            
            results.append(combined_results)
        
        # Convert results to DataFrame
        return pl.DataFrame(results).sort(["date"], descending=True)