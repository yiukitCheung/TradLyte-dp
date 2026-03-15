from dotenv import load_dotenv
import os
load_dotenv()  # Load environment variables first

from prefect import flow, task
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetch.batch import BatchDataExtractor
from ingest.batch import StockDataIngestor
from fetch.meta import MetaDataExtractor
from ingest.meta import StockMetaIngestor
from config.load_setting import load_setting
from tools.utils import DateTimeTools
from tools.polygon_client import PolygonTools
import logging

from datetime import datetime
from zoneinfo import ZoneInfo
import time
from datetime import timedelta
logger = logging.getLogger(__name__)



@flow(name="bronze-pipeline")
def bronze_pipeline(settings=None):
    """Main pipeline flow — runs after market close if today is a trading day"""
    
    # Load settings if not provided
    if settings is None:
        settings = load_setting()
    
    # Get the market status
    polygon_tools = PolygonTools(os.getenv("POLYGON_API_KEY"))
    market_status = polygon_tools.get_market_status()
    
    # Check if today is a trading day
    if not DateTimeTools.is_trading_day():
        logger.info(f"Today is not a trading day, skipping pipeline")
        return

    # Skip market open check — we WANT to run after market closes
    
    try:
        # Start both tasks concurrently
        batch_extractor_future = run_batch_extractor.submit(settings)
        batch_ingestor_future = run_batch_ingestor.submit(settings)
        
        # Wait for both tasks to complete
        batch_extractor_result = batch_extractor_future.result()
        batch_ingestor_result = batch_ingestor_future.result()
        
        # Check results after both tasks complete
        if batch_extractor_future.state.is_failed():
            raise Exception("Batch extractor failed")
        if batch_ingestor_future.state.is_failed():
            raise Exception("Batch ingestor failed")
                
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        raise
    
@flow(name="reset-mode-pipeline")
def reset_mode_pipeline(settings):
    """Reset mode pipeline"""
    try:
        # # Step 1. Run meta extractor and ingestor in parallel
        # meta_ingestor_future = run_meta_ingestor.submit(settings)
        # meta_extractor_future = run_meta_extractor.submit(settings)
        
        # # Wait for both meta tasks to complete
        # meta_extractor_result = meta_extractor_future.result()
        # meta_ingestor_result = meta_ingestor_future.result()
        
        # if meta_extractor_future.state.is_failed():
        #     raise Exception("Meta extractor failed")
        # if meta_ingestor_future.state.is_failed():
        #     raise Exception("Meta ingestor failed")
        
        # Step 2. Run batch extractor and ingestor in parallel
        batch_ingestor_future = run_batch_ingestor.submit(settings)
        batch_extractor_future = run_batch_extractor.submit(settings)

        # Wait for both batch tasks to complete
        batch_extractor_result = batch_extractor_future.result()
        batch_ingestor_result = batch_ingestor_future.result()
        
        if batch_extractor_future.state.is_failed():
            raise Exception("Batch extractor failed")
        if batch_ingestor_future.state.is_failed():
            raise Exception("Batch ingestor failed")
            
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        raise
        
@task
def run_batch_extractor(settings):
    batch_extractor = BatchDataExtractor(settings)
    batch_extractor.run()
    
@task
def run_batch_ingestor(settings):
    ingestor = StockDataIngestor(settings)
    ingestor.run()

@task
def run_meta_extractor(settings):
    meta_extractor = MetaDataExtractor(settings)
    meta_extractor.run()

@task
def run_meta_ingestor(settings):
    meta_ingestor = StockMetaIngestor(settings)
    meta_ingestor.run()

if __name__ == "__main__":
    settings = load_setting()
    reset_mode_pipeline(settings)