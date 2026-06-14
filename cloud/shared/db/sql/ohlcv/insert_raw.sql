-- Bulk upsert of raw daily/intraday bars. Used with psycopg2 execute_values
-- (the VALUES %s placeholder is expanded client-side).
INSERT INTO raw_ohlcv (timestamp, symbol, open, high, low, close, volume, interval)
VALUES %s
ON CONFLICT (timestamp, symbol, interval)
DO UPDATE SET
    open   = EXCLUDED.open,
    high   = EXCLUDED.high,
    low    = EXCLUDED.low,
    close  = EXCLUDED.close,
    volume = EXCLUDED.volume;
