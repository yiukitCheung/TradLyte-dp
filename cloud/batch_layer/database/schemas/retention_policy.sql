-- ================================================================================
-- DATA RETENTION POLICY FOR RAW_OHLCV
-- ================================================================================
-- Purpose: Keep only last 3 years + 1 month buffer in raw_ohlcv for fast queries
--          Archive older data to raw_ohlcv_archive table
-- Schedule: Run weekly (e.g., every Sunday at 2 AM)
-- ================================================================================

-- ============================================================
-- STEP 1: CREATE ARCHIVE TABLE (MIRROR OF RAW_OHLCV)
-- ============================================================

-- Create archive table with identical structure to raw_ohlcv
CREATE TABLE IF NOT EXISTS raw_ohlcv_archive (
    symbol VARCHAR(50) NOT NULL,
    open DECIMAL(10,2) NOT NULL,
    high DECIMAL(10,2) NOT NULL,
    low DECIMAL(10,2) NOT NULL,
    close DECIMAL(10,2) NOT NULL,
    volume BIGINT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    interval VARCHAR(10) NOT NULL DEFAULT '1d',
    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- Track when it was archived
    PRIMARY KEY (symbol, timestamp, interval)
);

-- Convert archive table to hypertable (same as raw_ohlcv)
SELECT create_hypertable('raw_ohlcv_archive', 'timestamp', 
    if_not_exists => TRUE, 
    chunk_time_interval => INTERVAL '30 days'  -- Larger chunks for archive
);

-- Enable compression on archive table (aggressive compression for cold storage)
ALTER TABLE raw_ohlcv_archive SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'timestamp DESC',
    timescaledb.compress_segmentby = 'symbol'
);

-- Compress archive data immediately (no delay needed)
SELECT add_compression_policy('raw_ohlcv_archive', INTERVAL '1 day');

-- Create index on archived_at for audit purposes
CREATE INDEX IF NOT EXISTS idx_raw_ohlcv_archive_archived_at 
ON raw_ohlcv_archive (archived_at);

COMMENT ON TABLE raw_ohlcv_archive IS 'Archive table for OHLCV data older than 3 years + 1 month';
COMMENT ON COLUMN raw_ohlcv_archive.archived_at IS 'Timestamp when record was moved from raw_ohlcv to archive';


-- ============================================================
-- STEP 2: CREATE RETENTION LOG TABLE (AUDIT TRAIL)
-- ============================================================

CREATE TABLE IF NOT EXISTS data_retention_log (
    id SERIAL PRIMARY KEY,
    execution_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    retention_threshold TIMESTAMP NOT NULL,  -- Date before which data was archived
    records_archived BIGINT NOT NULL,
    records_deleted BIGINT NOT NULL,
    status VARCHAR(50) NOT NULL,  -- 'success', 'partial_success', 'failed'
    error_message TEXT,
    execution_time_seconds DECIMAL(10,2),
    CONSTRAINT valid_status CHECK (status IN ('success', 'partial_success', 'failed'))
);

COMMENT ON TABLE data_retention_log IS 'Audit log for data retention and archival operations';

-- Create index for querying logs
CREATE INDEX IF NOT EXISTS idx_retention_log_execution_date 
ON data_retention_log (execution_date DESC);


-- ============================================================
-- STEP 3: CREATE STORED PROCEDURE FOR ARCHIVAL PROCESS
-- ============================================================

CREATE OR REPLACE PROCEDURE archive_old_ohlcv_data()
LANGUAGE plpgsql
AS $$
DECLARE
    v_retention_threshold TIMESTAMP;
    v_records_archived BIGINT := 0;
    v_records_deleted BIGINT := 0;
    v_start_time TIMESTAMP;
    v_end_time TIMESTAMP;
    v_execution_time DECIMAL(10,2);
    v_status VARCHAR(50) := 'success';
    v_error_message TEXT := NULL;
