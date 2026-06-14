-- SCD Type 2: close the current watermark row for a symbol. Positional param.
UPDATE data_ingestion_watermark
SET is_current = FALSE
WHERE symbol = %s AND is_current = TRUE;
