-- ============================================================================
-- V001 — Baseline schema (RDS PostgreSQL, non-TimescaleDB)
-- ============================================================================
-- Single source of truth for the core tables and indexes. Consolidates the
-- previously duplicated definitions in:
--   batch_layer/database/schemas/schema_init.sql
--   batch_layer/database/lambda_functions/db_init.py (inline)
--   shared/database/sql/daily_scan_signals.sql
-- All statements are idempotent so this migration cleanly ADOPTS an existing
-- production database (objects already present are left untouched).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Metadata layer
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS symbol_metadata (
    symbol           VARCHAR(50) PRIMARY KEY,
    name             VARCHAR(255),
    market           VARCHAR(100),
    locale           VARCHAR(100),
    active           VARCHAR(100),
    primary_exchange VARCHAR(100),
    type             VARCHAR(100),
    marketCap        BIGINT,
    industry         VARCHAR(255),
    description      TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- Bronze layer (source of truth)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_ohlcv (
    symbol    VARCHAR(50)   NOT NULL,
    open      DECIMAL(10,2) NOT NULL,
    high      DECIMAL(10,2) NOT NULL,
    low       DECIMAL(10,2) NOT NULL,
    close     DECIMAL(10,2) NOT NULL,
    volume    BIGINT        NOT NULL,
    timestamp TIMESTAMP     NOT NULL,
    interval  VARCHAR(10)   NOT NULL DEFAULT '1d',
    PRIMARY KEY (symbol, timestamp, interval)
);

-- ----------------------------------------------------------------------------
-- Silver layer (Fibonacci resampled)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver_3d  (symbol VARCHAR(50) NOT NULL, date DATE NOT NULL, open DECIMAL(10,2) NOT NULL, high DECIMAL(10,2) NOT NULL, low DECIMAL(10,2) NOT NULL, close DECIMAL(10,2) NOT NULL, volume BIGINT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS silver_5d  (symbol VARCHAR(50) NOT NULL, date DATE NOT NULL, open DECIMAL(10,2) NOT NULL, high DECIMAL(10,2) NOT NULL, low DECIMAL(10,2) NOT NULL, close DECIMAL(10,2) NOT NULL, volume BIGINT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS silver_8d  (symbol VARCHAR(50) NOT NULL, date DATE NOT NULL, open DECIMAL(10,2) NOT NULL, high DECIMAL(10,2) NOT NULL, low DECIMAL(10,2) NOT NULL, close DECIMAL(10,2) NOT NULL, volume BIGINT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS silver_13d (symbol VARCHAR(50) NOT NULL, date DATE NOT NULL, open DECIMAL(10,2) NOT NULL, high DECIMAL(10,2) NOT NULL, low DECIMAL(10,2) NOT NULL, close DECIMAL(10,2) NOT NULL, volume BIGINT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS silver_21d (symbol VARCHAR(50) NOT NULL, date DATE NOT NULL, open DECIMAL(10,2) NOT NULL, high DECIMAL(10,2) NOT NULL, low DECIMAL(10,2) NOT NULL, close DECIMAL(10,2) NOT NULL, volume BIGINT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS silver_34d (symbol VARCHAR(50) NOT NULL, date DATE NOT NULL, open DECIMAL(10,2) NOT NULL, high DECIMAL(10,2) NOT NULL, low DECIMAL(10,2) NOT NULL, close DECIMAL(10,2) NOT NULL, volume BIGINT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (symbol, date));

-- ----------------------------------------------------------------------------
-- Operational metadata
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS batch_jobs (
    job_id            VARCHAR(100) PRIMARY KEY,
    job_type          VARCHAR(50)  NOT NULL,
    status            VARCHAR(20)  NOT NULL,
    start_time        TIMESTAMP    NOT NULL,
    end_time          TIMESTAMP,
    symbols_processed TEXT,
    records_processed INTEGER      DEFAULT 0,
    error_message     TEXT,
    created_at        TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_ingestion_watermark (
    watermark_id  BIGSERIAL   PRIMARY KEY,
    symbol        VARCHAR(20) NOT NULL,
    latest_date   DATE        NOT NULL,
    ingested_at   TIMESTAMP   DEFAULT NOW(),
    records_count BIGINT      DEFAULT 0,
    is_current    BOOLEAN     DEFAULT TRUE
);

-- ----------------------------------------------------------------------------
-- Scanner layer (staging + final ranked picks)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_scan_signals (
    scan_date     DATE          NOT NULL,
    worker_idx    SMALLINT      NOT NULL,
    symbol        VARCHAR(50)   NOT NULL,
    strategy_name VARCHAR(255)  NOT NULL,
    signal        VARCHAR(10)   NOT NULL CHECK (signal IN ('BUY', 'SELL', 'HOLD')),
    price         DECIMAL(12,4) NOT NULL,
    confidence    DECIMAL(5,4),
    metadata      JSONB         DEFAULT '{}'::jsonb,
    created_at    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scan_date, symbol, strategy_name)
);

CREATE TABLE IF NOT EXISTS stock_picks (
    scan_date     DATE          NOT NULL,
    rank          INTEGER       NOT NULL CHECK (rank > 0),
    symbol        VARCHAR(50)   NOT NULL,
    strategy_name VARCHAR(255)  NOT NULL,
    signal        VARCHAR(10)   NOT NULL CHECK (signal IN ('BUY', 'SELL', 'HOLD')),
    price         DECIMAL(12,4) NOT NULL,
    confidence    DECIMAL(5,4)  NOT NULL DEFAULT 0.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    metadata      JSONB         DEFAULT '{}'::jsonb,
    created_at    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    -- One row per (scan_date, symbol, strategy_name) — matches the aggregator's
    -- ON CONFLICT target. rank is per-strategy-group and intentionally NOT unique
    -- (multiple strategies each emit a rank 1 for the same scan_date).
    PRIMARY KEY (scan_date, symbol, strategy_name)
);

-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_raw_ohlcv_symbol_timestamp ON raw_ohlcv(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_raw_ohlcv_timestamp        ON raw_ohlcv(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_raw_ohlcv_interval         ON raw_ohlcv(interval);
CREATE INDEX IF NOT EXISTS idx_raw_ohlcv_symbol           ON raw_ohlcv(symbol);

CREATE INDEX IF NOT EXISTS idx_silver_3d_symbol_date  ON silver_3d(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_silver_5d_symbol_date  ON silver_5d(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_silver_8d_symbol_date  ON silver_8d(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_silver_13d_symbol_date ON silver_13d(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_silver_21d_symbol_date ON silver_21d(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_silver_34d_symbol_date ON silver_34d(symbol, date DESC);

CREATE INDEX IF NOT EXISTS idx_symbol_metadata_active ON symbol_metadata(active);
CREATE INDEX IF NOT EXISTS idx_symbol_metadata_type   ON symbol_metadata(type);
CREATE INDEX IF NOT EXISTS idx_symbol_metadata_market ON symbol_metadata(market);

CREATE INDEX IF NOT EXISTS idx_batch_jobs_job_type   ON batch_jobs(job_type);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_status     ON batch_jobs(status);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_start_time ON batch_jobs(start_time DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_current_symbol
    ON data_ingestion_watermark(symbol) WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS idx_watermark_symbol_current
    ON data_ingestion_watermark(symbol, is_current) WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS idx_watermark_latest_date
    ON data_ingestion_watermark(latest_date) WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_daily_scan_signals_date ON daily_scan_signals(scan_date);
CREATE INDEX IF NOT EXISTS idx_stock_picks_date_rank   ON stock_picks(scan_date, rank);
CREATE INDEX IF NOT EXISTS idx_stock_picks_symbol_date ON stock_picks(symbol, scan_date);
