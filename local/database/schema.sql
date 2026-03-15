-- Drop Table 

-- DROP TABLE IF EXISTS resampled;
-- DROP TABLE IF EXISTS processed;
-- DROP TABLE IF EXISTS raw;
-- DROP TABLE IF EXISTS symbol_metadata;
DROP TABLE IF EXISTS signals CASCADE
DROP TABLE IF EXISTS alerts CASCADE
-- ───────────────────────────────────────────────────────
-- Scalable TimescaleDB Schema with Referential Integrity
-- Medallion Architecture: Bronze, Silver, Gold Layers
-- ───────────────────────────────────────────────────────

-- ─────────────
-- Metadata Layer
-- ─────────────
CREATE TABLE IF NOT EXISTS symbol_metadata (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    market TEXT,
    locale TEXT,
	active TEXT,
    primary_exchange TEXT,
	type TEXT
);

-- ─────────────
-- Bronze Layer
-- ─────────────
CREATE TABLE IF NOT EXISTS raw (
    date TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL REFERENCES symbol_metadata(symbol),
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    type TEXT,
    PRIMARY KEY (date, symbol)
);

SELECT create_hypertable('raw', 'date', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_raw_symbol_time ON raw (symbol, date DESC);
SELECT set_chunk_time_interval('raw', INTERVAL '1 month');
ALTER TABLE raw SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol'
);
SELECT add_compression_policy('raw', INTERVAL '1 month');

-- ─────────────
-- Silver Layer
-- ─────────────

CREATE TABLE IF NOT EXISTS resampled_staging (
    date TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    interval INT NOT NULL,
    candle_id INT NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    type TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS indicator_definitions (
    indicator_id SERIAL PRIMARY KEY,
    type TEXT NOT NULL,               -- e.g. 'ema', 'macd'
    parameters JSONB NOT NULL,        -- flexible: {"window":8}, {"short":13,"long":21,"signal":9}
    version TEXT DEFAULT 'v1',
    description TEXT
);

CREATE TABLE IF NOT EXISTS processed (
    date TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL REFERENCES symbol_metadata(symbol),
    interval TEXT NOT NULL,
    indicator_id INT REFERENCES indicator_definitions(indicator_id),
    value DOUBLE PRECISION,
    PRIMARY KEY (date, symbol, interval, indicator_id)
);

SELECT create_hypertable('processed', 'date', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_processed_symbol_type ON processed (symbol, interval, indicator_id, date DESC);
SELECT set_chunk_time_interval('processed', interval '1 month');
ALTER TABLE processed SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol, interval'
);
SELECT add_compression_policy('processed', INTERVAL '1 month');

-- Create processed_staging table
CREATE TABLE IF NOT EXISTS processed_staging (
    date TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    indicator_id INT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (date, symbol, interval, indicator_id)
);

-- Optional (recommended for performance)
-- Make it a hypertable if you are using TimescaleDB
SELECT create_hypertable('processed_staging', 'date', if_not_exists => TRUE);

-- ─────────────
-- Gold Layer
-- ─────────────
-- 1. Create table
CREATE TABLE IF NOT EXISTS alerts (
    alert_id SERIAL,  -- keep it as a surrogate ID but not PK
    symbol TEXT NOT NULL REFERENCES symbol_metadata(symbol),
    date TIMESTAMPTZ NOT NULL,
    interval TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    signal TEXT NOT NULL,
    PRIMARY KEY (symbol, interval, alert_type, date)  -- composite key includes partitioning column
);

-- 2. Convert to hypertable
SELECT create_hypertable('alerts', 'date', if_not_exists => TRUE);

-- 3. Create useful index for fast lookup/filtering
CREATE INDEX IF NOT EXISTS idx_alerts_symbol_type_date 
ON alerts (symbol, alert_type, date DESC);

-- 4. Enable compression (segment by symbol + alert_type)
ALTER TABLE alerts SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol, alert_type'
);

-- 5. Set compression policy (compress alerts older than 1 week)
SELECT add_compression_policy('alerts', INTERVAL '1 week');

-- 6. Set retention policy (delete alerts older than 5 years)
SELECT add_retention_policy('alerts', INTERVAL '5 years');

-- 1. Create table
CREATE TABLE IF NOT EXISTS signals (
    buy_id SERIAL NOT NULL,
    date TIMESTAMPTZ NOT NULL,
	symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    decision INT,
    confidence NUMERIC DEFAULT 0,
	PRIMARY KEY (date, symbol, strategy)
);

-- Hypertable on time column
SELECT create_hypertable('signals', 'date', if_not_exists => TRUE);

-- Index for strategy + date
CREATE INDEX IF NOT EXISTS idx_signals_strategy_date
ON signals (strategy, date DESC);

-- Auto-delete signals older than 5 year
SELECT add_retention_policy('signals', INTERVAL '5 year');

CREATE TABLE IF NOT EXISTS resampled_staging (LIKE resampled INCLUDING ALL);

-- -- Insert Popular indicaotr definion
-- INSERT INTO indicator_definitions (type, parameters, description)
-- VALUES 
-- ('ema', '{"window":5}', 'EMA-5'),
-- ('ema', '{"window":8}', 'EMA-8'),
-- ('ema', '{"window":13}', 'EMA-13'),
-- ('ema', '{"window":21}', 'EMA-21'),
-- ('ema', '{"window":30}', 'EMA-30'),
-- ('ema', '{"window":50}', 'EMA-50'),
-- ('ema', '{"window":100}', 'EMA-100'),
-- ('ema', '{"window":144}', 'EMA-144'),
-- ('ema', '{"window":150}', 'EMA-150'),
-- ('ema', '{"window":169}', 'EMA-169'),
-- ('ema', '{"window":200}', 'EMA-200');

-- -- MACD example
-- INSERT INTO indicator_definitions (type, parameters, description)
-- VALUES 
-- ('macd', '{"short":13, "long":21, "signal":9}', 'MACD 13/21/9');
