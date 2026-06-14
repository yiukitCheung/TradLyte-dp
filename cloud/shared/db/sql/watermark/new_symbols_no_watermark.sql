-- Active symbols that have no current watermark row (never ingested).
SELECT sm.symbol, sm.type, sm.name, 'NO_WATERMARK' AS reason
FROM symbol_metadata sm
LEFT JOIN data_ingestion_watermark diw
    ON sm.symbol = diw.symbol AND diw.is_current = TRUE
WHERE diw.symbol IS NULL
  AND LOWER(sm.active) = 'true';
