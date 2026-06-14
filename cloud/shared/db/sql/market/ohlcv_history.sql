-- OHLCV history for one symbol/interval over an optional date range.
-- __SORT__ is replaced by the repository with a validated ASC/DESC keyword.
SELECT symbol,
       interval,
       timestamp,
       timestamp::date AS trading_date,
       open,
       high,
       low,
       close,
       volume
FROM raw_ohlcv
WHERE symbol = %(symbol)s
  AND interval = %(interval)s
  AND (%(start_date)s::date IS NULL OR timestamp::date >= %(start_date)s::date)
  AND (%(end_date)s::date   IS NULL OR timestamp::date <= %(end_date)s::date)
ORDER BY timestamp __SORT__
LIMIT %(limit)s;
