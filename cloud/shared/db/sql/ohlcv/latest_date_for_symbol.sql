-- Latest ingested timestamp for one symbol. Positional param.
SELECT MAX(timestamp) AS latest_date
FROM raw_ohlcv
WHERE symbol = %s;
