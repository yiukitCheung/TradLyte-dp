# Serving API Infrastructure

Scripts to deploy the MVP serving API stack:

- `deploy_lambda.sh` - packages and deploys `dev-serving-api` (FastAPI + Mangum) into the same private VPC shape used by batch ingest Lambdas.
- `create_rds_proxy.sh` - provisions `dev-rds-proxy`, security groups, and target registration.
- `deploy_http_api.sh` - creates/updates HTTP API Gateway routes and stage (`v1`) with throttling + CORS.

## 1) Deploy Lambda

```bash
AWS_REGION=ca-west-1 \
FUNCTION_NAME=dev-serving-api \
SOURCE_VPC_LAMBDA=dev-batch-daily-ohlcv-ingest-handler \
RDS_SECRET_ARN=arn:aws:secretsmanager:...:secret:... \
SERVING_API_KEY=replace-me \
./cloud/serving_layer/infrastructure/serving_api/deploy_lambda.sh
```

## 2) Create RDS Proxy

```bash
AWS_REGION=ca-west-1 \
DB_PROXY_NAME=dev-rds-proxy \
DB_INSTANCE_ID=<db-instance-id> \
RDS_SECRET_ARN=arn:aws:secretsmanager:...:secret:... \
RDS_PROXY_ROLE_ARN=arn:aws:iam::<account-id>:role/<proxy-role> \
SOURCE_LAMBDA_FOR_VPC=dev-serving-api \
./cloud/serving_layer/infrastructure/serving_api/create_rds_proxy.sh
```

After creation, update your RDS secret `host` field to the proxy endpoint.

## 3) Deploy HTTP API

```bash
AWS_REGION=ca-west-1 \
API_NAME=dev-serving-http-api \
FUNCTION_NAME=dev-serving-api \
STAGE_NAME=v1 \
ALLOWED_ORIGIN=https://app.tradlyte.com \
./cloud/serving_layer/infrastructure/serving_api/deploy_http_api.sh
```

## Environment Variables

Set on `dev-serving-api` Lambda:

- `RDS_SECRET_ARN` - secret with `host`, `port`, `username`, `password`, `database/dbname`.
- `SERVING_API_KEY` - accepted `x-api-key` value (optional, but recommended).
- `ALLOWED_ORIGIN` - frontend origin for CORS.
- `SCREENER_CACHE_TTL_S` - default `60`.
- `RETURNS_CACHE_TTL_S` - default `300`.
