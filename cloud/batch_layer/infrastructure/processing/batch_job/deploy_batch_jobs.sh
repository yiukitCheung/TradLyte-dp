#!/bin/bash
# DEPRECATED: Consolidator and resampler moved to batch_layer/archive_scripts/
# Pipeline no longer runs these jobs. Use archive_scripts/deploy_batch_jobs.sh if you need to deploy them.
# This file is kept so existing references do not break; consider removing after reconfiguring CI/CD.

set -e

# Configuration
AWS_REGION="${AWS_REGION:-ca-west-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
ECR_REPOSITORY="${ECR_REPOSITORY:-dev-batch-processor}"
JOB_QUEUE="${JOB_QUEUE:-dev-batch-duckdb-resampler}"
LOG_GROUP="${LOG_GROUP:-/aws/batch/dev-batch-duckdb-resampler}"

# Job definition names (standardized)
CONSOLIDATOR_JOB_NAME="${ENVIRONMENT}-batch-bronze-consolidator"
RESAMPLER_JOB_NAME="${ENVIRONMENT}-batch-duckdb-resampler"

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --job TYPE       Job to deploy: consolidator, resampler, or all (default: all)"
    echo "  --region REGION  AWS region (default: ca-west-1)"
    echo "  -h, --help       Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                      # Deploy all jobs"
    echo "  $0 --job consolidator   # Deploy only consolidator"
    echo "  $0 --job resampler      # Deploy only resampler"
}

# Parse arguments
JOB_TYPE="all"
while [[ $# -gt 0 ]]; do
    case $1 in
        --job)
            JOB_TYPE="$2"
            shift 2
            ;;
        --region)
            AWS_REGION="$2"
            shift 2
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
echo "🚀 Deploying AWS Batch Job Definitions"
echo "============================================================"
echo "Region: $AWS_REGION"
echo "Account: $AWS_ACCOUNT_ID"
echo "ECR Repository: $ECR_REPOSITORY"
echo "Job Queue: $JOB_QUEUE"
echo "Deploy: $JOB_TYPE"
echo ""

# Get existing job role ARN from resampler job
get_job_roles() {
    echo "🔍 Getting IAM roles from existing jobs..."
    EXISTING_JOB=$(aws batch describe-job-definitions \
    --job-definition-name "$RESAMPLER_JOB_NAME" \
    --status ACTIVE \
    --region "$AWS_REGION" \
    --query 'jobDefinitions[0]' \
    --output json 2>/dev/null || echo "{}")

    if [ "$EXISTING_JOB" == "{}" ] || [ "$EXISTING_JOB" == "null" ]; then
        JOB_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ENVIRONMENT}-condvest-batch-processing-execution-role"
        EXECUTION_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ENVIRONMENT}-condvest-batch-processing-execution-role"
else
        JOB_ROLE_ARN=$(echo "$EXISTING_JOB" | jq -r '.containerProperties.jobRoleArn // empty')
        EXECUTION_ROLE_ARN=$(echo "$EXISTING_JOB" | jq -r '.containerProperties.executionRoleArn // empty')
        JOB_ROLE_ARN="${JOB_ROLE_ARN:-arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ENVIRONMENT}-condvest-batch-processing-execution-role}"
        EXECUTION_ROLE_ARN="${EXECUTION_ROLE_ARN:-$JOB_ROLE_ARN}"
    fi
    echo "   Job Role: $JOB_ROLE_ARN"
    echo "   Execution Role: $EXECUTION_ROLE_ARN"
}

