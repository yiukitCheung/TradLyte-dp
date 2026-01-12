"""
ECS Fargate WebSocket Service for Speed Layer

This service runs 24/7 during market hours and provides:
1. Persistent Polygon WebSocket connection (no 15-min timeout)
2. Real-time tick data ingestion 
3. Feeds data to Kinesis for signal processing
4. Uses official Polygon WebSocket client

Runs on ECS Fargate for continuous operation.
"""

import os
import json
import logging
import asyncio
import signal
import sys
import boto3
from datetime import datetime
from typing import List

# Polygon WebSocket Client (official)
from polygon import WebSocketClient
from polygon.websocket.models import WebSocketMessage

# HTTP server for health checks
from aiohttp import web
from aiohttp.web_runner import GracefulExit

# Import shared utilities
# Add parent directory to path for shared modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))
from shared.clients.kinesis_client import KinesisClient
from shared.clients.rds_timescale_client import RDSTimescaleClient
from shared.clients.polygon_client import PolygonClient
# Removed OHLCVData import - using direct dict for performance

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PolygonWebSocketService:
    def __init__(self):
        # Get Polygon API key - support both Secrets Manager (production) and direct env var (local testing)
        polygon_secret_arn = os.environ.get('POLYGON_API_KEY_SECRET_ARN')
        if polygon_secret_arn:
            # Production: Use Secrets Manager
            secrets_client = boto3.client('secretsmanager')
            polygon_secret = secrets_client.get_secret_value(SecretId=polygon_secret_arn)
            self.polygon_api_key = json.loads(polygon_secret['SecretString'])['POLYGON_API_KEY']
        else:
            # Local testing: Use direct environment variable
            self.polygon_api_key = os.environ.get('POLYGON_API_KEY')
            if not self.polygon_api_key:
                raise ValueError("Either POLYGON_API_KEY_SECRET_ARN or POLYGON_API_KEY must be set")
        
        # Environment variables
        self.kinesis_stream_name = os.environ.get('KINESIS_STREAM_NAME', 'market-data-raw')
        self.aws_region = os.environ.get('AWS_REGION', 'ca-west-1')
        self.skip_market_check = os.environ.get('SKIP_MARKET_CHECK', 'false').lower() == 'true'
        
        # Initialize clients
        self.polygon_client = PolygonClient(api_key=self.polygon_api_key)
        
        # Initialize RDS client - support both Secrets Manager (production) and direct credentials (local)
        rds_secret_arn = os.environ.get('RDS_SECRET_ARN')
        if rds_secret_arn:
            # Production: Use Secrets Manager
            self.rds_client = RDSTimescaleClient(secret_arn=rds_secret_arn)
        else:
            # Local testing: Use direct credentials from environment variables
            self.rds_client = RDSTimescaleClient(
                endpoint=os.environ.get('POSTGRES_HOST', 'localhost'),
                port=os.environ.get('POSTGRES_PORT', '5432'),
                username=os.environ.get('POSTGRES_USER'),
                password=os.environ.get('POSTGRES_PASSWORD'),
                database=os.environ.get('POSTGRES_DB')
            )
        
        # Initialize Kinesis client
        # Note: boto3 will automatically use AWS_ENDPOINT_URL if set (for LocalStack)
        self.kinesis_client = KinesisClient(
            stream_name=self.kinesis_stream_name,
            region_name=self.aws_region
        )
        
        # Log LocalStack usage if configured
        if os.environ.get('AWS_ENDPOINT_URL'):
            logger.info(f"Using LocalStack endpoint: {os.environ.get('AWS_ENDPOINT_URL')}")
        
        # WebSocket client and state
        self.websocket_client = None
        self.active_symbols = []
        self.running = False
        self.message_count = 0
        self.last_message_time = None
        self.market_check_interval = 300  # Check market status every 5 minutes
        self.last_market_check = None
        
        # Connection health and reconnection
        self.connection_health_check_interval = 60  # Check connection health every 60 seconds
        self.max_idle_time = 300  # Consider connection dead if no messages for 5 minutes
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 5  # Start with 5 seconds, exponential backoff
        self.connection_start_time = None
        
        logger.info("Polygon WebSocket Service initialized")
    
    async def check_market_status(self) -> bool:
        """
        Check if market is open (consistent with batch layer pattern)
        
        Returns:
            True if market is open, False if closed
        """
        try:
            if self.skip_market_check:
                logger.debug("⚠️  TESTING MODE: Skipping market status check")
                return True
            
            market_status = self.polygon_client.get_market_status()
            is_open = market_status.get('market') == 'open'
            
            if is_open:
                logger.debug("✅ Market is open")
            else:
                logger.info("⏸️  Market is closed")
            
            self.last_market_check = datetime.utcnow()
            return is_open
            
        except Exception as e:
            logger.error(f"Error checking market status: {str(e)}")
            # On error, assume market is open to avoid blocking
            return True
    
    async def start(self):
        """Start the WebSocket service"""
        try:
            logger.info("Starting Polygon WebSocket Service...")
            
            # 1. Check market status (consistent with batch layer pattern)
            if not await self.check_market_status():
                logger.info("Market is closed - service will wait for market to open")
                # Start a background task to periodically check market status
                asyncio.create_task(self.market_hours_monitor())
                return
            
            # 2. Load active symbols from RDS
            await self.load_active_symbols()
            
            # 3. Initialize Polygon WebSocket client  
            await self.initialize_websocket()
            
            # 4. Start market hours monitoring (to pause/resume based on market status)
            asyncio.create_task(self.market_hours_monitor())
            
            # 5. Start connection health monitoring
            asyncio.create_task(self.connection_health_monitor())
            
            # 6. Start the service loop
            await self.run_service_loop()
            
        except Exception as e:
            logger.error(f"Error starting service: {str(e)}")
            raise
    
    async def market_hours_monitor(self):
        """
        Background task to monitor market hours and pause/resume WebSocket connection
        Runs every 5 minutes to check market status
        """
        # Keep monitoring indefinitely (service should run 24/7, but pause when market closed)
        while True:
            try:
                await asyncio.sleep(self.market_check_interval)  # Wait 5 minutes
                
                is_market_open = await self.check_market_status()
                
                if is_market_open:
                    # Market is open - ensure WebSocket is connected
                    if not self.websocket_client or not self.running:
                        logger.info("Market opened - connecting WebSocket...")
                        if not self.active_symbols:
                            await self.load_active_symbols()
                        await self.initialize_websocket()
                        self.running = True
                        # Start service loop in background
                        asyncio.create_task(self.run_service_loop())
                else:
                    # Market is closed - pause WebSocket connection
                    if self.websocket_client and self.running:
                        logger.info("Market closed - pausing WebSocket connection...")
                        try:
                            await self.websocket_client.close()
                        except Exception as close_error:
                            logger.debug(f"Error closing WebSocket (expected): {close_error}")
                        self.running = False
                        self.websocket_client = None
                        
            except Exception as e:
                logger.error(f"Error in market hours monitor: {str(e)}")
                await asyncio.sleep(60)  # Wait 1 minute before retrying
    
    async def connection_health_monitor(self):
        """
        Background task to monitor WebSocket connection health
        Detects dead connections and triggers reconnection
        """
        while True:
            try:
                await asyncio.sleep(self.connection_health_check_interval)
                
                # Only check if market is open and we should be connected
                if not self.running or not self.websocket_client:
                    continue
                
                # Check if connection is alive (received messages recently)
                if self.last_message_time:
                    idle_time = (datetime.utcnow() - self.last_message_time).total_seconds()
                    
                    if idle_time > self.max_idle_time:
                        logger.warning(f"Connection appears dead - no messages for {idle_time:.0f} seconds")
                        logger.info("Triggering reconnection...")
                        
                        # Close current connection
                        try:
                            await self.websocket_client.close()
                        except Exception as close_error:
                            logger.debug(f"Error closing WebSocket during health check: {close_error}")
                        
                        self.running = False
                        self.websocket_client = None
                        
                        # Reinitialize and reconnect
                        try:
                            await self.initialize_websocket()
                            self.running = True
                            asyncio.create_task(self.run_service_loop())
                            logger.info("Connection reestablished after health check")
                        except Exception as e:
                            logger.error(f"Error reconnecting after health check: {str(e)}")
                    elif idle_time > self.max_idle_time / 2:
                        # Warning threshold (half of max idle time)
                        logger.debug(f"Connection idle for {idle_time:.0f} seconds (warning threshold)")
                else:
                    # No messages received yet, but connection exists
                    if self.connection_start_time:
                        connection_age = (datetime.utcnow() - self.connection_start_time).total_seconds()
                        if connection_age > 300:  # 5 minutes with no messages
                            logger.warning(f"Connection established {connection_age:.0f} seconds ago but no messages received")
                
            except Exception as e:
                logger.error(f"Error in connection health monitor: {str(e)}")
                await asyncio.sleep(30)  # Wait 30 seconds before retrying
    
    async def load_active_symbols(self):
        """Load active symbols from RDS symbol_metadata table"""
        try:
            # Use RDS client's get_active_symbols method (synchronous)
            # Since RDS client is synchronous, we'll run it in executor
            import asyncio
            loop = asyncio.get_event_loop()
            symbols = await loop.run_in_executor(
                None,
                self.rds_client.get_active_symbols
            )
            
            if symbols and len(symbols) > 0:
                self.active_symbols = symbols
                logger.info(f"Loaded {len(self.active_symbols)} active symbols from RDS")
            else:
                # Fallback symbols
                self.active_symbols = [
                    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA',
                    'META', 'NVDA', 'NFLX', 'AMD', 'PYPL'
                ]
                logger.warning(f"Using fallback symbols: {len(self.active_symbols)} symbols")
                
        except Exception as e:
            logger.error(f"Error loading symbols: {str(e)}")
            self.active_symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']
    
    async def initialize_websocket(self):
        """Initialize Polygon WebSocket client with active symbols"""
        try:
            # Create subscription list for 1 minute tick (T.*)
            subscriptions = [f"AM.{symbol}" for symbol in self.active_symbols]
            
            logger.info(f"Initializing WebSocket with {len(subscriptions)} AM.* subscriptions")
            
            # Initialize Polygon WebSocket client
            self.websocket_client = WebSocketClient(
                api_key=self.polygon_api_key,
                subscriptions=subscriptions
            )
            
            logger.info("Polygon WebSocket client initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing WebSocket: {str(e)}")
            raise
    
    async def run_service_loop(self):
        """Main service loop - runs during market hours with automatic reconnection"""
        while True:  # Keep trying to maintain connection
            try:
                if not self.websocket_client:
                    logger.warning("WebSocket client not initialized - skipping service loop")
                    await asyncio.sleep(10)
                    continue
                
                self.running = True
                self.connection_start_time = datetime.utcnow()
                self.reconnect_attempts = 0  # Reset on successful connection
                
                logger.info("Starting WebSocket connection...")
                await self.websocket_client.connect(self.handle_websocket_message)
                
                # If we get here, connection closed normally or error occurred
                logger.warning("WebSocket connection closed, will attempt reconnection...")
                self.running = False
                
            except Exception as e:
                logger.error(f"Error in service loop: {str(e)}")
                self.running = False
                
            # Exponential backoff reconnection
            if self.reconnect_attempts < self.max_reconnect_attempts:
                wait_time = min(self.reconnect_delay * (2 ** self.reconnect_attempts), 300)  # Max 5 minutes
                logger.info(f"Reconnecting in {wait_time} seconds (attempt {self.reconnect_attempts + 1}/{self.max_reconnect_attempts})...")
                await asyncio.sleep(wait_time)
                
                # Reinitialize WebSocket client
                try:
                    await self.initialize_websocket()
                    self.reconnect_attempts += 1
                except Exception as e:
                    logger.error(f"Error reinitializing WebSocket: {str(e)}")
                    await asyncio.sleep(30)  # Wait before retrying
            else:
                logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached. Waiting for market hours monitor...")
                # Let market_hours_monitor handle reconnection
                await asyncio.sleep(60)
    
    async def handle_websocket_message(self, messages: List[WebSocketMessage]):
        """Handle incoming WebSocket messages from Polygon"""
        try:
            for message in messages:
                await self.process_aggregate_message(message)
                self.message_count += 1
                self.last_message_time = datetime.utcnow()
                self.reconnect_attempts = 0  # Reset on successful message
                
                # Log stats every 100 messages
                if self.message_count % 100 == 0:
                    logger.info(f"Processed {self.message_count} messages")
                
        except Exception as e:
            logger.error(f"Error handling WebSocket messages: {str(e)}")
            # Don't raise - let run_service_loop handle reconnection
    
    async def process_aggregate_message(self, message: WebSocketMessage):
        """
        Process a single aggregate message (AM.* subscription)
        
        OPTIMIZED FOR FAST INGESTION:
        - No datetime conversions (keep as integer milliseconds)
        - No Decimal conversions (keep as float)
        - No intermediate objects (create dict directly)
        - Minimal transformations for maximum throughput
        
        Polygon AM.* format:
        {
            "ev": "AM",           # Event type (Aggregate Minute)
            "sym": "AAPL",        # Symbol
            "v": 12345,           # Volume
            "o": 150.85,          # Open price
            "c": 152.90,          # Close price
            "h": 153.17,          # High price
            "l": 150.50,          # Low price
            "a": 151.87,          # VWAP (average)
            "s": 1611082800000,   # Start timestamp (milliseconds)
            "e": 1611082860000    # End timestamp (milliseconds)
        }
        """
        try:
            # Convert WebSocketMessage to dict if needed
            if hasattr(message, '__dict__'):
                data = message.__dict__
            elif hasattr(message, 'data'):
                data = message.data
            else:
                data = message
            
            # Extract symbol using Polygon AM.* field names
            symbol = data.get('sym')  # Polygon uses 'sym' for symbol
            
            if not symbol:
                logger.warning(f"No symbol found in message: {data}")
                return
            
            # FAST PATH: Create Kinesis record directly with minimal transformations
            # Keep timestamp as integer milliseconds (no datetime conversion)
            # Keep prices as floats (no Decimal conversion)
            # No intermediate OHLCVData object creation
            end_timestamp_ms = data.get('e') or int(datetime.utcnow().timestamp() * 1000)
            
            kinesis_record = {
                'record_type': 'ohlcv',
                'symbol': symbol,
                'open_price': float(data.get('o', 0)),      # Direct float conversion
                'high_price': float(data.get('h', 0)),      # Direct float conversion
                'low_price': float(data.get('l', 0)),       # Direct float conversion
                'close_price': float(data.get('c', 0)),     # Direct float conversion
                'volume': int(data.get('v', 0)),            # Direct int conversion
                'timestamp_str': str(end_timestamp_ms),      # Keep as string milliseconds
                'interval_type': '1m',                       # AM.* provides 1-minute aggregates
                'source': 'polygon_websocket_am',
                'ingestion_time': int(datetime.utcnow().timestamp() * 1000)  # Milliseconds
            }
            
            # Send directly to Kinesis (no intermediate object creation)
            await self.kinesis_client.put_record(
                data=kinesis_record,
                partition_key=symbol
            )
            
            # Log sample data for debugging (only every 100 messages to reduce overhead)
            if self.message_count % 100 == 0:
                logger.debug(f"Processed {symbol}: O=${kinesis_record['open_price']} H=${kinesis_record['high_price']} L=${kinesis_record['low_price']} C=${kinesis_record['close_price']} V={kinesis_record['volume']}")
            
        except Exception as e:
            logger.error(f"Error processing aggregate message: {str(e)}")
            logger.error(f"Message data: {data}")
    
    async def stop(self):
        """Stop the service gracefully"""
        logger.info("Stopping service...")
        self.running = False
        
        # Close WebSocket connection
        if self.websocket_client:
            await self.websocket_client.close()
        
        # Close shared clients
        if hasattr(self.rds_client, 'close'):
            self.rds_client.close()
        
        if hasattr(self.kinesis_client, 'close'):
            await self.kinesis_client.close()
        
        logger.info("All clients closed successfully")

