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

#### Composition model

Every backtest is built from exactly three components — `setup`, `trigger`, and `exit` — composed in this order on each bar:

```text
                 setup_valid (bool)            signal == 'BUY'
   bar ──►  ┌───────────┐  AND  ┌───────────┐  ───────►  open Position
            │   SETUP   │       │  TRIGGER  │            (entry_price = close,
            └───────────┘       └───────────┘             entry_open/high/low/close captured)
                                                                │
                                                                ▼
                                                       ┌────────────────┐
                                                       │     EXIT       │
                                                       │ (OR of rules — │
                                                       │ first wins,    │
                                                       │ ties resolved  │
                                                       │ in check order)│
                                                       └────────────────┘
```

- **Setup** is a regime/trend filter — it produces a boolean `setup_valid` column but never opens a position on its own. Use `type: "NONE"` to disable.
- **Trigger** emits `BUY` (or `SELL` via `signal_value`) only on bars where `setup_valid && trigger_condition`. A new `BUY` while a position is open is ignored.
- **Exit** has two evaluation tiers:
  - **Position-relative** (`STOP_LOSS_PCT`, `STOP_LOSS_ANCHOR`, `TAKE_PROFIT_PCT`, `TRAILING_STOP_PCT`, `TIME_BASED`) — evaluated by `Backtester` against `Position.entry_price` / `peak_price` / entry-candle OHLC / entry date.
  - **Vectorized** (`INDICATOR_CROSS`, `EXPRESSION`) — evaluated by the strategy layer on the bar itself, regardless of position state.

The engine is **long-only**: a `BUY` opens a position, any exit closes it at the bar's `close` (gap-through fills also use `close`), and `SELL` triggers close an open position rather than going short.

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

#### Indicator reference

The expression DSL and legacy flat forms both go through the same indicator registry (`shared.analytics_core.indicators.technicals.INDICATOR_REGISTRY`). Indicator **names** are case-insensitive at the registry level (`"ema"` and `"EMA"` resolve identically), but indicator **columns** produced by the registry are lowercase. Anywhere a config field expects a **column name** (e.g. `trigger.indicator`, `trigger.indicator1`/`indicator2`, `exit.indicator`, `setup.indicator2`), you must use the column form — e.g. `"ema_8"`, not `"EMA"` or `"EMA(8)"`.

| Registry name | Defaults | Output column(s) — use these in `indicator` / `indicator1` / `indicator2` fields |
|---|---|---|
| `RSI` | `period=14` | `rsi_{period}` (e.g. `rsi_14`) |
| `SMA` | `period=20` | `sma_{period}` (e.g. `sma_50`) |
| `EMA` | `period=20` | `ema_{period}` (e.g. `ema_8`) |
| `ATR` | `period=14` | `atr_{period}` |
| `MACD` | `fast=12, slow=26, signal=9` | `macd_{fast}_{slow}` (line) / `macd_signal_{fast}_{slow}_{signal}` / `macd_hist_{fast}_{slow}_{signal}` — pick via `output` (`"signal"`, `"histogram"`, default = line) |
| `BB` | `period=20, std_dev=2.0` | `bb_middle_{period}_{std}`, `bb_upper_…`, `bb_lower_…` — pick via `output` (`"upper"`, `"lower"`, default = middle) |
| `STOCH` | `k_period=14, d_period=3` | `stoch_k_{k}_{d}`, `stoch_d_{k}_{d}` — pick via `output` (`"k"`, `"d"`, default = `k`) |

> **Convention recap.** In the `EXPRESSION` form, the registry is invoked automatically and you reference indicators **by registry name** (`{"indicator": "EMA", "params": {"period": 8}}`). In the legacy flat forms and the `PRICE_CROSSOVER` / `INDICATOR_CROSSOVER` triggers, you reference them **by column name** (`"indicator": "ema_8"`, `"indicator1": "ema_50"`, `"indicator2": "ema_200"`). Mixing the two breaks: e.g. `"indicator1": "EMA"` will fail with `unable to find column "EMA"`.

