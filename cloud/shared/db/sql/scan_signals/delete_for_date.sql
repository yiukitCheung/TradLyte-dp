-- Clear staged signals for a scan_date (after picks are written).
DELETE FROM daily_scan_signals
WHERE scan_date = %(scan_date)s;
