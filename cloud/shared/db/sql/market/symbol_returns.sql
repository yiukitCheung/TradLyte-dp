-- Trailing closes for one symbol used to compute 1d/5d/21d returns.
WITH prices AS (
    SELECT timestamp::date AS trade_date,
           close,
           ROW_NUMBER() OVER (ORDER BY timestamp DESC) AS rn
    FROM raw_ohlcv
    WHERE symbol = %(symbol)s
      AND interval = '1d'
)
SELECT MAX(CASE WHEN rn = 1  THEN trade_date END) AS as_of_date,
       MAX(CASE WHEN rn = 1  THEN close END)      AS close_now,
       MAX(CASE WHEN rn = 2  THEN close END)      AS close_1d_ago,
       MAX(CASE WHEN rn = 6  THEN close END)      AS close_5d_ago,
       MAX(CASE WHEN rn = 22 THEN close END)      AS close_21d_ago
FROM prices;
