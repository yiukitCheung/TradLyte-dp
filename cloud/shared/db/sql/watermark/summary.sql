-- Current ingestion watermark summary across all symbols.
SELECT
    MAX(latest_date)       AS max_date,
    MIN(latest_date)       AS min_date,
    COUNT(DISTINCT symbol) AS symbol_count
FROM data_ingestion_watermark
WHERE is_current = TRUE;
