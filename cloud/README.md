# TradLyte Cloud Data Pipeline

## Overview

This directory contains the AWS-native implementation of the TradLyte data pipeline using the **Lambda Architecture** pattern for financial data processing.

**MVP Strategy:** Batch + Serving layers. A Speed Layer (Kinesis/Flink) was designed but parked for MVP — archived code lives in `speed_layer/Archive/`.

## 🏗️ Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           TRADLYTE DATA PIPELINE                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                         DATA SOURCES                                     │   │
│   │  ┌──────────────┐                         ┌──────────────┐              │   │
│   │  │ Polygon REST │  Daily OHLCV            │ Polygon REST │  Latest Price│   │
│   │  │  (Batch)     │                         │  (On-demand) │              │   │
│   │  └──────┬───────┘                         └──────┬───────┘              │   │
│   └─────────┼────────────────────────────────────────┼──────────────────────┘   │
│             │                                        │                          │
│   ┌─────────▼──────────────────────────────────────────────────────────────┐    │
│   │                    BATCH LAYER (✅ Complete)                             │    │
│   │                                                                         │    │
│   │  ┌──────────────────────────────────────────────────────────────────┐  │    │
│   │  │              Step Functions Pipeline                              │  │    │
│   │  │                                                                   │  │    │
│   │  │  ┌──────────────────────┐                                        │  │    │
│   │  │  │  STAGE 1 (Parallel)  │                                        │  │    │
│   │  │  │  OHLCV Fetch         │                                        │  │    │
│   │  │  │  Metadata Fetch      │  (Lambda, 2 retries each)              │  │    │
│   │  │  └──────────┬───────────┘                                        │  │    │
│   │  │             ▼                                                     │  │    │
│   │  │  ┌──────────────────────┐                                        │  │    │
│   │  │  │  STAGE 2             │                                        │  │    │
│   │  │  │  Partition Symbols   │  (Lambda) → 10 chunk files on S3       │  │    │
│   │  │  └──────────┬───────────┘                                        │  │    │
│   │  │             ▼                                                     │  │    │
│   │  │  ┌──────────────────────┐                                        │  │    │
│   │  │  │  STAGE 3             │  Array Job (10 parallel Fargate        │  │    │
│   │  │  │  Scanner Workers x10 │  containers, 4 vCPU / 8 GB each)      │  │    │
│   │  │  │                      │  → daily_scan_signals (RDS staging)   │  │    │
│   │  │  └──────────┬───────────┘                                        │  │    │
│   │  │             ▼                                                     │  │    │
│   │  │  ┌──────────────────────┐                                        │  │    │
│   │  │  │  STAGE 4             │  Single Fargate container (2 vCPU /   │  │    │
│   │  │  │  Scanner Aggregator  │  4 GB) → global rank → stock_picks    │  │    │
│   │  │  └──────────┬───────────┘    → cleanup daily_scan_signals       │  │    │
│   │  │             ▼                                                     │  │    │
│   │  │        ✅ Pipeline Complete                                        │  │    │
│   │  └──────────────────────────────────────────────────────────────────┘  │    │
│   │                                                                         │    │
│   │  ┌──────────────────────────────────────────────────────────────────┐  │    │
│   │  │              Analytics Core (Shared)                              │  │    │
│   │  │  - Technical Indicators (RSI, SMA, MACD, Vegas Channel, etc.)    │  │    │
│   │  │  - Strategy Framework (3-step: Setup → Trigger → Exit)           │  │    │
│   │  │  - Pre-built Strategies (Golden Cross, Vegas Channel)             │  │    │
│   │  │  - DailyScanner: run() → rank() → write()                        │  │    │
│   │  └──────────────────────────────────────────────────────────────────┘  │    │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                       │                                         │
│   ┌───────────────────────────────────▼──────────────────────────────────────┐  │
│   │                    SERVING LAYER (📋 MVP Design)                          │  │
│   │                                                                          │  │
│   │     ┌─────────┐     ┌─────────────┐                                      │  │
│   │     │  Redis  │ ←── │ API Gateway │ ←── Frontend                        │  │
│   │     │ (cache) │     │  (REST)     │                                      │  │
│   │     └─────────┘     └──────┬──────┘                                      │  │
│   │                            │                                             │  │
│   │              ┌──────────────┼──────────────┐                            │  │
│   │              ▼              ▼              ▼                            │  │
│   │      ┌──────────┐  ┌──────────┐  ┌──────────┐                          │  │
│   │      │  Quote   │  │ Backtest │  │  Alert   │                          │  │
│   │      │ Service  │  │   API    │  │ Service  │                          │  │
│   │      │(Latest)  │  │(On-demand)│ │(Scheduled)│                         │  │
│   │      └──────────┘  └──────────┘  └──────────┘                          │  │
│   └──────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Directory Structure

