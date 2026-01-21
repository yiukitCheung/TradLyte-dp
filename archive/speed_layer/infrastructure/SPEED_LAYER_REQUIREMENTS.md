# Speed Layer Requirements & Architecture

## 🎯 MVP Requirements Summary

### Core Purpose
Simple, efficient real-time data pipeline for trading analytics:
- **WebSocket** → Fetch 1-minute base candles from Polygon.io
- **Flink/Kinesis Analytics** → Resample to multiple timeframes (5m, 15m, 30m, 1h, 2h, 4h)
- **DynamoDB** → Store recent candles with TTL-based retention
- **Downstream Services** → Push to Kafka/other analytics services (optional)

---

## 📊 Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    SPEED LAYER DATA FLOW                         │
└─────────────────────────────────────────────────────────────────┘

Polygon WebSocket (AM.*)
         │
         ▼
┌────────────────────┐
│  ECS WebSocket     │  → Maintains persistent connection
│  Service           │  → Handles 50+ symbols
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│  Kinesis Stream    │  → Raw 1-minute data ingestion
│  (market-data-raw) │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│  Kinesis Analytics │  → Flink SQL resampling
│  (Flink SQL)       │  → 5m, 15m, 30m, 1h, 2h, 4h
└────────┬───────────┘
         │
         ├─────────────────┬──────────────────┐
         ▼                 ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Kinesis      │  │  DynamoDB    │  │   Kafka      │
│ Output       │  │  Tables      │  │  (optional)  │
│ Streams      │  │  (with TTL)  │  │              │
└──────────────┘  └──────────────┘  └──────────────┘
         │                 │                  │
         └─────────────────┼──────────────────┘
                           ▼
                  Downstream Analytics Services
```

---

## 💾 DynamoDB Retention Strategy

### Recommended Retention Periods

| Interval | Retention | Trading Use Case | Storage/100 Symbols |
|----------|-----------|------------------|---------------------|
| **1min** | **7 days** | Intraday scalping, real-time alerts | 68 MB |
| **5min** | **7 days** | Short-term patterns, momentum | 14 MB |
| **15min** | **30 days** | Swing trading, weekly patterns | 20 MB |
| **30min** | **30 days** | Daily patterns, position sizing | 10 MB |
| **1h** | **90 days** | Quarterly trends, earnings cycles | 15 MB |
| **2h** | **90 days** | Major trends, seasonal patterns | 7 MB |
| **4h** | **90 days** | Long-term position analysis | 4 MB |

**Total Storage**: ~140 MB for 100 symbols  
**Monthly Cost**: ~$1.04 (storage + writes + reads)

### Rationale

1. **1min & 5min (7 days)**: 
   - Covers full trading week + weekend gap analysis
   - Sufficient for intraday patterns and real-time alerts
   - Minimal storage cost

2. **15min & 30min (30 days)**:
   - Full month of trading data
   - Covers swing trading strategies (1-2 week holds)
   - Weekly pattern analysis (4-5 weeks)

3. **1h, 2h & 4h (90 days)**:
   - One quarter of data
   - Covers quarterly earnings cycles
   - Major trend identification

---

## 🗄️ DynamoDB Table Structure

### Separate Table Per Interval

Each interval gets its own table for optimal query performance:

```
speed_layer_ohlcv_1min    (TTL: 7 days)
speed_layer_ohlcv_5min    (TTL: 7 days)
speed_layer_ohlcv_15min   (TTL: 30 days)
speed_layer_ohlcv_30min   (TTL: 30 days)
speed_layer_ohlcv_1h      (TTL: 90 days)
speed_layer_ohlcv_2h      (TTL: 90 days)
speed_layer_ohlcv_4h      (TTL: 90 days)
```

### Table Schema

```json
{
  "symbol": "AAPL",
  "timestamp": 1697658000123,  // Unix timestamp (milliseconds)
  "open": 205.50,
  "high": 206.20,
  "low": 205.30,
  "close": 205.90,
  "volume": 1000000,
  "interval": "1min",
  "ttl": 1698262800  // Unix timestamp for auto-deletion
}
```

**Primary Key**:
- Partition Key: `symbol` (String)
- Sort Key: `timestamp` (Number)

**Global Secondary Index** (optional):
- GSI: `symbol-timestamp-index` for efficient range queries

---

## 🔄 Kinesis Integration & Push/Pull Capabilities

### Yes, Kinesis Works Like Kafka - Push & Pull!

**Kinesis Data Streams** provides both **push** and **pull** consumption patterns, similar to Kafka:

#### 1. **Push Mode** (Event-Driven) - Recommended for MVP

**How it works**: Kinesis automatically invokes Lambda functions when new records arrive.

**Architecture**:
```
Kinesis Stream → Lambda Trigger (automatic) → DynamoDB/Kafka/S3
```

**Benefits**:
- ✅ **Serverless**: No infrastructure to manage
- ✅ **Auto-scaling**: Handles traffic spikes automatically
- ✅ **Built-in retry**: Automatic retry on failures
- ✅ **Cost-effective**: Pay only for invocations
- ✅ **Low latency**: Sub-second processing

**Configuration**:
```bash
# Create Lambda event source mapping
aws lambda create-event-source-mapping \
  --function-name kinesis-to-dynamodb \
  --event-source-arn arn:aws:kinesis:region:account:stream/market-data-1min \
  --starting-position LATEST \
  --batch-size 100 \
  --maximum-batching-window-in-seconds 1
