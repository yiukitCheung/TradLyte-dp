#!/usr/bin/env bash
# Build and push Backtester Lambda container image (linux/arm64) to ECR.
#
# Usage (from repo root):
#   AWS_REGION=ca-west-1 AWS_ACCOUNT_ID=123456789012 ECR_REPO=dev-serving-backtester \
#     ./cloud/serving_layer/infrastructure/docker/build_push_backtester.sh
#
# Prerequisites: docker buildx, aws cli, ecr login

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
AWS_REGION="${AWS_REGION:-ca-west-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")}"
ECR_REPO="${ECR_REPO:-dev-serving-backtester}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
DOCKERFILE="${REPO_ROOT}/cloud/serving_layer/lambda_functions/backtester/Dockerfile"

echo "Region:     $AWS_REGION"
echo "Account:    $AWS_ACCOUNT_ID"
echo "ECR repo:   $ECR_REPO"
echo "Tag:        $IMAGE_TAG"
echo "Dockerfile: $DOCKERFILE"

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"

aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "$ECR_REPO" --region "$AWS_REGION"

aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

docker buildx build \
  --platform linux/arm64 \
  --provenance=false \
  -f "$DOCKERFILE" \
  -t "$ECR_URI" \
  --push \
  "$REPO_ROOT"

echo "Pushed: $ECR_URI"
echo ""
echo "Create/update Lambda function with:"
echo "  Package type: Image"
echo "  Architecture: arm64"
echo "  Image URI:    $ECR_URI"
