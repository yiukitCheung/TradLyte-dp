#!/bin/bash
# Deploy Kinesis Data Stream for Speed Layer
# Purpose: Create Kinesis stream for real-time market data ingestion

set -e

AWS_REGION=${AWS_REGION:-ca-west-1}
STREAM_NAME=${STREAM_NAME:-dev-market-data-stream}
SHARD_COUNT=${SHARD_COUNT:-2}  # Start with 2 shards for MVP

echo "🚀 Creating Kinesis Data Stream in region: $AWS_REGION"
echo "================================================================"
echo "Stream Name: $STREAM_NAME"
echo "Shard Count: $SHARD_COUNT"
echo ""

# Check if stream already exists
if aws kinesis describe-stream --stream-name "$STREAM_NAME" --region "$AWS_REGION" &>/dev/null; then
    echo "⚠️  Stream '$STREAM_NAME' already exists. Skipping creation."
    echo "   To recreate, delete it first: aws kinesis delete-stream --stream-name $STREAM_NAME --region $AWS_REGION"
    exit 0
fi

# Create Kinesis stream
echo "📊 Creating Kinesis stream: $STREAM_NAME..."
aws kinesis create-stream \
    --stream-name "$STREAM_NAME" \
    --shard-count "$SHARD_COUNT" \
    --region "$AWS_REGION"

echo "⏳ Waiting for stream to become active..."
aws kinesis wait stream-exists --stream-name "$STREAM_NAME" --region "$AWS_REGION"

# Get stream details
STREAM_ARN=$(aws kinesis describe-stream \
    --stream-name "$STREAM_NAME" \
    --region "$AWS_REGION" \
    --query 'StreamDescription.StreamARN' \
    --output text)

echo ""
echo "================================================================"
echo "✅ Kinesis Data Stream created successfully!"
echo "================================================================"
echo ""
echo "Stream Details:"
echo "  Name: $STREAM_NAME"
echo "  ARN: $STREAM_ARN"
echo "  Shards: $SHARD_COUNT"
echo "  Region: $AWS_REGION"
echo ""
echo "💡 Next steps:"
echo "  1. Deploy Kinesis Analytics (Flink) applications"
echo "  2. Deploy ECS WebSocket service to publish to this stream"
echo "  3. Configure Lambda function to consume from this stream"
echo ""
echo "📊 Estimated cost:"
echo "  - Data ingestion: $0.014 per GB"
echo "  - Shard hours: $0.015 per shard-hour"
echo "  - Estimated monthly: ~$50-100 (depends on throughput)"
echo ""
