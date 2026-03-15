#!/bin/bash
# Register AWS Batch job definitions for the scanner.
#
# Two definitions are created from the same ECR image:
#
#   dev-batch-scanner-worker      4 vCPU / 8 GB   Array Job children
#   dev-batch-scanner-aggregator  2 vCPU / 4 GB   Single job, runs after all workers
#
# JOB_TYPE env var (set by Step Functions ContainerOverrides) controls which
# code path runs inside scan.py.
#
# Usage:
#   ./deploy_scanner_batch_jobs.sh              # register/update both
#   ./deploy_scanner_batch_jobs.sh --job worker
#   ./deploy_scanner_batch_jobs.sh --job aggregator
#   ./deploy_scanner_batch_jobs.sh --help

set -e

# ── path resolution ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── configuration ─────────────────────────────────────────────────────────────
AWS_REGION="${AWS_REGION:-ca-west-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
ECR_REPOSITORY="${ECR_REPOSITORY:-dev-batch-scanner}"
JOB_QUEUE="${JOB_QUEUE:-dev-batch-scanner}"
LOG_GROUP="${LOG_GROUP:-/aws/batch/dev-batch-scanner}"

WORKER_JOB_NAME="${ENVIRONMENT}-batch-scanner-worker"
AGGREGATOR_JOB_NAME="${ENVIRONMENT}-batch-scanner-aggregator"

S3_BUCKET="${S3_BUCKET:-dev-condvest-datalake}"
RDS_SECRET_ARN="${RDS_SECRET_ARN:-arn:aws:secretsmanager:${AWS_REGION}:${AWS_ACCOUNT_ID}:secret:dev-rds-credentials}"

# ── argument parsing ──────────────────────────────────────────────────────────
DEPLOY_TARGET="all"

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --job worker|aggregator|all   Which job(s) to register (default: all)"
    echo "  --region REGION               AWS region (default: ca-west-1)"
    echo "  --queue QUEUE                 Batch job queue name (default: dev-batch-scanner)"
    echo "  -h, --help                    Show this help"
    echo ""
    echo "Env vars:"
    echo "  AWS_REGION        (default: ca-west-1)"
    echo "  ENVIRONMENT       (default: dev)"
    echo "  ECR_REPOSITORY    (default: dev-batch-scanner)"
    echo "  RDS_SECRET_ARN    Secrets Manager ARN for RDS credentials"
    echo "  S3_BUCKET         Datalake bucket name (default: dev-condvest-datalake)"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --job)    DEPLOY_TARGET="$2"; shift 2 ;;
        --region) AWS_REGION="$2";    shift 2 ;;
        --queue)  JOB_QUEUE="$2";     shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

ECR_IMAGE="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:latest"

echo "============================================================"
echo "  Scanner Batch Job Definitions — Deploy"
echo "============================================================"
echo "Region:      $AWS_REGION"
echo "Account:     $AWS_ACCOUNT_ID"
echo "ECR Image:   $ECR_IMAGE"
echo "Job Queue:   $JOB_QUEUE"
echo "Log Group:   $LOG_GROUP"
echo "Deploying:   $DEPLOY_TARGET"
echo ""

# ── resolve IAM roles ─────────────────────────────────────────────────────────
get_iam_roles() {
    echo "Resolving IAM roles..."

    # Try to reuse roles from an existing active scanner job definition first
    for try_name in "$WORKER_JOB_NAME" "$AGGREGATOR_JOB_NAME"; do
        EXISTING=$(aws batch describe-job-definitions \
            --job-definition-name "$try_name" \
            --status ACTIVE \
            --region "$AWS_REGION" \
            --query 'jobDefinitions[0]' \
            --output json 2>/dev/null || echo "null")

        if [ "$EXISTING" != "null" ] && [ "$EXISTING" != "" ]; then
            JOB_ROLE_ARN=$(echo "$EXISTING" | jq -r '.containerProperties.jobRoleArn // empty')
            EXECUTION_ROLE_ARN=$(echo "$EXISTING" | jq -r '.containerProperties.executionRoleArn // empty')
            [ -n "$JOB_ROLE_ARN" ] && break
        fi
    done

    # Fall back to the resampler job's role (same account, same permissions pattern)
    if [ -z "$JOB_ROLE_ARN" ]; then
        RESAMPLER_JOB=$(aws batch describe-job-definitions \
            --job-definition-name "${ENVIRONMENT}-batch-duckdb-resampler" \
            --status ACTIVE \
            --region "$AWS_REGION" \
            --query 'jobDefinitions[0]' \
            --output json 2>/dev/null || echo "null")

        if [ "$RESAMPLER_JOB" != "null" ] && [ "$RESAMPLER_JOB" != "" ]; then
            JOB_ROLE_ARN=$(echo "$RESAMPLER_JOB" | jq -r '.containerProperties.jobRoleArn // empty')
            EXECUTION_ROLE_ARN=$(echo "$RESAMPLER_JOB" | jq -r '.containerProperties.executionRoleArn // empty')
        fi
    fi

    # Hard-coded fallback
    JOB_ROLE_ARN="${JOB_ROLE_ARN:-arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ENVIRONMENT}-condvest-batch-processing-execution-role}"
    EXECUTION_ROLE_ARN="${EXECUTION_ROLE_ARN:-$JOB_ROLE_ARN}"

    echo "  Job Role:       $JOB_ROLE_ARN"
    echo "  Execution Role: $EXECUTION_ROLE_ARN"
}

