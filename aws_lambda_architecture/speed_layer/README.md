# Speed Layer - Real-time Data Ingestion

## 🎯 Purpose

This Speed Layer is responsible for **real-time data ingestion only**. It connects to Massive (formerly Polygon.io) WebSocket and streams 1-minute OHLCV data to Kinesis for downstream processing.

## 🏗️ Architecture

```
Massive WebSocket (AM.*) → ECS WebSocket Service → Kinesis Stream → Kinesis Analytics
                                                                  ↓
                                                            Multi-timeframe 
                                                            Aggregation
                                                            (5m, 15m, 30m, 1h, 4h)
```

## 📦 Components

### ECS WebSocket Service (`fetching/data_stream_fetcher.py`)
- **Purpose**: Maintains persistent WebSocket connection to Massive (formerly Polygon.io)
- **Input**: Massive AM.* (1-minute aggregates, 15-minute delayed for testing)
- **Output**: Raw 1-minute OHLCV data to Kinesis Stream
- **Features**:
  - No 15-minute timeout (unlike Lambda)
  - Handles 50+ symbols simultaneously
  - Proper error handling and reconnection
  - Health check endpoint for ECS

## 🐳 Containerization

### Dockerfile
- **Base**: Python 3.10-slim
- **Security**: Non-root user
- **Health Check**: Built-in curl health check
- **Dependencies**: Minimal requirements for performance

### Local Development
```bash
# Build and run locally
docker-compose up -d

# View logs
docker-compose logs -f websocket-service

# Health check
curl http://localhost:8080/health
```

## 🔌 Data Flow

1. **WebSocket Connection**: Connects to Massive (formerly Polygon.io) with AM.* subscriptions using delayed feed
2. **Message Processing**: Parses 1-minute OHLCV data from Massive format
3. **Kinesis Publishing**: Sends raw data to Kinesis Stream with symbol as partition key
4. **Downstream**: Kinesis Analytics consumes for multi-timeframe aggregation

## 📊 Massive Data Format

```json
{
  "ev": "AM",           # Event type (Aggregate Minute)
  "sym": "AAPL",        # Symbol
  "v": 12345,           # Volume
  "o": 150.85,          # Open price
  "c": 152.90,          # Close price
  "h": 153.17,          # High price
  "l": 150.50,          # Low price
  "a": 151.87,          # VWAP (not used)
  "s": 1611082800000,   # Start timestamp (ms)
  "e": 1611082860000    # End timestamp (ms)
}
```

## 🚀 Deployment

### Local Development
```bash
docker-compose up -d
```

### AWS ECS Fargate
- Deploy via Terraform (see `../infrastructure/terraform/`)
- Environment variables required:
  - `POLYGON_API_KEY`
  - `KINESIS_STREAM_NAME`
  - `AURORA_ENDPOINT`
  - `AWS_REGION`

## 🔍 Monitoring

### Health Check
- **Endpoint**: `GET /health`
- **ECS Integration**: Health check every 30 seconds
- **Metrics**: Message count, last message time, connection status

### Logs
- **Format**: Structured JSON logging
- **CloudWatch**: Automatic log aggregation in ECS
- **Local**: Docker logs available via `docker-compose logs`

## ❌ What This Layer Does NOT Do

- ❌ Multi-timeframe aggregation (handled by Kinesis Analytics)
- ❌ Signal generation (handled by separate Signal Service)
- ❌ Price caching (not needed for this phase)
- ❌ Frontend notifications (handled by Signal Service)

## 🔄 Integration Points

### Upstream (Data Source)
- **Polygon.io WebSocket**: AM.* subscriptions for 1-minute data

### Downstream (Data Consumers)
- **Kinesis Analytics**: Multi-timeframe OHLCV aggregation
- **Signal Service**: Consumes aggregated data for strategy processing

## 🧪 Testing

### Local Testing
```bash
# Start services
docker-compose up -d

# Check WebSocket service logs
docker-compose logs -f websocket-service

# Verify health
curl http://localhost:8080/health
```

### Production Testing
- Health check endpoint monitoring
- CloudWatch metrics and alarms
- Kinesis stream monitoring for data flow

## 📈 Performance

### Throughput
- **Target**: 50+ symbols with 1-minute updates
- **Latency**: Sub-second from WebSocket to Kinesis
- **Reliability**: Auto-restart on failures via ECS

### Resource Usage
- **CPU**: 0.25 vCPU (typical)
- **Memory**: 512 MB
- **Network**: Minimal (WebSocket + Kinesis API calls)
