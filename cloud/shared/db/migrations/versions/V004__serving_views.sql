-- ============================================================================
-- V004 — Serving read-contract views
-- ============================================================================
-- These views are the single source of truth for how serving endpoints join
-- pick/quote data to symbol_metadata and expose market cap. Encoding the join
-- here means the serving API and any future consumer (batch QA, notebooks,
-- BI tools) share the exact same shape and can never drift apart again.
--
-- Ordering and row limits are intentionally NOT baked into the views — callers
-- add WHERE / ORDER BY / LIMIT so the planner can use indexes. The canonical
-- sort ("market cap DESC NULLS LAST, then rank") lives in the query catalog
-- (sql/picks/*.sql) so it is defined exactly once on the read side.
-- ============================================================================

-- Pick row joined to its symbol metadata (market cap, listing info).
CREATE OR REPLACE VIEW vw_picks AS
SELECT
    sp.scan_date,
    sp.rank,
    sp.symbol,
    sp.strategy_name,
    sp.signal,
    sp.price,
    sp.confidence,
    sp.metadata,
    m.name             AS asset_name,
    m.market,
    m.locale,
    m.type             AS asset_type,
    m.primary_exchange,
    m.industry,
    m.marketcap        AS market_cap
FROM stock_picks sp
LEFT JOIN symbol_metadata m ON m.symbol = sp.symbol;

COMMENT ON VIEW vw_picks IS
    'Ranked picks joined to symbol_metadata; canonical read contract for /picks endpoints.';


-- Symbol metadata joined to its daily bars (screener universe). Callers filter
-- to a single as-of date and apply active/industry/market-cap predicates.
CREATE OR REPLACE VIEW vw_screener_quotes AS
SELECT
    m.symbol, m.name, m.industry, m.marketcap AS market_cap, m.type,
    m.primary_exchange, m.active,
    o.timestamp, o.timestamp::date AS as_of_date,
    o.open, o.high, o.low, o.close, o.volume
FROM symbol_metadata m
JOIN raw_ohlcv o ON o.symbol = m.symbol AND o.interval = '1d';


COMMENT ON VIEW vw_screener_quotes IS
    'symbol_metadata x daily bars; read contract for /screener/quotes (caller filters as_of date).';


-- Per-symbol latest daily bar joined to metadata (single-symbol quote).
CREATE OR REPLACE VIEW vw_market_latest_quote AS
SELECT
    m.symbol, m.name, m.industry, m.marketcap AS market_cap, m.type,
    m.primary_exchange,
    o.timestamp::date AS as_of_date,
    o.open, o.high, o.low, o.close, o.volume
FROM symbol_metadata m
JOIN LATERAL (
    SELECT timestamp, open, high, low, close, volume
    FROM raw_ohlcv
    WHERE symbol = m.symbol AND interval = '1d'
    ORDER BY timestamp DESC
    LIMIT 1
) o ON TRUE;

COMMENT ON VIEW vw_market_latest_quote IS
    'Latest daily bar per symbol joined to metadata; read contract for /market/quote.';