```

#### 2. **Pull Mode** (Consumer Applications)

**How it works**: Applications actively poll Kinesis for records using KCL (Kinesis Client Library).

**Architecture**:
```
Kinesis Stream ← KCL Consumer Application → Your Analytics Service
```

**Use Cases**:
- Long-running analytics jobs
- Custom processing logic
- Integration with existing Kafka consumers
- Batch processing

**Example (Python with boto3)**:
```python
import boto3
import json

kinesis = boto3.client('kinesis')

# Get shard iterator
response = kinesis.get_shard_iterator(
    StreamName='market-data-1min',
    ShardId='shardId-000000000000',
    ShardIteratorType='LATEST'
)

shard_iterator = response['ShardIterator']

# Pull records
while True:
    response = kinesis.get_records(
        ShardIterator=shard_iterator,
        Limit=100
    )
    
    for record in response['Records']:
        data = json.loads(record['Data'])
        # Process record
        process_candle(data)
    
    shard_iterator = response['NextShardIterator']
```

**KCL (Kinesis Client Library)** - Similar to Kafka Consumer:
```java
// Java example (KCL)
public class CandleProcessor implements IRecordProcessor {
    public void processRecords(ProcessRecordsInput input) {
        for (Record record : input.getRecords()) {
            // Process each record
            processCandle(record.getData());
        }
    }
}
```

#### 3. **Kinesis → Kafka Bridge** (Forwarding)

**Use Case**: If you have existing Kafka infrastructure or need Kafka-specific features.

**Architecture**:
```
Kinesis Stream → Lambda → Kafka Producer → Kafka Topics
```

**Implementation**:
```python
# Lambda function: kinesis_to_kafka.py
import json
import base64
from kafka import KafkaProducer

producer = KafkaProducer(
    bootstrap_servers=['kafka-broker-1:9092', 'kafka-broker-2:9092'],
    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    acks='all',  # Wait for all replicas
    retries=3
)

def lambda_handler(event, context):
    for record in event['Records']:
        # Decode Kinesis record
        payload = base64.b64decode(record['kinesis']['data'])
        candle = json.loads(payload)
        
        # Forward to Kafka
        topic = f"market-data-{candle['interval']}"
        producer.send(topic, value=candle)
    
    producer.flush()  # Ensure all messages sent
    return {'statusCode': 200}
