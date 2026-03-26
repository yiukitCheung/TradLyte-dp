#!/bin/bash
# Build and deploy OHLCV ingest + planner Lambdas (VPC; RDS + S3 read)

set -e
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BATCH_LAYER_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
AWS_ARCH_DIR="$(dirname "$BATCH_LAYER_DIR")"
INGESTING_DIR="$BATCH_LAYER_DIR/ingesting"
SHARED_DIR="$AWS_ARCH_DIR/shared"

AWS_REGION="${AWS_REGION:-ca-west-1}"
FUNCTION_PREFIX="${FUNCTION_PREFIX:-dev-batch-}"

echo "🚀 Building and deploying ingesting Lambdas..."
echo "Region: $AWS_REGION"

build_and_deploy_lambda() {
    local function_name=$1
    local file_name
    file_name=$(echo "$function_name" | tr '-' '_')
    local package_dir="$SCRIPT_DIR/package/$function_name"

    echo ""
    echo "📦 Building $function_name..."

    mkdir -p "$package_dir"

    PIP_CMD=$(command -v pip3 || command -v pip || echo "pip3")
    $PIP_CMD install -r "$INGESTING_DIR/requirements.txt" -t "$package_dir" \
        --platform manylinux2014_x86_64 \
        --only-binary=:all: \
        --python-version 3.11 \
        --implementation cp \
        --no-cache-dir \
        --quiet 2>/dev/null || \
    $PIP_CMD install -r "$INGESTING_DIR/requirements.txt" -t "$package_dir" \
        --no-cache-dir \
        --quiet

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
        elif aws lambda get-function --function-name "$function_name" --region "$AWS_REGION" &>/dev/null; then
            aws lambda update-function-code \
                --function-name "$function_name" \
                --s3-bucket "$s3_bucket" \
                --s3-key "$s3_key" \
                --region "$AWS_REGION" \
                --output json
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
        elif aws lambda get-function --function-name "$function_name" --region "$AWS_REGION" &>/dev/null; then
            aws lambda update-function-code \
                --function-name "$function_name" \
                --zip-file "fileb://$SCRIPT_DIR/$function_name.zip" \
                --region "$AWS_REGION" \
                --output json
        else
            echo "❌ Function not found: $aws_function_name (create in console first)"
        fi
    fi
}

rm -rf "$SCRIPT_DIR/package"
mkdir -p "$SCRIPT_DIR/package"

build_and_deploy_lambda "daily-ohlcv-ingest-handler"
build_and_deploy_lambda "daily-ohlcv-planner"
build_and_deploy_lambda "daily-meta-ingest-handler"

rm -rf "$SCRIPT_DIR/package"
rm -f "$SCRIPT_DIR"/*.zip

echo ""
echo "🎉 Ingesting Lambdas packaged and deployed (if functions exist)."
echo "💡 Wire S3 → SQS → daily-ohlcv-ingest-handler; S3 manifest -> daily-meta-ingest-handler; EventBridge → daily-ohlcv-planner."
echo "💡 Set OHLCV_FETCHER_FUNCTION_NAME on the planner (e.g. ${FUNCTION_PREFIX}daily-ohlcv-fetcher)."
