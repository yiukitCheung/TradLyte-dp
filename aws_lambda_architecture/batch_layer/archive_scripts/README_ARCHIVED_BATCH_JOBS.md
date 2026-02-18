# Archived: Resampler & Consolidator Batch Jobs

These components were **removed from the active data pipeline**. Resampling is now done **on-the-fly** in the backtester from raw 1d OHLCV.

## Archived files

| File | Original location | Purpose |
|------|-------------------|--------|
| `resampler.py` | `processing/batch_jobs/resampler.py` | DuckDB + S3 Fibonacci resampling (3d, 5d, 8d, …) to silver layer |
| `consolidator.py` | `processing/batch_jobs/consolidator.py` | Merge bronze `date=*.parquet` into `data.parquet` per symbol |
| `deploy_batch_jobs.sh` | `infrastructure/processing/deploy_batch_jobs.sh` | Deploy AWS Batch job definitions for consolidator/resampler |
| `build_batch_container.sh` | `infrastructure/processing/build_batch_container.sh` | Build/push Docker image for Batch jobs |

## Pipeline change

- **Before:** Step Function ran Fetchers → Consolidator (Batch) → Parallel Resamplers (Batch) → Complete.
- **After:** Step Function runs Fetchers → Complete. Multi-timeframe data for backtesting is produced by resampling 1d OHLCV at runtime (e.g. Polars `group_by_dynamic`).

## Re-enabling (if needed)

To run consolidator or resampler again (e.g. for a one-off backfill), use the scripts and job code in this archive. Deploy the state machine and Batch definitions from the archive copies; the Step Function definition was updated so it no longer invokes these steps.
