-- ─────────────────────────────────────────────────────
-- FUNCTION NAME : Fetch_Symbol_Range
-- DESCRIPTION   : Returns OHLCV data for a given stock symbol over the past N days.
-- USAGE EXAMPLE : SELECT * FROM Fetch_Symbol_Range('AAPL', 30);
-- CREATED BY    : Condvest Analytics Team
-- CREATED DATE  : 2025-12-03
-- NOTES         : Used in real-time and historical backtesting queries.
-- ─────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION Fetch_Symbol_Range(
    p_symbol VARCHAR(10),         -- [Required] Stock symbol (e.g., 'AAPL')
    p_days INTEGER                -- [Required] Number of days to go back from today
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
    SELECT 
        r.open, r.high, r.close, r.low, r.volume, r.timestamp
    FROM 
        raw_ohlcv r
    WHERE 
        r.symbol = p_symbol
    AND 
        r.timestamp >= CURRENT_DATE - (p_days * INTERVAL '1 day')
    ORDER BY 
        r.timestamp ASC;
END;
$$ LANGUAGE plpgsql;

-- ─────────────────────────────────────────────────────
-- FUNCTION NAME : Fetch_Symbol_Period
-- DESCRIPTION   : Returns OHLCV data for a given symbol for YTD or MTD period.
-- USAGE EXAMPLE : SELECT * FROM Fetch_Symbol_Period('AAPL', 'YTD');
-- CREATED BY    : Condvest Analytics Team
-- CREATED DATE  : 2025-12-03
-- NOTES         : Used in trend analytics, seasonal evaluation, and dashboarding.
-- ─────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION Fetch_Symbol_Period(
    p_symbol VARCHAR(10),             -- [Required] Stock symbol (e.g., 'AAPL')
    p_period_type VARCHAR(3),         -- [Required] Period type: 'YTD' or 'MTD'
    p_end_date DATE DEFAULT CURRENT_DATE -- [Optional] Defaults to today
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
    -- Determine start date based on period type
    IF p_period_type = 'YTD' THEN
        v_start_date := DATE_TRUNC('year', p_end_date)::DATE;
    ELSIF p_period_type = 'MTD' THEN
        v_start_date := DATE_TRUNC('month', p_end_date)::DATE;
    ELSE
        RAISE EXCEPTION 'Unsupported period type: %', p_period_type;
    END IF;

    -- Fetch data in date range
    RETURN QUERY
    SELECT 
        r.open, r.high, r.close, r.low, r.volume, r.timestamp
    FROM 
        raw_ohlcv r
    WHERE 
        r.symbol = p_symbol
    AND 
        r.timestamp BETWEEN v_start_date AND p_end_date
    ORDER BY 
        r.timestamp ASC;
END;
$$ LANGUAGE plpgsql;

-- ─────────────────────────────────────────────────────
-- FUNCTION NAME : Growth
-- DESCRIPTION   : Computes % growth in closing price over the last N days
-- USAGE EXAMPLE : SELECT * FROM Growth('AAPL', 30);
-- CREATED BY    : Condvest Analytics Team
-- CREATED DATE  : 2025-12-03
-- NOTES         : Used in Gold Layer signal analytics
-- ─────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION Growth(
    p_symbol VARCHAR(10),     -- [Required] Symbol to analyze
    p_days INTEGER            -- [Required] Number of days to compare
)
RETURNS TABLE (
    percent_change DOUBLE PRECISION
)
AS $$
DECLARE
    v_old_price DECIMAL;
    v_new_price DECIMAL;
BEGIN
    -- Fetch oldest price N days ago
    SELECT close INTO v_old_price
    FROM raw_ohlcv
    WHERE symbol = p_symbol
    AND timestamp <= CURRENT_DATE - (p_days * INTERVAL '1 day')
    ORDER BY timestamp DESC
    LIMIT 1;

    -- Fetch most recent price
    SELECT close INTO v_new_price
    FROM raw_ohlcv
    WHERE symbol = p_symbol
    ORDER BY timestamp DESC
    LIMIT 1;

    -- Handle missing data
    IF v_old_price IS NULL OR v_new_price IS NULL THEN
        RAISE EXCEPTION 'Insufficient data to compute growth for symbol %', p_symbol;
    END IF;

    -- Return % growth
    RETURN QUERY
    SELECT 
    (((v_new_price - v_old_price) / v_old_price) * 100)::DOUBLE PRECISION
        AS growth_pct;
END;
$$ LANGUAGE plpgsql;

-- Test Function

-- Test Fetch Symbol Range
SELECT * FROM Fetch_Symbol_Range('XYZ', 60)
-- Test Fetch Symbol Period
SELECT * FROM Fetch_Symbol_Period('AMD', 'YTD')
-- Test Growth Pct
SELECT * FROM Growth('AAPL', 30)

SELECT symbol FROM data_ingestion_watermark WHERE is_current = 'true'
