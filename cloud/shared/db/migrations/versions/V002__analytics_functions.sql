-- ============================================================================
-- V002 — Analytics helper functions (set-returning / scalar)
-- ============================================================================
-- Migrated from batch_layer/database/schemas/functions.sql. The ad-hoc test
-- SELECT statements that lived at the bottom of that file were dropped — they
-- are not schema and would error on an empty database. CREATE OR REPLACE keeps
-- this idempotent.
-- ============================================================================

CREATE OR REPLACE FUNCTION Fetch_Symbol_Range(
    p_symbol VARCHAR(10),
    p_days   INTEGER
)
RETURNS TABLE (
    open_p   DECIMAL,
    high_p   DECIMAL,
    close_p  DECIMAL,
    low_p    DECIMAL,
    volume_p BIGINT,
    ts_p     TIMESTAMP
)
AS $$
BEGIN
    RETURN QUERY
    SELECT r.open, r.high, r.close, r.low, r.volume, r.timestamp
    FROM raw_ohlcv r
    WHERE r.symbol = p_symbol
      AND r.timestamp >= CURRENT_DATE - (p_days * INTERVAL '1 day')
    ORDER BY r.timestamp ASC;
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION Fetch_Symbol_Period(
    p_symbol      VARCHAR(10),
    p_period_type VARCHAR(3),
    p_end_date    DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    open_p   DECIMAL,
    high_p   DECIMAL,
    close_p  DECIMAL,
    low_p    DECIMAL,
    volume_p BIGINT,
    ts_p     TIMESTAMP
)
AS $$
DECLARE
    v_start_date DATE;
BEGIN
    IF p_period_type = 'YTD' THEN
        v_start_date := DATE_TRUNC('year', p_end_date)::DATE;
    ELSIF p_period_type = 'MTD' THEN
        v_start_date := DATE_TRUNC('month', p_end_date)::DATE;
    ELSE
        RAISE EXCEPTION 'Unsupported period type: %', p_period_type;
    END IF;

    RETURN QUERY
    SELECT r.open, r.high, r.close, r.low, r.volume, r.timestamp
    FROM raw_ohlcv r
    WHERE r.symbol = p_symbol
      AND r.timestamp BETWEEN v_start_date AND p_end_date
    ORDER BY r.timestamp ASC;
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION Growth(
    p_symbol VARCHAR(10),
    p_days   INTEGER
)
RETURNS TABLE (percent_change DOUBLE PRECISION)
AS $$
DECLARE
    v_old_price DECIMAL;
    v_new_price DECIMAL;
BEGIN
    SELECT close INTO v_old_price
    FROM raw_ohlcv
    WHERE symbol = p_symbol
      AND timestamp <= CURRENT_DATE - (p_days * INTERVAL '1 day')
    ORDER BY timestamp DESC
    LIMIT 1;

    SELECT close INTO v_new_price
    FROM raw_ohlcv
    WHERE symbol = p_symbol
    ORDER BY timestamp DESC
    LIMIT 1;

    IF v_old_price IS NULL OR v_new_price IS NULL THEN
        RAISE EXCEPTION 'Insufficient data to compute growth for symbol %', p_symbol;
    END IF;

    RETURN QUERY
    SELECT (((v_new_price - v_old_price) / v_old_price) * 100)::DOUBLE PRECISION;
END;
$$ LANGUAGE plpgsql;
