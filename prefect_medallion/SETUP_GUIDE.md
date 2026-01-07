# Prefect Medallion Pipeline - Setup Guide

## Overview

This guide will help you set up and run the Prefect Medallion data pipeline locally. The pipeline follows a Bronze → Silver → Gold architecture for processing financial market data.

## Prerequisites

1. **Python 3.8+** installed
2. **PostgreSQL/TimescaleDB** running locally or remotely
3. **Redis** (optional, for caching)
4. **Polygon.io API Key** (for market data)

## Step 1: Environment Setup

### 1.1 Create Virtual Environment

```bash
cd prefect_medallion
python3 -m venv .dp
source .dp/bin/activate  # On Windows: .dp\Scripts\activate
```

### 1.2 Install Dependencies

```bash
pip install -r requirements.txt
```

### 1.3 Configure Environment Variables

Create a `.env` file in the **parent directory** (or in `prefect_medallion/` if you prefer):

```bash
# Polygon.io API
POLYGON_API_KEY=your_polygon_api_key_here

# PostgreSQL Connection
POSTGRES_URL=postgresql://user:password@localhost:5434/condvest

# Optional: Redis (for caching)
REDIS_URL=redis://localhost:6379/0

# Optional: Kafka (for streaming)
KAFKA_BROKER=localhost:9092
```

**Note:** The pipeline will look for `.env` in:
1. Parent directory (`../.env`)
2. Current directory (`./.env`)

## Step 2: Database Setup

### 2.1 Create Database Schema

Connect to your PostgreSQL database and run:

```bash
psql -h localhost -p 5434 -U your_user -d condvest -f database/schema.sql
```

Or manually execute the SQL in `database/schema.sql` to create:
- `test_raw_ohlcv` table (Bronze layer)
- `test_symbol_metadata` table (symbol metadata)

### 2.2 Verify Database Connection

Test the connection:

```bash
python -c "from tools.postgres_client import PostgresTools; import os; from dotenv import load_dotenv; load_dotenv(); pt = PostgresTools(os.getenv('POSTGRES_URL')); print('✅ Database connection successful')"
```

## Step 3: Configuration

### 3.1 Review Settings

Edit `config/settings.yaml` to match your setup:

```yaml
data_extract:
  meta:
    table_name: "test_symbol_metadata"
    chunk_size: 100
    batch_size: 100
    max_workers: 3
  raw:
    table_name: "test_raw_ohlcv"
    rate_limit: 10
    max_workers: 10

process:
  silver_db_path: process/storage/silver/resampled.duckdb
  gold_path: process/storage/gold
  postgres_source:
    host: localhost
    port: 5434
    dbname: condvest
    user_env_var: your_username
    password_env_var: your_password
    schema: public
    raw_ohlcv_table: test_raw_ohlcv

mode: "reset"  # or "catch_up" for incremental processing
```

## Step 4: Start Prefect Server

### 4.1 Start Server

```bash
# Option 1: Use the provided script
./start_prefect.sh

# Option 2: Manual start
source .dp/bin/activate
prefect server start --host 127.0.0.1 --port 4200
```

The Prefect UI will be available at: **http://localhost:4200**

### 4.2 Verify Server is Running

```bash
prefect server status
```

## Step 5: Run the Pipeline

### 5.1 Run Individual Flows

#### Bronze Pipeline (Data Extraction)

```bash
# Option 1: Direct Python execution
source .dp/bin/activate
python flows/make_bronze_pipeline.py

# Option 2: Using the run script
./run_flow.sh bronze
```

**What it does:**
- Fetches symbol metadata from Polygon.io
- Extracts daily OHLCV data for active symbols
- Ingests data into PostgreSQL (`test_raw_ohlcv` table)

#### Silver Pipeline (Data Processing)

```bash
python flows/make_silver_pipeline.py
# OR
./run_flow.sh silver
```

**What it does:**
- Reads raw OHLCV data from PostgreSQL
- Resamples data into multiple timeframes (1m, 3m, 5m, 8m, 13m, 21m, 34m, 55m)
- Stores resampled data in DuckDB (`process/storage/silver/resampled.duckdb`)

#### Gold Pipeline (Analytics)

```bash
python flows/make_gold_pipeline.py
# OR
./run_flow.sh gold
```

**What it does:**
- Loads resampled data from DuckDB
- Calculates technical indicators (RSI, MACD, etc.)
- Applies trading strategies (Vegas Channel)
- Generates trading signals and alerts
- Saves results to Parquet files (`process/storage/gold/`)

### 5.2 Run Full Pipeline

Run all three pipelines in sequence:

