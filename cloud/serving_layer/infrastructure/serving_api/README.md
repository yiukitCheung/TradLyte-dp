# Serving API Infrastructure

Scripts to deploy the MVP serving API stack:

- `deploy_lambda.sh` - packages and deploys `dev-serving-api` (FastAPI + Mangum) into the same private VPC shape used by batch ingest Lambdas.
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

## 2) Create RDS Proxy (AWS Console)

Create this manually in the AWS Console:

1. Open **RDS > Proxies > Create proxy**.
2. Name: `dev-rds-proxy`; Engine: **PostgreSQL**.
3. Attach the same VPC and private subnets used by `dev-serving-api`.
4. Authentication: **Secrets Manager**, choose your existing `RDS_SECRET_ARN`.
5. IAM role: select/create the role that allows proxy access to that secret.
6. Security groups:
   - Proxy SG allows inbound TCP 5432 from Lambda SG.
   - RDS SG allows inbound TCP 5432 from Proxy SG.
7. Create proxy, wait until status is **Available**.
8. Register your DB instance/cluster in target group `default`.
9. Copy proxy endpoint and update the secret `host` value to that endpoint.

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