# ── ensure CloudWatch log group ───────────────────────────────────────────────
ensure_log_group() {
    if ! aws logs describe-log-groups \
            --log-group-name-prefix "$LOG_GROUP" \
            --region "$AWS_REGION" \
            --query 'logGroups[0].logGroupName' \
            --output text 2>/dev/null | grep -q "$LOG_GROUP"; then
        echo "Creating CloudWatch log group: $LOG_GROUP"
        aws logs create-log-group \
            --log-group-name "$LOG_GROUP" \
            --region "$AWS_REGION" 2>/dev/null || true
        aws logs put-retention-policy \
            --log-group-name "$LOG_GROUP" \
            --retention-in-days 30 \
            --region "$AWS_REGION" 2>/dev/null || true
    else
        echo "Log group exists: $LOG_GROUP"
    fi
}

# ── worker job definition (4 vCPU / 8 GB) ────────────────────────────────────
deploy_worker() {
    echo ""
    echo "Registering scanner-worker job definition..."
    echo "  Resources: 4 vCPU / 8192 MB"
    echo "  Mode:      Array Job (JOB_TYPE=scanner_worker)"

    JOB_DEF=$(cat <<EOF
{
    "jobDefinitionName": "${WORKER_JOB_NAME}",
    "type": "container",
    "containerProperties": {
        "image": "${ECR_IMAGE}",
        "jobRoleArn": "${JOB_ROLE_ARN}",
        "executionRoleArn": "${EXECUTION_ROLE_ARN}",
        "resourceRequirements": [
            {"type": "VCPU",   "value": "4"},
            {"type": "MEMORY", "value": "8192"}
        ],
        "environment": [
            {"name": "JOB_TYPE",       "value": "scanner_worker"},
            {"name": "AWS_REGION",     "value": "${AWS_REGION}"},
            {"name": "S3_BUCKET_NAME", "value": "${S3_BUCKET}"},
            {"name": "RDS_SECRET_ARN", "value": "${RDS_SECRET_ARN}"}
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group":         "${LOG_GROUP}",
                "awslogs-region":        "${AWS_REGION}",
                "awslogs-stream-prefix": "worker"
            }
        },
        "networkConfiguration":       {"assignPublicIp": "ENABLED"},
        "fargatePlatformConfiguration": {"platformVersion": "LATEST"}
    },
    "retryStrategy": {"attempts": 2},
    "timeout":       {"attemptDurationSeconds": 3600},
    "platformCapabilities": ["FARGATE"],
    "tags": {
        "Environment": "${ENVIRONMENT}",
        "Component":   "scanner-worker"
    }
}
EOF
)

    RESULT=$(aws batch register-job-definition \
        --cli-input-json "$JOB_DEF" \
        --region "$AWS_REGION" \
        --output json)

    REVISION=$(echo "$RESULT" | jq -r '.revision')
    echo "  Registered: $WORKER_JOB_NAME (revision $REVISION)"
}

# ── aggregator job definition (2 vCPU / 4 GB) ────────────────────────────────
deploy_aggregator() {
    echo ""
    echo "Registering scanner-aggregator job definition..."
    echo "  Resources: 2 vCPU / 4096 MB"
    echo "  Mode:      Single job (JOB_TYPE=scanner_aggregator)"

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
            {"name": "JOB_TYPE",       "value": "scanner_aggregator"},
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

    RESULT=$(aws batch register-job-definition \
        --cli-input-json "$JOB_DEF" \
        --region "$AWS_REGION" \
        --output json)

    REVISION=$(echo "$RESULT" | jq -r '.revision')
    echo "  Registered: $AGGREGATOR_JOB_NAME (revision $REVISION)"
}

# ── main ──────────────────────────────────────────────────────────────────────
get_iam_roles
ensure_log_group

case "$DEPLOY_TARGET" in
    worker)      deploy_worker ;;
    aggregator)  deploy_aggregator ;;
    all)         deploy_worker; deploy_aggregator ;;
    *)           echo "Unknown target: $DEPLOY_TARGET"; usage; exit 1 ;;
esac

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Deployment complete"
echo "============================================================"
echo ""
echo "Test commands:"
echo ""
echo "  # Test a single worker child (array index 0)"
echo "  aws batch submit-job \\"
echo "    --job-name test-scanner-worker-\$(date +%s) \\"
echo "    --job-queue $JOB_QUEUE \\"
echo "    --job-definition $WORKER_JOB_NAME \\"
echo "    --array-properties size=1 \\"
echo "    --container-overrides 'environment=[{name=SCAN_DATE,value=$(date +%Y-%m-%d)}]' \\"
echo "    --region $AWS_REGION"
echo ""
echo "  # Test the aggregator"
echo "  aws batch submit-job \\"
echo "    --job-name test-scanner-aggregator-\$(date +%s) \\"
echo "    --job-queue $JOB_QUEUE \\"
echo "    --job-definition $AGGREGATOR_JOB_NAME \\"
echo "    --container-overrides 'environment=[{name=SCAN_DATE,value=$(date +%Y-%m-%d)}]' \\"
echo "    --region $AWS_REGION"
echo ""
echo "  # Watch logs"
echo "  aws logs tail $LOG_GROUP --follow --region $AWS_REGION"
echo ""
