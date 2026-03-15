# Database Migration Guide

This directory contains tools to migrate data from your local Docker PostgreSQL to AWS RDS PostgreSQL.

## 🚀 Quick Start (For 2M+ Rows - RECOMMENDED)

For large datasets (1M+ rows), use the **FAST** migration script:

```bash
# Install dependencies
pip install psycopg2-binary boto3 python-dotenv

# Run fast migration (10-15 minutes for 2M rows)
python migrate_fast.py --use-test-tables --method copy --skip-indexes
```

**Why it's faster:** Uses PostgreSQL COPY + drops indexes during migration → **24x speedup!**

---

## 📋 Prerequisites

1. **Local Docker PostgreSQL running** on port 5434
2. **AWS credentials** configured (for Secrets Manager access)
3. **Python dependencies** installed:
   ```bash
   pip install psycopg2-binary boto3 python-dotenv
   ```

## 📊 Which Script Should I Use?

### ⚡ `migrate_fast.py` - FOR LARGE DATASETS (RECOMMENDED FOR 2M+ ROWS)

**Best for:**
- Initial bulk migration
- 1 million+ rows
- Empty destination tables
- Want maximum speed

**Performance:**
- 2M rows in **10-15 minutes** ⚡
- Uses PostgreSQL COPY (10-100x faster)
- Drops indexes during migration

**Usage:**
```bash
python migrate_fast.py --use-test-tables --method copy --skip-indexes
```

### 🐢 `migrate.py` - For Small Datasets or Incremental Updates

**Best for:**
- Small datasets (< 100K rows)
- Incremental updates
- Need UPSERT (conflict handling)
- Can't drop indexes

**Performance:**
- 2M rows in ~6 hours
- Safer with per-row error handling

**Usage:**
```bash
python migrate.py --use-test-tables --batch-size 10000
```

---

## 🚀 Quick Start (Fast Migration)

### 1. Setup Environment Variables
Make sure your `.env` file has RDS credentials:

```bash
# .env file
POSTGRES_DB=condvest
POSTGRES_USER=yiukitcheung
POSTGRES_PASSWORD=409219

RDS_HOST=dev-batch-postgres.czxi9iwguczt.ca-west-1.rds.amazonaws.com
RDS_PORT=5432
RDS_DATABASE=condvest
RDS_USER=postgres
RDS_PASSWORD=your_rds_password
```

### 2. Run Fast Migration (RECOMMENDED)
```bash
python migrate_fast.py --use-test-tables --method copy --skip-indexes
```

### 3. Or Use Original Method (Slower but Safer)
```bash
python migrate.py --use-test-tables --batch-size 10000
```

### 3. Migrate Specific Table
Migrate only one table:

```bash
# Migrate only symbol metadata
python migrate.py --table symbol_metadata

# Migrate only raw OHLCV data
python migrate.py --table raw_ohlcv
```

### 4. Skip Certain Tables
```bash
# Skip silver layer tables (only migrate metadata and raw data)
python migrate.py --skip-silver

# Skip metadata table
python migrate.py --skip-metadata
```

### 5. Custom Batch Size
Adjust batch size for performance:

```bash
# Larger batches (faster but more memory)
python migrate.py --batch-size 5000

# Smaller batches (safer for large tables)
python migrate.py --batch-size 500
```

## 📊 What Gets Migrated

The script migrates these tables in order:

1. **symbol_metadata** - Stock symbol information
2. **raw_ohlcv** - Daily OHLCV price data  
3. **silver_3d** - 3-day resampled data
4. **silver_5d** - 5-day resampled data
5. **silver_8d** - 8-day resampled data
6. **silver_13d** - 13-day resampled data
7. **silver_21d** - 21-day resampled data
8. **silver_34d** - 34-day resampled data

## 🔄 Migration Behavior

- **Conflict Handling**: Existing records are updated (UPSERT behavior)
- **Batch Processing**: Large tables are migrated in batches for efficiency
- **Verification**: Automatically verifies record counts after migration
- **Logging**: Creates detailed log file for each migration run
- **Safe**: Doesn't delete any data from source or destination

## 📁 Connection Details

### Source (Local Docker PostgreSQL)
- Host: `localhost`
- Port: `5434`
- Database: `condvest`
- User: `yiukitcheung`
- Container: `batch-timescaledb`

### Destination (AWS RDS PostgreSQL)
- Host: `dev-batch-postgres.czxi9iwguczt.ca-west-1.rds.amazonaws.com`
- Port: `5432`
- Database: `condvest`
- User: `postgres`
- Password: Retrieved from AWS Secrets Manager

## 📝 Example Output

