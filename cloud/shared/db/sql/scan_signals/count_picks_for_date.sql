-- Row count in stock_picks for a scan_date (post-write sanity check).
SELECT COUNT(*) AS cnt
FROM stock_picks
WHERE scan_date = %(scan_date)s;
