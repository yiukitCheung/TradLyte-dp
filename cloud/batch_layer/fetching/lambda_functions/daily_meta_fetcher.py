"""
AWS Lambda function for daily metadata updating (Simplified - Polygon API only)
Updates symbol metadata from Polygon API (no yfinance/pandas dependencies)
Uses asyncio for parallel fetching (10x faster!)
"""

import json
import boto3
import logging
import os
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, Any, List, Optional

# Import shared utilities (no layer dependencies)
from shared.clients.polygon_client import PolygonClient

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ.get("S3_DATALAKE_BUCKET", "dev-condvest-datalake")
S3_META_PREFIX = os.environ.get("S3_META_PREFIX", "bronze/raw_meta")


async def fetch_metadata_async(session: aiohttp.ClientSession, symbol: str, api_key: str) -> Optional[Dict[str, Any]]:
    """
    Async fetch metadata for a single symbol from Polygon API
    """
    try:
        url = f"https://api.polygon.io/v3/reference/tickers/{symbol}"
        params = {'apiKey': api_key}
        
        async with session.get(url, params=params) as response:
            if response.status != 200:
                logger.warning(f"API request failed for {symbol} with status {response.status}")
                return None
            
            data = await response.json()
            result = data.get('results', None)
            
            if result:
                # Transform to database schema
                metadata = {
                    'symbol': result.get('ticker', symbol),
                    'name': result.get('name'),
                    'market': result.get('market'),
                    'locale': result.get('locale'),
                    'active': str(result.get('active', False)),
                    'primary_exchange': result.get('primary_exchange'),
                    'type': result.get('type'),
                    'marketCap': result.get('market_cap'),
                    'industry': result.get('sic_description'),
                    'description': result.get('description')
                }
                logger.info(f"Fetched metadata for {symbol}")
                return metadata
            return None
            
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {str(e)}")
        return None


