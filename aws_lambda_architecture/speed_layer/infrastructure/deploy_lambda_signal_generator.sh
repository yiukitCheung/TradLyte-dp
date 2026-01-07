#!/bin/bash
# Deploy Signal Generator Lambda Function for Speed Layer
# Purpose: Deploy Lambda function that processes Kinesis Analytics output and generates alerts

set -e

AWS_REGION=${AWS_REGION:-ca-west-1}
FUNCTION_NAME=${FUNCTION_NAME:-dev-signal-generator}
LAMBDA_ROLE=${LAMBDA_ROLE:-dev-lambda-signal-generator-role}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "🚀 Deploying Signal Generator Lambda Function"
echo "================================================================"
echo "Region: $AWS_REGION"
echo "Function Name: $FUNCTION_NAME"
echo ""

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAMBDA_DIR="$SCRIPT_DIR/../lambda_functions"

# Check if Lambda function code exists
if [ ! -f "$LAMBDA_DIR/signal_generator.py" ]; then
    echo "❌ Error: Lambda function code not found: $LAMBDA_DIR/signal_generator.py"
    exit 1
fi

# Step 1: Create IAM role for Lambda if it doesn't exist
echo "🔐 Step 1: Setting up IAM role..."
if ! aws iam get-role --role-name "$LAMBDA_ROLE" --region "$AWS_REGION" &>/dev/null; then
    echo "   Creating Lambda execution role: $LAMBDA_ROLE..."
    
    # Create role
    aws iam create-role \
        --role-name "$LAMBDA_ROLE" \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }' \
        --region "$AWS_REGION"
    
    # Attach basic Lambda execution policy
    aws iam attach-role-policy \
        --role-name "$LAMBDA_ROLE" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole \
        --region "$AWS_REGION"
    
    # Create inline policy for Kinesis, DynamoDB, and SNS access
    aws iam put-role-policy \
        --role-name "$LAMBDA_ROLE" \
        --policy-name "SpeedLayerAccess" \
        --policy-document "{
            \"Version\": \"2012-10-17\",
            \"Statement\": [
                {
                    \"Effect\": \"Allow\",
                    \"Action\": [
                        \"kinesis:DescribeStream\",
                        \"kinesis:GetShardIterator\",
                        \"kinesis:GetRecords\",
                        \"kinesis:ListShards\"
                    ],
                    \"Resource\": \"arn:aws:kinesis:${AWS_REGION}:${ACCOUNT_ID}:stream/*\"
                },
                {
                    \"Effect\": \"Allow\",
                    \"Action\": [
                        \"dynamodb:Query\",
                        \"dynamodb:GetItem\",
                        \"dynamodb:PutItem\",
                        \"dynamodb:UpdateItem\"
                    ],
                    \"Resource\": [
                        \"arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/alert_configurations\",
                        \"arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/alert_configurations/index/*\",
                        \"arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/realtime_ticks\",
                        \"arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/alert_notifications_history\"
                    ]
                },
                {
                    \"Effect\": \"Allow\",
                    \"Action\": [
                        \"sns:Publish\"
                    ],
                    \"Resource\": \"arn:aws:sns:${AWS_REGION}:${ACCOUNT_ID}:*\"
                }
            ]
        }" \
        --region "$AWS_REGION"
    
    echo "   ✅ Lambda role created"
else
    echo "   ✅ Lambda role already exists"
fi

LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${LAMBDA_ROLE}"

# Step 2: Create deployment package
echo ""
echo "📦 Step 2: Creating deployment package..."
cd "$LAMBDA_DIR"

# Create temporary directory for package
PACKAGE_DIR=$(mktemp -d)
trap "rm -rf $PACKAGE_DIR" EXIT

# Copy Lambda function code
cp signal_generator.py "$PACKAGE_DIR/"

# Install dependencies
if [ -f requirements.txt ]; then
    echo "   Installing dependencies..."
    pip install -r requirements.txt -t "$PACKAGE_DIR" --quiet
fi

# Create ZIP package
cd "$PACKAGE_DIR"
ZIP_FILE="/tmp/${FUNCTION_NAME}.zip"
zip -r "$ZIP_FILE" . -q
PACKAGE_SIZE=$(du -h "$ZIP_FILE" | cut -f1)
echo "   ✅ Deployment package created: $ZIP_FILE ($PACKAGE_SIZE)"

# Step 3: Create or update Lambda function
echo ""
echo "⚡ Step 3: Creating/updating Lambda function..."

# Get SNS topic ARN (if exists)
SNS_TOPIC_ARN=""
if aws sns get-topic-attributes \
    --topic-arn "arn:aws:sns:${AWS_REGION}:${ACCOUNT_ID}:condvest-speed-layer-alerts" \
    --region "$AWS_REGION" &>/dev/null; then
    SNS_TOPIC_ARN="arn:aws:sns:${AWS_REGION}:${ACCOUNT_ID}:condvest-speed-layer-alerts"
fi

if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$AWS_REGION" &>/dev/null; then
    echo "   Updating existing function: $FUNCTION_NAME..."
    aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --zip-file "fileb://$ZIP_FILE" \
        --region "$AWS_REGION" \
        > /dev/null
    
    # Wait for update to complete
    echo "   ⏳ Waiting for function update to complete..."
    aws lambda wait function-updated \
        --function-name "$FUNCTION_NAME" \
        --region "$AWS_REGION"
    
    # Update configuration
    UPDATE_ENV="ALERTS_CONFIG_TABLE=alert_configurations"
    if [ -n "$SNS_TOPIC_ARN" ]; then
        UPDATE_ENV="$UPDATE_ENV,ALERTS_SNS_TOPIC_ARN=$SNS_TOPIC_ARN"
    fi
    
    aws lambda update-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --environment "Variables={$UPDATE_ENV}" \
        --timeout 60 \
        --memory-size 256 \
        --region "$AWS_REGION" \
        > /dev/null
    
    echo "   ✅ Function updated"
else
    echo "   Creating new function: $FUNCTION_NAME..."
    
    ENV_VARS="ALERTS_CONFIG_TABLE=alert_configurations"
    if [ -n "$SNS_TOPIC_ARN" ]; then
        ENV_VARS="$ENV_VARS,ALERTS_SNS_TOPIC_ARN=$SNS_TOPIC_ARN"
    fi
    
    aws lambda create-function \
        --function-name "$FUNCTION_NAME" \
        --runtime python3.10 \
        --role "$LAMBDA_ROLE_ARN" \
        --handler signal_generator.lambda_handler \
        --zip-file "fileb://$ZIP_FILE" \
        --timeout 60 \
        --memory-size 256 \
        --environment "Variables={$ENV_VARS}" \
        --region "$AWS_REGION" \
        > /dev/null
    
    echo "   ✅ Function created"
fi

echo ""
echo "================================================================"
echo "✅ Signal Generator Lambda Function deployed successfully!"
echo "================================================================"
echo ""
echo "Function Details:"
echo "  Name: $FUNCTION_NAME"
echo "  Runtime: Python 3.10"
echo "  Memory: 256 MB"
echo "  Timeout: 60 seconds"
echo ""
echo "💡 Next steps:"
echo "  1. Configure Kinesis stream as event source for this Lambda"
echo "  2. Test function with sample Kinesis event"
echo "  3. Monitor CloudWatch logs for function execution"
echo ""
echo "📊 Estimated cost:"
echo "  - Lambda: $0.20 per 1M requests + $0.0000166667 per GB-second"
echo "  - Estimated monthly: ~$5-10 (depends on Kinesis throughput)"
echo ""