BEGIN
    v_start_time := CLOCK_TIMESTAMP();
    
    -- Calculate retention threshold: 3 years + 1 month buffer
    v_retention_threshold := CURRENT_DATE - INTERVAL '3 years 1 month';
    
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Starting Data Archival Process';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Execution Time: %', v_start_time;
    RAISE NOTICE 'Retention Threshold: %', v_retention_threshold;
    RAISE NOTICE 'Will archive data older than: %', v_retention_threshold;
    
    -- ========================================
    -- STEP 3.1: COPY OLD DATA TO ARCHIVE
    -- ========================================
    BEGIN
        RAISE NOTICE 'Step 1: Copying old data to archive table...';
        
        WITH inserted AS (
            INSERT INTO raw_ohlcv_archive (
                symbol, open, high, low, close, volume, timestamp, interval
            )
            SELECT 
                symbol, open, high, low, close, volume, timestamp, interval
            FROM raw_ohlcv
            WHERE timestamp < v_retention_threshold
            ON CONFLICT (symbol, timestamp, interval) DO NOTHING  -- Skip duplicates
            RETURNING 1
        )
        SELECT COUNT(*) INTO v_records_archived FROM inserted;
        
        RAISE NOTICE '✅ Archived % records to raw_ohlcv_archive', v_records_archived;
        
    EXCEPTION WHEN OTHERS THEN
        v_status := 'failed';
        v_error_message := 'Error during archival: ' || SQLERRM;
        RAISE WARNING '❌ Error during archival: %', SQLERRM;
        
        -- Log the failure and exit
        INSERT INTO data_retention_log (
            retention_threshold, records_archived, records_deleted, 
            status, error_message
        ) VALUES (
            v_retention_threshold, 0, 0, v_status, v_error_message
        );
        
        RETURN;  -- Exit procedure on archival error
    END;
    
    -- ========================================
    -- STEP 3.2: DELETE OLD DATA FROM RAW_OHLCV
    -- ========================================
    BEGIN
        RAISE NOTICE 'Step 2: Deleting old data from raw_ohlcv...';
        
        -- Only delete if archival was successful
        IF v_records_archived > 0 THEN
            WITH deleted AS (
                DELETE FROM raw_ohlcv
                WHERE timestamp < v_retention_threshold
                RETURNING 1
            )
            SELECT COUNT(*) INTO v_records_deleted FROM deleted;
            
            RAISE NOTICE '✅ Deleted % records from raw_ohlcv', v_records_deleted;
        ELSE
            RAISE NOTICE '⚠️  No records archived, skipping deletion';
            v_status := 'partial_success';
        END IF;
        
    EXCEPTION WHEN OTHERS THEN
        v_status := 'partial_success';
        v_error_message := 'Error during deletion: ' || SQLERRM;
        RAISE WARNING '❌ Error during deletion: %', SQLERRM;
    END;
    
    -- ========================================
    -- STEP 3.3: VERIFY CONSISTENCY
    -- ========================================
    IF v_records_archived != v_records_deleted THEN
        RAISE WARNING '⚠️  Mismatch: Archived % but deleted % records', 
            v_records_archived, v_records_deleted;
        v_status := 'partial_success';
        v_error_message := COALESCE(v_error_message || '; ', '') || 
            'Record count mismatch: archived ' || v_records_archived || 
            ' but deleted ' || v_records_deleted;
    END IF;
    
    -- ========================================
    -- STEP 3.4: LOG THE RESULTS
    -- ========================================
    v_end_time := CLOCK_TIMESTAMP();
    v_execution_time := EXTRACT(EPOCH FROM (v_end_time - v_start_time));
    
    INSERT INTO data_retention_log (
        retention_threshold,
        records_archived,
        records_deleted,
        status,
        error_message,
        execution_time_seconds
    ) VALUES (
        v_retention_threshold,
        v_records_archived,
        v_records_deleted,
        v_status,
        v_error_message,
        v_execution_time
    );
    
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Data Archival Process Completed';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Status: %', v_status;
    RAISE NOTICE 'Records Archived: %', v_records_archived;
    RAISE NOTICE 'Records Deleted: %', v_records_deleted;
    RAISE NOTICE 'Execution Time: % seconds', v_execution_time;
    RAISE NOTICE '========================================';
    
    -- Commit the transaction
    COMMIT;
    
