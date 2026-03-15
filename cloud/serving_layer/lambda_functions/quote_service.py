"""
Quote Service Lambda Function

Purpose: Provide latest price for any symbol on-demand via REST API
Endpoint: GET /api/quote?symbol=AAPL

Flow:
1. Check Redis cache (60s TTL)
2. If cached: Return immediately
3. If not cached: Call Polygon.io REST API, cache result, return

Cost: ~$5/month (vs $115/month for Speed Layer)
Latency: ~200ms (acceptable for "Latest Price" use case)
"""

import json
import os
import logging
import boto3
from datetime import datetime
from typing import Dict, Any, Optional

# Polygon.io REST client
from polygon import RESTClient

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize clients
secrets_client = boto3.client('secretsmanager')
redis_client = None  # Will be initialized on first use


def get_redis_client():
    """Initialize Redis client (ElastiCache)"""
    global redis_client
    if redis_client is None:
        import redis
        redis_endpoint = os.environ.get('REDIS_ENDPOINT')
        redis_port = int(os.environ.get('REDIS_PORT', 6379))
        redis_client = redis.Redis(
            host=redis_endpoint,
            port=redis_port,
            decode_responses=True,
            socket_connect_timeout=2
        )
    return redis_client


def get_polygon_api_key() -> str:
    """Get Polygon API key from Secrets Manager"""
    try:
        secret_arn = os.environ.get('POLYGON_API_KEY_SECRET_ARN')
        if secret_arn:
            response = secrets_client.get_secret_value(SecretId=secret_arn)
            secret = json.loads(response['SecretString'])
            return secret.get('POLYGON_API_KEY') or secret.get('POLYGON_API_KEY')
        else:
            # Fallback to environment variable (for local testing)
            return os.environ.get('POLYGON_API_KEY')
    except Exception as e:
        logger.error(f"Error retrieving Polygon API key: {str(e)}")
        raise


def get_quote_from_polygon(symbol: str, api_key: str) -> Dict[str, Any]:
    """
    Fetch latest quote from Polygon.io REST API
    
    Args:
        symbol: Stock symbol (e.g., 'AAPL')
        api_key: Polygon.io API key
        
    Returns:
        Quote data dictionary
    """
    try:
        client = RESTClient(api_key)
        
        # Get snapshot (latest quote)
        snapshot = client.get_snapshot_ticker(symbol)
        
        # Extract relevant data
        if snapshot and hasattr(snapshot, 'ticker'):
            ticker_data = snapshot.ticker
            quote = {
                'symbol': symbol,
                'last_quote': {
                    'price': getattr(ticker_data, 'last_quote', {}).get('p', 0),
                    'timestamp': getattr(ticker_data, 'last_quote', {}).get('t', 0)
                },
                'last_trade': {
                    'price': getattr(ticker_data, 'last_trade', {}).get('p', 0),
                    'timestamp': getattr(ticker_data, 'last_trade', {}).get('t', 0)
                },
                'prev_day': {
                    'close': getattr(ticker_data, 'prev_day', {}).get('c', 0),
                    'high': getattr(ticker_data, 'prev_day', {}).get('h', 0),
                    'low': getattr(ticker_data, 'prev_day', {}).get('l', 0),
                    'open': getattr(ticker_data, 'prev_day', {}).get('o', 0),
                    'volume': getattr(ticker_data, 'prev_day', {}).get('v', 0)
                },
                'fetched_at': datetime.utcnow().isoformat()
            }
            return quote
        else:
            raise ValueError(f"No data returned for symbol: {symbol}")
            
    except Exception as e:
        logger.error(f"Error fetching quote from Polygon for {symbol}: {str(e)}")
        raise


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for Quote Service
    
    Args:
        event: API Gateway event with queryStringParameters
        context: Lambda context
        
    Returns:
        API Gateway response
    """
    try:
        # Extract symbol from query parameters
        query_params = event.get('queryStringParameters') or {}
        symbol = query_params.get('symbol', '').upper()
        
        if not symbol:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'Missing required parameter: symbol'})
            }
        
        # Get API key
        api_key = get_polygon_api_key()
        
        # Check Redis cache (60s TTL)
        cache_key = f'quote:{symbol}'
        redis = get_redis_client()
        
        try:
            cached_quote = redis.get(cache_key)
            if cached_quote:
                logger.info(f"Cache hit for {symbol}")
                return {
                    'statusCode': 200,
                    'headers': {'Content-Type': 'application/json'},
                    'body': cached_quote
                }
        except Exception as cache_error:
            logger.warning(f"Redis cache error (continuing without cache): {str(cache_error)}")
        
        # Cache miss - fetch from Polygon.io
        logger.info(f"Cache miss for {symbol}, fetching from Polygon.io")
        quote = get_quote_from_polygon(symbol, api_key)
        
        # Cache result (60 seconds TTL)
        try:
            redis.setex(cache_key, 60, json.dumps(quote))
            logger.info(f"Cached quote for {symbol}")
        except Exception as cache_error:
            logger.warning(f"Failed to cache quote: {str(cache_error)}")
        
        # Return quote
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps(quote)
        }
        
    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        return {
            'statusCode': 400,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }
        
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': 'Internal server error'})
        }
