-- Initialize local TimescaleDB for batch layer testing
-- Purpose: Create tables that match AWS Aurora schema, optimized for TimescaleDB with chunking and compression

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Create symbol metadata table (not a hypertable, just regular table)
CREATE TABLE IF NOT EXISTS symbol_metadata (
    symbol VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255),
    market VARCHAR(100),
    locale VARCHAR(100),
    active VARCHAR(100),
    primary_exchange VARCHAR(100),
    type VARCHAR(100),
	marketCap BIGINT,
	description TEXT,
	industry VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create raw OHLCV table and convert to hypertable with chunk and compression
CREATE TABLE IF NOT EXISTS raw_ohlcv (
    symbol VARCHAR(50) NOT NULL,
    open DECIMAL(10,2) NOT NULL,
    high DECIMAL(10,2) NOT NULL,
    low DECIMAL(10,2) NOT NULL,
    close DECIMAL(10,2) NOT NULL,
    volume BIGINT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    interval VARCHAR(10) NOT NULL DEFAULT '1d',
    PRIMARY KEY (symbol, timestamp, interval)
);

-- Convert to hypertable with 7 day chunk interval (adjust as needed)
SELECT create_hypertable('raw_ohlcv', 'timestamp', if_not_exists => TRUE, chunk_time_interval => INTERVAL '7 days');

-- Enable compression on raw_ohlcv, compress by symbol and timestamp
ALTER TABLE raw_ohlcv SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'timestamp DESC',
    timescaledb.compress_segmentby = 'symbol'
);

-- Set compression policy to compress chunks older than 7 days (adjust as needed)
SELECT add_compression_policy('raw_ohlcv', INTERVAL '7 days');

-- Create Fibonacci resampled tables (silver layer) as hypertables with chunking and compression

CREATE TABLE IF NOT EXISTS silver_3d (
    symbol VARCHAR(50) NOT NULL,
    date DATE NOT NULL,
    open DECIMAL(10,2) NOT NULL,
    high DECIMAL(10,2) NOT NULL,
    low DECIMAL(10,2) NOT NULL,
    close DECIMAL(10,2) NOT NULL,
    volume BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, date)
);

SELECT create_hypertable('silver_3d', 'date', if_not_exists => TRUE, chunk_time_interval => INTERVAL '30 days');

ALTER TABLE silver_3d SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'date DESC',
    timescaledb.compress_segmentby = 'symbol'
);

SELECT add_compression_policy('silver_3d', INTERVAL '7 days');

CREATE TABLE IF NOT EXISTS silver_5d (
    symbol VARCHAR(50) NOT NULL,
    date DATE NOT NULL,
    open DECIMAL(10,2) NOT NULL,
    high DECIMAL(10,2) NOT NULL,
    low DECIMAL(10,2) NOT NULL,
    close DECIMAL(10,2) NOT NULL,
    volume BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, date)
);

SELECT create_hypertable('silver_5d', 'date', if_not_exists => TRUE, chunk_time_interval => INTERVAL '30 days');

ALTER TABLE silver_5d SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'date DESC',
    timescaledb.compress_segmentby = 'symbol'
);

SELECT add_compression_policy('silver_5d', INTERVAL '7 days');

CREATE TABLE IF NOT EXISTS silver_8d (
    symbol VARCHAR(50) NOT NULL,
    date DATE NOT NULL,
    open DECIMAL(10,2) NOT NULL,
    high DECIMAL(10,2) NOT NULL,
    low DECIMAL(10,2) NOT NULL,
    close DECIMAL(10,2) NOT NULL,
    volume BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, date)
);

SELECT create_hypertable('silver_8d', 'date', if_not_exists => TRUE, chunk_time_interval => INTERVAL '30 days');

ALTER TABLE silver_8d SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'date DESC',
    timescaledb.compress_segmentby = 'symbol'
);

SELECT add_compression_policy('silver_8d', INTERVAL '7 days');

CREATE TABLE IF NOT EXISTS silver_13d (
    symbol VARCHAR(50) NOT NULL,
    date DATE NOT NULL,
    open DECIMAL(10,2) NOT NULL,
    high DECIMAL(10,2) NOT NULL,
    low DECIMAL(10,2) NOT NULL,
    close DECIMAL(10,2) NOT NULL,
    volume BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, date)
);

SELECT create_hypertable('silver_13d', 'date', if_not_exists => TRUE, chunk_time_interval => INTERVAL '30 days');

ALTER TABLE silver_13d SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'date DESC',
    timescaledb.compress_segmentby = 'symbol'
);

SELECT add_compression_policy('silver_13d', INTERVAL '7 days');

CREATE TABLE IF NOT EXISTS silver_21d (
    symbol VARCHAR(50) NOT NULL,
    date DATE NOT NULL,
    open DECIMAL(10,2) NOT NULL,
    high DECIMAL(10,2) NOT NULL,
    low DECIMAL(10,2) NOT NULL,
    close DECIMAL(10,2) NOT NULL,
    volume BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, date)
);

SELECT create_hypertable('silver_21d', 'date', if_not_exists => TRUE, chunk_time_interval => INTERVAL '30 days');

ALTER TABLE silver_21d SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'date DESC',
    timescaledb.compress_segmentby = 'symbol'
);

SELECT add_compression_policy('silver_21d', INTERVAL '7 days');

CREATE TABLE IF NOT EXISTS silver_34d (
    symbol VARCHAR(50) NOT NULL,
    date DATE NOT NULL,
    open DECIMAL(10,2) NOT NULL,
    high DECIMAL(10,2) NOT NULL,
    low DECIMAL(10,2) NOT NULL,
    close DECIMAL(10,2) NOT NULL,
    volume BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, date)
);

SELECT create_hypertable('silver_34d', 'date', if_not_exists => TRUE, chunk_time_interval => INTERVAL '30 days');

ALTER TABLE silver_34d SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'date DESC',
    timescaledb.compress_segmentby = 'symbol'
);

SELECT add_compression_policy('silver_34d', INTERVAL '7 days');