async def fetch_batch_async(symbols: List[str], api_key: str, max_concurrent: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch metadata for multiple symbols concurrently
    
    Args:
        symbols: List of symbols to fetch
        api_key: Polygon API key
        max_concurrent: Maximum concurrent requests (default 10)
    
    Returns:
        List of metadata dictionaries
    """
    connector = aiohttp.TCPConnector(limit=max_concurrent)
    timeout = aiohttp.ClientTimeout(total=30)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [fetch_metadata_async(session, symbol, api_key) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out None and exceptions
        metadata_list = []
        failed_symbols = []
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.error(f"Exception for {symbol}: {str(result)}")
                failed_symbols.append(symbol)
            elif result is not None:
                metadata_list.append(result)
            else:
                failed_symbols.append(symbol)
        
        if failed_symbols:
            logger.warning(f"Failed to fetch {len(failed_symbols)} symbols")
        
        return metadata_list


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Lambda handler for daily metadata updating using Polygon API only
    
    Event can contain:
    - symbols: List of symbols to update (optional, defaults to all active)
    - batch_size: Number of symbols to process per batch (default: 50)
    - force: Boolean to force execution
    """
    
    try:
        # Get Polygon API key from Secrets Manager
        secrets_client = boto3.client('secretsmanager')
        polygon_secret = secrets_client.get_secret_value(
            SecretId=os.environ['POLYGON_API_KEY_SECRET_ARN']
        )
        # Load the API keys from the secrets manager
        polygon_api_key = json.loads(polygon_secret['SecretString'])['POLYGON_API_KEY']
        
        # Initialize clients
        polygon_client = PolygonClient(api_key=polygon_api_key)
        
        # Parse event parameters
        symbols = event.get('symbols', None)
        batch_size = int(event.get('batch_size', '128'))
        force_full_sync = event.get('force_full_sync', False)  # Force sync with Polygon API to discover new symbols
        
        # Create batch job record
        job_id = f"daily-metadata-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        
        logger.info(f"Starting metadata update job: {job_id} (force_full_sync={force_full_sync})")
        
        # Determine if the market is open (skip check if force_full_sync)
        market_status = polygon_client.get_market_status()
        if (market_status['market'] == 'closed') and (not force_full_sync):
            logger.info("Skipping execution - market is closed (use force_full_sync=true to override)")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Skipping execution - market is closed'})
            }
            
        # Get symbols to process
        if symbols is not None:
            # Manual mode: use specified symbols
            logger.info(f"📋 Manual mode: processing {len(symbols)} specified symbols")
        elif force_full_sync:
            # Force sync: always fetch from Polygon API to discover new symbols (IPOs, new listings)
            symbols = polygon_client.get_active_symbols()
            logger.info(f"🔄 Full sync mode: fetched {len(symbols)} symbols from Polygon API")
        else:
            # Decoupled meta fetcher does not read RDS; default to Polygon active symbols.
            symbols = polygon_client.get_active_symbols()
            logger.info(f"📊 Active symbols mode: fetched {len(symbols)} symbols from Polygon API")
        
        run_date = event.get("run_date") or datetime.utcnow().date().isoformat()
        run_prefix = f"{S3_META_PREFIX}/run_date={run_date}/run_id={job_id}"
        s3_client = boto3.client("s3")

        part_keys: List[str] = []
        total_metadata = 0
        failed_count = 0

        # Process symbols in batches using async fetching (10 concurrent requests per batch)
        for i in range(0, len(symbols), batch_size):
            batch_symbols = symbols[i : i + batch_size]
            part_index = i // batch_size
            logger.info(
                "Processing batch %s/%s: %s symbols (async)",
                part_index + 1,
                (len(symbols) + batch_size - 1) // batch_size,
                len(batch_symbols),
            )

            try:
                # Fetch entire batch concurrently (10x faster!)
                batch_metadata = asyncio.run(
                    fetch_batch_async(
                        batch_symbols,
                        polygon_api_key,
                        max_concurrent=10,
                    )
                )

                logger.info("Fetched %s metadata records from Polygon API", len(batch_metadata))

                if batch_metadata:
                    part_key = f"{run_prefix}/part-{part_index:06d}.json"
                    s3_client.put_object(
                        Bucket=S3_BUCKET,
                        Key=part_key,
                        Body=json.dumps(batch_metadata).encode("utf-8"),
                        ContentType="application/json",
                    )
                    part_keys.append(part_key)
                    total_metadata += len(batch_metadata)
                    logger.info("Wrote part to S3: s3://%s/%s", S3_BUCKET, part_key)
                else:
                    logger.warning("No metadata fetched for batch %s", part_index + 1)

            except Exception as e:
                logger.error("Error processing batch %s: %s", part_index + 1, str(e))
                failed_count += len(batch_symbols)

        manifest_key = f"{run_prefix}/_manifest.json"
        manifest = {
            "run_id": job_id,
            "run_date": run_date,
            "symbol_count": len(symbols),
            "metadata_count": total_metadata,
            "part_keys": part_keys,
            "generated_at": datetime.utcnow().isoformat(),
        }

        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=manifest_key,
            Body=json.dumps(manifest).encode("utf-8"),
            ContentType="application/json",
        )

        logger.info(
            "Job completed: metadata_count=%s failed_symbols=%s manifest=s3://%s/%s",
            total_metadata,
            failed_count,
            S3_BUCKET,
            manifest_key,
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "status": "completed",
                    "job_id": job_id,
                    "manifest_key": manifest_key,
                    "symbols_processed": len(symbols),
                    "symbols_failed": failed_count,
                    "metadata_count": total_metadata,
                    "message": "Metadata fetch completed and manifest written to S3",
                }
            ),
        }
        
    except Exception as e:
        logger.error(f"Job failed with error: {str(e)}", exc_info=True)
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'status': 'error',
                'error': str(e),
                'error_type': type(e).__name__
            })
        }
