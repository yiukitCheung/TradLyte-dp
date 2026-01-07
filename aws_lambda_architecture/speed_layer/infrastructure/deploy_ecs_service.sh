#!/bin/bash
# Deploy ECS Fargate Service for Speed Layer WebSocket
# Purpose: Deploy containerized WebSocket service to ECS Fargate

set -e

AWS_REGION=${AWS_REGION:-ca-west-1}
CLUSTER_NAME=${CLUSTER_NAME:-dev-speed-layer-cluster}
SERVICE_NAME=${SERVICE_NAME:-dev-websocket-service}
TASK_FAMILY=${TASK_FAMILY:-dev-websocket-task}
IMAGE_NAME=${IMAGE_NAME:-speed-layer-websocket}
ECR_REPO=${ECR_REPO:-speed-layer-websocket}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "🚀 Deploying ECS Fargate Service for Speed Layer"
echo "================================================================"
echo "Region: $AWS_REGION"
echo "Cluster: $CLUSTER_NAME"
echo "Service: $SERVICE_NAME"
echo "ECR Repository: $ECR_REPO"
echo ""

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR/../../.."

# Check required environment variables
if [ -z "$POLYGON_API_KEY" ]; then
    echo "❌ Error: POLYGON_API_KEY environment variable not set"
    exit 1
fi

if [ -z "$KINESIS_STREAM_NAME" ]; then
    KINESIS_STREAM_NAME="dev-market-data-stream"
    echo "⚠️  KINESIS_STREAM_NAME not set, using default: $KINESIS_STREAM_NAME"
fi

if [ -z "$AURORA_ENDPOINT" ]; then
    echo "❌ Error: AURORA_ENDPOINT environment variable not set"
    exit 1
fi

# Step 1: Create ECR repository if it doesn't exist
echo "📦 Step 1: Setting up ECR repository..."
if ! aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" &>/dev/null; then
    echo "   Creating ECR repository: $ECR_REPO..."
    aws ecr create-repository \
        --repository-name "$ECR_REPO" \
        --region "$AWS_REGION" \
        --image-scanning-configuration scanOnPush=true
    echo "   ✅ ECR repository created"
else
    echo "   ✅ ECR repository already exists"
fi

ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:latest"

# Step 2: Build and push Docker image
echo ""
echo "🐳 Step 2: Building and pushing Docker image..."

# Build Docker image from project root (Dockerfile expects workspace root as context)
echo "   Building Docker image from project root: $PROJECT_ROOT..."
cd "$PROJECT_ROOT"
docker build -t "$IMAGE_NAME:latest" \
    -f "$SCRIPT_DIR/../data_fetcher/Dockerfile" \
    --build-arg BUILDKIT_INLINE_CACHE=1 \
    .

# Tag for ECR
docker tag "$IMAGE_NAME:latest" "$ECR_URI"

# Login to ECR
echo "   Logging in to ECR..."
aws ecr get-login-password --region "$AWS_REGION" | \
    docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Push to ECR
echo "   Pushing image to ECR..."
docker push "$ECR_URI"
echo "   ✅ Image pushed to ECR: $ECR_URI"

# Step 3: Create ECS cluster if it doesn't exist
echo ""
echo "🏗️  Step 3: Setting up ECS cluster..."
if ! aws ecs describe-clusters --clusters "$CLUSTER_NAME" --region "$AWS_REGION" --query 'clusters[0].status' --output text 2>/dev/null | grep -q "ACTIVE"; then
    echo "   Creating ECS cluster: $CLUSTER_NAME..."
    aws ecs create-cluster \
        --cluster-name "$CLUSTER_NAME" \
        --capacity-providers FARGATE FARGATE_SPOT \
        --default-capacity-provider-strategy \
            capacityProvider=FARGATE,weight=1 \
        --region "$AWS_REGION"
    echo "   ✅ ECS cluster created"
else
    echo "   ✅ ECS cluster already exists"
fi

# Step 4: Create task execution role if it doesn't exist
echo ""
echo "🔐 Step 4: Setting up IAM roles..."
TASK_EXECUTION_ROLE="dev-ecs-task-execution-role"
TASK_ROLE="dev-ecs-websocket-task-role"

# Create task execution role (for ECS to pull images, write logs)
if ! aws iam get-role --role-name "$TASK_EXECUTION_ROLE" --region "$AWS_REGION" &>/dev/null; then
    echo "   Creating task execution role: $TASK_EXECUTION_ROLE..."
    aws iam create-role \
        --role-name "$TASK_EXECUTION_ROLE" \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }' \
        --region "$AWS_REGION"
    
    # Attach managed policy
    aws iam attach-role-policy \
        --role-name "$TASK_EXECUTION_ROLE" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy \
        --region "$AWS_REGION"
    
    echo "   ✅ Task execution role created"
else
    echo "   ✅ Task execution role already exists"
fi

TASK_EXECUTION_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${TASK_EXECUTION_ROLE}"