EXCEPTION WHEN OTHERS THEN
    -- Catch any unexpected errors
    v_error_message := 'Unexpected error: ' || SQLERRM;
    RAISE ERROR '❌ Unexpected error in archival process: %', SQLERRM;
    
    INSERT INTO data_retention_log (
        retention_threshold, records_archived, records_deleted,
        status, error_message
    ) VALUES (
        v_retention_threshold, v_records_archived, v_records_deleted,
        'failed', v_error_message
    );
    
    ROLLBACK;
END;
$$;

COMMENT ON PROCEDURE archive_old_ohlcv_data() IS 
'Weekly archival procedure: Moves data older than 3 years + 1 month from raw_ohlcv to raw_ohlcv_archive, then deletes from raw_ohlcv';


-- ============================================================
-- STEP 4: CREATE HELPER VIEWS FOR MONITORING
-- ============================================================

-- View: Current data distribution in raw_ohlcv
CREATE OR REPLACE VIEW v_raw_ohlcv_data_distribution AS
SELECT
    DATE_TRUNC('month', timestamp) AS month,
    COUNT(*) AS record_count,
    COUNT(DISTINCT symbol) AS unique_symbols,
    MIN(timestamp) AS earliest_timestamp,
    MAX(timestamp) AS latest_timestamp
FROM raw_ohlcv
GROUP BY DATE_TRUNC('month', timestamp)
ORDER BY month DESC;

COMMENT ON VIEW v_raw_ohlcv_data_distribution IS 
'Monthly data distribution in raw_ohlcv for monitoring';

-- View: Archive statistics
CREATE OR REPLACE VIEW v_archive_statistics AS
SELECT
    COUNT(*) AS total_archived_records,
    COUNT(DISTINCT symbol) AS unique_symbols,
    MIN(timestamp) AS earliest_timestamp,
    MAX(timestamp) AS latest_timestamp,
    MIN(archived_at) AS first_archived_at,
    MAX(archived_at) AS last_archived_at,
    pg_size_pretty(pg_total_relation_size('raw_ohlcv_archive')) AS table_size
FROM raw_ohlcv_archive;

COMMENT ON VIEW v_archive_statistics IS 
'Statistics about archived data in raw_ohlcv_archive';

-- View: Retention log summary
CREATE OR REPLACE VIEW v_retention_log_summary AS
SELECT
    execution_date,
    retention_threshold,
    records_archived,
    records_deleted,
    status,
    execution_time_seconds,
    CASE 
        WHEN records_archived = records_deleted THEN '✅ Consistent'
        ELSE '⚠️ Mismatch'
    END AS consistency_check
FROM data_retention_log
ORDER BY execution_date DESC
LIMIT 20;

COMMENT ON VIEW v_retention_log_summary IS 
'Last 20 retention job executions with consistency check';


-- ============================================================
-- STEP 5: USAGE EXAMPLES AND MANUAL EXECUTION
-- ============================================================

-- To manually run the archival process:
-- CALL archive_old_ohlcv_data();

-- To check what will be archived (dry run):
-- SELECT 
--     COUNT(*) AS records_to_archive,
--     MIN(timestamp) AS oldest_record,
--     MAX(timestamp) AS newest_record_to_archive
-- FROM raw_ohlcv
-- WHERE timestamp < CURRENT_DATE - INTERVAL '3 years 1 month';

-- To view current data distribution:
-- SELECT * FROM v_raw_ohlcv_data_distribution;

-- To view archive statistics:
-- SELECT * FROM v_archive_statistics;

-- To view recent archival jobs:
-- SELECT * FROM v_retention_log_summary;

-- To check storage sizes:
-- SELECT 
--     'raw_ohlcv' AS table_name,
--     pg_size_pretty(pg_total_relation_size('raw_ohlcv')) AS size
-- UNION ALL
-- SELECT 
--     'raw_ohlcv_archive' AS table_name,
--     pg_size_pretty(pg_total_relation_size('raw_ohlcv_archive')) AS size;

