-- Latest ingested timestamp across all symbols.
SELECT MAX(timestamp) AS latest_date
FROM raw_ohlcv;
