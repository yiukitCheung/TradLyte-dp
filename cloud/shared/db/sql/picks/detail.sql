-- One symbol's pick row(s) for a scan_date, with listing metadata.
SELECT scan_date,
       rank,
       symbol,
       strategy_name,
       signal,
       price,
       confidence,
       metadata AS pick_metadata,
       asset_name,
       market,
       locale,
       asset_type,
       primary_exchange,
       industry,
       market_cap
FROM vw_picks
WHERE scan_date = %(scan_date)s::date
  AND UPPER(TRIM(symbol)) = %(symbol)s
  AND (%(strategy_name)s::text IS NULL OR strategy_name = %(strategy_name)s)
ORDER BY rank ASC, strategy_name ASC;
