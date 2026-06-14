-- Active symbols with only a sliver of history (single watermark row,
-- records_count <= 5) — candidates for a historical backfill.
SELECT sm.symbol, sm.type, sm.name, 'LIMITED_HISTORY' AS reason
FROM symbol_metadata sm
JOIN (
    SELECT symbol, COUNT(*) AS record_count
    FROM data_ingestion_watermark
    GROUP BY symbol
    HAVING COUNT(*) = 1
) counts ON sm.symbol = counts.symbol
JOIN data_ingestion_watermark diw
    ON sm.symbol = diw.symbol AND diw.is_current = TRUE
WHERE LOWER(sm.active) = 'true'
  AND diw.records_count <= 5
ORDER BY sm.type, sm.symbol;
