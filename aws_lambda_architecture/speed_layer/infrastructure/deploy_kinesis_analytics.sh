#!/bin/bash
# Deploy Kinesis Analytics (Flink) Applications for Speed Layer
# Purpose: Deploy Flink SQL applications for multi-timeframe OHLCV resampling

set -e

AWS_REGION=${AWS_REGION:-ca-west-1}
INPUT_STREAM_NAME=${INPUT_STREAM_NAME:-dev-market-data-stream}
APPLICATION_NAME_PREFIX=${APPLICATION_NAME_PREFIX:-dev-market-data}
SERVICE_EXECUTION_ROLE=${SERVICE_EXECUTION_ROLE:-dev-kinesis-analytics-role}

echo "🚀 Deploying Kinesis Analytics (Flink) Applications"
echo "================================================================"
echo "Region: $AWS_REGION"
echo "Input Stream: $INPUT_STREAM_NAME"
echo "Application Prefix: $APPLICATION_NAME_PREFIX"
echo ""

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLINK_APPS_DIR="$SCRIPT_DIR/../kinesis_analytics/flink_apps"
SCHEMAS_DIR="$SCRIPT_DIR/../kinesis_analytics/schemas"

# Check if Flink apps directory exists
if [ ! -d "$FLINK_APPS_DIR" ]; then
    echo "❌ Error: Flink apps directory not found: $FLINK_APPS_DIR"
    exit 1
fi

# Get input stream ARN
INPUT_STREAM_ARN=$(aws kinesis describe-stream \
    --stream-name "$INPUT_STREAM_NAME" \
    --region "$AWS_REGION" \
    --query 'StreamDescription.StreamARN' \
    --output text 2>/dev/null || echo "")

if [ -z "$INPUT_STREAM_ARN" ]; then
    echo "❌ Error: Input stream '$INPUT_STREAM_NAME' not found in region $AWS_REGION"
    echo "   Please create the stream first using deploy_kinesis_stream.sh"
    exit 1
fi

echo "✅ Found input stream: $INPUT_STREAM_ARN"
echo ""

# Function to deploy a Flink application
deploy_flink_app() {
    local app_name=$1
    local sql_file=$2
    local output_stream_name=$3
    
    echo "📦 Deploying Flink application: $app_name"
    echo "   SQL File: $sql_file"
    echo "   Output Stream: $output_stream_name"
    
    # Check if application already exists
    if aws kinesisanalyticsv2 describe-application \
        --application-name "$app_name" \
        --region "$AWS_REGION" &>/dev/null; then
        echo "   ⚠️  Application '$app_name' already exists. Skipping creation."
        echo "   To update, delete it first: aws kinesisanalyticsv2 delete-application --application-name $app_name --region $AWS_REGION"
        return 0
    fi
    
    # Read SQL file
    if [ ! -f "$sql_file" ]; then
        echo "   ❌ Error: SQL file not found: $sql_file"
        return 1
    fi
    
    SQL_CODE=$(cat "$sql_file")
    
    # Create output stream if it doesn't exist
    if ! aws kinesis describe-stream --stream-name "$output_stream_name" --region "$AWS_REGION" &>/dev/null; then
        echo "   📊 Creating output stream: $output_stream_name..."
        aws kinesis create-stream \
            --stream-name "$output_stream_name" \
            --shard-count 2 \
            --region "$AWS_REGION"
        aws kinesis wait stream-exists --stream-name "$output_stream_name" --region "$AWS_REGION"
    fi
    
    OUTPUT_STREAM_ARN=$(aws kinesis describe-stream \
        --stream-name "$output_stream_name" \
        --region "$AWS_REGION" \
        --query 'StreamDescription.StreamARN' \
        --output text)
    
    # Create Flink application
    echo "   🔨 Creating Flink application..."
    aws kinesisanalyticsv2 create-application \
        --application-name "$app_name" \
        --runtime-environment FLINK-1_18 \
        --service-execution-role "arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/$SERVICE_EXECUTION_ROLE" \
        --application-configuration "{
            \"ApplicationCodeConfiguration\": {
                \"CodeContent\": {
                    \"TextContent\": \"$SQL_CODE\"
                },
                \"CodeContentType\": \"PLAINTEXT\"
            },
            \"EnvironmentProperties\": {
                \"PropertyGroups\": [
                    {
                        \"PropertyGroupId\": \"FlinkApplicationProperties\",
                        \"PropertyMap\": {
                            \"input.stream.name\": \"$INPUT_STREAM_NAME\",
                            \"output.stream.name\": \"$output_stream_name\"
                        }
                    }
                ]
            }
        }" \
        --region "$AWS_REGION"
    
    echo "   ✅ Application '$app_name' created successfully"
    echo ""
}

# Deploy 1-minute resampler (processes raw input)
echo "📋 Step 1: Deploying 1-minute resampler..."
deploy_flink_app \
    "${APPLICATION_NAME_PREFIX}-1min" \
    "$FLINK_APPS_DIR/1min_resampler.sql" \
    "${APPLICATION_NAME_PREFIX}-1min-output"

# Deploy 5-minute resampler
echo "📋 Step 2: Deploying 5-minute resampler..."
deploy_flink_app \
    "${APPLICATION_NAME_PREFIX}-5min" \
    "$FLINK_APPS_DIR/5min_resampler.sql" \
    "${APPLICATION_NAME_PREFIX}-5min-output"

# Deploy 15-minute resampler
echo "📋 Step 3: Deploying 15-minute resampler..."
deploy_flink_app \
    "${APPLICATION_NAME_PREFIX}-15min" \
    "$FLINK_APPS_DIR/15min_resampler.sql" \
    "${APPLICATION_NAME_PREFIX}-15min-output"

# Deploy 1-hour resampler
echo "📋 Step 4: Deploying 1-hour resampler..."
deploy_flink_app \
    "${APPLICATION_NAME_PREFIX}-1hour" \
    "$FLINK_APPS_DIR/1hour_resampler.sql" \
    "${APPLICATION_NAME_PREFIX}-1hour-output"

# Deploy 4-hour resampler
echo "📋 Step 5: Deploying 4-hour resampler..."
deploy_flink_app \
    "${APPLICATION_NAME_PREFIX}-4hour" \
    "$FLINK_APPS_DIR/4hour_resampler.sql" \
    "${APPLICATION_NAME_PREFIX}-4hour-output"

# Deploy Vegas Channel signals
echo "📋 Step 6: Deploying Vegas Channel signals..."
deploy_flink_app \
    "${APPLICATION_NAME_PREFIX}-vegas-signals" \
    "$FLINK_APPS_DIR/vegas_channel_signals.sql" \
    "${APPLICATION_NAME_PREFIX}-signals-output"

echo ""
echo "================================================================"
echo "✅ All Kinesis Analytics applications deployed!"
echo "================================================================"
echo ""
echo "💡 Next steps:"
echo "  1. Start each application: aws kinesisanalyticsv2 start-application --application-name <app-name> --region $AWS_REGION"
echo "  2. Monitor application status in AWS Console"
echo "  3. Configure Lambda function to consume from output streams"
echo ""
echo "📊 Estimated cost:"
echo "  - Kinesis Analytics: $0.11 per Kinesis Processing Unit (KPU) per hour"
echo "  - Estimated monthly: ~$50-100 (depends on throughput)"
echo ""
