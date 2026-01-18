"""
Polygon.io client for AWS Lambda Architecture
Adapted from the existing Prefect Medallion implementation
"""

import requests
import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from massive import RESTClient
from decimal import Decimal
import boto3
import re
from botocore.client import Config
from concurrent import futures
import os
from dotenv import load_dotenv

from ..models.data_models import OHLCVData

logger = logging.getLogger(__name__)

load_dotenv()


class PolygonClient:
    """
    Polygon.io API client optimized for AWS Lambda usage
    """
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = RESTClient(api_key=self.api_key)
        self.base_url = "https://api.polygon.io"
    
    def get_active_symbols(self, limit: int = 100) -> List[str]:
        """
        Get active stock symbols for data fetching
        Returns a limited list suitable for AWS Lambda batch processing
        
        Args:
            limit: Maximum number of symbols to return
            
        Returns:
            List of stock symbols
        """
        try:
            tickers_response = self.client.list_tickers(
                market="stocks",
                active=True,
                limit=limit
            )
            # Filter for common stocks on major exchanges
            symbols = [
                ticker.ticker for ticker in tickers_response 
                if ticker.type in ['CS', 'ADRC'] 
                and ticker.primary_exchange in ['XNYS', 'XNAS', 'XAMS']
            ]
            
            if symbols:
                logger.info(f"Retrieved {len(symbols)} active symbols from API")
                return symbols[:limit]  # Ensure we don't exceed the limit
            else:
                logger.warning("No symbols returned from API, using fallback")
                # Fallback to default symbols (weekend-safe)
                fallback_symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM", "V", "JNJ"]
                return fallback_symbols[:limit]
            
        except Exception as e:
            logger.error(f"Error fetching active symbols: {e}")
            # Fallback to default symbols (weekend-safe)
            fallback_symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM", "V", "JNJ"]
            return fallback_symbols[:limit]
    
    def fetch_meta(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch metadata for a given symbol
        
        Args:
            symbol: Stock symbol (e.g., "AAPL")
            
        Returns:
            Dictionary containing symbol metadata or None if not found
        """
        try:
            params = {
                'ticker': symbol,
                'apiKey': self.api_key,
            }

            url = "https://api.polygon.io/v3/reference/tickers/"
            response = requests.get(url, params=params)

            if response.status_code != 200:
                logger.error(f"API request failed with status code {response.status_code}")
                return None

            response_json = response.json()
            return response_json['results'][0] if response_json.get('results') else None
            
        except Exception as e:
            logger.error(f"Error fetching metadata for {symbol}: {e}")
            return None
    
    def fetch_ohlcv_data(
        self, 
        symbol: str, 
        target_date: date,
        timespan: str = "day",
        multiplier: int = 1
    ) -> Optional[OHLCVData]:
        """
        Fetch OHLCV data for a specific symbol and date
        
        Args:
            symbol: Stock symbol (e.g., "AAPL")
            target_date: Date to fetch data for
            timespan: "minute", "hour", "day", "week", "month", "quarter", "year"
            multiplier: Size of timespan multiplier
            
        Returns:
            OHLCVData object or None if no data found
        """
        try:
            # Convert date to string format for API
            date_str = target_date.strftime('%Y-%m-%d')
            
            # Use the aggregates endpoint for OHLCV data
            aggs = self.client.get_aggs(
                ticker=symbol,
                multiplier=multiplier,
                timespan=timespan,
                from_=date_str,
                to=date_str
            )
            
            if not aggs or len(aggs) == 0:
                logger.warning(f"No OHLCV data found for {symbol} on {date_str}")
                return None
            
            # Take the first (and usually only) result
            bar = aggs[0]
            
            # Convert to our data model
            ohlcv_data = OHLCVData(
                symbol=symbol,
                timestamp=datetime.fromtimestamp(bar.timestamp / 1000),  # Convert from milliseconds
                open=Decimal(str(bar.open)),
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=Decimal(str(bar.close)),
                volume=int(bar.volume),
                interval=f"{multiplier}{timespan[0]}"
            )
            
            logger.info(f"Fetched OHLCV data for {symbol} on {date_str}")
            return ohlcv_data
            
        except Exception as e:
            logger.error(f"Error fetching OHLCV data for {symbol} on {target_date}: {e}")
            return None
    
    def fetch_ticker_ohlcv(self, symbol: str, start_date: str, end_date: str) -> Optional[List[Dict]]:
        """
        Fetch OHLCV data for a date range (compatible with existing code)
        
        Args:
            symbol: Stock symbol
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            
        Returns:
            List of OHLCV data dictionaries or None if error
        """
        try:
            params = {
                'multiplier': 1,
                'timespan': 'day',
                'adjusted': False,
                'sort': 'asc',
                'apiKey': self.api_key
            }
            url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{start_date}/{end_date}"
            response = requests.get(url, params=params)
            response_json = response.json()
            
            return response_json.get('results', [])
        
        except Exception as e:
            logger.error(f"Error fetching OHLCV data for {symbol}: {e}")
            return None
    
    def fetch_batch_ohlcv_data(
        self, 
        symbols: List[str], 
        target_date: date
    ) -> List[OHLCVData]:
        """
        Fetch OHLCV data for multiple symbols for a specific date
        
        Args:
            symbols: List of stock symbols
            target_date: Date to fetch data for
            
        Returns:
            List of OHLCVData objects
        """
        results = []
        
        for symbol in symbols:
            try:
                ohlcv_data = self.fetch_ohlcv_data(symbol, target_date)
                if ohlcv_data:
                    results.append(ohlcv_data)
            except Exception as e:
                logger.error(f"Error fetching data for {symbol}: {e}")
                continue
        
        logger.info(f"Successfully fetched OHLCV data for {len(results)}/{len(symbols)} symbols")
        return results
    
    def get_market_status(self) -> Dict[str, Any]:
        """
        Get current market status
        Returns a dictionary containing market status information
        """
        try:
            result = self.client.get_market_status()
            
            # Parse the response into a dictionary
            market_status = {
                'after_hours': result.after_hours,
                'currencies': {
                    'crypto': result.currencies.crypto,
                    'fx': result.currencies.fx
                },
                'early_hours': result.early_hours,
                'exchanges': {
                    'nasdaq': result.exchanges.nasdaq,
                    'nyse': result.exchanges.nyse,
                    'otc': result.exchanges.otc
                },
                'market': result.market,
                'server_time': result.server_time
            }
            
            return market_status
            
        except Exception as e:
            logger.error(f"Error getting market status: {e}")
            return {}
    
    def get_previous_trading_day(self, from_date: Optional[date] = None) -> date:
        """
        Get the previous trading day from a given date
        Simple implementation - in production, use a proper market calendar
        
        Args:
            from_date: Date to start from (defaults to today)
            
        Returns:
            Previous trading day (excludes weekends)
        """
        if from_date is None:
            from_date = date.today()
        
        # Simple approach: subtract days until we find a weekday
        current_date = from_date - timedelta(days=1)
        print(f"   Current date: {current_date}")
        while current_date.weekday() >= 5:  # Saturday=5, Sunday=6
            current_date -= timedelta(days=1)
        
        return current_date

class PolygonAWS_S3Client:
    def __init__(self, aws_access_key_id, aws_secret_access_key, endpoint_url, bucket_name, local_path, max_workers=4):
        self.session = boto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )
        self.s3 = self.session.client(
            's3',
            endpoint_url=endpoint_url,
            config=Config(signature_version='s3v4'),
        )
        self.paginator = self.s3.get_paginator('list_objects_v2')
        self.bucket_name = bucket_name
        self.local_path = local_path
        self.max_workers = max_workers
        self.regexp = re.compile(
            r'us_stocks_sip/(day_aggs|minute_aggs|trades|quote)_v1/(20[2-3]\d)/(0[1-9]|1[0-2])/(20[2-3]\d)-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01]).csv.gz'
        )

    def fetch(self, object_key, local_file_path):
        try:
            self.s3.download_file(self.bucket_name, object_key, local_file_path)
            return True
        except Exception as e:
            return str(e)

    def _list_objects(self, prefix):
        objects = []
        for page in self.paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
            objects.extend(page.get('Contents', []))
        return objects

    def _validate_object(self, object_key, data_type, obj_size):
        if not self.regexp.fullmatch(object_key):
            return False
        local_file_name = object_key.split('/')[-1]
        local_file_path = f'{self.local_path}/{data_type}/{local_file_name}'
        if os.path.exists(local_file_path):
            if obj_size == os.path.getsize(local_file_path):
                return False
            else:
                print(f"Size mismatch for {local_file_name}: {obj_size} <> {os.path.getsize(local_file_path)}")
        local_file_path = f'{self.local_path}/{data_type}-imported/{local_file_name}'
        if os.path.exists(local_file_path):
            if obj_size == os.path.getsize(local_file_path):
                return False
            else:
                print(f"Size mismatch for {local_file_name}: {obj_size} <> {os.path.getsize(local_file_path)}")
        return True

    def check_new_files(self, data_type):
        now = datetime.now()
        first_day_of_current_month = datetime(now.year, now.month, 1)
        last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)
        previous_month_str = last_day_of_previous_month.strftime("%Y/%m")
        current_month_str = now.strftime("%Y/%m")
        prefixes = [
            f'us_stocks_sip/{data_type}_aggs_v1/{previous_month_str}',
            f'us_stocks_sip/{data_type}_aggs_v1/{current_month_str}'
        ]
        objects = []
        for prefix in prefixes:
            objects.extend(self._list_objects(prefix))
        new_files = [obj for obj in objects if self._validate_object(obj['Key'], data_type, obj['Size'])]
        return new_files

    def dl(self, data_type):
        new_files = self.check_new_files(data_type)
        works = [(obj['Key'], f'{self.local_path}/{data_type}/{obj["Key"].split("/")[-1]}')
                for obj in new_files]

        with futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_key = {executor.submit(self.fetch, key[0], key[1]): key for key in works}

            for future in futures.as_completed(future_to_key):
                key = future_to_key[future]
                result = future.result()
                yield key, result

if __name__ == "__main__":
    client = PolygonAWS_S3Client(
        aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],
        endpoint_url=os.environ['AWS_ENDPOINT_URL'],
        bucket_name=os.environ['AWS_BUCKET_NAME'],
        local_path=os.environ['LOCAL_PATH']
    )
    # Download daily aggregates (OHLCV data)
    for key, result in client.dl('day'):
        if result is True:
            print(f"✅ Downloaded: {key[0]}")
        else:
            print(f"❌ Error downloading {key[0]}: {result}")

    # Download minute aggregates
    for key, result in client.dl('minute'):
        if result is True:
            print(f"✅ Downloaded: {key[0]}")
        else:
            print(f"❌ Error downloading {key[0]}: {result}")