# Serving Layer - MVP Architecture

**Last Updated:** January 2026  
**Status:** Design Complete, Ready for Implementation

---

## 🎯 MVP Strategy

**Strategic Decision:** Removed Speed Layer for MVP to align with "Clarity Over Noise" mission. Real-time streaming encourages reactive behavior, which conflicts with product goals.

**MVP Focus:**
- ✅ **Latest Price** - On-demand quote service (not real-time streaming)
- ✅ **Backtesting** - Historical data queries from Batch Layer
- ⏳ **Alerts** - Scheduled checks (future, not real-time)

**Cost Savings:** ~$110/month (Quote Service: $5/month vs Speed Layer: $115/month)

---

## 📊 Architecture Overview

```
┌─────────────────────────────────────────────────┐
│              Frontend Dashboard                  │
└───────────────────┬─────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────┐
│            API Gateway (REST)                    │
│  - GET /api/quote?symbol=AAPL                   │
│  - GET /api/backtest?symbol=AAPL&interval=3d    │
└───────────────┬─────────────────────────────────┘
                │
        ┌───────┼───────┐
        ▼       ▼       ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│  Quote   │ │ Backtest │ │  Alert   │
│ Service  │ │   API    │ │ Service  │
│ Lambda   │ │ Lambda   │ │ Lambda   │
└────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │            │
     ▼            ▼            ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│  Redis   │ │   RDS     │ │   SNS     │
│  Cache   │ │  (5yr)    │ │ (Alerts) │
│ (60s TTL)│ │   S3      │ │          │
└────┬─────┘ └───────────┘ └──────────┘
     │
     ▼
┌──────────┐
│ Polygon  │
│ REST API │
│(On-demand)│
└──────────┘
```

---

## 🔑 Key Components

### 1. Quote Service (Key Deliverable)

**Purpose:** Provide latest price for any symbol on-demand

**Endpoint:** `GET /api/quote?symbol=AAPL`

**Flow:**
1. Check Redis cache: `quote:AAPL` (TTL: 60 seconds)
2. If cached: Return immediately ✅
3. If not cached:
   - Call Polygon.io REST API: `/v2/snapshot/ticker/AAPL`
   - Cache result in Redis (60s TTL)
   - Return latest price ✅

**Lambda Function:** `quote_service.py`

**Cost:** ~$5/month
- Lambda: $0.20 per 1M requests (~25M requests/month)
- Redis: ~$3/month (t3.micro ElastiCache)
- API Gateway: ~$2/month

**Latency:** ~200ms (acceptable for "Latest Price" use case)

**Benefits:**
- ✅ No 24/7 infrastructure (unlike Speed Layer)
- ✅ 95% cheaper than real-time streaming
- ✅ Simple, maintainable architecture
- ✅ Aligns with "Clarity Over Noise" mission

---

### 2. Backtest API

**Purpose:** Query historical OHLCV data for backtesting

**Endpoints:**
- `GET /api/backtest?symbol=AAPL&start=2020-01-01&end=2025-01-01`
- `GET /api/backtest?symbol=AAPL&interval=3d&start=2020-01-01&end=2025-01-01`

**Data Sources:**
- **Recent (5 years):** RDS PostgreSQL (fast queries)
- **Full History:** S3 Data Lake (Parquet files)

**Intervals Supported:**
- Daily (raw)
- Fibonacci: 3d, 5d, 8d, 13d, 21d, 34d

**Lambda Function:** `backtest_api.py`

**Cost:** ~$3/month
- Lambda: ~$1/month
- RDS queries: Included in Batch Layer cost
- S3 queries: ~$2/month (data transfer)

---

### 3. Alert Service (Future)

**Purpose:** Scheduled checks for watchlist conditions

**Architecture:**
- EventBridge schedule: Every 15 minutes
- Lambda function checks watchlist from DynamoDB
- For each symbol:
  - Get latest price (via Quote Service)
  - Get daily indicators (from RDS)
  - Check conditions (e.g., `Close < Moving_Average`)
  - If triggered: Send SNS notification

**Lambda Function:** `alert_checker.py`

**Cost:** ~$2/month
- Lambda: ~$1/month (scheduled execution)
- SNS: ~$1/month (notifications)

**Note:** Not real-time - aligns with "Purpose Over Profit" mission

---

