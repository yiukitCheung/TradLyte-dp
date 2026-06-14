-- Bulk upsert of symbol metadata. Used with psycopg2 execute_values.
INSERT INTO symbol_metadata (
    symbol, name, market, locale, active,
    primary_exchange, type, marketcap, industry, description
)
VALUES %s
ON CONFLICT (symbol)
DO UPDATE SET
    name             = EXCLUDED.name,
    market           = EXCLUDED.market,
    locale           = EXCLUDED.locale,
    active           = EXCLUDED.active,
    primary_exchange = EXCLUDED.primary_exchange,
    type             = EXCLUDED.type,
    marketcap        = EXCLUDED.marketcap,
    industry         = EXCLUDED.industry,
    description      = EXCLUDED.description;
