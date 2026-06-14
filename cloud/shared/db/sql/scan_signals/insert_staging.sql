-- Upsert scanner staging signals. Positional params (used with executemany).
INSERT INTO daily_scan_signals
    (scan_date, worker_idx, symbol, strategy_name, signal, price, confidence, metadata)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (scan_date, symbol, strategy_name)
DO UPDATE SET
    signal     = EXCLUDED.signal,
    price      = EXCLUDED.price,
    confidence = EXCLUDED.confidence,
    metadata   = EXCLUDED.metadata,
    worker_idx = EXCLUDED.worker_idx;
