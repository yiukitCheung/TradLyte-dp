from kafka import KafkaProducer
import pandas as pd
from functools import partial
import json, logging, pytz
from datetime import datetime
from dotenv import load_dotenv
from prefect import get_run_logger
import concurrent.futures
import threading
import time
from curl_cffi import requests
import yfinance as yf
import sys, os 
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import postgres_client, kafka_client, polygon_client
from datetime import datetime
from config.load_setting import load_setting

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

POSTGRES_URL = os.getenv('POSTGRES_URL')
POLYGON_API_KEY = os.getenv('POLYGON_API_KEY')
KAFKA_BROKER = os.getenv('KAFKA_BROKER')

class MetaDataExtractor:
    def __init__(self, settings):
        # Extract configs
        self.mode = settings['mode']
        self.current_date = pd.to_datetime("today").strftime('%Y-%m-%d')
        self.topic_names = settings['data_extract']['meta']['topic_names']
        self.table_name = settings['data_extract']['meta']['table_name']
        self.chunk_size = settings['data_extract']['meta']['chunk_size']
        self.max_workers = settings['data_extract']['meta'].get('max_workers', 3)  # Default to 3 for metadata
        self.delay_between_requests = settings['data_extract']['meta'].get('delay_between_requests', 1.0)
        
        # Initialize tools
        self.timescale_tool = postgres_client.PostgresTools(POSTGRES_URL)
        self.polygon_tool = polygon_client.PolygonTools(POLYGON_API_KEY)
        self.kafka_tool = kafka_client.KafkaTools()
        
        # Initialize enhanced session for Yahoo Finance with better rate limiting
        self.yf_session = requests.Session(impersonate="chrome")
        self.yf_session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        
        self.logger = get_run_logger()
        
    
        # Find all available symbols from polygon api
        self.symbols = self.polygon_tool.fetch_all_tickers()
        
        # Initialize thread-safe components
        self.symbol_locks = {}  # Locks for each symbol
        
        # Initialize Kafka producer
        try:
            # Delete and recreate topic if reset mode is enabled
            if self.mode == "reset":
                self.kafka_tool.create_kafka_topic(self.topic_names, KAFKA_BROKER)
                self.logger.info(f"MetaDataExtractor: Recreated Kafka topic {self.topic_names}")

            self.batch_producer = KafkaProducer(
                bootstrap_servers=KAFKA_BROKER,
                value_serializer=lambda x: json.dumps(x).encode('utf-8')
            )
            self.logger.info(f"MetaDataExtractor: Kafka Producer connected to {KAFKA_BROKER}")
        except Exception as e:
            self.logger.error(f"MetaDataExtractor: Error initializing Kafka producer: {e}")
            raise e

    def _get_symbol_meta(self, symbol, max_retries=3):
        """Thread-safe version of fetching symbol metadata"""
        try:
            if symbol not in self.symbol_locks:
                self.symbol_locks[symbol] = threading.Lock()

            with self.symbol_locks[symbol]:
                for attempt in range(max_retries):
                    try:
                        # Fetch metadata using the polygon client first
                        meta_data = self.polygon_tool.fetch_meta(ticker=symbol)
                        
                        # Add configurable delay before Yahoo Finance requests to avoid rate limiting
                        time.sleep(self.delay_between_requests)
                        
                        # Fetch additional metadata using yfinance client with retry logic
                        ticker = yf.Ticker(symbol, session=self.yf_session)
                        
                        # Get ticker info with timeout and error handling
                        try:
                            info = ticker.info
                            meta_data["marketCap"] = info.get("marketCap", 0) if info else 0
                            meta_data["sector"] = info.get("sector", None) if info else None
                            meta_data["industry"] = info.get("industry", None) if info else None
                        except Exception as yf_error:
                            self.logger.warning(f"Yahoo Finance error for {symbol}: {str(yf_error)}")
                            # Fallback values if Yahoo Finance fails
                            meta_data["marketCap"] = 0
                            meta_data["sector"] = None
                            meta_data["industry"] = None
                        
                        if meta_data:
                            # Add timestamp to metadata
                            return meta_data, symbol
                        else:
                            self.logger.warning(f"MetaDataExtractor: No metadata returned for {symbol}")
                            return None, symbol
                            
                    except Exception as e:
                        error_msg = str(e).lower()
                        if any(rate_limit_indicator in error_msg for rate_limit_indicator in 
                                ["too many requests", "rate limit", "429", "throttle"]):
                            wait_time = min(30 * (2 ** attempt), 300)  # Exponential backoff, max 5 minutes
                            self.logger.warning(f"Rate limited for {symbol} (attempt {attempt + 1}/{max_retries}), waiting {wait_time}s")
                            time.sleep(wait_time)
                            
                            if attempt == max_retries - 1:
                                self.logger.error(f"Max retries exceeded for {symbol} due to rate limiting")
                                return None, symbol
                            continue
                        else:
                            self.logger.error(f"MetaDataExtractor: Error fetching metadata for {symbol}: {e}")
                            return None, symbol

        except Exception as e:
            self.logger.error(f"MetaDataExtractor: Error in metadata fetch for {symbol}: {e}")
            return None, symbol

    def _produce_meta_to_kafka(self, meta_data, symbol: str):
        """Thread-safe version of producing metadata to Kafka"""
        if not meta_data:
            return
        
        try:
            # Create metadata record for kafka
            meta_record = {
                'symbol': symbol,
                'name': meta_data['name'],
                'market': meta_data['market'],
                'locale': meta_data['locale'],
                'active': meta_data['active'],
                'primary_exchange': meta_data['primary_exchange'],
                'type': meta_data['type'],
                'marketCap': meta_data['marketCap'],
                'sector': meta_data['sector'],
                'industry': meta_data['industry']
            }
            
            # Send to Kafka
            self.batch_producer.send(self.topic_names, meta_record)
            self.batch_producer.flush()
            self.logger.info(f"Successfully sent metadata for {symbol}")
            
        except Exception as e:
            self.logger.error(f"Failed to send metadata for {symbol}: {e}")

    def _process_symbol(self, symbol):
        """Process a single symbol: fetch metadata and produce to Kafka"""
        try:
            # Fetch metadata
            meta_data, symbol = self._get_symbol_meta(symbol)
            
            if meta_data is not None:
                self._produce_meta_to_kafka(meta_data, symbol)
                
        except Exception as e:
            self.logger.error(f"Error processing symbol {symbol}: {e}")
            raise

    def fetch_and_produce_meta(self):
        """Process all symbols in parallel using thread pool"""
        try:
            # Process symbols in chunks to avoid overwhelming the API
            chunks = [self.symbols[i:i + self.chunk_size] 
                    for i in range(0, len(self.symbols), self.chunk_size)]
            
            for chunk in chunks:
                # Create a thread pool for processing this chunk
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    # Submit all tasks for this chunk
                    futures = [executor.submit(self._process_symbol, symbol) for symbol in chunk]
                    
                    # Wait for all tasks in this chunk to complete
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            future.result()  # This will raise any exceptions that occurred
                        except Exception as e:
                            self.logger.error(f"Task error: {e}")
                
                # Add delay between chunks
                time.sleep(2)  # 2 second delay between chunks
                self.logger.info(f"MetaDataExtractor: Processed chunk of {len(chunk)} symbols")
                
        except Exception as e:
            self.logger.error(f"Error in meta processing: {e}")
            raise

    def run(self):
        """Entry point with proper cleanup"""
        try:
            self.fetch_and_produce_meta()
            self.logger.info("MetaDataExtractor: Metadata fetching complete")
        except KeyboardInterrupt:
            self.logger.info("MetaDataExtractor: Received interrupt signal")
        except Exception as e:
            self.logger.error(f"MetaDataExtractor: Fatal error: {e}")
        finally:
            # Ensure synchronous cleanup
            if hasattr(self, 'batch_producer') and self.batch_producer:
                try:
                    self.batch_producer.flush(timeout=30)
                    self.batch_producer.close(timeout=30)
                except Exception as e:
                    self.logger.error(f"MetaDataExtractor: Error during final cleanup: {e}")

if __name__ == "__main__":
    # Load Data Pipeline Configuration
    mode = 'development'
    settings = load_setting(mode)
    
    # Initialize and run the MetaDataExtractor
    meta_extractor = MetaDataExtractor(settings)
    meta_extractor.run()