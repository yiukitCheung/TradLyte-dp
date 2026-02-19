#!/bin/bash
# Build and deploy Lambda functions to AWS

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Point to batch_layer/fetching (application code) not infrastructure/fetching
INFRA_FETCHING_DIR="$(dirname "$SCRIPT_DIR")"
BATCH_LAYER_DIR="$(dirname "$(dirname "$INFRA_FETCHING_DIR")")"
AWS_ARCH_DIR="$(dirname "$BATCH_LAYER_DIR")"
FETCHING_DIR="$BATCH_LAYER_DIR/fetching"
SHARED_DIR="$AWS_ARCH_DIR/shared"  # Canonical shared package

# AWS Configuration
AWS_REGION="${AWS_REGION:-ca-west-1}"
FUNCTION_PREFIX="${FUNCTION_PREFIX:-dev-batch-}"  # Customize this prefix

echo "🚀 Building and deploying Lambda functions..."
echo "Region: $AWS_REGION"

# Function to build and deploy a Lambda package
build_and_deploy_lambda() {
    local function_name=$1
    local file_name=$(echo "$function_name" | tr '-' '_')  # Convert dashes to underscores for file name
    local package_dir="$SCRIPT_DIR/package/$function_name"
    
    echo ""
    echo "=" "=" "=" "=" "=" "=" "=" "=" "=" "="
    echo "📦 Building $function_name..."
    echo "=" "=" "=" "=" "=" "=" "=" "=" "=" "="
    
    # Create package directory
    mkdir -p "$package_dir"
    
    # Install dependencies (for Linux x86_64 - Lambda runtime)
    echo "📥 Installing dependencies for Linux x86_64..."
    # Use pip3 if available, otherwise fall back to pip
    PIP_CMD=$(command -v pip3 || command -v pip || echo "pip3")
    $PIP_CMD install -r "$FETCHING_DIR/requirements.txt" -t "$package_dir" \
        --platform manylinux2014_x86_64 \
        --only-binary=:all: \
        --python-version 3.11 \
        --implementation cp \
        --no-cache-dir \
        --quiet 2>/dev/null || \
    $PIP_CMD install -r "$FETCHING_DIR/requirements.txt" -t "$package_dir" \
        --no-cache-dir \
        --quiet
    
    # Copy Lambda function
    echo "📄 Copying Lambda function code..."
    cp "$FETCHING_DIR/lambda_functions/${file_name}.py" "$package_dir/${file_name}.py"
    
    # Copy shared modules (only what Lambda needs)
    echo "📁 Copying shared modules..."
    mkdir -p "$package_dir/shared/clients"
    mkdir -p "$package_dir/shared/models"
    mkdir -p "$package_dir/shared/utils"
    
    # Copy only required clients (not redis, kinesis, aurora)
    cp "$SHARED_DIR/clients/polygon_client.py" "$package_dir/shared/clients/"
    cp "$SHARED_DIR/clients/rds_timescale_client.py" "$package_dir/shared/clients/"
    
    # Create minimal __init__.py for Lambda (only what we need)
    cat > "$package_dir/shared/clients/__init__.py" << 'EOF'
"""Client modules for Lambda functions"""
from .polygon_client import PolygonClient
from .rds_timescale_client import RDSTimescaleClient

__all__ = ['PolygonClient', 'RDSTimescaleClient']
EOF
    
    # Copy models and utils
    cp -r "$SHARED_DIR/models/"* "$package_dir/shared/models/" 2>/dev/null || true
    if [ -n "$(ls -A "$SHARED_DIR/utils/" 2>/dev/null)" ]; then
        cp -r "$SHARED_DIR/utils/"* "$package_dir/shared/utils/"
    fi
    cp "$SHARED_DIR/__init__.py" "$package_dir/shared/"
    
    # Remove cache files
    echo "🧹 Cleaning cache files..."
    find "$package_dir" -name "*.pyc" -delete
    find "$package_dir" -name "*.pyo" -delete
    find "$package_dir" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    
    # Create ZIP file
    echo "📦 Creating deployment package..."
    cd "$package_dir"
    zip -r9 "$SCRIPT_DIR/$function_name.zip" . -x "*.pyc" "*/__pycache__/*"
    cd "$SCRIPT_DIR"
    
    # Check package size
    local size=$(du -h "$SCRIPT_DIR/$function_name.zip" | cut -f1)
    local size_bytes=$(stat -f%z "$SCRIPT_DIR/$function_name.zip" 2>/dev/null || stat -c%s "$SCRIPT_DIR/$function_name.zip")
    echo "✅ Created $function_name.zip ($size)"
    
    # Deploy to AWS
    echo "🚀 Deploying to AWS Lambda..."
    
    # Try with prefix first, then without
    local aws_function_name="${FUNCTION_PREFIX}${function_name}"
    
    # For packages > 50MB, upload to S3 first
    if [ "$size_bytes" -gt 52428800 ]; then
        echo "📦 Package is large ($size), uploading to S3 first..."
        local s3_bucket="${LAMBDA_DEPLOY_BUCKET:-dev-condvest-lambda-deploy}"
        local s3_key="lambda-packages/$function_name-$(date +%s).zip"
        
        # Create S3 bucket if it doesn't exist
        if ! aws s3 ls "s3://$s3_bucket" --region "$AWS_REGION" 2>/dev/null; then
            echo "📦 Creating S3 bucket: $s3_bucket"
            aws s3 mb "s3://$s3_bucket" --region "$AWS_REGION" 2>/dev/null || true
        fi
        
        # Upload to S3
        echo "⬆️  Uploading to S3: s3://$s3_bucket/$s3_key"
        aws s3 cp "$SCRIPT_DIR/$function_name.zip" "s3://$s3_bucket/$s3_key" --region "$AWS_REGION"
        
        # Update Lambda from S3
        if aws lambda get-function --function-name "$aws_function_name" --region "$AWS_REGION" &>/dev/null; then
            echo "📝 Updating function from S3: $aws_function_name"
            result=$(aws lambda update-function-code \
                --function-name "$aws_function_name" \
                --s3-bucket "$s3_bucket" \
                --s3-key "$s3_key" \
                --region "$AWS_REGION" \
                --output json)
            
            echo "✅ Updated successfully from S3!"
            echo "   Last Modified: $(echo $result | jq -r '.LastModified')"
            echo "   Code Size: $(echo $result | jq -r '.CodeSize') bytes"
        elif aws lambda get-function --function-name "$function_name" --region "$AWS_REGION" &>/dev/null; then
            echo "📝 Updating function from S3: $function_name"
            result=$(aws lambda update-function-code \
                --function-name "$function_name" \
                --s3-bucket "$s3_bucket" \
                --s3-key "$s3_key" \
                --region "$AWS_REGION" \
                --output json)
            
            echo "✅ Updated successfully from S3!"
            echo "   Last Modified: $(echo $result | jq -r '.LastModified')"
            echo "   Code Size: $(echo $result | jq -r '.CodeSize') bytes"
        else
            echo "❌ Function not found in AWS (tried: $aws_function_name and $function_name)"
            echo "💡 Create it first via AWS Console, then run this script again."
        fi
    else
        # Small package, direct upload
        if aws lambda get-function --function-name "$aws_function_name" --region "$AWS_REGION" &>/dev/null; then
            echo "📝 Updating function: $aws_function_name"
            result=$(aws lambda update-function-code \
                --function-name "$aws_function_name" \
                --zip-file "fileb://$SCRIPT_DIR/$function_name.zip" \
                --region "$AWS_REGION" \
                --output json)
            
            echo "✅ Updated successfully!"
            echo "   Last Modified: $(echo $result | jq -r '.LastModified')"
            echo "   Code Size: $(echo $result | jq -r '.CodeSize') bytes"
        elif aws lambda get-function --function-name "$function_name" --region "$AWS_REGION" &>/dev/null; then
            echo "📝 Updating function: $function_name"
            result=$(aws lambda update-function-code \
                --function-name "$function_name" \
                --zip-file "fileb://$SCRIPT_DIR/$function_name.zip" \
                --region "$AWS_REGION" \
                --output json)
            
            echo "✅ Updated successfully!"
            echo "   Last Modified: $(echo $result | jq -r '.LastModified')"
            echo "   Code Size: $(echo $result | jq -r '.CodeSize') bytes"
        else
            echo "❌ Function not found in AWS (tried: $aws_function_name and $function_name)"
            echo "💡 Create it first via AWS Console, then run this script again."
        fi
    fi
}

