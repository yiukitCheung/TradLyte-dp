-- SCD Type 2: open a new current watermark row. Positional params (symbol, date).
INSERT INTO data_ingestion_watermark
    (symbol, latest_date, ingested_at, records_count, is_current)
VALUES (%s, %s, NOW(), 1, TRUE);
