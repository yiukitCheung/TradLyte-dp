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
from datetime import datetime, time
from typing import List, Dict, Any
from decimal import Decimal

# Polygon WebSocket Client (official)
from polygon import WebSocketClient
from polygon.websocket.models import WebSocketMessage

# HTTP server for health checks
from aiohttp import web, WSMsgType
from aiohttp.web_runner import GracefulExit

# Import shared utilities
# Add /app to path for shared modules
sys.path.insert(0, '/app')
from shared.clients.kinesis_client import KinesisClient
from shared.clients.aurora_client import AuroraClient
from shared.models.data_models import TickData, OHLCVData

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PolygonWebSocketService:
    def __init__(self):
        # Environment variables
        self.polygon_api_key = os.environ['POLYGON_API_KEY']
        self.kinesis_stream_name = os.environ['KINESIS_STREAM_NAME']
        self.aurora_endpoint = os.environ['AURORA_ENDPOINT']
        self.aws_region = os.environ.get('AWS_REGION', 'us-east-1')
        
        # Initialize shared clients
        self.kinesis_client = KinesisClient(
            stream_name=self.kinesis_stream_name,
            region_name=self.aws_region
        )
        self.aurora_client = AuroraClient(endpoint=self.aurora_endpoint)
        
        # WebSocket client and state
        self.websocket_client = None
        self.active_symbols = []
        self.running = False
        self.message_count = 0
        self.last_message_time = None
        
        logger.info("Polygon WebSocket Service initialized")
    
    async def start(self):
        """Start the WebSocket service"""
        try:
            logger.info("Starting Polygon WebSocket Service...")
            
            # 1. Load active symbols from Aurora
            await self.load_active_symbols()
            
            # 2. Initialize Polygon WebSocket client  
            await self.initialize_websocket()
            
            # 3. Start the service loop
            await self.run_service_loop()
            
        except Exception as e:
            logger.error(f"Error starting service: {str(e)}")
            raise
    
    async def load_active_symbols(self):
        """Load active symbols from Aurora symbol metadata table"""
        try:
            query = """ SELECT symbol FROM data_ingestion_watermark WHERE is_current = 'true' """
            
            result = await self.aurora_client.execute_query(query)
            
            if result and len(result) > 0:
                self.active_symbols = [row[0] for row in result]
                logger.info(f"Loaded {len(self.active_symbols)} active symbols from Aurora")
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
        """Main service loop - runs during market hours"""
        try:
            self.running = True
            
            logger.info("Starting WebSocket connection...")
            await self.websocket_client.connect(self.handle_websocket_message)
                        
        except Exception as e:
            logger.error(f"Error in service loop: {str(e)}")
            self.running = False
            raise
    
    async def handle_websocket_message(self, messages: List[WebSocketMessage]):
        """Handle incoming WebSocket messages from Polygon"""
        try:
            for message in messages:
                await self.process_aggregate_message(message)
                self.message_count += 1
                self.last_message_time = datetime.utcnow()
                
                # Log stats every 100 messages
                if self.message_count % 100 == 0:
                    logger.info(f"Processed {self.message_count} messages")
                
        except Exception as e:
            logger.error(f"Error handling WebSocket messages: {str(e)}")
    
    async def process_aggregate_message(self, message: WebSocketMessage):
        """
        Process a single aggregate message (AM.* subscription)
        
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
            
            # Convert timestamps from milliseconds to datetime
            start_timestamp_ms = data.get('s', 0)
            end_timestamp_ms = data.get('e', 0)
            
            # Use end timestamp as the candle timestamp
            if end_timestamp_ms:
                timestamp = datetime.fromtimestamp(end_timestamp_ms / 1000.0)
            else:
                timestamp = datetime.utcnow()
            
            # Extract OHLCV data using Polygon AM.* field names
            ohlcv_data = OHLCVData(
                symbol=symbol,
                open=Decimal(str(data.get('o', 0))),    # 'o' = open
                high=Decimal(str(data.get('h', 0))),    # 'h' = high
                low=Decimal(str(data.get('l', 0))),     # 'l' = low
                close=Decimal(str(data.get('c', 0))),   # 'c' = close
                volume=int(data.get('v', 0)),           # 'v' = volume
                timestamp=timestamp,
                interval='1m'  # AM.* provides 1-minute aggregates
            )
            
            # Send to Kinesis for signal processing
            await self.send_to_kinesis(ohlcv_data)
            
            # Log sample data for debugging
            logger.debug(f"Processed {symbol}: O=${ohlcv_data.open} H=${ohlcv_data.high} L=${ohlcv_data.low} C=${ohlcv_data.close} V={ohlcv_data.volume} at {timestamp}")
            
        except Exception as e:
            logger.error(f"Error processing aggregate message: {str(e)}")
            logger.error(f"Message data: {data}")
    
    async def send_to_kinesis(self, ohlcv_data: OHLCVData):
        """Send raw 1-minute OHLCV data to Kinesis for downstream processing"""
        try:
            # Use KinesisClient helper to create standardized OHLCV record
            kinesis_record = self.kinesis_client.create_ohlcv_record(
                symbol=ohlcv_data.symbol,
                open_price=float(ohlcv_data.open),
                high_price=float(ohlcv_data.high),
                low_price=float(ohlcv_data.low),
                close_price=float(ohlcv_data.close),
                volume=ohlcv_data.volume,
                timestamp=ohlcv_data.timestamp,
                interval=ohlcv_data.interval,
                source='polygon_websocket_am'
            )
            
            # Send to Kinesis with symbol as partition key for proper ordering
            # This feeds into Kinesis Analytics for multi-timeframe aggregation
            await self.kinesis_client.put_record(
                data=kinesis_record,
                partition_key=ohlcv_data.symbol
            )
            
            logger.debug(f"Sent {ohlcv_data.symbol} 1m data to Kinesis for aggregation")
            
        except Exception as e:
            logger.error(f"Error sending {ohlcv_data.symbol} to Kinesis: {str(e)}")
    
    async def stop(self):
        """Stop the service gracefully"""
        logger.info("Stopping service...")
        self.running = False
        
        # Close WebSocket connection
        if self.websocket_client:
            await self.websocket_client.close()
        
        # Close shared clients
        if hasattr(self.aurora_client, 'close'):
            await self.aurora_client.close()
        
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