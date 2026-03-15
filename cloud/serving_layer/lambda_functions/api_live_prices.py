"""
AWS Lambda function for live price API endpoints
Serves real-time stock prices with Redis caching
"""

import json
import boto3
import logging
from datetime import datetime
from typing import Dict, Any, Optional
import os
from decimal import Decimal

# Import shared utilities
import sys
sys.path.append('/opt/python')
from shared.clients.redis_client import RedisClient
from shared.clients.aurora_client import AuroraClient
from shared.models.data_models import LivePriceResponse, APIResponse

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Lambda handler for live price API requests
    
    Handles:
    - GET /live/{symbol} - Get live price for a symbol
    - GET /live/multiple - Get live prices for multiple symbols
    """
    
    try:
        # Parse request
        http_method = event.get('httpMethod', 'GET')
        path_parameters = event.get('pathParameters', {})
        query_parameters = event.get('queryStringParameters', {}) or {}
        
        # Initialize clients
        redis_client = RedisClient(endpoint=os.environ['REDIS_ENDPOINT'])
        aurora_client = AuroraClient(
            cluster_arn=os.environ['AURORA_CLUSTER_ARN'],
            secret_arn=os.environ['AURORA_SECRET_ARN'],
            database_name=os.environ['DATABASE_NAME']
        )
        
        # Route request
        if path_parameters and 'symbol' in path_parameters:
            # Single symbol request
            symbol = path_parameters['symbol'].upper()
            response_data = get_single_live_price(symbol, redis_client, aurora_client)
            
        elif 'symbols' in query_parameters:
            # Multiple symbols request
            symbols_param = query_parameters['symbols']
            symbols = [s.strip().upper() for s in symbols_param.split(',')]
            response_data = get_multiple_live_prices(symbols, redis_client, aurora_client)
            
        else:
            return create_error_response(400, "Missing symbol parameter")
        
        # Create successful response
        return create_success_response(response_data)
        
    except Exception as e:
        logger.error(f"Error in live prices API: {str(e)}")
        return create_error_response(500, f"Internal server error: {str(e)}")


def get_single_live_price(
    symbol: str, 
    redis_client: RedisClient, 
    aurora_client: AuroraClient
) -> LivePriceResponse:
    """
    Get live price for a single symbol
    """
    try:
        # Try Redis first (fastest)
        cached_data = redis_client.get_latest_price_data(symbol)
        
        if cached_data:
            logger.info(f"Cache hit for {symbol}")
            return LivePriceResponse(
                symbol=symbol,
                price=Decimal(str(cached_data['price'])),
                change=Decimal(str(cached_data.get('change', 0))),
                change_percent=cached_data.get('change_percent', 0.0),
                volume=cached_data.get('volume'),
                timestamp=datetime.fromisoformat(cached_data['timestamp']),
                source='CACHE'
            )
        
        # Cache miss - check DynamoDB for recent tick data
        dynamodb_data = get_latest_tick_from_dynamodb(symbol)
        
        if dynamodb_data:
            logger.info(f"DynamoDB hit for {symbol}")
            return LivePriceResponse(
                symbol=symbol,
                price=Decimal(str(dynamodb_data['price'])),
                volume=dynamodb_data.get('volume'),
                timestamp=datetime.fromisoformat(dynamodb_data['timestamp']),
                source='DYNAMODB'
            )
        
        # Last resort - get latest from Aurora (historical data)
        aurora_data = aurora_client.get_latest_price(symbol)
        
        if aurora_data:
            logger.info(f"Aurora hit for {symbol}")
            return LivePriceResponse(
                symbol=symbol,
                price=Decimal(str(aurora_data['close'])),
                timestamp=datetime.fromisoformat(aurora_data['date']),
                source='DATABASE'
            )
        
        # No data found
        raise ValueError(f"No price data found for symbol {symbol}")
        
    except Exception as e:
        logger.error(f"Error getting live price for {symbol}: {str(e)}")
        raise


def get_multiple_live_prices(
    symbols: list, 
    redis_client: RedisClient, 
    aurora_client: AuroraClient
) -> Dict[str, Any]:
    """
    Get live prices for multiple symbols
    """
    results = {}
    cache_hits = 0
    
    for symbol in symbols:
        try:
            price_data = get_single_live_price(symbol, redis_client, aurora_client)
            results[symbol] = price_data.dict()
            
            if price_data.source == 'CACHE':
                cache_hits += 1
                
        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {str(e)}")
            results[symbol] = {
                'error': f'Price data not available: {str(e)}'
            }
    
    return {
        'prices': results,
        'cache_hit_rate': cache_hits / len(symbols) if symbols else 0,
        'timestamp': datetime.utcnow().isoformat()
    }


def get_latest_tick_from_dynamodb(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Get latest tick data from DynamoDB
    """
    try:
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(os.environ['DYNAMODB_TICKS_TABLE'])
        
        response = table.query(
            KeyConditionExpression='symbol = :symbol',
            ExpressionAttributeValues={':symbol': symbol},
            ScanIndexForward=False,  # Latest first
            Limit=1
        )
        
        if response['Items']:
            item = response['Items'][0]
            return {
                'price': item['price'],
                'volume': item.get('volume'),
                'timestamp': item['timestamp']
            }
        
        return None
        
    except Exception as e:
        logger.error(f"Error querying DynamoDB for {symbol}: {str(e)}")
        return None


def create_success_response(data: Any) -> Dict[str, Any]:
    """Create a successful API response"""
    
    # Handle Decimal serialization
    def decimal_serializer(obj):
        if isinstance(obj, Decimal):
            return float(obj)
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS'
        },
        'body': json.dumps({
            'success': True,
            'data': data,
            'timestamp': datetime.utcnow().isoformat()
        }, default=decimal_serializer)
    }


def create_error_response(status_code: int, message: str) -> Dict[str, Any]:
    """Create an error API response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS'
        },
        'body': json.dumps({
            'success': False,
            'error': message,
            'timestamp': datetime.utcnow().isoformat()
        })
    }


# Extension methods for Aurora client
def get_latest_price_extension(self, symbol: str) -> Optional[Dict[str, Any]]:
    """Extension method for AuroraClient to get latest price"""
    sql = """
    SELECT date, close
    FROM raw
    WHERE symbol = :symbol
    ORDER BY date DESC
    LIMIT 1
    """
    
    parameters = [{'name': 'symbol', 'value': {'stringValue': symbol}}]
    
    try:
        response = self.execute_query(sql, parameters)
        
        if 'records' in response and response['records']:
            record = response['records'][0]
            if len(record) >= 2:
                return {
                    'date': record[0].get('stringValue'),
                    'close': record[1].get('doubleValue')
                }
        
        return None
        
    except Exception as e:
        logger.error(f"Error getting latest price for {symbol}: {str(e)}")
        return None

# Monkey patch the extension method
import types
AuroraClient.get_latest_price = types.MethodType(get_latest_price_extension, None)