# Create task role (for application to access AWS services)
if ! aws iam get-role --role-name "$TASK_ROLE" --region "$AWS_REGION" &>/dev/null; then
    echo "   Creating task role: $TASK_ROLE..."
    aws iam create-role \
        --role-name "$TASK_ROLE" \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }' \
        --region "$AWS_REGION"
    
    # Create inline policy for Kinesis and Aurora access
    aws iam put-role-policy \
        --role-name "$TASK_ROLE" \
        --policy-name "KinesisAndAuroraAccess" \
        --policy-document "{
            \"Version\": \"2012-10-17\",
            \"Statement\": [
                {
                    \"Effect\": \"Allow\",
                    \"Action\": [
                        \"kinesis:PutRecord\",
                        \"kinesis:PutRecords\"
                    ],
                    \"Resource\": \"arn:aws:kinesis:${AWS_REGION}:${ACCOUNT_ID}:stream/${KINESIS_STREAM_NAME}*\"
                },
                {
                    \"Effect\": \"Allow\",
                    \"Action\": [
                        \"rds:DescribeDBInstances\",
                        \"rds:Connect\"
                    ],
                    \"Resource\": \"*\"
                }
            ]
        }" \
        --region "$AWS_REGION"
    
    echo "   ✅ Task role created"
else
    echo "   ✅ Task role already exists"
fi

TASK_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${TASK_ROLE}"

# Step 5: Register task definition
echo ""
echo "📋 Step 5: Registering ECS task definition..."
cat > /tmp/task-definition.json <<EOF
{
    "family": "$TASK_FAMILY",
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "cpu": "256",
    "memory": "512",
    "executionRoleArn": "$TASK_EXECUTION_ROLE_ARN",
    "taskRoleArn": "$TASK_ROLE_ARN",
    "containerDefinitions": [
        {
            "name": "websocket-service",
            "image": "$ECR_URI",
            "essential": true,
            "portMappings": [
                {
                    "containerPort": 8080,
                    "protocol": "tcp"
                }
            ],
            "environment": [
                {
                    "name": "POLYGON_API_KEY",
                    "value": "$POLYGON_API_KEY"
                },
                {
                    "name": "KINESIS_STREAM_NAME",
                    "value": "$KINESIS_STREAM_NAME"
                },
                {
                    "name": "AURORA_ENDPOINT",
                    "value": "$AURORA_ENDPOINT"
                },
                {
                    "name": "AWS_REGION",
                    "value": "$AWS_REGION"
                }
            ],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": "/ecs/$TASK_FAMILY",
                    "awslogs-region": "$AWS_REGION",
                    "awslogs-stream-prefix": "ecs"
                }
            },
            "healthCheck": {
                "command": ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"],
                "interval": 30,
                "timeout": 5,
                "retries": 3,
                "startPeriod": 60
            }
        }
    ]
}
EOF

# Create CloudWatch log group if it doesn't exist
LOG_GROUP="/ecs/$TASK_FAMILY"
if ! aws logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" --region "$AWS_REGION" --query "logGroups[?logGroupName=='$LOG_GROUP'].logGroupName" --output text | grep -q "$LOG_GROUP"; then
    echo "   Creating CloudWatch log group: $LOG_GROUP..."
    aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$AWS_REGION"
fi

# Register task definition
aws ecs register-task-definition \
    --cli-input-json file:///tmp/task-definition.json \
    --region "$AWS_REGION" \
    > /dev/null

echo "   ✅ Task definition registered"

# Step 6: Create or update ECS service
echo ""
echo "🚢 Step 6: Creating/updating ECS service..."

# Get default VPC and subnets
VPC_ID=$(aws ec2 describe-vpcs \
    --filters "Name=isDefault,Values=true" \
    --region "$AWS_REGION" \
    --query 'Vpcs[0].VpcId' \
    --output text)

SUBNET_IDS=$(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$VPC_ID" \
    --region "$AWS_REGION" \
    --query 'Subnets[*].SubnetId' \
    --output text | tr '\t' ',')

SECURITY_GROUP_ID=$(aws ec2 describe-security-groups \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=default" \
    --region "$AWS_REGION" \
    --query 'SecurityGroups[0].GroupId' \
    --output text)

if aws ecs describe-services \
    --cluster "$CLUSTER_NAME" \
    --services "$SERVICE_NAME" \
    --region "$AWS_REGION" \
    --query 'services[0].status' \
    --output text 2>/dev/null | grep -q "ACTIVE"; then
    echo "   Updating existing service: $SERVICE_NAME..."
    aws ecs update-service \
        --cluster "$CLUSTER_NAME" \
        --service "$SERVICE_NAME" \
        --task-definition "$TASK_FAMILY" \
        --force-new-deployment \
        --region "$AWS_REGION" \
        > /dev/null
    echo "   ✅ Service updated"
else
    echo "   Creating new service: $SERVICE_NAME..."
    aws ecs create-service \
        --cluster "$CLUSTER_NAME" \
        --service-name "$SERVICE_NAME" \
        --task-definition "$TASK_FAMILY" \
        --desired-count 1 \
        --launch-type FARGATE \
        --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_IDS],securityGroups=[$SECURITY_GROUP_ID],assignPublicIp=ENABLED}" \
        --region "$AWS_REGION" \
        > /dev/null
    echo "   ✅ Service created"
fi

echo ""
echo "================================================================"
echo "✅ ECS Fargate Service deployed successfully!"
echo "================================================================"
echo ""
echo "Service Details:"
echo "  Cluster: $CLUSTER_NAME"
echo "  Service: $SERVICE_NAME"
echo "  Task Family: $TASK_FAMILY"
echo "  Image: $ECR_URI"
echo ""
echo "💡 Next steps:"
echo "  1. Monitor service in ECS Console"
echo "  2. Check CloudWatch logs: /ecs/$TASK_FAMILY"
echo "  3. Verify health check endpoint is responding"
echo ""
echo "📊 Estimated cost:"
echo "  - Fargate: ~$0.04 per vCPU-hour + ~$0.004 per GB-hour"
echo "  - Estimated monthly: ~$30-50 (24/7 operation)"
echo ""