```

**Benefits of Kinesis → Kafka**:
- ✅ Reuse existing Kafka infrastructure
- ✅ Kafka ecosystem tools (Kafka Connect, Streams, etc.)
- ✅ Multiple consumers from same topic
- ✅ Topic partitioning strategies

---

### Complete Data Flow Options

#### Option A: Kinesis → Lambda → DynamoDB (Recommended for MVP)
```
Kinesis Stream → Lambda (push) → DynamoDB
```
- Simple, serverless
- Automatic scaling
- Built-in error handling

#### Option B: Kinesis → Lambda → Kafka → Multiple Consumers
```
Kinesis Stream → Lambda (push) → Kafka → [Consumer1, Consumer2, ...]
```
- Flexible downstream processing
- Multiple analytics services
- Kafka ecosystem benefits

#### Option C: Kinesis → Direct Pull (KCL/Application)
```
Kinesis Stream ← KCL Consumer → Your Service
```
- Full control over processing
- Custom batching logic
- Long-running jobs

#### Option D: Hybrid Approach
```
Kinesis Stream → Lambda → DynamoDB (fast queries)
              → Lambda → Kafka → Analytics Services (batch processing)
```
- Best of both worlds
- DynamoDB for real-time queries
- Kafka for complex analytics

---

### Kinesis vs Kafka Comparison

| Feature | Kinesis | Kafka |
|---------|---------|-------|
| **Push Mode** | ✅ Lambda triggers | ✅ Kafka Connect, Consumers |
| **Pull Mode** | ✅ KCL | ✅ Native consumers |
| **Managed Service** | ✅ Fully managed | ⚠️ Self-managed or Confluent Cloud |
| **Scaling** | ✅ Automatic | ⚠️ Manual shard/partition management |
| **Cost** | Pay per shard hour + data | Pay for infrastructure |
| **Retention** | 24h-365 days | Unlimited (disk space) |
| **Multi-Region** | ✅ Built-in | ⚠️ Complex setup |
| **Integration** | ✅ Native AWS services | ✅ Rich ecosystem |

**For MVP**: Kinesis is simpler (fully managed, auto-scaling)  
**For Production**: Both work, choose based on your infrastructure preferences

---

### Recommended Architecture for Speed Layer

```
Polygon WebSocket → ECS Service → Kinesis Stream (raw)
                                         ↓
                            Kinesis Analytics (Flink SQL)
                                         ↓
                    ┌────────────────────┼────────────────────┐
                    ↓                    ↓                    ↓
            Kinesis Streams      Lambda (push)        Lambda (push)
            (1min, 5min...)      → DynamoDB          → Kafka (optional)
            (for other services)   (with TTL)          (for analytics)
```

**Why this works**:
1. **Kinesis Analytics** processes and resamples data
2. **Lambda (push)** automatically writes to DynamoDB for fast queries
3. **Lambda (push)** optionally forwards to Kafka for downstream services
4. **Kinesis Streams** remain available for other consumers to pull if needed

---

### Kinesis → DynamoDB Implementation

Lambda function that automatically receives Kinesis records:

```python
# Lambda function: kinesis_to_dynamodb.py
import json
import base64
import boto3
from datetime import datetime, timedelta

dynamodb = boto3.client('dynamodb')

# Retention policy (days)
RETENTION_DAYS = {
    '1min': 7,
    '5min': 7,
    '15min': 30,
    '30min': 30,
    '1h': 90,
    '2h': 90,
    '4h': 90
}

def format_dynamodb_item(candle):
    """Convert candle dict to DynamoDB item format"""
    return {
        'symbol': {'S': candle['symbol']},
        'timestamp': {'N': str(candle['timestamp'])},
        'open': {'N': str(candle['open'])},
        'high': {'N': str(candle['high'])},
        'low': {'N': str(candle['low'])},
        'close': {'N': str(candle['close'])},
        'volume': {'N': str(candle['volume'])},
        'interval': {'S': candle['interval']},
        'ttl': {'N': str(candle['ttl'])}
    }

