import json
import logging
import os
import sys
import time
import threading
from datetime import datetime
from functools import partial
import concurrent.futures
from curl_cffi import requests

import pandas as pd
import pytz
import yfinance as yf
from kafka import KafkaProducer
from prefect import get_run_logger
import pandas_market_calendars as mcal

# Add parent directory to path for local imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Local imports
from tools import postgres_client, kafka_client, polygon_client, utils
from config.load_setting import load_setting

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

POSTGRES_URL = os.getenv('POSTGRES_URL')
if not POSTGRES_URL:
    raise ValueError("POSTGRES_URL environment variable is not set. Please set it in your .env file.")
POLYGON_API_KEY = os.getenv('POLYGON_API_KEY')
KAFKA_BROKER = os.getenv('KAFKA_BROKER')

class BatchDataExtractor:
    
    def __init__(self, settings):
        # Extract configs
        self.mode = settings['mode']
        self.current_date = pd.to_datetime("today").strftime('%Y-%m-%d')
        self.topic_names = "1d"
        self.table_name = "raw"
        
        # Initialize core components
        self.timescale_tool = postgres_client.PostgresTools(POSTGRES_URL)
        self.polygon_tool = polygon_client.PolygonTools(POLYGON_API_KEY)
        self.kafka_tool = kafka_client.KafkaTools()
        self.logger = self.get_logger()
        
        # Initialize common components
        self.symbol_locks = {}
        self.session = None
        self.chunk_delay = settings['data_extract']['raw']['chunk_delay']
        self.max_workers = settings['data_extract']['raw']['max_workers']
        
        # Initialize browser-like session for yfinance
        self.yf_session = requests.Session(impersonate="chrome")
    
        # Initialize Kafka producer
        self._init_kafka_producer()
        
        # Mode-specific initialization
        if self.mode == "production":
            self._init_production_mode()
        elif self.mode == "reset":
            self._init_reset_mode()
        elif self.mode == "catch_up":
            self._init_catch_up_mode()
            
    def _init_kafka_producer(self):
        """Initialize Kafka producer with optimized settings"""
        try:
            self.batch_kafka_config = {
                "bootstrap_servers": KAFKA_BROKER,
                "value_serializer": lambda x: json.dumps(x).encode('utf-8')
            }
            self.batch_producer = KafkaProducer(**self.batch_kafka_config)
            self.logger.info(f"BatchDataExtractor: Kafka Producer connected to {KAFKA_BROKER}")
        except Exception as e:
            self.logger.error(f"BatchDataExtractor: Error initializing Kafka producer: {e}")
            raise e
            
    def _init_production_mode(self):
        """Initialize production mode specific components"""
        # Get market schedule for today
        nyse = mcal.get_calendar('NYSE')
        today = datetime.now(pytz.timezone('America/New_York')).strftime('%Y-%m-%d')
        schedule = nyse.schedule(start_date=today, end_date=today)
        
        if not schedule.empty:
            self.market_close = schedule.iloc[0]['market_close'].tz_convert('America/New_York')
            self.logger.info(f"BatchDataExtractor: Market close time is {self.market_close}")
        else:
            self.logger.info("BatchDataExtractor: No market schedule for today")
            self.market_close = None
            
        self.last_fetch_dict = self.get_last_fetch_dict()
        self.logger.info(f"BatchDataExtractor: Fetched latest dates for {len(self.last_fetch_dict)} symbols")
        
        self.symbols = self.timescale_tool.fetch_all_tickers()
        self.logger.info(f"BatchDataExtractor: Fetched {len(self.symbols)} stock symbols from timescale")
        
        self._init_kafka_producer()

    def _init_reset_mode(self):
        """Initialize reset mode specific components"""
        self.last_fetch_dict = self.get_last_fetch_dict()
        self.logger.info(f"BatchDataExtractor: Fetched latest dates for {len(self.last_fetch_dict)} symbols")
        
        self.symbols = self.timescale_tool.fetch_all_tickers()
        self.logger.info(f"BatchDataExtractor: Fetched {len(self.symbols)} stock symbols from timescale")
        
        self._init_kafka_producer()
        
    def _init_catch_up_mode(self):
        """Initialize catch up mode specific components"""
        self.last_fetch_dict = self.get_last_fetch_dict()
        self.logger.info(f"BatchDataExtractor: Fetched latest dates for {len(self.last_fetch_dict)} symbols")
        
        self.symbols = list(self.last_fetch_dict.keys())
        self.logger.info(f"BatchDataExtractor: Fetched {len(self.symbols)} stock symbols from timescale")

        self._init_kafka_producer()
    
    def get_logger(self):
        return get_run_logger()
    
    def get_last_fetch_dict(self):
        # Fetch the latest date from each collection
        df = self.timescale_tool.fetch_latest_date(self.table_name)
        
        # Convert DataFrame to dictionary with symbol as key and timestamp as value
        if not df.empty:
            return dict(zip(df['symbol'], df['timestamp']))
        return {}
    
    async def init_session(self):
        """Initialize aiohttp session"""
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()
            self.session = None

    def _get_symbol_data(self, symbol, table_name):
        """Synchronous version of _get_symbol_data for thread pool execution"""
        try:
            if symbol not in self.symbol_locks:
                self.symbol_locks[symbol] = threading.Lock()

            with self.symbol_locks[symbol]:
                latest_record = self.last_fetch_dict.get(symbol)
                if latest_record is None:
                    self.logger.info(f"BatchDataExtractor: No previous data for {symbol}, fetching all data")
                    # Use browser-like session for yfinance
                    ticker = yf.Ticker(symbol, session=self.yf_session)
                    data_df = ticker.history(period='max', interval='1d')
                    
                    if data_df.empty:
                        self.logger.warning(f"BatchDataExtractor: No data returned for {symbol}")
                        return None, symbol
                        
                    # Reset the index and standardize columns
                    data_df = data_df.reset_index()
                    data_df = data_df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
                    data_df.columns = ['t', 'o', 'h', 'l', 'c', 'v']
                    
                    data_json = json.loads(data_df.to_json(orient='records'))
                    return data_json, symbol
                        
                # Case 2: We have data but we are not sure if it's current
                elif latest_record:
                    # case 2.1: The last date in the database is today
                    last_date_in_db = pd.to_datetime(latest_record).strftime('%Y-%m-%d')
                    if last_date_in_db == self.current_date:
                        self.logger.info(f"BatchDataExtractor: Data for {symbol} is up to date")
                        return None, symbol
                    # case 2.2: The last date in the database is not today
                    elif last_date_in_db < self.current_date:
                        data_json = self.polygon_tool.fetch_ticker_ohlcv(ticker=symbol, start_date=last_date_in_db, end_date=self.current_date)
                        if data_json: 
                            self.logger.info(f"BatchDataExtractor: Fetched data for {symbol} from {last_date_in_db} to {self.current_date}")
                        else:
                            self.logger.info(f"BatchDataExtractor: No new data fetched for {symbol} from {last_date_in_db} to {self.current_date}")
                
                return data_json, symbol

        except Exception as e:
            self.logger.error(f"Error in fetch for {symbol}: {e}")
            return None, symbol

    def _process_symbol(self, symbol):
        """Process a single symbol: fetch data and produce to Kafka"""
        try:
            # Fetch data
            data, symbol = self._get_symbol_data(symbol, self.table_name)
            
            if data is not None and len(data) > 0:
                # Produce to Kafka
                self._produce_data_to_kafka(data, symbol)
                
        except Exception as e:
            self.logger.error(f"Error processing symbol {symbol}: {e}")
            raise

    def _produce_data_to_kafka(self, data, symbol: str):
        """Synchronous version of _produce_data_to_kafka with batch support"""
        if not data or len(data) == 0:
            return
        
        try:
            # Prepare all records for this symbol
            records = []
            for record in data:
                try:
                    # Convert timestamp to date string if needed
                    timestamp_str = pd.to_datetime(record['t'], unit='ms').strftime('%Y-%m-%d %H:%M:%S')
                    
                    # Create stock record for kafka
                    stock_record = {
                        'symbol': symbol,
                        'timestamp': timestamp_str,
                        'open': record['o'],
                        'high': record['h'],
                        'low': record['l'],
                        'close': record['c'],
                        'volume': int(record['v']),
                        'interval': '1d'
                    }
                    records.append(stock_record)
                except Exception as e:
                    self.logger.info(f"Error processing record for {symbol}: {e}")
                    continue
            
            if not records:
                return
                
            # Send all records for this symbol
            self._send_batch_to_kafka(records, symbol)
            
            self.logger.info(f"BatchDataExtractor: Successfully sent {len(records)} records for {symbol} to {self.topic_names}")
            
        except Exception as e:
            self.logger.info(f"BatchDataExtractor: Error in batch production for {symbol}: {e}")
    
    def _send_batch_to_kafka(self, records, symbol):
        """Helper method to send a batch of records to Kafka"""
        try:
            # Send all records for this symbol
            for record in records:
                self.batch_producer.send(
                    self.topic_names,
                    value=record,
                    key=symbol.encode('utf-8')  # Use symbol as key for partitioning
                )
            
            # Flush after all records for this symbol are sent
            self.batch_producer.flush()
            
        except Exception as e:
            self.logger.info(f"Error sending batch for {symbol}: {e}")
            raise

    def fetch_and_produce_batch_data(self):
        """Process all symbols in parallel using thread pool"""
        try:
            # Create a thread pool for processing
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all tasks
                futures = [executor.submit(self._process_symbol, symbol) for symbol in self.symbols]
                
                # Wait for all tasks to complete
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()  # This will raise any exceptions that occurred
                    except Exception as e:
                        self.logger.error(f"Task error: {e}")
                
            self.logger.info(f"BatchDataExtractor: Completed processing {len(self.symbols)} symbols")
        
        except Exception as e:
            self.logger.error(f"Error in batch processing: {e}")
            raise

    def run(self):
        """Entry point with proper cleanup"""
        try:
            if self.mode == "catch_up" or self.mode == "reset":
                self.fetch_and_produce_batch_data()
                return
            else:
                current_time = datetime.now(pytz.timezone('America/New_York'))
                if self.market_close and current_time > self.market_close:
                    self.logger.info("BatchExtractor: Market closed, fetching end of day data...")
                    self.fetch_and_produce_batch_data()
                    self.logger.info("BatchExtractor: Trading day complete, exiting...")
                    return
                else:
                    self.logger.info("BatchExtractor: Waiting for market close...")
                    return
                    
        except KeyboardInterrupt:
            self.logger.info("BatchDataExtractor: Received interrupt signal")
        except Exception as e:
            self.logger.info(f"BatchDataExtractor: Fatal error: {e}")
        finally:
            # Ensure synchronous cleanup
            if hasattr(self, 'batch_producer') and self.batch_producer:
                try:
                    self.batch_producer.flush(timeout=30)
                    self.batch_producer.close(timeout=30)
                except Exception as e:
                    self.logger.info(f"BatchDataExtractor: Error during final cleanup: {e}")

if __name__ == "__main__": 
    # Load Data Pipeline Configuration
    mode = 'development'
    settings = load_setting(mode)
    
    # Initialize the BatchDataExtractor
    batch_extractor = BatchDataExtractor(settings)
    
    # Run the BatchDataExtractor
    batch_extractor.run()