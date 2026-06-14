-- Latest daily bar for one symbol with listing metadata.
SELECT symbol,
       name,
       industry,
       market_cap,
       type,
       primary_exchange,
       as_of_date,
       open,
       high,
       low,
       close,
       volume
FROM vw_market_latest_quote
WHERE symbol = %(symbol)s
LIMIT 1;
