# Serving layer

REST-facing Lambdas for quotes and backtests, fronted by API Gateway (deploy separately).

## Code layout

| Path | Role |
|------|------|
| `lambda_functions/quote_api/quote_service.py` | GET quote: Redis → Polygon |
| `lambda_functions/backtester/backtest_handler.py` | POST backtest: RDS → Polars |
| `lambda_functions/backtester/Dockerfile` | Container image for backtester (ARM64) |
| `lambda_functions/backtester/requirements.backtester.txt` | Lean deps for that image |
| `infrastructure/docker/build_push_backtester.sh` | Build & push image to ECR |

## Backtester container

From **repository root**:

```bash
AWS_REGION=ca-west-1 ECR_REPO=dev-serving-backtester \
  ./cloud/serving_layer/infrastructure/docker/build_push_backtester.sh
```

Lambda env vars:

- `RDS_SECRET_ARN` — Secrets Manager secret with `host`, `port`, `username`, `password`, `database` or `dbname`
- Optional: `BACKTEST_MAX_LOOKBACK_DAYS` (default `1825` ≈ 5 years)

Use the **RDS Proxy** endpoint in the secret’s `host` for production.

## Phase 1 fixes (code)

- Backtester uses `MultiTimeframeExecutor(rds_connection_string=...)` only; `execute(...)` with no `use_s3`.
- Timeframes are collected from JSON `components` dicts via `timeframe` keys.
- Quote service reads Polygon snapshot fields whether SDK returns dicts or objects.

Next steps: API Gateway routes, usage plans, VPC + security groups for Lambdas, Redis/ RDS Proxy wiring.
