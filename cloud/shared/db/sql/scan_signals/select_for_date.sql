-- All staged signals for a scan_date, optionally filtered to a set of
-- strategy names (pass NULL for all). %(strategies)s is a text[] or NULL.
SELECT symbol,
       scan_date::text AS date,
       strategy_name,
       signal,
       price,
       confidence,
       metadata
FROM daily_scan_signals
WHERE scan_date = %(scan_date)s
  AND (%(strategies)s::text[] IS NULL OR strategy_name = ANY(%(strategies)s));