# Health check server for ECS
class HealthCheckServer:
    def __init__(self, websocket_service, port=8080):
        self.websocket_service = websocket_service
        self.port = port
    
    async def health_check(self, request):
        """ECS health check endpoint"""
        if self.websocket_service.running:
            return web.json_response({
                'status': 'healthy',
                'message_count': self.websocket_service.message_count,
                'last_message': self.websocket_service.last_message_time.isoformat() if self.websocket_service.last_message_time else None
            })
        else:
            return web.json_response({'status': 'unhealthy'}, status=503)
    
    async def start(self):
        """Start health check server"""
        app = web.Application()
        app.router.add_get('/health', self.health_check)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', self.port)
        await site.start()
        
        logger.info(f"Health check server started on port {self.port}")

async def main():
    """Main entry point for ECS service"""
    websocket_service = PolygonWebSocketService()
    health_server = HealthCheckServer(websocket_service)
    
    # Handle shutdown signals
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        asyncio.create_task(websocket_service.stop())
        raise GracefulExit()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Start both services
        await asyncio.gather(
            health_server.start(),
            websocket_service.start()
        )
    except (GracefulExit, KeyboardInterrupt):
        await websocket_service.stop()
    except Exception as e:
        logger.error(f"Service error: {str(e)}")
        await websocket_service.stop()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())