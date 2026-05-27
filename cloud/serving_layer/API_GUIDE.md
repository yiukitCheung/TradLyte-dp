# TradLyte Serving API — HTTP guide

FastAPI app packaged as Lambda (`dev-serving-api` by default), exposed through **Amazon API Gateway HTTP API**. Most routes are GET; backtest is POST.

---

## Base URL

After running `infrastructure/serving_api/deploy_http_api.sh`, the script prints:

```text
https://{API_ID}.execute-api.{AWS_REGION}.amazonaws.com/{STAGE_NAME}
```

| Setting | Default (script) |
|--------|---------------------|
| Region | `ca-west-1` |
| Stage | `v1` (`STAGE_NAME`) |
| API name | `dev-serving-http-api` (`API_NAME`) |

**Example**

```text
https://abc123xyz.execute-api.ca-west-1.amazonaws.com/v1
```

Append the route path **without** an extra `/v1` prefix on each resource (the stage is already in the URL). Example:

```text
GET https://abc123xyz.execute-api.ca-west-1.amazonaws.com/v1/picks/today
```

To discover `API_ID` again:

```bash
aws apigatewayv2 get-apis --region ca-west-1 \
  --query "Items[?Name=='dev-serving-http-api'].ApiId | [0]" --output text
```

---

## Authentication

- If the Lambda has **`SERVING_API_KEY`** or **`SERVING_API_KEY_SECRET_ARN`** set, every **protected** route requires header:

  ```http
  x-api-key: <your-key>
  ```

- Secret mode (recommended): set `SERVING_API_KEY_SECRET_ARN` to an AWS Secrets Manager secret containing `SERVING_API_KEY` (or `api_key` / `x_api_key`).
- Env fallback mode: set `SERVING_API_KEY` directly.
- If neither is set, the app does not enforce a key (use only in dev).

- **`GET /health`** is **not** behind the API-key dependency in code; it is intended for load balancers and quick checks. Still register a route in API Gateway if you use `deploy_http_api.sh` (it includes `GET /health`).

API Gateway does **not** validate `x-api-key` natively for HTTP APIs; validation happens inside Lambda.

For browser apps, any key sent from frontend JavaScript is visible to end users. For production, prefer user auth (JWT/Cognito/Lambda authorizer) or route API calls through your own backend/BFF instead of shipping a static shared key in public client code.

---

## CORS

Configured in `deploy_http_api.sh`: allowed methods **GET**, **POST**, **OPTIONS**; allowed headers include **`content-type`** and **`x-api-key`**. Set **`ALLOWED_ORIGIN`** when deploying (e.g. your frontend origin).

---

## Response shapes

### Success (most routes)

```json
{
  "data": {},
  "meta": {}
}
```

Fields vary by endpoint (`cache_hit`, `count`, etc.).

### Errors

Handled routes return JSON such as:

```json
{
  "error": {
    "code": "http_error",
    "message": "..."
  }
}
```

HTTP status reflects the error (`401`, `404`, `500`, …).

---

## Endpoints

Routes must exist in API Gateway — keep **`deploy_http_api.sh`** `ROUTES` array in sync when you add FastAPI paths.

### Health (no API key on app router)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness: `status`, `service`, `timestamp` (UTC) |

### Screener (`/screener` — requires API key if configured)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/screener/quotes` | Filtered universe with latest daily OHLCV as of watermark `as_of` |

**Query parameters** (`/screener/quotes`)

| Param | Type | Default | Notes |
|-------|------|---------|--------|
| `industry` | string | — | Exact match on `symbol_metadata.industry` |
| `type` | string | — | e.g. asset type |
| `min_market_cap` | int | — | `>= 0` |
| `max_market_cap` | int | — | `>= 0` |
| `sort` | string | `marketcap:desc` | `field:asc\|desc`; fields: `marketcap`, `symbol`, `close`, `volume` |
| `limit` | int | `50` | `1–500` |
| `offset` | int | `0` | Pagination |

### Picks (`/picks` — requires API key if configured)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/picks/today` | Latest `scan_date` rows from `stock_picks` (ranked) |
| GET | `/picks/today/metadata` | Same date scope; columns include `metadata` JSON |
| GET | `/picks/detail` | One symbol + scan date, joined with `symbol_metadata` |
| GET | `/picks/{scan_date}/returns` | Per-pick horizons vs `raw_ohlcv` after `scan_date` |

