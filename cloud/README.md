# AWS Lambda Architecture Implementation

## Overview

This directory contains the AWS-native implementation of the TradLyte data pipeline using **Lambda Architecture** pattern for financial data processing.

**MVP Strategy:** Two-layer architecture (Batch + Serving) focused on "Clarity Over Noise" mission. Speed Layer removed to avoid encouraging reactive trading behavior.

## 🏗️ Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           TRADLYTE DATA PIPELINE (MVP)                            │
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
│   ┌─────────▼─────────────────────────────────────────────────────────────┐   │
│   │                    BATCH LAYER (✅ 95% Complete)                        │   │
│   │                                                                         │   │
│   │  ┌──────────────────────────────────────────────────────────────────┐  │   │
│   │  │              Step Functions Pipeline                              │  │   │
│   │  │  ┌────────┐ ┌────────┐                                           │  │   │
│   │  │  │Fetchers│→│Consol. │                                           │  │   │
│   │  │  │(Lambda)│ │(Batch) │                                           │  │   │
│   │  │  └────────┘ └───┬────┘                                           │  │   │
│   │  │       ┌─────────▼──────┐                                          │  │   │
│   │  │       │Resamplers (6x) │  → S3 Silver (Fibonacci intervals)       │  │   │
│   │  │       │  (Parallel)    │                                          │  │   │
│   │  │       └────────────────┘                                          │  │   │
│   │  └──────────────────────────────────────────────────────────────────┘  │   │
│   │                                                                         │   │
│   │  ┌──────────────────────────────────────────────────────────────────┐  │   │
│   │  │              Analytics Core (Shared)                              │  │   │
│   │  │  - Technical Indicators (RSI, SMA, MACD, etc.)                   │  │   │
│   │  │  - Strategy Framework (3-step: Setup → Trigger → Exit)           │  │   │
│   │  │  - Pre-built Strategies (Golden Cross, RSI, etc.)                │  │   │
│   │  │  - Composite Strategy Builder (from JSON config)                  │  │   │
│   │  └──────────────────────────────────────────────────────────────────┘  │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                        │                              │                         │
│                        └──────────────┬───────────────┘                         │
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
│   │      │(Latest)  │  │(Historical)│ │(Scheduled)│                          │  │
│   │      └──────────┘  └──────────┘  └──────────┘                          │  │
│   └──────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Directory Structure

```
cloud/
├── batch_layer/                 # ✅ Daily batch processing (100% complete)
│   ├── database/               # Database schemas
│   ├── fetching/               # Lambda functions
│   │   └── lambda_functions/
│   │       ├── daily_ohlcv_fetcher.py
│   │       └── daily_meta_fetcher.py
│   ├── processing/             # AWS Batch jobs
│   │   └── batch_jobs/             # (resampler/consolidator moved to archive_scripts)
│   │       └── vaccume.py          # Cleanup old files (local)
│   ├── archive_scripts/         # Archived: resampler, consolidator, deploy scripts
│   ├── infrastructure/         # Deployment & orchestration
│   │   ├── fetching/           # Lambda deployment scripts
│   │   ├── processing/         # Batch container & job deployment
│   │   └── orchestration/      # Step Functions pipeline
│   │       ├── state_machine_definition.json
│   │       └── deploy_step_functions.sh
│   ├── shared/                 # Shared utilities
│   └── BATCH_LAYER_IMPLEMENTATION_SUMMARY.md
│
├── serving_layer/               # 📋 API serving (MVP Design)
│   ├── lambda_functions/       # Quote Service, Backtest API, Alert Service
│   │   ├── quote_service.py    # Latest price endpoint
│   │   └── backtester/         # Backtesting API (future)
│   └── README.md               # Serving Layer design
│
├── shared/                      # Common utilities
│   ├── clients/                # AWS service clients
│   ├── models/                 # Data models
│   ├── utils/                  # Utility functions
│   └── analytics_core/         # Analytics Engine (The Brain)
│       ├── indicators/         # Technical indicators (Polars)
│       ├── strategies/          # Strategy framework + library
│       ├── inputs.py           # Data loading utilities
│       └── models.py           # Pydantic models
│
└── README.md                    # This file
```

---

## ✅ Implementation Status

### Batch Layer (100% Complete) 🎉

| Component | Status | Description |
|-----------|--------|-------------|
| **Lambda OHLCV Fetcher** | ✅ Deployed | Daily data ingestion from Polygon |
| **Lambda Meta Fetcher** | ✅ Deployed | Symbol metadata updates |
| **Watermark System** | ✅ Working | Incremental processing tracking |
| **S3 Bronze Layer** | ✅ Working | Raw data storage (symbol partitioned) |
| **Step Functions** | ✅ Deployed | Pipeline: Fetchers → Complete (no consolidator/resampler) |
| **Resampling** | 📋 On-the-fly | Backtester resamples 1d→3d/5d/… from raw OHLCV (no silver pre-store) |
| **Archived** | 📁 archive_scripts | consolidator.py, resampler.py, deploy/build scripts (see README_ARCHIVED_BATCH_JOBS.md) |
| **SNS Alerts** | ✅ Configured | Failure notifications |

### Serving Layer (📋 MVP Design - Ready for Implementation)

| Component | Status | Description |
|-----------|--------|-------------|
| **Quote Service** | 📋 Designed | Latest price endpoint (on-demand REST API) |
| **Backtest API** | 📋 Designed | Historical data queries from RDS/S3 |
| **Alert Service** | 📋 Designed | Scheduled checks (future, not real-time) |
| **API Gateway** | ⚠️ Not Deployed | REST API endpoints |
| **Redis Cache** | ⚠️ Not Deployed | Quote caching (60s TTL) |

**Strategic Decision:** Speed Layer removed for MVP - aligns with "Clarity Over Noise" mission. Real-time streaming encourages reactive behavior. Simple on-demand quote service is sufficient and 95% cheaper (~$5/month vs $115/month).

---

## 📊 Data Pipeline Summary

### Daily Flow (Fully Automated via Step Functions)

```
Market Close (4:00 PM ET)
         │
         ▼ 4:05 PM ET (21:05 UTC)
   EventBridge → Step Functions Pipeline
         │
         ▼ STAGE 1 (Parallel)
   ┌─────────────┬──────────────────┐
   │ OHLCV Fetch │  Metadata Fetch  │  ← Lambda (2 retries each)
   └─────────────┴──────────────────┘
         │
         ▼
   ✅ Pipeline Complete

   (Resampling for backtesting is done on-the-fly from raw 1d; consolidator/resampler Batch jobs archived.)

   ON FAILURE → SNS Alert → Email notification
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
| Lambda (fetchers) | $5 |
| RDS (t3.micro) | $20 |
| S3 Storage | $10 |
| AWS Batch | $15 |
| Step Functions | $2 |
| SNS Alerts | $1 |
| **Batch Layer Total** | **$53** |
| | |
| **Serving Layer (MVP)** | |
| Quote Service (Lambda + Redis) | $5 |
| Backtest API (Lambda) | $3 |
| API Gateway | $2 |
| **Serving Layer Total** | **$10** |
| | |
| **TOTAL MVP** | **~$63/month** |

**Cost Savings:** Removed Speed Layer saves ~$110/month (91% reduction)

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

**Last Updated:** January 2026  
**Overall Status:** ✅ Batch Layer 95% Complete | 📋 Serving Layer MVP Design Ready | 🧠 Analytics Core Implemented