```bash
./run_flow.sh bronze
./run_flow.sh silver
./run_flow.sh gold
```

## Step 6: Monitor and Debug

### 6.1 Prefect UI

Access the Prefect UI at http://localhost:4200 to:
- View flow runs and their status
- Check task logs
- Monitor execution times
- Debug failed tasks

### 6.2 Check Data

#### Verify Bronze Data

```sql
-- Connect to PostgreSQL
psql -h localhost -p 5434 -U your_user -d condvest

-- Check raw OHLCV data
SELECT COUNT(*), MIN(date), MAX(date) FROM test_raw_ohlcv;

-- Check symbol metadata
SELECT COUNT(*) FROM test_symbol_metadata;
```

#### Verify Silver Data

```python
import duckdb

# Connect to DuckDB
con = duckdb.connect('process/storage/silver/resampled.duckdb')

# List all tables
tables = con.execute("SHOW TABLES").fetchall()
print("Silver tables:", tables)

# Check data in a table
df = con.execute("SELECT * FROM silver_1 LIMIT 10").df()
print(df)
```

#### Verify Gold Data

```python
import polars as pl

# Load gold data
gold_df = pl.read_parquet('process/storage/gold/gold.parquet')
print(gold_df.head())
print(f"Total rows: {len(gold_df)}")
```

## Troubleshooting

### Issue: Prefect Server Won't Start

**Problem:** Port 4200 is already in use

**Solution:**
```bash
# Find process using port 4200
lsof -i :4200

# Kill the process
kill -9 <PID>

# Or use a different port
prefect server start --port 4201
```

### Issue: Database Connection Failed

**Problem:** Cannot connect to PostgreSQL

**Solution:**
1. Verify PostgreSQL is running: `pg_isready -h localhost -p 5434`
2. Check `POSTGRES_URL` in `.env` file
3. Verify database exists: `psql -h localhost -p 5434 -l`
4. Check credentials are correct

### Issue: Import Errors

**Problem:** `ModuleNotFoundError` or import issues

**Solution:**
1. Ensure virtual environment is activated: `source .dp/bin/activate`
2. Reinstall dependencies: `pip install -r requirements.txt`
3. Check Python path in scripts - they should add project root to `sys.path`

### Issue: No Data in Silver Tables

**Problem:** Silver pipeline runs but no tables created

**Solution:**
1. Verify Bronze pipeline ran successfully
2. Check PostgreSQL has data: `SELECT COUNT(*) FROM test_raw_ohlcv;`
3. Check DuckDB file exists: `ls -lh process/storage/silver/resampled.duckdb`
4. Review Silver pipeline logs in Prefect UI

### Issue: Configuration Key Errors

**Problem:** `KeyError` when accessing settings

**Solution:**
1. Verify `config/settings.yaml` has all required keys
2. Check for typos in configuration keys
3. Ensure `gold_path` (not `gold`) is in settings

## Common Workflows

### Daily Pipeline Run

```bash
# 1. Start Prefect server (if not running)
./start_prefect.sh &

# 2. Run Bronze pipeline (after market close)
./run_flow.sh bronze

# 3. Run Silver pipeline
./run_flow.sh silver

# 4. Run Gold pipeline
./run_flow.sh gold
```

### Reset Mode (Full Reload)

Set `mode: "reset"` in `config/settings.yaml` and run:

```bash
./run_flow.sh bronze  # This will fetch all historical data
./run_flow.sh silver
./run_flow.sh gold
```

### Incremental Mode (Daily Updates)

Set `mode: "catch_up"` in `config/settings.yaml` and run daily:

```bash
./run_flow.sh bronze  # Only fetches new data
./run_flow.sh silver  # Only processes new data
./run_flow.sh gold    # Only analyzes new data
```

## Next Steps

1. **Customize Strategies**: Add your own trading strategies in `process/strategies/`
2. **Add Indicators**: Extend `process/core/indicator.py` with new technical indicators
3. **Optimize Performance**: Adjust `max_workers` and `batch_size` in `config/settings.yaml`
4. **Schedule Flows**: Use Prefect deployments to schedule automatic daily runs

## Additional Resources

- [Prefect Documentation](https://docs.prefect.io/)
- [DuckDB Documentation](https://duckdb.org/docs/)
- [Polars Documentation](https://pola-rs.github.io/polars/)
- [Polygon.io API Docs](https://polygon.io/docs)

## Support

If you encounter issues:
1. Check Prefect UI logs for detailed error messages
2. Review this troubleshooting section
3. Check database connection and data availability
4. Verify all environment variables are set correctly