```
================================================================================
PostgreSQL Database Migration: Local Docker → AWS RDS
================================================================================
Source:      localhost:5434 (batch-timescaledb)
Destination: dev-batch-postgres.czxi9iwguczt.ca-west-1.rds.amazonaws.com
Database:    condvest
Mode:        LIVE MIGRATION
Batch Size:  1,000 records
================================================================================

2025-10-04 10:30:45 - INFO - Connecting to local PostgreSQL...
2025-10-04 10:30:45 - INFO - ✓ Connected to local PostgreSQL
2025-10-04 10:30:46 - INFO - Loading RDS credentials from AWS Secrets Manager...
2025-10-04 10:30:47 - INFO - ✓ Connected to AWS RDS PostgreSQL

================================================================================
Migrating table: symbol_metadata
================================================================================
2025-10-04 10:30:48 - INFO - Local records: 8,543
2025-10-04 10:30:48 - INFO - RDS records (before): 0
2025-10-04 10:30:49 - INFO -   ✓ Migrated 8,543/8,543 records (100%)
2025-10-04 10:30:49 - INFO - RDS records (after): 8,543
2025-10-04 10:30:49 - INFO - ✓ Successfully migrated 8,543 records

================================================================================
Verifying migration...
================================================================================
2025-10-04 10:35:12 - INFO - ✓ symbol_metadata    : Local=  8,543 | RDS=  8,543
2025-10-04 10:35:13 - INFO - ✓ raw_ohlcv         : Local=425,672 | RDS=425,672
2025-10-04 10:35:13 - INFO - ✓ silver_3d         : Local= 45,231 | RDS= 45,231

================================================================================
MIGRATION SUMMARY
================================================================================
Tables migrated: 8/8
Records migrated: 545,892
Records failed: 0
Duration: 267.3 seconds
Rate: 2,042 records/second
✓ Verification PASSED - All counts match!
================================================================================
```

## 🎯 Alternative Migration Methods

### Method 1: Using pg_dump/pg_restore (Recommended for Large Datasets)

**Pros**: Fastest for large datasets, most reliable
**Cons**: Requires direct access to both servers, needs disk space

```bash
# Step 1: Dump local database
docker exec -it batch-timescaledb pg_dump \
  -U yiukitcheung \
  -d condvest \
  -F c \
  -f /tmp/condvest_backup.dump

# Step 2: Copy dump out of container
docker cp batch-timescaledb:/tmp/condvest_backup.dump ./condvest_backup.dump

# Step 3: Get RDS password
export PGPASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id arn:aws:secretsmanager:ca-west-1:471112909340:secret:dev-batch-postgres-credentials-IWxGJx \
  --region ca-west-1 \
  --query SecretString --output text | jq -r '.password')

# Step 4: Restore to RDS
pg_restore \
  -h dev-batch-postgres.czxi9iwguczt.ca-west-1.rds.amazonaws.com \
  -U postgres \
  -d condvest \
  -F c \
  --no-owner \
  --no-privileges \
  condvest_backup.dump
```

### Method 2: Using AWS DMS (Database Migration Service)

**Pros**: Managed service, can do continuous replication
**Cons**: Costs money, more complex setup

1. Create source endpoint (EC2 instance with local DB)
2. Create target endpoint (RDS)
3. Create replication instance
4. Create migration task
5. Monitor and verify

### Method 3: Using psql COPY command

**Pros**: Simple, good for single tables
**Cons**: Manual for each table, slower than pg_dump

```bash
# Export from local
docker exec -it batch-timescaledb psql -U yiukitcheung -d condvest -c \
  "COPY symbol_metadata TO STDOUT CSV HEADER" > symbol_metadata.csv

# Import to RDS
PGPASSWORD='<password>' psql \
  -h dev-batch-postgres.czxi9iwguczt.ca-west-1.rds.amazonaws.com \
  -U postgres \
  -d condvest \
  -c "\COPY symbol_metadata FROM STDIN CSV HEADER" < symbol_metadata.csv
```

### Method 4: Using this Python Script (migrate.py)

**Pros**: Flexible, good logging, handles errors gracefully
**Cons**: Slower than pg_dump for very large datasets

This is the recommended method for most use cases!

## 🔍 Troubleshooting

### Connection Issues
```bash
# Test local connection
docker exec -it batch-timescaledb psql -U yiukitcheung -d condvest -c "SELECT version();"

# Test RDS connection
aws secretsmanager get-secret-value \
  --secret-id arn:aws:secretsmanager:ca-west-1:471112909340:secret:dev-batch-postgres-credentials-IWxGJx \
  --region ca-west-1 \
  --query SecretString --output text | jq -r '.password'
```

### Memory Issues
If you get memory errors:
```bash
# Use smaller batch size
python migrate.py --batch-size 100
```

### Partial Migration
If migration fails partway through:
```bash
# The script handles conflicts, so you can safely re-run
python migrate.py

# Or migrate remaining tables individually
python migrate.py --table silver_21d
```

## 📊 Checking Migration Status

After migration, verify in RDS:

```bash
# Get RDS password
export PGPASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id arn:aws:secretsmanager:ca-west-1:471112909340:secret:dev-batch-postgres-credentials-IWxGJx \
  --region ca-west-1 \
  --query SecretString --output text | jq -r '.password')

# Connect to RDS
psql -h dev-batch-postgres.czxi9iwguczt.ca-west-1.rds.amazonaws.com \
     -U postgres \
     -d condvest

# Check table counts
SELECT 
    schemaname,
    tablename,
    n_tup_ins as inserts,
    n_tup_upd as updates
FROM pg_stat_user_tables 
WHERE schemaname = 'public'
ORDER BY tablename;
```

## ⚠️ Important Notes

1. **Backup First**: Always have a backup before major migrations
2. **Dry Run**: Always do a dry run first to see what will happen
3. **Network**: Ensure stable network connection (migration can take time)
4. **Costs**: RDS charges for data storage and data transfer
5. **Verification**: Always verify counts after migration
6. **Log Files**: Migration logs are saved with timestamp for debugging

## 📞 Support

If you encounter issues:
1. Check the log file created during migration
2. Verify both databases are accessible
3. Check AWS credentials and Secrets Manager access
4. Try with `--dry-run` first to diagnose issues

