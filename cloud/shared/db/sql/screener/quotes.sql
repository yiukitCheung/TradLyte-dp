-- Screener universe as of the latest ingested trading day. Reads the
-- vw_screener_quotes contract. __ORDER_BY__ is replaced by the repository with a
-- whitelisted, validated ORDER BY clause (never raw user input).
WITH md AS (
    SELECT MAX(latest_date) AS as_of
    FROM data_ingestion_watermark
    WHERE is_current
)
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
FROM vw_screener_quotes
WHERE as_of_date = (SELECT as_of FROM md)
  AND LOWER(COALESCE(active::text, 'true')) = 'true'
  AND (%(industry)s::text IS NULL OR industry = %(industry)s)
  AND (%(type)s::text     IS NULL OR type = %(type)s)
  AND (%(min_mc)s::bigint  IS NULL OR market_cap >= %(min_mc)s)
  AND (%(max_mc)s::bigint  IS NULL OR market_cap <= %(max_mc)s)
ORDER BY __ORDER_BY__
LIMIT %(limit)s OFFSET %(offset)s;
