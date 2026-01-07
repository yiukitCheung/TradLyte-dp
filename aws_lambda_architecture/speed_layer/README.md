# Speed Layer - Real-time Data Ingestion

## 🎯 Purpose

This Speed Layer is responsible for **real-time data ingestion only**. It connects to Polygon.io WebSocket and streams 1-minute OHLCV data to Kinesis for downstream processing.

## 🏗️ Architecture

```
Polygon WebSocket (AM.*) → ECS WebSocket Service → Kinesis Stream → Kinesis Analytics
                                                                  ↓
                                                            Multi-timeframe 
                                                            Aggregation
                                                            (5m, 15m, 30m, 1h, 4h)
```

## 📦 Components

### ECS WebSocket Service (`services/webosket_service.py`)
- **Purpose**: Maintains persistent WebSocket connection to Polygon.io
- **Input**: Polygon AM.* (1-minute aggregates)
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

1. **WebSocket Connection**: Connects to Polygon.io with AM.* subscriptions
2. **Message Processing**: Parses 1-minute OHLCV data from Polygon format
3. **Kinesis Publishing**: Sends raw data to Kinesis Stream with symbol as partition key
4. **Downstream**: Kinesis Analytics consumes for multi-timeframe aggregation

## 📊 Polygon Data Format

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

### Prerequisites

1. **AWS CLI configured** with appropriate IAM permissions
2. **Environment variables set**:
   ```bash
   export POLYGON_API_KEY='your-polygon-api-key'
   export AURORA_ENDPOINT='your-aurora-endpoint.region.rds.amazonaws.com'
   export AWS_REGION='ca-west-1'  # Optional, defaults to ca-west-1
   ```

3. **Docker installed** (for ECS service deployment)

### Quick Deployment (All Components)

Deploy all Speed Layer components in one command:

```bash
cd aws_lambda_architecture/speed_layer
./deploy.sh
```

This will deploy:
1. DynamoDB Tables (alert configs, ticks, notifications)
2. Kinesis Data Stream (raw market data ingestion)
3. SNS Topic (alert notifications)
4. Signal Generator Lambda (process alerts)
5. ECS Fargate Service (WebSocket data fetcher)

### Manual Deployment (Step-by-Step)

#### 1. DynamoDB Tables
```bash
cd infrastructure
./deploy_dynamodb_tables.sh
```

Creates:
- `alert_configurations` - User alert configurations
- `realtime_ticks` - Latest tick data (with 24h TTL)
- `alert_notifications_history` - Alert audit log

#### 2. Kinesis Data Stream
```bash
./deploy_kinesis_stream.sh
```

Creates:
- `dev-market-data-stream` - Raw market data ingestion stream

#### 3. SNS Topic
```bash
./deploy_sns_topic.sh
```

Creates:
- `condvest-speed-layer-alerts` - Alert notification topic

**Subscribe to alerts:**
```bash
aws sns subscribe \
  --topic-arn arn:aws:sns:ca-west-1:ACCOUNT_ID:condvest-speed-layer-alerts \
  --protocol email \
  --notification-endpoint your-email@example.com \
  --region ca-west-1
```

#### 4. Signal Generator Lambda
```bash
./deploy_lambda_signal_generator.sh
```

Deploys:
- `dev-signal-generator` - Lambda function to process Kinesis Analytics output

**Configure Kinesis event source:**
```bash
aws lambda create-event-source-mapping \
  --function-name dev-signal-generator \
  --event-source-arn arn:aws:kinesis:ca-west-1:ACCOUNT_ID:stream/dev-market-data-stream \
  --starting-position LATEST \
  --region ca-west-1
```

#### 5. Kinesis Analytics (Flink)

**Note:** Kinesis Analytics deployment requires manual configuration via AWS Console or updated deployment script.

The Flink SQL applications are located in:
- `kinesis_analytics/flink_apps/`

Deploy via AWS Console:
1. Go to Kinesis Analytics → Studio
2. Create new Flink application
3. Copy SQL from `flink_apps/*.sql` files
4. Configure input/output streams
5. Start application

#### 6. ECS Fargate Service
```bash
./deploy_ecs_service.sh
```

Deploys:
- ECS Cluster: `dev-speed-layer-cluster`
- ECS Service: `dev-websocket-service`
- Task Definition: `dev-websocket-task`
- ECR Repository: `speed-layer-websocket`

**Monitor service:**
```bash
# Check service status
aws ecs describe-services \
  --cluster dev-speed-layer-cluster \
  --services dev-websocket-service \
  --region ca-west-1

# View logs
aws logs tail /ecs/dev-websocket-task --follow --region ca-west-1
```

### Local Development

```bash
# Build and run locally
cd data_fetcher
docker-compose up -d

# View logs
docker-compose logs -f websocket-service

# Health check
curl http://localhost:8080/health
```

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

## 💰 Cost Estimation

| Component | Monthly Cost |
|-----------|--------------|
| DynamoDB Tables | $15-25 |
| Kinesis Data Stream | $50-100 |
| Kinesis Analytics (Flink) | $50-100 |
| ECS Fargate | $30-50 |
| Lambda (Signal Generator) | $5-10 |
| SNS | $1-5 |
| **Total** | **~$150-290/month** |

## 🔧 Troubleshooting

### ECS Service Not Starting
- Check CloudWatch logs: `/ecs/dev-websocket-task`
- Verify environment variables are set correctly
- Check IAM role permissions for Kinesis and Aurora

### No Data in Kinesis Stream
- Verify WebSocket service is running: `aws ecs describe-services --cluster dev-speed-layer-cluster --services dev-websocket-service`
- Check Polygon API key is valid
- Verify Aurora endpoint is accessible from ECS

### Lambda Not Processing Records
- Check Lambda CloudWatch logs
- Verify event source mapping is configured
- Check DynamoDB table permissions

### Kinesis Analytics Not Processing
- Verify Flink application is running
- Check input stream configuration
- Review Flink application logs in CloudWatch

## 📚 Additional Resources

- [DynamoDB Tables Documentation](./infrastructure/dynamodb_tables.md)
- [Redis ElastiCache (Optional)](./infrastructure/redis_elasticache.md)
- [Kinesis Analytics Flink SQL Apps](./kinesis_analytics/flink_apps/)
