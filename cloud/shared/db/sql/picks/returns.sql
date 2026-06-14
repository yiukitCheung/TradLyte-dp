-- Picks for a scan_date plus forward closes at +1/+5/+21 trading days and the
-- most recent close, market-cap ranked. Reads the vw_picks contract.
WITH picks AS (
    SELECT symbol,
           rank,
           strategy_name,
           signal,
           price AS pick_price,
           scan_date,
           market_cap
    FROM vw_picks
    WHERE scan_date = %(scan_date)s::date
      AND (%(industry)s::text  IS NULL OR industry = %(industry)s)
      AND (%(min_mc)s::bigint   IS NULL OR market_cap >= %(min_mc)s)
      AND (%(max_mc)s::bigint   IS NULL OR market_cap <= %(max_mc)s)
)
SELECT p.*,
       (
            SELECT close FROM raw_ohlcv
            WHERE symbol = p.symbol AND interval = '1d' AND timestamp::date > p.scan_date
            ORDER BY timestamp ASC LIMIT 1 OFFSET 0
       ) AS close_1d,
       (
            SELECT close FROM raw_ohlcv
            WHERE symbol = p.symbol AND interval = '1d' AND timestamp::date > p.scan_date
            ORDER BY timestamp ASC LIMIT 1 OFFSET 4
       ) AS close_5d,
       (
            SELECT close FROM raw_ohlcv
            WHERE symbol = p.symbol AND interval = '1d' AND timestamp::date > p.scan_date
            ORDER BY timestamp ASC LIMIT 1 OFFSET 20
       ) AS close_21d,
       (
            SELECT close FROM raw_ohlcv
            WHERE symbol = p.symbol AND interval = '1d'
            ORDER BY timestamp DESC LIMIT 1
       ) AS close_now
FROM picks p
ORDER BY p.market_cap DESC NULLS LAST, p.rank ASC;