```
cloud/
├── batch_layer/                        # ✅ Daily batch processing
│   ├── database/
│   │   └── schemas/                    # schema_init.sql (incl. daily_scan_signals)
│   ├── fetching/
│   │   └── lambda_functions/
│   │       ├── daily_ohlcv_fetcher.py
│   │       └── daily_meta_fetcher.py
│   ├── processing/
│   │   ├── batch_jobs/
│   │   │   ├── scan.py                 # Scanner worker + aggregator entry point
│   │   │   ├── requirements.scanner.txt # Lean scanner deps (polars, sqlalchemy…)
│   │   │   └── requirements.txt        # Full deps (all batch jobs)
│   │   └── lambda_functions/
│   │       └── scan_partitioner.py     # Partitioner Lambda (symbols → S3 chunks)
│   ├── infrastructure/
│   │   ├── fetching/                   # Lambda deployment scripts
│   │   ├── processing/
│   │   │   ├── batch_job/
│   │   │   │   ├── Dockerfile          # Resampler/consolidator image
│   │   │   │   ├── Dockerfile.scanner  # Scanner-specific image
│   │   │   │   ├── build_scanner_container.sh
│   │   │   │   └── deploy_scanner_batch_jobs.sh
│   │   │   └── lambda_functions/
│   │   │       └── deploy_processing_lambda.sh  # Deploys scan_partitioner
│   │   └── orchestration/
│   │       ├── state_machine_definition.json
│   │       └── deploy_step_functions.sh
│   └── BATCH_LAYER_IMPLEMENTATION_SUMMARY.md
│
├── serving_layer/                      # 📋 API serving (MVP Design)
│   ├── lambda_functions/
│   │   ├── quote_service.py
│   │   └── backtester/
│   └── README.md
│
├── speed_layer/                        # 📁 Archived Kinesis/Flink design
│   └── Archive/
│       ├── fetching/                   # ECS data stream fetcher
│       ├── infrastructure/             # Task definition, build scripts
│       ├── kinesis_analytics/          # Flink SQL resampler apps
│       └── lambda_functions/           # Kinesis → DynamoDB handlers
│
├── shared/                             # Common utilities (used by batch + serving)
│   ├── clients/                        # RDS, Polygon clients
│   ├── models/                         # Pydantic data models
│   ├── utils/                          # Market calendar, helpers
│   └── analytics_core/                 # Analytics Engine
│       ├── indicators/                 # Technical indicators (Polars)
│       ├── strategies/                 # Strategy framework + library
│       ├── scanner.py                  # DailyScanner: run() → rank() → write()
│       ├── inputs.py                   # OHLCV data loader
│       └── models.py                   # SignalResult, OHLCVData
│
└── README.md                           # This file
```

---

## ✅ Implementation Status

### Batch Layer ✅ Complete

| Component | Status | Description |
|-----------|--------|-------------|
| **Lambda OHLCV Fetcher** | ✅ Deployed | Daily data ingestion from Polygon |
| **Lambda Meta Fetcher** | ✅ Deployed | Symbol metadata updates |
| **Watermark System** | ✅ Working | Incremental processing tracking |
| **S3 Bronze Layer** | ✅ Working | Raw data storage (symbol partitioned) |
| **Step Functions** | ✅ Deployed | 4-stage pipeline (see Daily Flow below) |
| **Scanner Partitioner Lambda** | ✅ Deployed | Splits 5,000+ symbols into 10 S3 chunk files |
| **Scanner Workers (Array Job x10)** | ✅ Defined | Parallel Fargate: strategies → `daily_scan_signals` |
| **Scanner Aggregator** | ✅ Defined | Global rank → `stock_picks` → staging cleanup |
| **`daily_scan_signals` table** | ✅ Schema ready | Intra-day staging table for worker output |
| **SNS Alerts** | ✅ Configured | Failure notifications on any stage |
| **Resampling** | On-the-fly | Backtester resamples 1d → Fibonacci intervals at query time |

### Serving Layer (📋 MVP Design — Ready for Implementation)

