#!/bin/bash
# Build and push the scanner Docker image to ECR.
#
# Usage:
#   ./build_scanner_container.sh                  # build + push (default)
#   ./build_scanner_container.sh --local-only     # build locally, skip ECR push
#   ./build_scanner_container.sh --tag v1.2.0     # custom image tag
#   ./build_scanner_container.sh --help
#
# The image is used for both scanner_worker (Array Job) and scanner_aggregator
# (single job). Step Functions sets JOB_TYPE per submission.

set -e

# ── path resolution ───────────────────────────────────────────────────────────
# Script lives at: cloud/batch_layer/infrastructure/processing/batch_job/
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_PROCESSING_DIR="$(dirname "$SCRIPT_DIR")"   # infrastructure/processing
INFRA_DIR="$(dirname "$INFRA_PROCESSING_DIR")"    # infrastructure
BATCH_LAYER_DIR="$(dirname "$INFRA_DIR")"         # batch_layer
CLOUD_DIR="$(dirname "$BATCH_LAYER_DIR")"         # cloud

PROCESSING_DIR="$BATCH_LAYER_DIR/processing"      # cloud/batch_layer/processing
SHARED_DIR="$CLOUD_DIR/shared"                    # cloud/shared

# ── configuration ─────────────────────────────────────────────────────────────
AWS_REGION="${AWS_REGION:-ca-west-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")}"
ECR_REPOSITORY="${ECR_REPOSITORY:-dev-batch-scanner}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
LOCAL_ONLY=false

# ── argument parsing ──────────────────────────────────────────────────────────
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --tag TAG        Image tag (default: latest)"
    echo "  --repo REPO      ECR repository name (default: dev-batch-scanner)"
    echo "  --region REGION  AWS region (default: ca-west-1)"
    echo "  --local-only     Build locally without pushing to ECR"
    echo "  -h, --help       Show this help"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --tag)    IMAGE_TAG="$2";      shift 2 ;;
        --repo)   ECR_REPOSITORY="$2"; shift 2 ;;
        --region) AWS_REGION="$2";     shift 2 ;;
        --local-only) LOCAL_ONLY=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

ECR_IMAGE="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:$IMAGE_TAG"

echo "============================================================"
echo "  Scanner Container Build"
echo "============================================================"
echo "Region:     $AWS_REGION"
echo "Account:    $AWS_ACCOUNT_ID"
echo "Repository: $ECR_REPOSITORY"
echo "Tag:        $IMAGE_TAG"
echo "ECR Image:  $ECR_IMAGE"
echo "Mode:       $( $LOCAL_ONLY && echo 'LOCAL ONLY' || echo 'BUILD + PUSH' )"
echo ""

# ── ECR login ─────────────────────────────────────────────────────────────────
if ! $LOCAL_ONLY; then
    echo "Authenticating with ECR..."
    aws ecr get-login-password --region "$AWS_REGION" \
        | docker login --username AWS --password-stdin \
            "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

    # Create ECR repo if it doesn't exist
    if ! aws ecr describe-repositories \
            --repository-names "$ECR_REPOSITORY" \
            --region "$AWS_REGION" &>/dev/null; then
        echo "Creating ECR repository: $ECR_REPOSITORY"
        aws ecr create-repository \
            --repository-name "$ECR_REPOSITORY" \
            --image-scanning-configuration scanOnPush=true \
            --region "$AWS_REGION" \
            --output json | jq '{repositoryUri}'
    fi
fi

# ── stage shared modules into Docker build context ────────────────────────────
echo "Staging shared modules into build context..."
SHARED_STAGING="$PROCESSING_DIR/shared"
rm -rf "$SHARED_STAGING"
cp -r "$SHARED_DIR" "$SHARED_STAGING"
echo "  Staged: $SHARED_STAGING"

# ── docker build ──────────────────────────────────────────────────────────────
echo "Building Docker image (linux/amd64)..."
docker build \
    --platform linux/amd64 \
    -f "$SCRIPT_DIR/Dockerfile.scanner" \
    -t "$ECR_REPOSITORY:$IMAGE_TAG" \
    --build-arg BUILD_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    --build-arg VCS_REF="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')" \
    "$BATCH_LAYER_DIR"

# ── push to ECR ───────────────────────────────────────────────────────────────
if ! $LOCAL_ONLY; then
    echo "Tagging for ECR..."
    docker tag "$ECR_REPOSITORY:$IMAGE_TAG" "$ECR_IMAGE"

    echo "Pushing to ECR..."
    docker push "$ECR_IMAGE"
    echo "Pushed: $ECR_IMAGE"
fi

# ── cleanup staged shared dir ─────────────────────────────────────────────────
echo "Cleaning up staged shared modules..."
rm -rf "$SHARED_STAGING"

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Build complete"
echo "============================================================"
docker images "$ECR_REPOSITORY:$IMAGE_TAG" \
    --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"
echo ""
if ! $LOCAL_ONLY; then
    echo "ECR image ready: $ECR_IMAGE"
    echo ""
    echo "Next step — register Batch job definitions:"
    echo "  ./deploy_scanner_batch_jobs.sh"
fi
echo ""