def lambda_handler(event, context):
    """Process Kinesis records and write to DynamoDB"""
    processed = 0
    errors = []
    
    for record in event['Records']:
        try:
            # Decode Kinesis record
            payload = base64.b64decode(record['kinesis']['data'])
            candle = json.loads(payload)
            
            # Determine table name from interval
            interval = candle['interval']
            table_name = f'speed_layer_ohlcv_{interval}'
            
            # Calculate TTL
            retention = RETENTION_DAYS.get(interval, 7)
            ttl_timestamp = int((datetime.now() + timedelta(days=retention)).timestamp())
            candle['ttl'] = ttl_timestamp
            
            # Write to DynamoDB
            dynamodb.put_item(
                TableName=table_name,
                Item=format_dynamodb_item(candle)
            )
            
            processed += 1
            
        except Exception as e:
            errors.append({
                'record_id': record['eventID'],
                'error': str(e)
            })
    
    return {
        'statusCode': 200,
        'processed': processed,
        'errors': len(errors)
    }
```

---

## 📋 Implementation Checklist

### Phase 1: Infrastructure Setup
- [ ] Create DynamoDB tables (7 tables, one per interval)
- [ ] Enable TTL on all tables
- [ ] Create Kinesis Data Streams (raw + output streams)
- [ ] Deploy Kinesis Analytics Flink SQL applications
- [ ] Create Lambda function for Kinesis → DynamoDB

### Phase 2: ECS WebSocket Service
- [ ] Deploy ECS Fargate service
- [ ] Configure health checks
- [ ] Set up CloudWatch monitoring
- [ ] Test WebSocket connection stability

### Phase 3: Integration & Testing
- [ ] Test end-to-end data flow
- [ ] Verify TTL expiration works
- [ ] Monitor DynamoDB costs
- [ ] Test query performance
- [ ] Set up alerts for failures

---

## 💰 Cost Estimates

### MVP (50-100 symbols)

| Component | Monthly Cost |
|-----------|--------------|
| DynamoDB Storage (140 MB) | $0.04 |
| DynamoDB Writes (25M/day) | $0.90 |
| DynamoDB Reads (13M/day) | $0.10 |
| Kinesis Streams | $5-10 |
| Kinesis Analytics | $10-20 |
| ECS Fargate | $5-10 |
| **TOTAL** | **~$21-41/month** |

### Production (500-1000 symbols)

| Component | Monthly Cost |
|-----------|--------------|
| DynamoDB | $10-20 |
| Kinesis Streams | $50-100 |
| Kinesis Analytics | $50-100 |
| ECS Fargate | $20-40 |
| **TOTAL** | **~$130-260/month** |

---

## 🎯 Key Design Decisions

1. **Separate Tables Per Interval**: 
   - ✅ Faster queries (no filtering by interval)
   - ✅ Independent scaling
   - ✅ Easier to adjust retention per interval

2. **TTL-Based Retention**:
   - ✅ Automatic cleanup (no manual maintenance)
   - ✅ Cost-effective (only pay for active data)
   - ✅ No background jobs needed

3. **On-Demand Billing**:
   - ✅ Pay only for what you use
   - ✅ No capacity planning needed
   - ✅ Easy to switch to provisioned if needed

4. **Kinesis → DynamoDB via Lambda**:
   - ✅ Serverless, auto-scaling
   - ✅ Built-in error handling
   - ✅ Can forward to multiple destinations

---

## 📚 Related Documentation

- [Speed Layer README](../README.md) - Overall architecture
- [Kinesis Analytics Flink SQL](../kinesis_analytics/flink_apps/) - Resampling queries
- [WebSocket Service](../fetching/data_stream_fetcher.py) - ECS service implementation

---

**Last Updated**: 2025-01-07  
**Status**: Ready for Implementation
