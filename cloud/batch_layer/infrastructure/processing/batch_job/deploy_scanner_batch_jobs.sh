#!/bin/bash
# Register the scanner aggregator AWS Batch job definition.
#
# Usage:
#   ./deploy_scanner_batch_jobs.sh
#   ./deploy_scanner_batch_jobs.sh --help

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

AWS_REGION="${AWS_REGION:-ca-west-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
ECR_REPOSITORY="${ECR_REPOSITORY:-dev-batch-scanner}"
JOB_QUEUE="${JOB_QUEUE:-dev-batch-scanner}"
LOG_GROUP="${LOG_GROUP:-/aws/batch/dev-batch-scanner}"

AGGREGATOR_JOB_NAME="${ENVIRONMENT}-batch-scanner-aggregator"

S3_BUCKET="${S3_BUCKET:-dev-condvest-datalake}"
RDS_SECRET_ARN="${RDS_SECRET_ARN:-arn:aws:secretsmanager:${AWS_REGION}:${AWS_ACCOUNT_ID}:secret:dev-batch-postgres-credentials}"

usage() {
    echo "Usage: $0 [--region REGION] [--queue QUEUE] [-h|--help]"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --region) AWS_REGION="$2"; shift 2 ;;
        --queue)  JOB_QUEUE="$2";  shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

ECR_IMAGE="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:latest"

echo "============================================================"
echo "  Scanner Aggregator — Batch Job Definition"
echo "============================================================"
echo "Region:      $AWS_REGION"
echo "ECR Image:   $ECR_IMAGE"
echo "Job Queue:   $JOB_QUEUE"
echo ""

get_iam_roles() {
    EXISTING=$(aws batch describe-job-definitions \
        --job-definition-name "$AGGREGATOR_JOB_NAME" \
        --status ACTIVE \
        --region "$AWS_REGION" \
        --query 'jobDefinitions[0]' \
        --output json 2>/dev/null || echo "null")

    if [ "$EXISTING" != "null" ] && [ "$EXISTING" != "" ]; then
        JOB_ROLE_ARN=$(echo "$EXISTING" | jq -r '.containerProperties.jobRoleArn // empty')
        EXECUTION_ROLE_ARN=$(echo "$EXISTING" | jq -r '.containerProperties.executionRoleArn // empty')
    fi

    JOB_ROLE_ARN="${JOB_ROLE_ARN:-arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ENVIRONMENT}-condvest-batch-processing-execution-role}"
    EXECUTION_ROLE_ARN="${EXECUTION_ROLE_ARN:-$JOB_ROLE_ARN}"
}

ensure_log_group() {
    if ! aws logs describe-log-groups \
            --log-group-name-prefix "$LOG_GROUP" \
            --region "$AWS_REGION" \
            --query 'logGroups[0].logGroupName' \
            --output text 2>/dev/null | grep -q "$LOG_GROUP"; then
        aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$AWS_REGION" 2>/dev/null || true
        aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 30 --region "$AWS_REGION" 2>/dev/null || true
    fi
}

deploy_aggregator() {
    echo "Registering $AGGREGATOR_JOB_NAME (2 vCPU / 4 GB)..."

    JOB_DEF=$(cat <<EOF
{
    "jobDefinitionName": "${AGGREGATOR_JOB_NAME}",
    "type": "container",
    "containerProperties": {
        "image": "${ECR_IMAGE}",
        "jobRoleArn": "${JOB_ROLE_ARN}",
        "executionRoleArn": "${EXECUTION_ROLE_ARN}",
        "resourceRequirements": [
            {"type": "VCPU",   "value": "2"},
            {"type": "MEMORY", "value": "4096"}
        ],
        "environment": [
            {"name": "AWS_REGION",     "value": "${AWS_REGION}"},
            {"name": "S3_BUCKET_NAME", "value": "${S3_BUCKET}"},
            {"name": "RDS_SECRET_ARN", "value": "${RDS_SECRET_ARN}"}
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group":         "${LOG_GROUP}",
                "awslogs-region":        "${AWS_REGION}",
                "awslogs-stream-prefix": "aggregator"
            }
        },
        "networkConfiguration":        {"assignPublicIp": "ENABLED"},
        "fargatePlatformConfiguration": {"platformVersion": "LATEST"}
    },
    "retryStrategy": {"attempts": 2},
    "timeout":       {"attemptDurationSeconds": 1200},
    "platformCapabilities": ["FARGATE"],
    "tags": {
        "Environment": "${ENVIRONMENT}",
        "Component":   "scanner-aggregator"
    }
}
EOF
)

    aws batch register-job-definition \
        --cli-input-json "$JOB_DEF" \
        --region "$AWS_REGION" \
        --output json | jq '{jobDefinitionName, revision}'
}

get_iam_roles
ensure_log_group
deploy_aggregator

echo ""
echo "Test:"
echo "  aws batch submit-job --job-name test-scanner-aggregator-\$(date +%s) \\"
echo "    --job-queue $JOB_QUEUE --job-definition $AGGREGATOR_JOB_NAME \\"
echo "    --container-overrides 'environment=[{name=SCAN_DATE,value=$(date +%Y-%m-%d)}]' \\"
echo "    --region $AWS_REGION"
