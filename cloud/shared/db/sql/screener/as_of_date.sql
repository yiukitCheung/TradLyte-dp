-- Latest ingested trading day across the universe.
SELECT MAX(latest_date) AS as_of_date
FROM data_ingestion_watermark
WHERE is_current;