# Clean up previous builds
echo "🧹 Cleaning previous builds..."
rm -rf "$SCRIPT_DIR"/package/
mkdir -p "$SCRIPT_DIR"/package

# Build and deploy Lambda functions (fetchers only)
# Note: Consolidation moved to AWS Batch (see processing/batch_jobs/)
build_and_deploy_lambda "daily-ohlcv-fetcher"
build_and_deploy_lambda "daily-meta-fetcher"

# Clean up package directory
rm -rf "$SCRIPT_DIR"/package/

echo ""
echo "=" "=" "=" "=" "=" "=" "=" "=" "=" "="
echo "🎉 Deployment complete!"
echo "=" "=" "=" "=" "=" "=" "=" "=" "=" "="
echo ""
echo "📊 Deployed packages (now stored in S3):"
for zip_file in "$SCRIPT_DIR"/*.zip; do
    if [ -f "$zip_file" ]; then
        size=$(du -h "$zip_file" | cut -f1)
        echo "  $(basename "$zip_file"): $size → s3://$LAMBDA_DEPLOY_BUCKET/lambda-packages/"
    fi
done

# Clean up local zip files (they're now in S3)
echo ""
echo "🧹 Cleaning up local zip files (already uploaded to S3)..."
rm -f "$SCRIPT_DIR"/*.zip
echo "✅ Local zip files removed"

echo ""
echo "💡 Tips:"
echo "  - View logs: aws logs tail /aws/lambda/daily_ohlcv_fetcher --follow"
echo "  - Test function: aws lambda invoke --function-name daily_ohlcv_fetcher response.json"
echo ""
echo "📦 Lambda packages stored in S3: s3://${LAMBDA_DEPLOY_BUCKET:-dev-condvest-lambda-deploy}/lambda-packages/"
echo ""
echo "📝 Note: Consolidator/resampler Batch jobs are archived. Pipeline is fetchers only."
echo "   See batch_layer/archive_scripts/README_ARCHIVED_BATCH_JOBS.md"