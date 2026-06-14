-- ============================================================================
-- V003 — Serving-layer read indexes
-- ============================================================================
-- Migrated from batch_layer/database/migrations/v3_serving_indexes.sql.
-- Improves read latency for /screener/quotes, /market/quote, and
-- /picks/{scan_date}/returns (market-cap ordering + per-symbol latest bar).
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_symbol_metadata_marketcap
    ON symbol_metadata(marketCap DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_raw_ohlcv_symbol_interval_ts
    ON raw_ohlcv(symbol, interval, timestamp DESC);
