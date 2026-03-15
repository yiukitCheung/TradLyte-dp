import polars as pl
import time
from .data_loader import DataLoader

class IndicatorCalculator:
    def __init__(self):
        pass
        
    def add_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        """Add technical indicators to the dataframe"""
        df = df.sort("date")
        
        # Compute EMAs
        df = df.with_columns([
            pl.col("close").ewm_mean(span=8).alias("EMA_8"),
            pl.col("close").ewm_mean(span=13).alias("EMA_13"),
            pl.col("close").ewm_mean(span=21).alias("EMA_21"),
            pl.col("close").ewm_mean(span=144).alias("EMA_144"),
            pl.col("close").ewm_mean(span=169).alias("EMA_169"),
            pl.col("close").ewm_mean(span=55).alias("EMA_55"),
            pl.col("close").ewm_mean(span=89).alias("EMA_89"),
        ])
        
        # Compute MACD
        df = df.with_columns([
            (pl.col("EMA_13") - pl.col("EMA_21")).alias("macd_fast"),
            (pl.col("EMA_55") - pl.col("EMA_89")).alias("macd_slow"),
        ])
        
        return df

    def apply(self, df: pl.DataFrame) -> pl.DataFrame:
        """Calculate indicators for each symbol and interval combination"""
        start_time = time.time()
        
        # Create a unique identifier for each symbol-interval combination
        df = df.with_columns([
            pl.concat_str([pl.col("symbol"), pl.col("interval")], separator="_").alias("group_id")
        ])
        
        # Add indicators grouped by the combined identifier
        df_with_indicators = df.group_by("group_id", maintain_order=True).map_groups(self.add_indicators)
        
        # Remove the temporary group_id column
        df_with_indicators = df_with_indicators.drop("group_id")
        
        # Sort the dataframe
        df_with_indicators = df_with_indicators.sort(["symbol", "interval", "date"])
        
        print(f"Indicator calculation time: {time.time() - start_time:.2f} seconds")
        return df_with_indicators

if __name__ == "__main__":
    try:
        # Initialize calculator and data loader
        indicator_calculator = IndicatorCalculator()
        
        # Use context manager for data loader
        with DataLoader() as loader:
            # Load silver data
            print("Loading silver data...")
            silver_data = loader.load_silver_data()
            
            # Calculate indicators
            print("Calculating indicators...")
            results = indicator_calculator.apply(silver_data)
            
            # Save to gold layer
            print("Saving to gold layer...")
            loader.save_gold_data(results)
            
            # Print summary
            print("\nResults summary:")
            print(f"Total rows: {len(results)}")
            print("\nFirst 5 rows of result:")
            print(results.head(5))
            
    except Exception as e:
        print(f"Error in main execution: {str(e)}")