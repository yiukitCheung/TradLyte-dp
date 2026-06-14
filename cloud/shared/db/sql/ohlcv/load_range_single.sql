-- All bars for one symbol over a half-open timestamp range. __TABLE__ is
-- replaced by the repository with a validated table name (default raw_ohlcv).
-- Positional params: (symbol, start, end_exclusive).
SELECT *
FROM __TABLE__
WHERE symbol = %s
  AND timestamp >= %s
  AND timestamp <  %s
ORDER BY timestamp ASC;
