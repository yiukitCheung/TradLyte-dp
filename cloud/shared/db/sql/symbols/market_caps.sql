-- Market cap per symbol for ranking tie-breaks.
SELECT symbol, marketcap
FROM symbol_metadata
WHERE symbol = ANY(%(symbols)s);
