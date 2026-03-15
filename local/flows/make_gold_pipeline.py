import polars as pl
from prefect import flow, task
from prefect import get_run_logger
from prefect.tasks import task_input_hash
from prefect.cache_policies import NO_CACHE
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# Load the settings
from config import load_setting
settings = load_setting()

from process.core import IndicatorCalculator, TrendAlertProcessor, DataLoader, CacheManager
from process import VegasChannelStrategy
from tools.redis_client import RedisTools

@flow(name="gold-pipeline")
def gold_pipeline(settings: dict) -> pl.DataFrame:
    """Make gold data from silver data"""
    # logger = get_run_logger()
    print("Starting make_gold flow")
    
    # Initialize data loader `1 `
    data_loader = DataLoader(settings)
    
    try:
        print("Loading silver data")
        df = load_silver(data_loader)
        
        print("Applying indicators")
        df = apply_indicators(df)
        
        print("Applying alert rules")
        df = apply_alert_rules(df)
        
        print("Saving gold data")
        data_loader.save_gold_data(df, table_name="gold")
        print("Gold data saved")
        
        print("Applying strategies")
        df = apply_strategies(df)
        
        print("Saving gold picks")
        data_loader.save_gold_data(df, table_name="gold_picks")
        print("Gold picks saved")
        
        # Close the data loader
        data_loader.close()
        
        print("Caching gold data")
        df = cache_gold(df)
        print("Gold data cached")
        
        return df
        
    finally:
        data_loader.close()

@task
def apply_indicators(df: pl.DataFrame) -> pl.DataFrame:
    """Apply indicators to the data"""
    indicator_calculator = IndicatorCalculator()
    df = indicator_calculator.apply(df)
    return df

@task
def apply_alert_rules(df: pl.DataFrame) -> pl.DataFrame:
    """Apply alert rules to the data"""
    trend_alert_processor = TrendAlertProcessor(df, rolling_window=50)
    df = trend_alert_processor.apply()
    return df

@task
def apply_strategies(df: pl.DataFrame) -> pl.DataFrame:
    """Apply strategies to the data"""
    vegas_channel_strategy = VegasChannelStrategy(df)
    df = vegas_channel_strategy.process()
    return df

@task(cache_policy=NO_CACHE)
def load_silver(data_loader: DataLoader) -> pl.DataFrame:
    """Load the silver data"""
    df = data_loader.load_silver_data()
    return df

@task(cache_policy=NO_CACHE)
def cache_gold(df: pl.DataFrame) -> pl.DataFrame:
    """Cache the gold data"""
    cache_manager = CacheManager()
    cache_manager.cache_latest_picks(df)
    return df

if __name__ == "__main__":
    print(settings)
    # Run the flow
    result = gold_pipeline(settings)
    print("\nPipeline completed successfully!")
    print(f"Processed {len(result)} rows")
