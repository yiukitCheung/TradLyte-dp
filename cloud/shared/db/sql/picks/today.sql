-- Latest scan_date picks, market-cap ranked. Reads the vw_picks contract.
WITH latest AS (SELECT MAX(scan_date) AS d FROM stock_picks)
SELECT scan_date,
       rank,
       symbol,
       strategy_name,
       signal,
       price,
       confidence,
       metadata,
       market_cap
FROM vw_picks
WHERE scan_date = (SELECT d FROM latest)
  AND (%(industry)s::text  IS NULL OR industry = %(industry)s)
  AND (%(min_mc)s::bigint   IS NULL OR market_cap >= %(min_mc)s)
  AND (%(max_mc)s::bigint   IS NULL OR market_cap <= %(max_mc)s)
ORDER BY market_cap DESC NULLS LAST, rank ASC
LIMIT %(limit)s;
