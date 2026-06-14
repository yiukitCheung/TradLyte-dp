-- Deduped 1d bars for a single calendar day. One row per symbol: the latest
-- intraday bar. Positional param. Numeric columns cast to float8.
SELECT DISTINCT ON (symbol, timestamp::date)
       symbol,
       timestamp::date AS date,
       open::float8    AS open,
       high::float8    AS high,
       low::float8     AS low,
       close::float8   AS close,
       volume::float8  AS volume
FROM raw_ohlcv
WHERE interval = '1d'
  AND timestamp::date = %s
ORDER BY symbol, timestamp::date, timestamp DESC;
