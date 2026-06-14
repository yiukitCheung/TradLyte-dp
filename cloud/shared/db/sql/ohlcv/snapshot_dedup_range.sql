-- Deduped 1d bars over a half-open timestamp range [from, to). One row per
-- (symbol, calendar day): the latest intraday bar. Positional params.
-- Numeric columns cast to float8 so psycopg2 never returns Decimal.
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
  AND timestamp >= %s
  AND timestamp <  %s
ORDER BY symbol, timestamp::date, timestamp DESC;
