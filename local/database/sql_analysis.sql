-- check background transaction
SELECT pid, query, state, wait_event_type, wait_event, backend_start
FROM pg_stat_activity
WHERE state != 'idle';

-- kill all transaction related to certain task 
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state != 'idle'
  AND query ILIKE '%INSERT INTO processed%'
  AND pid <> pg_backend_pid();
  
-- Check duplicates in resampled 
 SELECT
    date,
    symbol,
    interval,
    COUNT(*)
FROM resampled
GROUP BY
    date,
    symbol,
    interval
HAVING COUNT(*) > 1;

-- Analyze a table query
EXPLAIN ANALYZE
SELECT * FROM your_table_name
WHERE symbol = 'AAPL' AND interval = '1' AND date > now() - interval '1 day';

-- Analyze table size and index size
SELECT 
    relname AS table_name,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
    pg_size_pretty(pg_relation_size(relid)) AS data_size,
    pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid)) AS index_size
FROM pg_catalog.pg_statio_user_tables
WHERE relname = 'your_table_name';