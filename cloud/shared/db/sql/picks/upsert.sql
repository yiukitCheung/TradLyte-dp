-- Upsert final ranked picks. Positional params (used with executemany).
INSERT INTO stock_picks
    (scan_date, symbol, strategy_name, signal, price, confidence, metadata, rank)
VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
ON CONFLICT (scan_date, symbol, strategy_name)
DO UPDATE SET
    signal     = EXCLUDED.signal,
    price      = EXCLUDED.price,
    confidence = EXCLUDED.confidence,
    metadata   = EXCLUDED.metadata,
    rank       = EXCLUDED.rank;
