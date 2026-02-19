# TradLyte - Backend Data Pipeline

## Purpose

This repository contains the **backend data pipeline** for TradLyte, a trading analytics platform focused on "Clarity Over Noise" and "Purpose Over Profit". The pipeline ingests, processes, and serves financial market data to power:

1. **📊 Backtesting Engine** - Historical data for strategy validation
2. **📈 Latest Price Service** - On-demand quote service for dashboard
3. **🤖 Analytics Engine** - Strategy scanning and signal generation

## Solution Architecture

### Production Stack (`aws_lambda_architecture/`)

**Two-Layer MVP Architecture:**

```
Polygon.io API (Daily + On-demand)
         │
         ├── Batch Layer ──► S3 Data Lake + RDS (5yr cache)
         │   └── Analytics Core ──► Strategy Scanning
         │
         └── Serving Layer ──► API Gateway ──► Frontend
             ├── Quote Service (Latest Price)
             ├── Backtest API (Historical Data)
             └── Alert Service (Scheduled Checks)
```

**Components:**
- **Batch Layer**: Daily OHLCV ingestion, Fibonacci resampling (3d-34d), S3 + RDS storage
- **Analytics Core**: Reusable strategy framework (3-step: Setup → Trigger → Exit)
- **Serving Layer**: REST APIs for quotes, backtesting, and alerts

**Cost:** ~$63/month (MVP)

### Development Stack (`prefect_medallion/`)

Local development environment for rapid prototyping:
- Bronze-Silver-Gold pipeline
- Prefect orchestration
- Local PostgreSQL + DuckDB + Redis

## Technology Stack

**Production (AWS):**
- **Storage**: S3 (Parquet) + RDS PostgreSQL (5yr cache)
- **Compute**: AWS Lambda + Batch (serverless)
- **APIs**: API Gateway (REST)
- **Caching**: ElastiCache Redis
- **Orchestration**: Step Functions + EventBridge
- **Analytics**: Polars (high-performance), Pydantic (validation)

**Development (Local):**
- **Database**: PostgreSQL/TimescaleDB
- **Analytics**: DuckDB
- **Orchestration**: Prefect
- **Deployment**: Docker Compose

## Key Features

- **Historical Data**: 63+ years of market data (1962-present)
- **Fibonacci Resampling**: 3d, 5d, 8d, 13d, 21d, 34d intervals
- **Analytics Engine**: Reusable strategy framework with 3-step logic
- **On-Demand Quotes**: Latest price service (REST API, 60s cache)
- **Backtesting API**: Historical data queries from RDS/S3
- **Daily Automation**: Step Functions pipeline (fully automated)

## Project Status

| Component | Status |
|-----------|--------|
| **Batch Layer** | ✅ 95% Complete |
| **Analytics Core** | ✅ Implemented |
| **Serving Layer** | 📋 Designed (MVP) |

**Current Focus:** Batch Layer testing → Serving Layer implementation

## License And Usage

This repository is published for visibility only. It is **private intellectual property** of the owner and is **not open source**.

- You may view the code on GitHub.
- You may **not** copy, modify, distribute, sublicense, sell, or use this code for commercial or non-commercial purposes without explicit written permission from the owner.
- All rights are reserved by the owner.

See the `LICENSE` file for full legal terms.

## Related Projects

- **[TradLyte Frontend](https://github.com/yiukitCheung/TradLyte-frontend.git)** - React frontend application

## Documentation

- [AWS Lambda Architecture](aws_lambda_architecture/README.md) - Production architecture overview
- [Implementation Status](aws_lambda_architecture/IMPLEMENTATION_STATUS.md) - Current progress and roadmap
- [Serving Layer Design](aws_lambda_architecture/serving_layer/README.md) - MVP API design
- [Analytics Core](aws_lambda_architecture/shared/analytics_core/README.md) - Strategy framework documentation

## Repository Structure

```
TradLyte-dp/
├── aws_lambda_architecture/          # Production AWS implementation
│   ├── batch_layer/                  # Daily data processing
│   │   ├── fetching/                 # Lambda: OHLCV + Meta fetchers
│   │   ├── processing/                # AWS Batch: Consolidator + Resampler
│   │   ├── database/                 # RDS schemas + migrations
│   │   └── infrastructure/           # Deployment scripts + Step Functions
│   ├── serving_layer/                # API Layer (MVP)
│   │   └── lambda_functions/         # Quote Service, Backtest API
│   └── shared/                       # Shared utilities
│       └── analytics_core/          # Analytics Engine (strategy framework)
│           ├── indicators/           # Technical indicators (Polars)
│           ├── strategies/            # Strategy framework + library
│           └── models.py             # Pydantic models
├── prefect_medallion/                # Local development environment
│   ├── fetch/                        # Data ingestion
│   ├── process/                      # Transformations
│   └── flows/                        # Prefect workflows
└── docs/                             # Architecture diagrams
```

---

**Private Proprietary Project (All Rights Reserved)**  
**Maintained by:** TradLyte Development Team  
**Last Updated:** January 2026