### Backtest (`/backtest` — requires API key if configured)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/backtest` | Run a single-symbol strategy backtest over date range and return performance metrics |

`dev-serving-api` handles request auth/validation and proxies execution to a dedicated backtester Lambda (`BACKTEST_FUNCTION_NAME`, default `dev-serving-backtester`). This keeps serving ZIP packages small while heavy analytics deps (Polars, psycopg2, etc.) live in the containerized backtester function.

**Timeouts.** API Gateway HTTP API integration timeout is set to **30 s** (the HTTP API maximum). Lambda timeouts: `dev-serving-api` = 30 s, `dev-serving-backtester` = 120 s. A request that runs longer than 30 s at the gateway returns `503 Service Unavailable` even if the backtester is still working; keep `(end_date - start_date) ≤ BACKTEST_MAX_LOOKBACK_DAYS` (default `1825` ≈ 5 years) and prefer warm invocations for interactive use.

#### Request body

```json
{
  "strategy_name": "Momentum_Swing",
  "symbol": "AAPL",
  "timeframe": "1d",
  "start_date": "2022-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 10000,
  "components": {
    "setup": { /* SetupComponentConfig */ },
    "trigger": { /* TriggerComponentConfig */ },
    "exit": { /* ExitComponentConfig */ }
  }
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `strategy_name` | string | yes | Free-form identifier echoed back in `meta`; used for logging / catalog joins |
| `symbol` | string | yes | Single ticker, upper-cased server-side |
| `timeframe` | string | no (`1d`) | Base candle interval (`1d`, `1h`, …) |
| `start_date`, `end_date` | date | yes | `YYYY-MM-DD`; `start_date ≤ end_date` and range ≤ `BACKTEST_MAX_LOOKBACK_DAYS` (~5 y) |
| `initial_capital` | number | no (`10000`) | Must be `> 0` |
| `components.setup` | object | yes | See [setup types](#setup-types) |
| `components.trigger` | object | yes | See [trigger types](#trigger-types) |
| `components.exit` | object | yes | See [exit types](#exit-types) |

Validation is done by Pydantic (`RequirementsStrategyConfig`). Unknown types or missing required fields return `400` with a field-level message.

#### Setup types

Setup answers "is the regime/trend valid?" — it gates entries but does not place them.

| `type` | Authoring style | Key fields |
|---|---|---|
| `INDICATOR_THRESHOLD` | Legacy flat | `indicator`, `params`, `operator` (`>` `<` `>=` `<=` `==` `CROSS_ABOVE` `CROSS_BELOW`), `value`, optional `indicator2` |
| `EXPRESSION` | Recommended | `expression`: a recursive boolean tree (see [expression DSL](#expression-dsl)) |
| `NONE` | — | No setup gate; trigger fires on its own |

#### Trigger types

Trigger answers "did the entry happen on this bar?" — when true the engine emits a BUY (or SELL if `signal_value: "SELL"`).

| `type` | Key fields |
|---|---|
| `CANDLE_PATTERN` | `pattern` (`BULLISH_ENGULFING`, `BEARISH_ENGULFING`, `HAMMER`, `SHOOTING_STAR`, `DOJI`, `MORNING_STAR`, `EVENING_STAR`, `GREEN_CANDLE`, `RED_CANDLE`) |
| `PRICE_CROSSOVER` | `direction` (`ABOVE`/`BELOW`) and either `price_level` (number) or `indicator` (string) |
| `INDICATOR_CROSSOVER` | `indicator1`, `indicator2`, `crossover_type` (`GOLDEN_CROSS`/`DEATH_CROSS`) |
| `EXPRESSION` | `expression` tree; optional `signal_value` (`BUY`/`SELL`, default `BUY`) |

#### Exit types

Exit answers "when do we close the position?" The backtester applies position-relative exits (stop loss, take profit, trailing stop, anchor stop, time-based) against `Position.entry_price`, `Position.peak_price`, or the captured entry-candle OHLC. Whichever condition fires first wins; ties on the same bar resolve in the order checked by `_check_exit_conditions`.

Two authoring shapes are supported:

1. **`CONDITIONAL_OR_FIXED`** — wrap one or more conditions under `conditions[]` (OR logic).
2. **Top-level form** — set `exit.type` directly to one of the leaf exit types below (single-rule exit).

| Leaf `type` (in `conditions[]` **or** top-level) | Fields | Behaviour |
|---|---|---|
| `STOP_LOSS_PCT` | `value` (0–1) | Exit when `close ≤ entry_price × (1 − value)` |
| `STOP_LOSS_ANCHOR` | `anchor` (`ENTRY_OPEN` \| `ENTRY_HIGH` \| `ENTRY_LOW` \| `ENTRY_CLOSE`), optional `offset_pct` (0–1, default `0`) | Structural stop: exit when `close ≤ anchor_value × (1 − offset_pct)`. `anchor_value` is the entry candle's OHLC, captured at position open |
| `TAKE_PROFIT_PCT` | `value` (≥ 0) | Exit when `close ≥ entry_price × (1 + value)` |
| `TRAILING_STOP_PCT` | `value` (0–1) | Exit when `close ≤ peak_price × (1 − value)` (peak tracked since entry) |
| `TIME_BASED` | `max_holding_days` (int > 0) | Force exit after N calendar days since entry. Top-level form only; not selectable inside `conditions[]` |
| `INDICATOR_CROSS` | `indicator`, `direction` (`UP`/`DOWN`), optional `value` | Exit on indicator cross |
| `EXPRESSION` | `expression` tree | Exit when the expression is true on a bar |

> **`STOP_LOSS_ANCHOR` notes.** `ENTRY_LOW` is the typical choice for longs — it places a structural stop just under the swing low of the entry bar. `ENTRY_HIGH` sits at or above the entry close so on the long side it tends to trigger on the first down-tick (allowed for completeness; rarely useful). On a gap-through, the exit fill is the next bar's `close`, so realized loss can exceed the configured `offset_pct`. Fires `exit_reason = "stop_loss_anchor"`.

#### Expression DSL

`EXPRESSION` components accept a recursive boolean tree (discriminated by `op`). Three node families:

| Family | Examples |
|---|---|
| **Operands** | `{"indicator": "RSI", "params": {"period": 13}}`, `{"indicator": "MACD", "output": "signal"}`, `{"price": "close"}`, `{"const": 50}` |
| **Comparators / patterns** | `{"op": "GT" \| "LT" \| "GTE" \| "LTE" \| "EQ" \| "NEQ" \| "CROSS_ABOVE" \| "CROSS_BELOW", "left": …, "right": …}`, `{"op": "PATTERN", "pattern": "DOJI"}` |
| **Combinators** | `{"op": "AND" \| "OR", "conditions": [...]}`, `{"op": "NOT", "condition": …}` |

#### Example payloads

**1. Minimal smoke test** (always-on trigger, fixed take-profit):

```json
{
  "strategy_name": "smoke_test",
  "symbol": "AAPL",
  "timeframe": "1d",
  "start_date": "2025-01-01",
  "end_date": "2025-01-31",
  "initial_capital": 10000,
  "components": {
    "setup":   { "type": "NONE",           "timeframe": "1d" },
    "trigger": { "type": "CANDLE_PATTERN", "timeframe": "1d", "pattern": "GREEN_CANDLE" },
    "exit":    { "type": "TAKE_PROFIT_PCT","timeframe": "1d", "value": 0.10 }
  }
}
```

**2. EMA trend with `STOP_LOSS_ANCHOR` exit** (validated against the live API; produces structural stops anchored to entry-bar low):

```json
{
  "strategy_name": "anchor_exit_test_aapl",
  "symbol": "AAPL",
  "timeframe": "1d",
  "start_date": "2023-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 10000,
  "components": {
    "setup": {
      "type": "EXPRESSION", "timeframe": "1d",
      "expression": {
        "op": "GT",
        "left":  { "indicator": "EMA", "params": { "period": 8  } },
        "right": { "indicator": "EMA", "params": { "period": 21 } }
      }
    },
    "trigger": {
      "type": "EXPRESSION", "timeframe": "1d", "signal_value": "BUY",
      "expression": {
        "op": "CROSS_ABOVE",
        "left":  { "price": "close" },
        "right": { "indicator": "EMA", "params": { "period": 8 } }
      }
    },
    "exit": {
      "type": "CONDITIONAL_OR_FIXED", "timeframe": "1d",
      "conditions": [
        { "type": "STOP_LOSS_ANCHOR", "anchor": "ENTRY_LOW", "offset_pct": 0.01 },
        { "type": "TAKE_PROFIT_PCT",  "value": 0.10 }
      ]
    }
  }
}
```

**3. Top-level `STOP_LOSS_ANCHOR`** (no other rules; for testing the structural stop in isolation):

```json
"exit": {
  "type": "STOP_LOSS_ANCHOR",
  "timeframe": "1d",
  "anchor": "ENTRY_LOW",
  "offset_pct": 0.005
}
```

#### Response shape

```json
{
  "data": {
    "total_return": 9509.23,
    "total_return_pct": 0.9509,
    "total_trades": 10,
    "winning_trades": 8,
    "losing_trades": 2,
    "win_rate": 0.80,
    "avg_win": 16.97,
    "avg_loss": -5.99,
    "profit_factor": 11.33,
    "max_drawdown": 1338.31,
    "max_drawdown_pct": 0.1338,
    "sharpe_ratio": 2.54,
    "equity_curve": [10000.0, 10000.0, "..."],
    "initial_capital": 10000,
    "final_capital": 19509.23,
    "trades": [
      {
        "entry_date": "2023-07-12",
        "entry_price": 187.68,
        "entry_open":  187.59,
        "entry_high":  189.58,
        "entry_low":   186.39,
        "entry_close": 187.68,
        "exit_date":   "2023-08-04",
        "exit_price":  179.98,
        "pnl": -7.70,
        "pnl_pct": -0.041,
        "holding_days": 23,
        "exit_reason": "stop_loss_anchor"
      }
    ]
  },
  "meta": {
    "symbol": "AAPL",
    "strategy_name": "anchor_exit_test_aapl",
    "timeframe": "1d",
    "start_date": "2023-01-01",
    "end_date": "2024-12-31",
    "source": "dev-serving-backtester"
  }
}
```

`data.trades[].exit_reason` is one of: `stop_loss`, `take_profit`, `trailing_stop`, `time_based`, `stop_loss_anchor`, `signal`, `end_of_data`.

`entry_open` / `entry_high` / `entry_low` / `entry_close` capture the entry candle's OHLC. They are populated for every position so consumers can recompute structural-stop math (e.g. `anchor = entry_low`, `effective_stop = entry_low × (1 − offset_pct)`) without re-pulling raw OHLCV.

**`/picks/today` and `/picks/today/metadata`**

| Param | Type | Default | Notes |
|-------|------|---------|--------|
| `limit` | int | `25` | `1–200` |
| `industry` | string | — | Exact match on `symbol_metadata.industry` (same as screener) |
| `min_market_cap` | int | — | `>= 0`; filter `symbol_metadata.marketcap` |
| `max_market_cap` | int | — | `>= 0`; filter `symbol_metadata.marketcap` |

Picks are joined to `symbol_metadata` on `symbol`. Omitting filters returns the full ranked list for the latest `scan_date` (subject to `limit`). Rows without metadata still appear when **no** industry/cap filters are applied; with filters applied, symbols missing metadata usually drop out (unknown industry/cap).

**`/picks/detail`**

| Param | Type | Required | Notes |
|-------|------|----------|--------|
| `symbol` | string | yes | Ticker |
| `scan_date` | date | yes | `YYYY-MM-DD` |
| `strategy_name` | string | no | Narrow to one strategy |

**`/picks/{scan_date}/returns`**

| Param | Type | Default | Notes |
|-------|------|---------|--------|
| `horizons` | string | `1,5,21` | Comma-separated trading days `1–252` |
| `industry` | string | — | Exact match on `symbol_metadata.industry` |
| `min_market_cap` | int | — | `>= 0`; filter `symbol_metadata.marketcap` |
| `max_market_cap` | int | — | `>= 0`; filter `symbol_metadata.marketcap` |

`{scan_date}` is a path segment, e.g. `2026-04-28`.

### Market (`/market` — requires API key if configured)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/market/quote/{symbol}` | Latest daily bar + metadata |
| GET | `/market/news/{symbol}` | Latest Polygon news for a symbol (live API call) |
| GET | `/market/ohlcv/{symbol}` | OHLCV history |
| GET | `/market/returns/{symbol}` | Simple multi-horizon returns from daily closes |

