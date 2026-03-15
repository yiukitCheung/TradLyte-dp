#!/bin/bash
# DEPRECATED: Resampler/consolidator container. Jobs moved to batch_layer/archive_scripts/
# Pipeline uses raw 1d only; resampling is on-the-fly in backtester.
# Use archive_scripts/build_batch_container.sh if you need to build this image.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
BATCH_DIR="$(dirname "$INFRA_DIR")"
AWS_ARCH_DIR="$(dirname "$BATCH_DIR")"
PROCESSING_DIR="$BATCH_DIR/processing"
SHARED_DIR="$AWS_ARCH_DIR/shared"

# Default values
AWS_REGION=${AWS_REGION:-ca-west-1}
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}
ECR_REPOSITORY=${ECR_REPOSITORY:-dev-batch-processor}
IMAGE_TAG=${IMAGE_TAG:-latest}

# Usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --tag TAG        Image tag (default: latest)"
    echo "  --repo REPO      ECR repository name (default: dev-batch-processor)"
    echo "  --region REGION  AWS region (default: ca-west-1)"
    echo "  --local-only     Build locally without pushing to ECR"
    echo "  -h, --help       Show this help message"
    echo ""
    echo "The container supports both resampler and consolidator jobs."
    echo ""
    echo "Examples:"
    echo "  # Build and push (default)"
    echo "  $0"
    echo ""
    echo "  # Build with custom tag"
    echo "  $0 --tag v1.2.0"
    echo ""
    echo "  # Build locally only (no push)"
    echo "  $0 --local-only"
}

# Parse arguments
LOCAL_ONLY=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --repo)
            ECR_REPOSITORY="$2"
            shift 2
            ;;
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        --local-only)
            LOCAL_ONLY=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

echo "============================================================"
echo "🔧 Building Docker container for Batch Jobs"
echo "============================================================"
echo "📍 Region: $AWS_REGION"
echo "🏗️ Repository: $ECR_REPOSITORY"
echo "🏷️ Tag: $IMAGE_TAG"
echo "📦 Jobs included: resampler, consolidator"
echo ""

# Get ECR login token (if not local-only)
if [ "$LOCAL_ONLY" = false ]; then
    echo "🔐 Authenticating with ECR..."
    aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
fi

# Copy shared files to processing directory for Docker build
echo "📁 Copying shared modules..."
rm -rf "$PROCESSING_DIR/shared"
cp -r "$SHARED_DIR" "$PROCESSING_DIR/"

# Build Docker image
echo "🐳 Building Docker image..."
cd "$BATCH_DIR"
docker build \
    --platform linux/amd64 \
    -f "$SCRIPT_DIR/Dockerfile" \
    -t "$ECR_REPOSITORY:$IMAGE_TAG" \
    --build-arg BUILD_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    --build-arg VCS_REF="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')" \
    .

if [ "$LOCAL_ONLY" = false ]; then
    # Tag for ECR
    echo "🏷️ Tagging image for ECR..."
    docker tag "$ECR_REPOSITORY:$IMAGE_TAG" "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:$IMAGE_TAG"

    # Push to ECR
    echo "⬆️ Pushing to ECR..."
    docker push "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:$IMAGE_TAG"
    
    echo ""
    echo "✅ Docker container built and pushed successfully!"
    echo "📍 Image URI: $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:$IMAGE_TAG"
else
    echo ""
    echo "✅ Docker container built locally (not pushed to ECR)"
fi

# Clean up copied shared files
echo "🧹 Cleaning up..."
rm -rf "$PROCESSING_DIR/shared"

# Show image details
echo ""
echo "📊 Image details:"
docker images "$ECR_REPOSITORY:$IMAGE_TAG" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"

# Show usage instructions
echo ""
echo "============================================================"
echo "📝 AWS Batch Job Configuration"
echo "============================================================"
echo ""
echo "To run as RESAMPLER (default):"
echo "  Container Command: python resampler.py"
echo ""
echo "To run as CONSOLIDATOR:"
echo "  Container Command: python consolidator.py"
echo ""
echo "Environment Variables for Consolidator:"
echo "  S3_BUCKET         - S3 bucket name (default: dev-condvest-datalake)"
echo "  S3_PREFIX         - S3 prefix (default: bronze/raw_ohlcv)"
echo "  MODE              - incremental or full (default: incremental)"
echo "  MAX_WORKERS       - Parallel workers (default: 10)"
echo "  RETENTION_DAYS    - Days to keep date files (default: 30)"
echo ""