| Component | Status | Description |
|-----------|--------|-------------|
| **Quote Service** | 📋 Designed | Latest price endpoint (on-demand REST API) |
| **Backtest API** | 📋 Designed | Historical data queries from RDS/S3 |
| **Alert Service** | 📋 Designed | Scheduled checks (future, not real-time) |
| **API Gateway** | ⚠️ Not Deployed | REST API endpoints |
| **Redis Cache** | ⚠️ Not Deployed | Quote caching (60s TTL) |

### Speed Layer (📁 Archived)

A Kinesis Data Streams + Flink real-time pipeline was designed (ECS stream fetcher, Flink SQL resampler apps, DynamoDB signal sink). Parked for MVP — code preserved in `speed_layer/Archive/` for future reference.

---

## 📊 Data Pipeline Summary

### Daily Flow (Fully Automated via Step Functions)

```
Market Close (4:00 PM ET)
         │
         ▼ 4:05 PM ET (21:05 UTC)
   EventBridge → Step Functions Pipeline
         │
         ▼ STAGE 1 — ~3 min (Parallel Lambda)
   ┌─────────────┬──────────────────┐
   │ OHLCV Fetch │  Metadata Fetch  │  ← 2 retries each
   └─────────────┴──────────────────┘
         │
         ▼ STAGE 2 — ~30 sec (Lambda)
   Partition Symbols
   (queries RDS once → writes 10 chunk files to S3)
         │
         ▼ STAGE 3 — ~10–20 min (Batch Array Job, 10 containers in parallel)
   Scanner Workers x10
   (each downloads its S3 chunk, loads OHLCV, runs strategies)
   → writes raw signals to daily_scan_signals (RDS staging)
         │
         ▼ STAGE 4 — ~1–2 min (Batch single container)
   Scanner Aggregator
   (reads all signals → global rank → stock_picks → cleanup staging)
         │
         ▼
   ✅ Pipeline Complete (~15–25 min total)

   ON FAILURE (any stage) → SNS Alert → Email notification
```

### Monthly Flow (Maintenance)
```
Vacuum Script (local) → Deep clean old date files if needed
```

---

## 💰 Estimated Monthly Costs (MVP)

| Service | Cost |
|---------|------|
| **Batch Layer** | |
| Lambda (fetchers + partitioner) | $5 |
| RDS (t3.micro) | $20 |
| S3 Storage | $10 |
| AWS Batch (scanner array + aggregator) | $20 |
| ECR (scanner image) | $1 |
| Step Functions | $2 |
| SNS Alerts | $1 |
| **Batch Layer Total** | **$59** |
| | |
| **Serving Layer (MVP)** | |
| Quote Service (Lambda + Redis) | $5 |
| Backtest API (Lambda) | $3 |
| API Gateway | $2 |
| **Serving Layer Total** | **$10** |
| | |
| **TOTAL MVP** | **~$69/month** |

**Speed Layer (archived):** activating Kinesis + Flink would add ~$110/month. Parked until real-time signals are a product requirement.

---

## 📚 Documentation

- [**Implementation Status**](./IMPLEMENTATION_STATUS.md) - Overall progress and roadmap
- [**Serving Layer Design**](./serving_layer/README.md) - MVP architecture (Quote Service + Backtest API)
- [**Analytics Core**](./shared/analytics_core/README.md) - Analytics Engine documentation
- [**Orchestration Guide**](./batch_layer/infrastructure/orchestration/README.md) - Step Functions pipeline
- [**Database Setup**](./batch_layer/database/README.md) - Database initialization guide

---

## 🎯 Key Benefits

1. **Serverless-First**: Pay only for what you use
2. **Auto-Scaling**: Handle traffic spikes automatically
3. **Managed Services**: Minimal operational overhead
4. **Incremental Processing**: Smart data compaction
5. **Cost-Optimized**: ~$63/month for MVP (vs $200+ with Speed Layer)
6. **Industry Standards**: Delta Lake/Iceberg-style patterns
7. **Orchestrated Pipeline**: Step Functions for reliability & visibility
8. **Simplified Pipeline**: Fetchers only; resampling at backtest time
9. **Failure Alerts**: SNS notifications on pipeline failures
10. **Analytics Engine**: Reusable strategy framework for scanning & backtesting
11. **MVP-Aligned**: "Clarity Over Noise" - no real-time streaming distractions

---

**Last Updated:** March 2026  
**Overall Status:** ✅ Batch Layer Complete (4-stage pipeline with scanner) | 📋 Serving Layer MVP Design Ready | 🧠 Analytics Core Implemented
