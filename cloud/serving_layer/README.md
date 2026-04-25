# Serving layer

REST-facing APIs for frontend and consumers.

## MVP scope

The MVP serving API exposes three RDS-backed endpoints:

- `GET /v1/screener/quotes` - latest daily quote by filters (`industry`, `type`, `market cap` band).
- `GET /v1/picks/today` - ranked picks from latest `scan_date`.
- `GET /v1/picks/{scan_date}/returns` - pick performance (1d/5d/21d + return-to-date).

All three are implemented in `lambda_functions/serving_api/` and deployed as a single VPC Lambda (`dev-serving-api`) behind HTTP API Gateway.

## Code layout

| Path | Role |
| ------ | ------ |
| `lambda_functions/serving_api/` | FastAPI + Mangum serving Lambda (`dev-serving-api`) |
| `lambda_functions/quote_api/quote_service.py` | GET quote: Redis → Polygon (live tick path; separate from MVP screener endpoints) |
| `lambda_functions/backtester/backtest_handler.py` | POST backtest: RDS → Polars |
| `lambda_functions/backtester/Dockerfile` | Container image for backtester (ARM64) |
| `lambda_functions/backtester/requirements.backtester.txt` | Lean deps for that image |
| `infrastructure/serving_api/deploy_lambda.sh` | Package + deploy `dev-serving-api` |
| `infrastructure/serving_api/create_rds_proxy.sh` | Create/update `dev-rds-proxy` + SG rules |
| `infrastructure/serving_api/deploy_http_api.sh` | Create/update HTTP API routes + stage |
| `infrastructure/docker/build_push_backtester.sh` | Build & push backtester image to ECR |

## Serving API deploy sequence

From repository root:

```bash
./cloud/serving_layer/infrastructure/serving_api/deploy_lambda.sh
./cloud/serving_layer/infrastructure/serving_api/create_rds_proxy.sh
./cloud/serving_layer/infrastructure/serving_api/deploy_http_api.sh
```

Detailed variables and examples are in:
`cloud/serving_layer/infrastructure/serving_api/README.md`.

## Backtester container

From repository root:

```bash
AWS_REGION=ca-west-1 ECR_REPO=dev-serving-backtester \
  ./cloud/serving_layer/infrastructure/docker/build_push_backtester.sh
```

Lambda env vars:

- `RDS_SECRET_ARN` - Secrets Manager secret with `host`, `port`, `username`, `password`, `database` or `dbname`
- Optional: `BACKTEST_MAX_LOOKBACK_DAYS` (default `1825` ~= 5 years)

Use the **RDS Proxy** endpoint in the secret's `host` for production.