#### Setup types

Setup answers "is the regime/trend valid?" — it produces the boolean `setup_valid` column that gates the trigger. It never opens a position on its own.

| `type` | Authoring style | Required fields |
|---|---|---|
| `NONE` | — | none; `setup_valid` is always `true` |
| `INDICATOR_THRESHOLD` | Legacy flat (one-indicator vs threshold OR cross with `indicator2`) | `indicator` (registry name, e.g. `"RSI"`), `params` (e.g. `{"period": 14}`), `operator` (`>` `<` `>=` `<=` `==` `CROSS_ABOVE` `CROSS_BELOW`), `value` (threshold) **or** `indicator2` (column name) for the cross variants |
| `EXPRESSION` | Recommended for anything beyond a single comparison | `expression` (recursive boolean tree — see [expression DSL](#expression-dsl)) |

#### Trigger types

Trigger answers "did the entry happen on this bar?" Signals fire only on bars where `setup_valid && trigger_condition`. The result is written to `signal` (`BUY` / `SELL` / `HOLD`).

| `type` | Required fields | Notes |
|---|---|---|
| `CANDLE_PATTERN` | `pattern` | Signal direction is implicit per pattern (BUY: `BULLISH_ENGULFING`, `HAMMER`, `MORNING_STAR`, `GREEN_CANDLE`; SELL: `BEARISH_ENGULFING`, `SHOOTING_STAR`, `EVENING_STAR`, `RED_CANDLE`; `DOJI` is neutral — no signal emitted) |
| `PRICE_CROSSOVER` | `direction` (`ABOVE` / `BELOW`) plus **either** `price_level` (number) **or** `indicator` (column name like `"ema_8"`) | Direction determines signal: `ABOVE` → `BUY`, `BELOW` → `SELL` |
| `INDICATOR_CROSSOVER` | `indicator1`, `indicator2` (both column names), `crossover_type` (`GOLDEN_CROSS` → `BUY` / `DEATH_CROSS` → `SELL`) | Strict crossover (current > / < AND prior <= / >=) — only fires on the bar that actually crosses |
| `EXPRESSION` | `expression`, optional `signal_value` (`BUY` default, or `SELL`) | Full DSL access — use this for compound trigger conditions |

#### Exit types

Exit answers "when do we close the position?" Two evaluation tiers (see [composition model](#composition-model)):

- **Position-relative** rules (`STOP_LOSS_*`, `TAKE_PROFIT_PCT`, `TRAILING_STOP_PCT`, `TIME_BASED`) are extracted from the JSON by `backtest_handler` and applied by `Backtester.run(...)` against `Position.entry_price`, `peak_price`, the captured entry-candle OHLC, or the entry date. First condition to fire wins; ties on the same bar resolve in the order checked by `_check_exit_conditions` (`stop_loss → take_profit → trailing_stop → stop_loss_anchor → time_based`).
- **Vectorized** rules (`INDICATOR_CROSS`, `EXPRESSION`) are evaluated by the strategy layer on each bar regardless of position state and fire `exit_reason = "signal"`.

Two authoring shapes are supported and treated symmetrically — `backtest_handler` extracts the same leaf parameters from either:

1. **`CONDITIONAL_OR_FIXED`** — wrap one or more conditions under `conditions[]` (OR logic). Use this when you want stops/targets composed together.
2. **Top-level form** — set `exit.type` directly to one of the leaf types below. Use this for single-rule exits, and as the only way to author `TIME_BASED` (its config is not yet in the `conditions[]` union).

| Leaf `type` (in `conditions[]` **or** top-level) | Fields | Behaviour |
|---|---|---|
| `STOP_LOSS_PCT` | `value` (0–1) | Exit when `close ≤ entry_price × (1 − value)`. `exit_reason = "stop_loss"` |
| `STOP_LOSS_ANCHOR` | `anchor` (`ENTRY_OPEN` \| `ENTRY_HIGH` \| `ENTRY_LOW` \| `ENTRY_CLOSE`), optional `offset_pct` (0–1, default `0`) | Structural stop: exit when `close ≤ anchor_value × (1 − offset_pct)`. `anchor_value` is the entry candle's OHLC, captured at position open. `exit_reason = "stop_loss_anchor"` |
| `TAKE_PROFIT_PCT` | `value` (≥ 0) | Exit when `close ≥ entry_price × (1 + value)`. `exit_reason = "take_profit"` |
| `TRAILING_STOP_PCT` | `value` (0–1) | Exit when `close ≤ peak_price × (1 − value)` where `peak_price` is the highest close seen since entry. `exit_reason = "trailing_stop"` |
| `TIME_BASED` | `max_holding_days` (int > 0) | Force exit after N calendar days since entry. `exit_reason = "time_based"`. Use **top-level form** to author this — the Pydantic discriminated union for `CONDITIONAL_OR_FIXED.conditions[]` does not include a `TimeBasedConfig` variant today |
| `INDICATOR_CROSS` | `indicator` (column name like `"rsi_14"`), `direction` (`UP` / `DOWN`), `value` (threshold) | Exit when the indicator crosses the threshold in the given direction. `exit_reason = "signal"` |
| `EXPRESSION` | `expression` (boolean tree) | Exit when the expression is true on a bar. `exit_reason = "signal"` |

> **`STOP_LOSS_ANCHOR` notes.** `ENTRY_LOW` is the typical choice for longs — it places a structural stop just under the swing low of the entry bar. `ENTRY_HIGH` sits at or above the entry close, so on the long side it tends to trigger on the first down-tick (allowed for completeness; rarely useful). On a gap-through, the exit fill is the next bar's `close`, so realized loss can exceed the configured `offset_pct`. The entry-candle OHLC is returned on every trade in the response, so the frontend can render the stop level without re-pulling raw bars.

#### Expression DSL

`EXPRESSION` components accept a recursive boolean tree (discriminated by `op`). Three node families:

| Family | Examples |
|---|---|
| **Operands** (produce a column) | `{"indicator": "RSI", "params": {"period": 13}}`, `{"indicator": "MACD", "output": "signal"}`, `{"indicator": "BB", "output": "lower"}`, `{"price": "close"}`, `{"const": 50}` |
| **Comparators / patterns** (produce a boolean) | `{"op": "GT" \| "LT" \| "GTE" \| "LTE" \| "EQ" \| "NEQ" \| "CROSS_ABOVE" \| "CROSS_BELOW", "left": …, "right": …}`, `{"op": "PATTERN", "pattern": "DOJI"}` |
| **Combinators** (combine booleans) | `{"op": "AND" \| "OR", "conditions": [...]}`, `{"op": "NOT", "condition": …}` |

The DSL uses **registry names** for indicators (`"RSI"`, `"EMA"`, …) and resolves the right column automatically. Use `output` to pick a non-default role (e.g. MACD signal line, Bollinger lower band, Stochastic `%D`).

#### Multi-timeframe

Every component carries its own `timeframe` (default `1d`). The executor loads 1d data from RDS Proxy, resamples to every distinct `timeframe` referenced in the request, runs each component on its own dataframe, then aligns results back to the base `timeframe`:

- **State columns** (`setup_valid`, indicator values, …) are forward-filled when aligning from a higher TF down to base — so a weekly EMA-trend setup remains "true" for every 1d bar inside that week.
- **Event columns** (`signal`, `exit_signal`) are **not** forward-filled — a `BUY` emitted on a `3d` bar does not replicate onto each underlying `1d` bar in the same window.

Typical use: setup on a higher TF (regime filter), trigger and exit on the base TF (entry timing). See [example 4](#example-4--multi-timeframe-weekly-regime--daily-entry).

#### Common gotchas

- **Indicator naming.** Registry names (`"EMA"`) in the `EXPRESSION` form; column names (`"ema_8"`) anywhere else. Mixing them yields `unable to find column "EMA"`.
- **Setup gates everything.** If the setup never returns `true` you'll get zero trades; smoke-test by setting `setup.type = "NONE"`.
- **`TIME_BASED` placement.** Pydantic currently rejects `TIME_BASED` inside `CONDITIONAL_OR_FIXED.conditions[]` (no `TimeBasedConfig` in the discriminated union). Author it as a top-level exit, e.g. `{"type": "TIME_BASED", "max_holding_days": 20}`. If you need a time cap alongside a stop/target, you can extend the union in `cloud/shared/analytics_core/models.py`.
- **Long-only.** A `BUY` opens a position; `SELL` triggers (and any matched exit rule) close it. No shorts.
- **Exit fill is `close`.** Gap-through stops fill at the same bar's close, so realized loss can exceed the configured stop distance.
- **Date range cap.** `BACKTEST_MAX_LOOKBACK_DAYS = 1825` (~5 y) on `dev-serving-backtester`. Longer ranges return `400`.

#### Example payloads

All examples below have been validated against the live `POST /backtest` API. They cover the main composition patterns; substitute `symbol` / dates / parameters as needed.

##### Example 1 — Minimal smoke test

Always-on setup, every green candle is an entry, single 10% take-profit. Useful for sanity-checking the pipeline end-to-end.

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

##### Example 2 — EMA trend + STOP_LOSS_ANCHOR exit

EMA(8) > EMA(21) regime, enter on close crossing above EMA(8), exit on whichever fires first: structural stop 1% below the entry candle's low, or 10% take-profit.

```json
{
  "strategy_name": "ema_trend_anchor",
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

##### Example 3 — Golden cross + trailing stop

Classic SMA(50) / SMA(200) golden cross. No regime filter, ride winners with a 5% trailing stop. Note: trigger uses **column names** (`"sma_50"`, `"sma_200"`), not registry names — the indicator columns are pre-computed by `MultiTimeframeExecutor.prepare_dataframe` (which calls `calculate_all_indicators`).

```json
{
  "strategy_name": "golden_cross_trailing",
  "symbol": "SPY",
  "timeframe": "1d",
  "start_date": "2020-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 10000,
  "components": {
    "setup":   { "type": "NONE", "timeframe": "1d" },
    "trigger": {
      "type": "INDICATOR_CROSSOVER", "timeframe": "1d",
      "indicator1": "sma_50",
      "indicator2": "sma_200",
      "crossover_type": "GOLDEN_CROSS"
    },
    "exit": {
      "type": "CONDITIONAL_OR_FIXED", "timeframe": "1d",
      "conditions": [
        { "type": "TRAILING_STOP_PCT", "value": 0.05 }
      ]
    }
  }
}
```

##### Example 4 — Multi-timeframe (weekly regime → daily entry)

5d trend gate (EMA(8) > EMA(21) on the weekly view) drives a 1d crossover entry. The exit runs on the base 1d TF and uses an `INDICATOR_CROSS` rule (vectorized) alongside a fixed stop — first to fire wins.

```json
{
  "strategy_name": "weekly_regime_daily_entry",
  "symbol": "QQQ",
  "timeframe": "1d",
  "start_date": "2022-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 10000,
  "components": {
    "setup": {
      "type": "EXPRESSION", "timeframe": "5d",
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
        { "type": "STOP_LOSS_PCT", "value": 0.04 },
        { "type": "INDICATOR_CROSS", "indicator": "rsi_14", "direction": "DOWN", "value": 50 }
      ]
    }
  }
}
```

##### Example 5 — RSI mean-reversion (legacy flat setup)

Demonstrates the legacy `INDICATOR_THRESHOLD` form: enter when RSI(14) was oversold and bounces with a bullish engulfing, exit on either a fixed take-profit or trailing-stop.

```json
{
  "strategy_name": "rsi_oversold_reversal",
  "symbol": "MSFT",
  "timeframe": "1d",
  "start_date": "2022-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 10000,
  "components": {
    "setup": {
      "type": "INDICATOR_THRESHOLD", "timeframe": "1d",
      "indicator": "RSI",
      "params": { "period": 14 },
      "operator": "<",
      "value": 30
    },
    "trigger": { "type": "CANDLE_PATTERN", "timeframe": "1d", "pattern": "BULLISH_ENGULFING" },
    "exit": {
      "type": "CONDITIONAL_OR_FIXED", "timeframe": "1d",
      "conditions": [
        { "type": "TAKE_PROFIT_PCT",   "value": 0.08 },
        { "type": "TRAILING_STOP_PCT", "value": 0.04 }
      ]
    }
  }
}
```

##### Example 6 — Bollinger Band bounce (EXPRESSION with multi-output indicator)

Demonstrates the `output` role for multi-output indicators. Trigger fires on a `CROSS_ABOVE` of the lower BB band — the classic "bounce back into the channel" entry. Setup is `NONE` because the cross itself captures the bounce; exit takes profit at the middle band region via a structural anchor stop and a fixed take-profit.

```json
{
  "strategy_name": "bb_bounce",
  "symbol": "AAPL",
  "timeframe": "1d",
  "start_date": "2023-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 10000,
  "components": {
    "setup":   { "type": "NONE", "timeframe": "1d" },
    "trigger": {
      "type": "EXPRESSION", "timeframe": "1d", "signal_value": "BUY",
      "expression": {
        "op": "CROSS_ABOVE",
        "left":  { "price": "close" },
        "right": { "indicator": "BB", "output": "lower" }
      }
    },
    "exit": {
      "type": "CONDITIONAL_OR_FIXED", "timeframe": "1d",
      "conditions": [
        { "type": "STOP_LOSS_ANCHOR", "anchor": "ENTRY_LOW", "offset_pct": 0.005 },
        { "type": "TAKE_PROFIT_PCT",  "value": 0.06 }
      ]
    }
  }
}
```

##### Example 7 — Time-based exit (top-level form)

Single-rule exit: force-close after 20 calendar days, regardless of P&L. Useful for swing-strategy max-holding caps. Note `TIME_BASED` must be **top-level** (not inside `conditions[]`).

```json
{
  "strategy_name": "time_capped_swing",
  "symbol": "NVDA",
  "timeframe": "1d",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 10000,
  "components": {
    "setup":   { "type": "NONE", "timeframe": "1d" },
    "trigger": { "type": "CANDLE_PATTERN", "timeframe": "1d", "pattern": "HAMMER" },
    "exit":    { "type": "TIME_BASED", "timeframe": "1d", "max_holding_days": 20 }
  }
}
```

##### Example 8 — Compound EXPRESSION trigger (AND of conditions)

When you need more than a single comparator, use combinator nodes. Here: trigger only on bars where RSI is between 40 and 60 **and** close crosses above EMA(20).

```json
{
  "strategy_name": "rsi_band_breakout",
  "symbol": "AMZN",
  "timeframe": "1d",
  "start_date": "2023-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 10000,
  "components": {
    "setup":   { "type": "NONE", "timeframe": "1d" },
    "trigger": {
      "type": "EXPRESSION", "timeframe": "1d", "signal_value": "BUY",
      "expression": {
        "op": "AND",
        "conditions": [
          { "op": "GT", "left": { "indicator": "RSI", "params": { "period": 14 } }, "right": { "const": 40 } },
          { "op": "LT", "left": { "indicator": "RSI", "params": { "period": 14 } }, "right": { "const": 60 } },
          { "op": "CROSS_ABOVE", "left": { "price": "close" }, "right": { "indicator": "EMA", "params": { "period": 20 } } }
        ]
      }
    },
    "exit": {
      "type": "CONDITIONAL_OR_FIXED", "timeframe": "1d",
      "conditions": [
        { "type": "STOP_LOSS_PCT",  "value": 0.03 },
        { "type": "TAKE_PROFIT_PCT","value": 0.09 }
      ]
    }
  }
}
```

##### Example 9 — Top-level STOP_LOSS_ANCHOR (isolation test)

Single-rule exit for unit-testing the structural stop without other conditions firing first:

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
