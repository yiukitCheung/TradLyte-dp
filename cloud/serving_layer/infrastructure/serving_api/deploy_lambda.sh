#!/usr/bin/env bash
# Build and deploy the FastAPI serving Lambda (zip package).
#
# Usage:
#   AWS_REGION=ca-west-1 FUNCTION_NAME=dev-serving-api \
#   SOURCE_VPC_LAMBDA=dev-batch-daily-ohlcv-ingest-handler \
#   RDS_SECRET_ARN=arn:aws:secretsmanager:...:secret:... \
#   SERVING_API_KEY=change-me \
#   ./cloud/serving_layer/infrastructure/serving_api/deploy_lambda.sh

set -euo pipefail
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
SERVING_DIR="$REPO_ROOT/cloud/serving_layer/lambda_functions/serving_api"
COMMON_DIR="$REPO_ROOT/cloud/batch_layer/infrastructure/common"

# shellcheck source=/dev/null
source "$COMMON_DIR/pip_for_lambda.sh"

AWS_REGION="${AWS_REGION:-ca-west-1}"
FUNCTION_NAME="${FUNCTION_NAME:-dev-serving-api}"
LAMBDA_RUNTIME="${LAMBDA_RUNTIME:-python3.11}"
LAMBDA_ARCH="${LAMBDA_ARCH:-x86_64}"
LAMBDA_TIMEOUT="${LAMBDA_TIMEOUT:-10}"
LAMBDA_MEMORY="${LAMBDA_MEMORY:-512}"
LAMBDA_RESERVED_CONCURRENCY="${LAMBDA_RESERVED_CONCURRENCY:-50}"
SOURCE_VPC_LAMBDA="${SOURCE_VPC_LAMBDA:-dev-batch-daily-ohlcv-ingest-handler}"
LAMBDA_ROLE_ARN="${LAMBDA_ROLE_ARN:-}"

RDS_SECRET_ARN="${RDS_SECRET_ARN:-}"
SERVING_API_KEY="${SERVING_API_KEY:-}"
ALLOWED_ORIGIN="${ALLOWED_ORIGIN:-*}"
SCREENER_CACHE_TTL_S="${SCREENER_CACHE_TTL_S:-60}"
RETURNS_CACHE_TTL_S="${RETURNS_CACHE_TTL_S:-300}"

PACKAGE_DIR="$SCRIPT_DIR/package"
ZIP_PATH="$SCRIPT_DIR/${FUNCTION_NAME}.zip"

cleanup() {
  rm -rf "$PACKAGE_DIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "🚀 Deploying Lambda: $FUNCTION_NAME"
echo "Region: $AWS_REGION"

if ! aws lambda get-function --function-name "$SOURCE_VPC_LAMBDA" --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "❌ SOURCE_VPC_LAMBDA not found: $SOURCE_VPC_LAMBDA"
  exit 1
fi

VPC_JSON="$(aws lambda get-function-configuration --function-name "$SOURCE_VPC_LAMBDA" --region "$AWS_REGION" --query 'VpcConfig' --output json)"
SUBNET_IDS="$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(' '.join(d.get('SubnetIds', [])))" "$VPC_JSON")"
SECURITY_GROUP_IDS="$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(' '.join(d.get('SecurityGroupIds', [])))" "$VPC_JSON")"
SUBNET_IDS_CSV="$(echo "$SUBNET_IDS" | tr ' ' ',')"
SECURITY_GROUP_IDS_CSV="$(echo "$SECURITY_GROUP_IDS" | tr ' ' ',')"

if [[ -z "$SUBNET_IDS" || -z "$SECURITY_GROUP_IDS" ]]; then
  echo "❌ Could not derive VPC config from $SOURCE_VPC_LAMBDA"
  exit 1
fi

rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR/serving_api"

pip_for_lambda_x86_64 "$SERVING_DIR/requirements.txt" "$PACKAGE_DIR"
cp -R "$SERVING_DIR/"* "$PACKAGE_DIR/serving_api/"

find "$PACKAGE_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$PACKAGE_DIR" -name "*.pyc" -delete

cd "$PACKAGE_DIR"
zip -r9 "$ZIP_PATH" . -x "*.pyc" "*/__pycache__/*"
cd "$SCRIPT_DIR"

if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "⬆️  Updating existing function code"
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$ZIP_PATH" \
    --region "$AWS_REGION" >/dev/null
else
  if [[ -z "$LAMBDA_ROLE_ARN" ]]; then
    echo "❌ LAMBDA_ROLE_ARN is required to create a new Lambda function"
    exit 1
  fi
  echo "➕ Creating new function"
  aws lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --runtime "$LAMBDA_RUNTIME" \
    --role "$LAMBDA_ROLE_ARN" \
    --handler "serving_api.handler.lambda_handler" \
    --architectures "$LAMBDA_ARCH" \
    --zip-file "fileb://$ZIP_PATH" \
    --timeout "$LAMBDA_TIMEOUT" \
    --memory-size "$LAMBDA_MEMORY" \
    --vpc-config "SubnetIds=${SUBNET_IDS_CSV},SecurityGroupIds=${SECURITY_GROUP_IDS_CSV}" \
    --region "$AWS_REGION" >/dev/null
fi

echo "⚙️  Applying runtime/network/environment settings"
aws lambda update-function-configuration \
  --function-name "$FUNCTION_NAME" \
  --runtime "$LAMBDA_RUNTIME" \
  --handler "serving_api.handler.lambda_handler" \
  --timeout "$LAMBDA_TIMEOUT" \
  --memory-size "$LAMBDA_MEMORY" \
  --vpc-config "SubnetIds=${SUBNET_IDS_CSV},SecurityGroupIds=${SECURITY_GROUP_IDS_CSV}" \
  --environment "Variables={RDS_SECRET_ARN=${RDS_SECRET_ARN},SERVING_API_KEY=${SERVING_API_KEY},ALLOWED_ORIGIN=${ALLOWED_ORIGIN},SCREENER_CACHE_TTL_S=${SCREENER_CACHE_TTL_S},RETURNS_CACHE_TTL_S=${RETURNS_CACHE_TTL_S}}" \
  --region "$AWS_REGION" >/dev/null

aws lambda put-function-concurrency \
  --function-name "$FUNCTION_NAME" \
  --reserved-concurrent-executions "$LAMBDA_RESERVED_CONCURRENCY" \
  --region "$AWS_REGION" >/dev/null

echo "✅ Lambda deployed: $FUNCTION_NAME"
