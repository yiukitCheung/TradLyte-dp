#!/bin/bash
# Deploy SNS Topic for Speed Layer Alerts
# Purpose: Create SNS topic for alert notifications

set -e

AWS_REGION=${AWS_REGION:-ca-west-1}
TOPIC_NAME=${TOPIC_NAME:-condvest-speed-layer-alerts}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "🚀 Creating SNS Topic for Speed Layer Alerts"
echo "================================================================"
echo "Region: $AWS_REGION"
echo "Topic Name: $TOPIC_NAME"
echo ""

# Check if topic already exists
TOPIC_ARN="arn:aws:sns:${AWS_REGION}:${ACCOUNT_ID}:${TOPIC_NAME}"
if aws sns get-topic-attributes --topic-arn "$TOPIC_ARN" --region "$AWS_REGION" &>/dev/null; then
    echo "⚠️  Topic '$TOPIC_NAME' already exists. Skipping creation."
    echo "   ARN: $TOPIC_ARN"
    exit 0
fi

# Create SNS topic
echo "📢 Creating SNS topic: $TOPIC_NAME..."
aws sns create-topic \
    --name "$TOPIC_NAME" \
    --region "$AWS_REGION" \
    > /dev/null

echo "✅ SNS topic created successfully"
echo ""
echo "================================================================"
echo "✅ SNS Topic created successfully!"
echo "================================================================"
echo ""
echo "Topic Details:"
echo "  Name: $TOPIC_NAME"
echo "  ARN: $TOPIC_ARN"
echo ""
echo "💡 Next steps:"
echo "  1. Subscribe to topic (email, SMS, HTTP endpoint, etc.):"
echo "     aws sns subscribe --topic-arn $TOPIC_ARN --protocol email --notification-endpoint your-email@example.com --region $AWS_REGION"
echo "  2. Update Signal Generator Lambda with this topic ARN"
echo "  3. Test alert notifications"
echo ""
echo "📊 Estimated cost:"
echo "  - SNS: $0.50 per 100,000 requests"
echo "  - Estimated monthly: ~$1-5 (depends on alert volume)"
echo ""
