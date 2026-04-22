#!/bin/bash
# Build and deploy OHLCV ingest + planner Lambdas (VPC; RDS + S3 read)

set -e
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BATCH_LAYER_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
AWS_ARCH_DIR="$(dirname "$BATCH_LAYER_DIR")"
INGESTING_DIR="$BATCH_LAYER_DIR/ingesting"
SHARED_DIR="$AWS_ARCH_DIR/shared"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../common/pip_for_lambda.sh"

AWS_REGION="${AWS_REGION:-ca-west-1}"
FUNCTION_PREFIX="${FUNCTION_PREFIX:-dev-batch-}"
LAMBDA_RUNTIME="${LAMBDA_RUNTIME:-python3.11}"

echo "🚀 Building and deploying ingesting Lambdas..."
echo "Region: $AWS_REGION"

cleanup_artifacts() {
    rm -rf "$SCRIPT_DIR/package" 2>/dev/null || true
    rm -f "$SCRIPT_DIR"/*.zip 2>/dev/null || true
}
trap cleanup_artifacts EXIT INT TERM

set_lambda_python_handler() {
    local deployed_name=$1
    local module_base=$2
    local handler="${module_base}.lambda_handler"
    if ! aws lambda get-function --function-name "$deployed_name" --region "$AWS_REGION" &>/dev/null; then
        return 0
    fi
    echo "⚙️  Setting handler on $deployed_name → $handler"
    aws lambda wait function-updated-v2 --function-name "$deployed_name" --region "$AWS_REGION" 2>/dev/null || sleep 5
    aws lambda update-function-configuration \
        --function-name "$deployed_name" \
        --handler "$handler" \
        --region "$AWS_REGION" \
        --output json >/dev/null
}

ensure_lambda_runtime() {
    local deployed_name=$1
    if ! aws lambda get-function --function-name "$deployed_name" --region "$AWS_REGION" &>/dev/null; then
        return 0
    fi
    aws lambda wait function-updated-v2 --function-name "$deployed_name" --region "$AWS_REGION" 2>/dev/null || sleep 5
    local current_runtime
    current_runtime=$(aws lambda get-function-configuration \
        --function-name "$deployed_name" \
        --region "$AWS_REGION" \
        --query 'Runtime' \
        --output text)
    if [ "$current_runtime" != "$LAMBDA_RUNTIME" ]; then
        echo "⚙️  Updating runtime on $deployed_name: $current_runtime -> $LAMBDA_RUNTIME"
        aws lambda update-function-configuration \
            --function-name "$deployed_name" \
            --runtime "$LAMBDA_RUNTIME" \
            --region "$AWS_REGION" \
            --output json >/dev/null
        aws lambda wait function-updated-v2 --function-name "$deployed_name" --region "$AWS_REGION" 2>/dev/null || sleep 5
    fi
}

get_timeout_for_function() {
    local fn=$1
    case "$fn" in
        daily-ohlcv-ingest-handler) echo "${TIMEOUT_DAILY_OHLCV_INGEST_HANDLER:-300}" ;;
        daily-meta-ingest-handler) echo "${TIMEOUT_DAILY_META_INGEST_HANDLER:-180}" ;;
        *) echo "${TIMEOUT_DEFAULT:-60}" ;;
    esac
}

get_memory_for_function() {
    local fn=$1
    case "$fn" in
        daily-ohlcv-ingest-handler) echo "${MEMORY_DAILY_OHLCV_INGEST_HANDLER:-1024}" ;;
        daily-meta-ingest-handler) echo "${MEMORY_DAILY_META_INGEST_HANDLER:-1024}" ;;
        *) echo "${MEMORY_DEFAULT:-512}" ;;
    esac
}

ensure_lambda_perf_config() {
    local deployed_name=$1
    local function_name=$2
    if ! aws lambda get-function --function-name "$deployed_name" --region "$AWS_REGION" &>/dev/null; then
        return 0
    fi
    aws lambda wait function-updated-v2 --function-name "$deployed_name" --region "$AWS_REGION" 2>/dev/null || sleep 5
    local desired_timeout desired_memory current_timeout current_memory
    desired_timeout=$(get_timeout_for_function "$function_name")
    desired_memory=$(get_memory_for_function "$function_name")
    current_timeout=$(aws lambda get-function-configuration \
        --function-name "$deployed_name" \
        --region "$AWS_REGION" \
        --query 'Timeout' \
        --output text)
    current_memory=$(aws lambda get-function-configuration \
        --function-name "$deployed_name" \
        --region "$AWS_REGION" \
        --query 'MemorySize' \
        --output text)
    if [ "$current_timeout" != "$desired_timeout" ] || [ "$current_memory" != "$desired_memory" ]; then
        echo "⚙️  Updating perf on $deployed_name: timeout ${current_timeout}s -> ${desired_timeout}s, memory ${current_memory}MB -> ${desired_memory}MB"
        aws lambda update-function-configuration \
            --function-name "$deployed_name" \
            --timeout "$desired_timeout" \
            --memory-size "$desired_memory" \
            --region "$AWS_REGION" \
            --output json >/dev/null
        aws lambda wait function-updated-v2 --function-name "$deployed_name" --region "$AWS_REGION" 2>/dev/null || sleep 5
    fi
}

build_and_deploy_lambda() {
    local function_name=$1
    local file_name
    file_name=$(echo "$function_name" | tr '-' '_')
    local package_dir="$SCRIPT_DIR/package/$function_name"

    echo ""
    echo "📦 Building $function_name..."

    mkdir -p "$package_dir"

    pip_for_lambda_x86_64 "$INGESTING_DIR/requirements.txt" "$package_dir"

    cp "$INGESTING_DIR/lambda_functions/${file_name}.py" "$package_dir/${file_name}.py"

    mkdir -p "$package_dir/shared/clients"
    mkdir -p "$package_dir/shared/models"
    mkdir -p "$package_dir/shared/utils"

    cp "$SHARED_DIR/clients/rds_timescale_client.py" "$package_dir/shared/clients/"
    cat > "$package_dir/shared/clients/__init__.py" << 'EOF'
"""Client modules for Lambda functions"""
from .rds_timescale_client import RDSTimescaleClient

__all__ = ['RDSTimescaleClient']
EOF

    cp -r "$SHARED_DIR/models/"* "$package_dir/shared/models/" 2>/dev/null || true
    if [ -n "$(ls -A "$SHARED_DIR/utils/" 2>/dev/null)" ]; then
        cp -r "$SHARED_DIR/utils/"* "$package_dir/shared/utils/"
    fi
    cp "$SHARED_DIR/__init__.py" "$package_dir/shared/"

    find "$package_dir" -name "*.pyc" -delete
    find "$package_dir" -name "*.pyo" -delete
    find "$package_dir" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

    cd "$package_dir"
    zip -r9 "$SCRIPT_DIR/$function_name.zip" . -x "*.pyc" "*/__pycache__/*"
    cd "$SCRIPT_DIR"

    local size_bytes
    size_bytes=$(stat -f%z "$SCRIPT_DIR/$function_name.zip" 2>/dev/null || stat -c%s "$SCRIPT_DIR/$function_name.zip")
    local aws_function_name="${FUNCTION_PREFIX}${function_name}"

    echo "🚀 Deploying to AWS Lambda..."

    if [ "$size_bytes" -gt 52428800 ]; then
        local s3_bucket="${LAMBDA_DEPLOY_BUCKET:-dev-condvest-lambda-deploy}"
        local s3_key="lambda-packages/$function_name-$(date +%s).zip"
        if ! aws s3 ls "s3://$s3_bucket" --region "$AWS_REGION" 2>/dev/null; then
            aws s3 mb "s3://$s3_bucket" --region "$AWS_REGION" 2>/dev/null || true
        fi
        aws s3 cp "$SCRIPT_DIR/$function_name.zip" "s3://$s3_bucket/$s3_key" --region "$AWS_REGION"
        if aws lambda get-function --function-name "$aws_function_name" --region "$AWS_REGION" &>/dev/null; then
            aws lambda update-function-code \
                --function-name "$aws_function_name" \
                --s3-bucket "$s3_bucket" \
                --s3-key "$s3_key" \
                --region "$AWS_REGION" \
                --output json
            ensure_lambda_runtime "$aws_function_name"
            ensure_lambda_perf_config "$aws_function_name" "$function_name"
            set_lambda_python_handler "$aws_function_name" "$file_name"
        elif aws lambda get-function --function-name "$function_name" --region "$AWS_REGION" &>/dev/null; then
            aws lambda update-function-code \
                --function-name "$function_name" \
                --s3-bucket "$s3_bucket" \
                --s3-key "$s3_key" \
                --region "$AWS_REGION" \
                --output json
            ensure_lambda_runtime "$function_name"
            ensure_lambda_perf_config "$function_name" "$function_name"
            set_lambda_python_handler "$function_name" "$file_name"
        else
            echo "❌ Function not found: $aws_function_name (create in console first)"
        fi
    else
        if aws lambda get-function --function-name "$aws_function_name" --region "$AWS_REGION" &>/dev/null; then
            aws lambda update-function-code \
                --function-name "$aws_function_name" \
                --zip-file "fileb://$SCRIPT_DIR/$function_name.zip" \
                --region "$AWS_REGION" \
                --output json
            ensure_lambda_runtime "$aws_function_name"
            ensure_lambda_perf_config "$aws_function_name" "$function_name"
            set_lambda_python_handler "$aws_function_name" "$file_name"
        elif aws lambda get-function --function-name "$function_name" --region "$AWS_REGION" &>/dev/null; then
            aws lambda update-function-code \
                --function-name "$function_name" \
                --zip-file "fileb://$SCRIPT_DIR/$function_name.zip" \
                --region "$AWS_REGION" \
                --output json
            ensure_lambda_runtime "$function_name"
            ensure_lambda_perf_config "$function_name" "$function_name"
            set_lambda_python_handler "$function_name" "$file_name"
        else
            echo "❌ Function not found: $aws_function_name (create in console first)"
        fi
    fi
}

rm -rf "$SCRIPT_DIR/package"
mkdir -p "$SCRIPT_DIR/package"

build_and_deploy_lambda "daily-ohlcv-ingest-handler"
build_and_deploy_lambda "daily-meta-ingest-handler"

echo ""
echo "🎉 Ingesting Lambdas packaged and deployed (if functions exist)."
echo "💡 Wire S3 → SQS → daily-ohlcv-ingest-handler."
echo "💡 Optional async meta path: infrastructure/ingesting/wire_meta_ingest_s3_sqs.sh (skip if Step Functions already sync-ingests meta; avoid duplicate RDS writes unless upserts are idempotent)."
echo "💡 OHLCV planner is built from batch_layer/fetching (see infrastructure/fetching/deploy_lambda.sh); set RDS_SECRET_ARN + OHLCV_FETCHER_FUNCTION_NAME on that Lambda."
