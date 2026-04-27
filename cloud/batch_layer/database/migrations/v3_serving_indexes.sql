-- ============================================================================
-- TRADLYTE SCHEMA V3 - SERVING LAYER READ INDEXES
-- ============================================================================
-- Purpose:
--   Improve read latency for serving-layer endpoints:
--   - /v1/screener/quotes
--   - /v1/picks/{scan_date}/returns
-- ============================================================================

BEGIN;

CREATE INDEX IF NOT EXISTS idx_symbol_metadata_marketcap
ON symbol_metadata(marketCap DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_raw_ohlcv_symbol_interval_ts
ON raw_ohlcv(symbol, interval, timestamp DESC);

COMMIT;