# Deploy Consolidator Job
deploy_consolidator() {
echo ""
    echo "📦 Deploying Consolidator Job Definition..."
    
JOB_DEF_JSON=$(cat <<EOF
{
    "jobDefinitionName": "${CONSOLIDATOR_JOB_NAME}",
    "type": "container",
    "containerProperties": {
        "image": "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}:latest",
        "command": ["python", "consolidator.py"],
        "jobRoleArn": "${JOB_ROLE_ARN}",
        "executionRoleArn": "${EXECUTION_ROLE_ARN}",
        "resourceRequirements": [
            {"type": "VCPU", "value": "2"},
            {"type": "MEMORY", "value": "4096"}
        ],
        "environment": [
            {"name": "AWS_REGION", "value": "${AWS_REGION}"},
            {"name": "S3_BUCKET", "value": "dev-condvest-datalake"},
            {"name": "S3_PREFIX", "value": "bronze/raw_ohlcv"},
            {"name": "MODE", "value": "incremental"},
            {"name": "MAX_WORKERS", "value": "10"},
            {"name": "RETENTION_DAYS", "value": "30"},
            {"name": "SKIP_CLEANUP", "value": "false"}
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": "${LOG_GROUP}",
                "awslogs-region": "${AWS_REGION}",
                "awslogs-stream-prefix": "consolidator"
            }
        },
        "networkConfiguration": {"assignPublicIp": "ENABLED"},
        "fargatePlatformConfiguration": {"platformVersion": "LATEST"}
    },
    "retryStrategy": {"attempts": 2},
    "timeout": {"attemptDurationSeconds": 1800},
    "platformCapabilities": ["FARGATE"],
    "tags": {
        "Environment": "${ENVIRONMENT}",
        "Component": "consolidator"
    }
}
EOF
)
    
    RESULT=$(aws batch register-job-definition \
        --cli-input-json "$JOB_DEF_JSON" \
        --region "$AWS_REGION" \
        --output json)
    
    REVISION=$(echo "$RESULT" | jq -r '.revision')
    echo "   ✅ Consolidator: $CONSOLIDATOR_JOB_NAME (revision $REVISION)"
}

# Deploy Resampler Job (update)
deploy_resampler() {
    echo ""
    echo "📦 Deploying Resampler Job Definition..."
    
    JOB_DEF_JSON=$(cat <<EOF
{
    "jobDefinitionName": "${RESAMPLER_JOB_NAME}",
    "type": "container",
    "containerProperties": {
        "image": "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}:latest",
        "command": ["python", "resampler.py"],
        "jobRoleArn": "${JOB_ROLE_ARN}",
        "executionRoleArn": "${EXECUTION_ROLE_ARN}",
        "resourceRequirements": [
            {"type": "VCPU", "value": "2"},
            {"type": "MEMORY", "value": "4096"}
        ],
        "environment": [
            {"name": "AWS_REGION", "value": "${AWS_REGION}"},
            {"name": "S3_BUCKET_NAME", "value": "dev-condvest-datalake"},
            {"name": "RESAMPLING_INTERVALS", "value": "3,5,8,13,21,34"}
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": "${LOG_GROUP}",
                "awslogs-region": "${AWS_REGION}",
                "awslogs-stream-prefix": "resampler"
            }
        },
        "networkConfiguration": {"assignPublicIp": "ENABLED"},
        "fargatePlatformConfiguration": {"platformVersion": "LATEST"}
    },
    "retryStrategy": {"attempts": 3},
    "timeout": {"attemptDurationSeconds": 3600},
    "platformCapabilities": ["FARGATE"],
    "tags": {
        "Environment": "${ENVIRONMENT}",
        "Component": "resampler"
    }
}
EOF
)

RESULT=$(aws batch register-job-definition \
    --cli-input-json "$JOB_DEF_JSON" \
    --region "$AWS_REGION" \
    --output json)

REVISION=$(echo "$RESULT" | jq -r '.revision')
    echo "   ✅ Resampler: $RESAMPLER_JOB_NAME (revision $REVISION)"
}

# Main execution
get_job_roles

case $JOB_TYPE in
    consolidator)
        deploy_consolidator
        ;;
    resampler)
        deploy_resampler
        ;;
    all)
        deploy_consolidator
        deploy_resampler
        ;;
    *)
        echo "Unknown job type: $JOB_TYPE"
        usage
        exit 1
        ;;
esac

echo ""
echo "============================================================"
echo "✅ Deployment Complete!"
echo "============================================================"
echo ""
echo "📝 Manual test commands:"
echo ""
echo "  # Test Consolidator:"
echo "  aws batch submit-job \\"
echo "    --job-name test-consolidator-\$(date +%Y%m%d%H%M%S) \\"
echo "    --job-queue $JOB_QUEUE \\"
echo "    --job-definition $CONSOLIDATOR_JOB_NAME \\"
echo "    --region $AWS_REGION"
echo ""
echo "  # Test Resampler:"
echo "  aws batch submit-job \\"
echo "    --job-name test-resampler-\$(date +%Y%m%d%H%M%S) \\"
echo "    --job-queue $JOB_QUEUE \\"
echo "    --job-definition $RESAMPLER_JOB_NAME \\"
echo "    --region $AWS_REGION"
echo ""
