-- Drop a table 
DROP TABLE raw_market_data_30m;

-- Create a table
CREATE TABLE table_name(
    date DATE,
    symbol VARCHAR(10),
    open FLOAT,
    high FLOAT,
    low FLOAT,
    close FLOAT,
    volume FLOAT,
    type VARCHAR(10)
);

-- Create a hypertable
SELECT create_hypertable('raw_market_data_1d', 'date');

-- Create partition for a table
SELECT create_hypertable('raw_market_data_1d', 'date', migrate_data => true);

-- Check a symbol from raw data
SELECT * FROM raw_market_data_1d
WHERE symbol = 'AAPL'
ORDER BY date;

-- Check the number of records by symbol
SELECT symbol, COUNT(*) FROM raw_market_data_1d
GROUP BY symbol;

-- Check the number of records by date
SELECT date, COUNT(*) FROM raw_market_data_1d
GROUP BY date;

-- Check the number of records by date and symbol
SELECT date, symbol, COUNT(*) FROM raw_market_data_1d
GROUP BY date, symbol;

-- Check the last fetch dict
WITH LatestDates AS (
    SELECT symbol, MAX(date) as date
    FROM raw_market_data_1d
    GROUP BY symbol
)
SELECT symbol, date 
FROM LatestDates
ORDER BY symbol

-- Select the distinct symbols
SELECT DISTINCT symbol FROM raw_market_data_1d;