**`/market/news/{symbol}`**

| Param | Type | Default | Notes |
|-------|------|---------|--------|
| `limit` | int | `10` | `1–50` |
| `order` | string | `desc` | `asc` or `desc` by `published_utc` |
| `published_utc_gte` | date | — | Lower bound filter |
| `published_utc_lte` | date | — | Upper bound filter |

Uses Polygon endpoint `v2/reference/news` with ticker filter and reads API key from AWS Secrets Manager via `POLYGON_API_KEY_SECRET_ARN`. The response includes `data` (articles) and `meta` (count/query params/`next_url` with `apiKey` stripped).

**`/market/ohlcv/{symbol}`**

| Param | Type | Default | Notes |
|-------|------|---------|--------|
| `interval` | string | `1d` | `1d`, `1h`, `15m`, `5m`, `1m` |
| `start_date` | date | — | Filter lower bound |
| `end_date` | date | — | Filter upper bound |
| `limit` | int | `200` | `1–2000` |
| `sort` | string | `desc` | `asc` or `desc` by timestamp |

**`/market/returns/{symbol}`**

| Param | Type | Default |
|-------|------|---------|
| `horizons` | string | `1,5,21` |

---

## Examples (`curl`)

Replace `BASE` and `KEY`.

```bash
BASE="https://abc123xyz.execute-api.ca-west-1.amazonaws.com/v1"
KEY="your-serving-api-key"

curl -sS -H "x-api-key: $KEY" "$BASE/picks/today?limit=10"

curl -sS -H "x-api-key: $KEY" \
  "$BASE/picks/detail?symbol=AAPL&scan_date=2026-04-28"

curl -sS -H "x-api-key: $KEY" \
  "$BASE/market/ohlcv/MSFT?interval=1d&limit=50"

curl -sS -H "x-api-key: $KEY" \
  "$BASE/market/news/AAPL?limit=5&order=desc"

curl -sS -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  "$BASE/backtest" \
  -d '{"strategy_name":"smoke_test","symbol":"AAPL","timeframe":"1d","start_date":"2025-01-01","end_date":"2025-01-31","initial_capital":10000,"components":{"setup":{"type":"NONE","timeframe":"1d"},"trigger":{"type":"CANDLE_PATTERN","timeframe":"1d","pattern":"GREEN_CANDLE"},"exit":{"type":"TAKE_PROFIT_PCT","timeframe":"1d","value":0.1}}}'

# Backtest with structural stop-loss anchor (entry-candle low − 1% buffer) + 10% take-profit
curl -sS -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  "$BASE/backtest" \
  -d '{"strategy_name":"anchor_exit_test_aapl","symbol":"AAPL","timeframe":"1d","start_date":"2023-01-01","end_date":"2024-12-31","initial_capital":10000,"components":{"setup":{"type":"EXPRESSION","timeframe":"1d","expression":{"op":"GT","left":{"indicator":"EMA","params":{"period":8}},"right":{"indicator":"EMA","params":{"period":21}}}},"trigger":{"type":"EXPRESSION","timeframe":"1d","signal_value":"BUY","expression":{"op":"CROSS_ABOVE","left":{"price":"close"},"right":{"indicator":"EMA","params":{"period":8}}}},"exit":{"type":"CONDITIONAL_OR_FIXED","timeframe":"1d","conditions":[{"type":"STOP_LOSS_ANCHOR","anchor":"ENTRY_LOW","offset_pct":0.01},{"type":"TAKE_PROFIT_PCT","value":0.1}]}}}'

curl -sS "$BASE/health"
```

---

## Deploy checklist when adding routes

1. Implement the route in FastAPI (`cloud/serving_layer/lambda_functions/serving_api/`).
2. Package and update the **Lambda** function code.
3. Add the route key (for example **`GET /path`** or **`POST /path`**) to the `ROUTES` array in `infrastructure/serving_api/deploy_http_api.sh`.
4. Run **`deploy_http_api.sh`** so API Gateway exposes the new path (otherwise you may see `403` / missing route).

---

## Related files

| File | Purpose |
|------|---------|
| `lambda_functions/serving_api/app.py` | FastAPI app, CORS, API-key dependency |
| `lambda_functions/serving_api/routers/*.py` | Route handlers |
| `infrastructure/serving_api/deploy_http_api.sh` | HTTP API + routes + Lambda permission |