## 💰 Cost Estimate (MVP)

| Component | Monthly Cost | Notes |
|-----------|--------------|-------|
| **Quote Service** | $5 | Lambda + Redis + API Gateway |
| **Backtest API** | $3 | Lambda + S3 queries |
| **Alert Service** | $2 | Scheduled Lambda + SNS |
| **Total Serving Layer** | **~$10/month** | |

**Comparison:**
- Speed Layer (original): ~$115/month
- Serving Layer (MVP): ~$10/month
- **Savings: $105/month (91% reduction)**

---

## 📋 Implementation Checklist

### Phase 1: Quote Service (Priority 1)
- [ ] Create `quote_service.py` Lambda function
- [ ] Set up Redis ElastiCache cluster
- [ ] Deploy API Gateway REST endpoint
- [ ] Configure Lambda environment variables
- [ ] Test quote caching (60s TTL)
- [ ] Test Polygon.io REST API integration
- [ ] Add error handling and retries

### Phase 2: Backtest API (Priority 2)
- [ ] Create `backtest_api.py` Lambda function
- [ ] Implement RDS query logic (5-year cache)
- [ ] Implement S3 query logic (full history)
- [ ] Support interval filtering (3d, 5d, 8d, etc.)
- [ ] Add pagination for large date ranges
- [ ] Deploy API Gateway endpoint
- [ ] Test with various symbols and intervals

### Phase 3: Alert Service (Future)
- [ ] Create `alert_checker.py` Lambda function
- [ ] Set up DynamoDB table for watchlists
- [ ] Create EventBridge schedule (15 min)
- [ ] Implement condition evaluation logic
- [ ] Set up SNS topic for notifications
- [ ] Test alert triggering

---

## 🔧 Technical Details

### Quote Service Implementation

```python
# quote_service.py
import json
import boto3
import redis
from polygon import RESTClient

def lambda_handler(event, context):
    symbol = event['queryStringParameters']['symbol']
    
    # Check Redis cache
    cached = redis_client.get(f'quote:{symbol}')
    if cached:
        return {'statusCode': 200, 'body': json.dumps(json.loads(cached))}
    
    # Fetch from Polygon.io
    quote = polygon_client.get_snapshot(symbol)
    
    # Cache for 60 seconds
    redis_client.setex(f'quote:{symbol}', 60, json.dumps(quote))
    
    return {'statusCode': 200, 'body': json.dumps(quote)}
```

### Backtest API Implementation

```python
# backtest_api.py
import json
import boto3
from datetime import datetime

def lambda_handler(event, context):
    symbol = event['queryStringParameters']['symbol']
    start = event['queryStringParameters']['start']
    end = event['queryStringParameters']['end']
    interval = event['queryStringParameters'].get('interval', 'daily')
    
    # Check if within 5 years (use RDS)
    if is_recent(start, end):
        data = query_rds(symbol, start, end, interval)
    else:
        data = query_s3(symbol, start, end, interval)
    
    return {'statusCode': 200, 'body': json.dumps(data)}
```

---

## 🚀 Deployment Roadmap

### Week 1: Quote Service
- Day 1-2: Implement Lambda function
- Day 3: Set up Redis ElastiCache
- Day 4: Deploy API Gateway
- Day 5: Testing and optimization

### Week 2: Backtest API
- Day 1-2: Implement Lambda function
- Day 3: Test RDS queries
- Day 4: Test S3 queries
- Day 5: Deploy and test

### Week 3: Integration & Testing
- End-to-end testing
- Performance optimization
- Error handling improvements
- Documentation

---

## 📚 Related Documentation

- [Batch Layer README](../batch_layer/README.md) - Data source
- [Implementation Status](../IMPLEMENTATION_STATUS.md) - Overall progress
- [API Specifications](../../docs/api.md) - API design (to be created)

---

## 🎯 Success Criteria

- [ ] Quote Service returns latest price in <200ms
- [ ] Redis cache reduces Polygon API calls by >90%
- [ ] Backtest API supports all Fibonacci intervals
- [ ] RDS queries return in <500ms for 5-year range
- [ ] S3 queries return in <2s for full history
- [ ] API Gateway handles 100+ requests/second
- [ ] Total cost <$15/month

---

**Last Updated:** January 2026  
**Status:** Design Complete, Ready for Implementation
