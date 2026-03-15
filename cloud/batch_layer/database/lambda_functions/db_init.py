"""
Database Initialization Lambda Function
Creates all required tables and indexes for the batch layer
"""

import json
import boto3
import psycopg2
import logging
import os
from typing import Dict, Any

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Lambda handler for database initialization
    
    Creates all required tables and indexes for the batch layer
    """
    
    try:
        # Get database connection details from environment
        db_host = os.environ['DB_HOST']
        db_port = os.environ['DB_PORT']
        db_name = os.environ['DB_NAME']
        db_user = os.environ['DB_USER']
        db_password = os.environ['DB_PASSWORD']
        
        # Connect to database
        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            database=db_name,
            user=db_user,
            password=db_password
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        logger.info("Connected to database successfully")
        
        # Execute schema initialization
        schema_sql = get_schema_sql()
        cursor.execute(schema_sql)
        
        logger.info("Database schema initialized successfully")
        
        # Verify tables were created
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name IN ('symbol_metadata', 'raw_ohlcv', 'silver_3d', 'silver_5d', 'silver_8d', 'silver_13d', 'silver_21d', 'silver_34d')
            ORDER BY table_name;
        """)
        
        tables = cursor.fetchall()
        table_names = [row[0] for row in tables]
        
        cursor.close()
        conn.close()
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Database initialized successfully',
                'tables_created': table_names,
                'count': len(table_names)
            })
        }
        
    except Exception as e:
        logger.error(f"Database initialization failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Database initialization failed',
                'message': str(e)
            })
        }

def get_schema_sql() -> str:
    """Return the complete schema initialization SQL"""
    return """
-- ============================================================================
-- PostgreSQL Schema Initialization for AWS RDS (WITHOUT TimescaleDB)
-- ============================================================================

-- Step 1: Create symbol metadata table
CREATE TABLE IF NOT EXISTS symbol_metadata (
    symbol VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255),
    market VARCHAR(100),
    locale VARCHAR(100),
    active VARCHAR(100),
    primary_exchange VARCHAR(100),
    type VARCHAR(100),
    marketCap BIGINT,
    sector VARCHAR(50),
    industry VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Step 2: Create raw OHLCV table (standard PostgreSQL)
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

-- Step 3: Create Fibonacci resampled tables (silver layer)
-- 3D interval table
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

-- 5D interval table
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

-- 8D interval table
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

-- 13D interval table
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

-- 21D interval table
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

-- 34D interval table
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

-- Step 4: Create performance indexes
-- Raw OHLCV indexes (optimized for time-series queries)
CREATE INDEX IF NOT EXISTS idx_raw_ohlcv_symbol_timestamp ON raw_ohlcv(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_raw_ohlcv_timestamp ON raw_ohlcv(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_raw_ohlcv_symbol ON raw_ohlcv(symbol);

-- Silver layer indexes
CREATE INDEX IF NOT EXISTS idx_silver_3d_symbol_date ON silver_3d(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_silver_5d_symbol_date ON silver_5d(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_silver_8d_symbol_date ON silver_8d(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_silver_13d_symbol_date ON silver_13d(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_silver_21d_symbol_date ON silver_21d(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_silver_34d_symbol_date ON silver_34d(symbol, date DESC);

-- Metadata indexes
CREATE INDEX IF NOT EXISTS idx_symbol_metadata_active ON symbol_metadata(active);
CREATE INDEX IF NOT EXISTS idx_symbol_metadata_sector ON symbol_metadata(sector);
CREATE INDEX IF NOT EXISTS idx_symbol_metadata_industry ON symbol_metadata(industry);

-- Step 5: Create sample data for testing
INSERT INTO symbol_metadata (symbol, name, market, locale, active, primary_exchange, type, marketCap, sector, industry) 
VALUES 
    ('AAPL', 'Apple Inc.', 'stocks', 'US', 'true', 'NASDAQ', 'CS', 3000000000000, 'Technology', 'Consumer Electronics'),
    ('MSFT', 'Microsoft Corporation', 'stocks', 'US', 'true', 'NASDAQ', 'CS', 2800000000000, 'Technology', 'Software'),
    ('GOOGL', 'Alphabet Inc.', 'stocks', 'US', 'true', 'NASDAQ', 'CS', 1800000000000, 'Technology', 'Internet Content & Information')
ON CONFLICT (symbol) DO NOTHING;

-- Success message
DO $$
BEGIN
    RAISE NOTICE '==========================================================';
    RAISE NOTICE 'PostgreSQL schema initialization completed successfully!';
    RAISE NOTICE 'Tables created:';
    RAISE NOTICE '  - symbol_metadata (metadata for stock symbols)';
    RAISE NOTICE '  - raw_ohlcv (daily price data)';
    RAISE NOTICE '  - silver_3d to silver_34d (Fibonacci resampled data)';
    RAISE NOTICE 'Performance indexes created for time-series queries';
    RAISE NOTICE 'Sample data inserted for testing';
    RAISE NOTICE 'Your batch layer is ready for data ingestion!';
    RAISE NOTICE '==========================================================';
END $$;
"""
