# Quick Start Guide - Prefect Medallion Pipeline

## Prerequisites Check
- ✅ Python 3.8+ installed
- ✅ Prefect installed
- ✅ .env file exists in parent directory

## Step-by-Step Setup

### 1. Navigate to the prefect_medallion directory
```bash
cd prefect_medallion
```

### 2. Create and activate virtual environment
```bash
# Create virtual environment
python3 -m venv .dp

# Activate virtual environment
source .dp/bin/activate  # On macOS/Linux
# OR on Windows: .dp\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Verify environment variables
Make sure your `.env` file (in parent directory) contains:
- `POLYGON_API_KEY` - Your Polygon.io API key
- `POSTGRES_URL` - PostgreSQL connection string (format: postgresql://user:password@host:port/dbname)
- `KAFKA_BROKER` (optional) - Kafka broker URL
- `REDIS_URL` (optional) - Redis connection URL

### 5. Start Prefect Server
```bash
# Option 1: Use the provided script
./start_prefect.sh

# Option 2: Manual start
source .dp/bin/activate
prefect server start --host 127.0.0.1 --port 4200
```

The Prefect UI will be available at: **http://localhost:4200**

### 6. Run Flows (in a new terminal)

#### Option A: Run individual flows directly
```bash
cd prefect_medallion
source .dp/bin/activate

# Bronze pipeline (data extraction)
python flows/make_bronze_pipeline.py

# Silver pipeline (data processing)
python flows/make_silver_pipeline.py

# Gold pipeline (analytics)
python flows/make_gold_pipeline.py
```

#### Option B: Use Prefect deployments (scheduled)
```bash
# Deploy flows (if not already deployed)
prefect deploy flows/make_bronze_pipeline.py:bronze_pipeline -n bronze-pipeline
prefect deploy flows/make_silver_pipeline.py:silver_pipeline -n silver-pipeline
prefect deploy flows/make_gold_pipeline.py:gold_pipeline -n gold-pipeline

# Run a deployment manually
prefect deployment run bronze-pipeline/bronze-pipeline
```

## Troubleshooting

### Prefect server won't start
- Check if port 4200 is already in use: `lsof -i :4200`
- Kill existing process: `kill -9 <PID>`
- Try a different port: `prefect server start --port 4201`

### Database connection issues
- Verify PostgreSQL is running: `pg_isready -h localhost -p 5434`
- Check POSTGRES_URL format in .env file
- Ensure database exists and credentials are correct

### Missing dependencies
- Activate virtual environment: `source .dp/bin/activate`
- Reinstall: `pip install -r requirements.txt`

### Environment variables not loading
- Ensure .env file is in parent directory (data_pipeline/.env)
- Check that `python-dotenv` is installed
- Verify .env file has correct format (KEY=value, no spaces around =)

## Quick Commands Reference

```bash
# Start Prefect server
prefect server start

# View all flows
prefect flow ls

# View all deployments
prefect deployment ls

# Run a flow manually
prefect deployment run <deployment-name>/<flow-name>

# View flow runs
prefect flow-run ls

# Check Prefect server status
prefect server status
```

## Architecture Overview

1. **Bronze Layer**: Extracts raw data from Polygon.io API → PostgreSQL
2. **Silver Layer**: Processes and resamples data → DuckDB
3. **Gold Layer**: Generates trading signals and analytics → Parquet files

Each layer runs as a separate Prefect flow and can be executed independently.

