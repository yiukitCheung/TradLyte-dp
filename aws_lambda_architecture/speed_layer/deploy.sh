#!/bin/bash
# Master Deployment Script for Speed Layer
# Purpose: Deploy all Speed Layer components in the correct order

set -e

AWS_REGION=${AWS_REGION:-ca-west-1}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$SCRIPT_DIR/infrastructure"

echo "🚀 Speed Layer Deployment"
echo "================================================================"
echo "Region: $AWS_REGION"
echo ""

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to run a deployment step
run_step() {
    local step_name=$1
    local script_path=$2
    local description=$3
    
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}Step: $step_name${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo "$description"
    echo ""
    
    if [ -f "$script_path" ]; then
        bash "$script_path"
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✅ $step_name completed successfully${NC}"
        else
            echo -e "${RED}❌ $step_name failed${NC}"
            exit 1
        fi
    else
        echo -e "${RED}❌ Script not found: $script_path${NC}"
        exit 1
    fi
}

# Check required environment variables
echo "🔍 Checking required environment variables..."
MISSING_VARS=()

if [ -z "$POLYGON_API_KEY" ]; then
    MISSING_VARS+=("POLYGON_API_KEY")
fi

if [ -z "$AURORA_ENDPOINT" ]; then
    MISSING_VARS+=("AURORA_ENDPOINT")
fi

if [ ${#MISSING_VARS[@]} -gt 0 ]; then
    echo -e "${RED}❌ Missing required environment variables:${NC}"
    for var in "${MISSING_VARS[@]}"; do
        echo "   - $var"
    done
    echo ""
    echo "Please set these variables before running deployment:"
    echo "  export POLYGON_API_KEY='your-api-key'"
    echo "  export AURORA_ENDPOINT='your-aurora-endpoint'"
    exit 1
fi

echo -e "${GREEN}✅ All required environment variables are set${NC}"
echo ""

# Deployment steps in order
echo "📋 Deployment Plan:"
echo "  1. DynamoDB Tables (alert configs, ticks, notifications)"
echo "  2. Kinesis Data Stream (raw market data ingestion)"
echo "  3. SNS Topic (alert notifications)"
echo "  4. Signal Generator Lambda (process alerts)"
echo "  5. Kinesis Analytics (Flink) - Multi-timeframe resampling"
echo "  6. ECS Fargate Service (WebSocket data fetcher)"
echo ""

read -p "Continue with deployment? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled."
    exit 0
fi

# Step 1: DynamoDB Tables
run_step \
    "1. DynamoDB Tables" \
    "$INFRA_DIR/deploy_dynamodb_tables.sh" \
    "Creating DynamoDB tables for alert configurations, real-time ticks, and notification history"

# Step 2: Kinesis Data Stream
run_step \
    "2. Kinesis Data Stream" \
    "$INFRA_DIR/deploy_kinesis_stream.sh" \
    "Creating Kinesis Data Stream for real-time market data ingestion"

# Step 3: SNS Topic
run_step \
    "3. SNS Topic" \
    "$INFRA_DIR/deploy_sns_topic.sh" \
    "Creating SNS topic for alert notifications"

# Step 4: Signal Generator Lambda
run_step \
    "4. Signal Generator Lambda" \
    "$INFRA_DIR/deploy_lambda_signal_generator.sh" \
    "Deploying Lambda function to process Kinesis Analytics output and generate alerts"

# Step 5: Kinesis Analytics (Flink)
echo ""
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}Step: 5. Kinesis Analytics (Flink)${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "Deploying Kinesis Analytics Flink applications for multi-timeframe OHLCV resampling"
echo ""
echo -e "${YELLOW}⚠️  Note: Kinesis Analytics deployment requires manual configuration.${NC}"
echo "   Please review and update the Flink SQL files in:"
echo "   $SCRIPT_DIR/kinesis_analytics/flink_apps/"
echo ""
echo "   Then deploy manually via AWS Console or update deploy_kinesis_analytics.sh"
echo "   with proper Flink application configuration."
echo ""

# Step 6: ECS Fargate Service
run_step \
    "6. ECS Fargate Service" \
    "$INFRA_DIR/deploy_ecs_service.sh" \
    "Deploying ECS Fargate service for WebSocket data fetcher"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}🎉 Speed Layer Deployment Complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "📋 Next Steps:"
echo ""
echo "1. Configure Kinesis Analytics (Flink) Applications:"
echo "   - Review Flink SQL files in: $SCRIPT_DIR/kinesis_analytics/flink_apps/"
echo "   - Deploy via AWS Console or update deployment script"
echo ""
echo "2. Configure Lambda Event Source:"
echo "   - Add Kinesis stream as event source for Signal Generator Lambda"
echo "   - aws lambda create-event-source-mapping --function-name dev-signal-generator --event-source-arn <kinesis-stream-arn> --region $AWS_REGION"
echo ""
echo "3. Subscribe to SNS Topic:"
echo "   - aws sns subscribe --topic-arn arn:aws:sns:$AWS_REGION:$(aws sts get-caller-identity --query Account --output text):condvest-speed-layer-alerts --protocol email --notification-endpoint your-email@example.com --region $AWS_REGION"
echo ""
echo "4. Monitor Services:"
echo "   - ECS Service: Check ECS Console for service health"
echo "   - Lambda: Monitor CloudWatch logs"
echo "   - Kinesis: Monitor stream metrics"
echo ""
echo "5. Test the Pipeline:"
echo "   - Verify WebSocket service is receiving data"
echo "   - Check Kinesis stream for incoming records"
echo "   - Test alert generation with sample data"
echo ""
echo "📊 Estimated Monthly Costs:"
echo "   - DynamoDB: ~$15-25"
echo "   - Kinesis Data Stream: ~$50-100"
echo "   - Kinesis Analytics: ~$50-100"
echo "   - ECS Fargate: ~$30-50"
echo "   - Lambda: ~$5-10"
echo "   - SNS: ~$1-5"
echo "   ─────────────────────────────"
echo "   Total: ~$150-290/month"
echo ""
