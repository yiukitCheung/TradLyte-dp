-- All bars for many symbols over a half-open timestamp range. __TABLE__ is
-- replaced by the repository with a validated table name (default raw_ohlcv).
-- Positional params: (symbols[], start, end_exclusive).
SELECT *
FROM __TABLE__
WHERE symbol = ANY(%s)
  AND timestamp >= %s
  AND timestamp <  %s
ORDER BY symbol, timestamp ASC;
