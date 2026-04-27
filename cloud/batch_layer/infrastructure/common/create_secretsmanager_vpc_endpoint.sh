#!/usr/bin/env bash
# Create an interface VPC endpoint for AWS Secrets Manager so Lambdas in
# private subnets can call the service without NAT.
#
# Usage:
#   ./create_secretsmanager_vpc_endpoint.sh <lambda_function_name> [region]
#
# What it does (idempotent):
#   1) Reads the Lambda's VpcConfig (VPC, subnets, security groups).
#   2) Ensures a dedicated SG "secretsmanager-endpoint-sg" exists and
#      allows TCP 443 from the Lambda's first security group.
#   3) Creates an interface endpoint com.amazonaws.<region>.secretsmanager
#      in those subnets with Private DNS enabled (if not already present).
#
# Requirements: AWS CLI v2, IAM perms for lambda/ec2 (describe + create).

set -euo pipefail

LAMBDA_NAME="${1:-}"
AWS_REGION="${2:-${AWS_REGION:-ca-west-1}}"

if [[ -z "$LAMBDA_NAME" ]]; then
  echo "Usage: $0 <lambda_function_name> [region]" >&2
  exit 2
fi

echo "🔍 Reading VpcConfig for Lambda: $LAMBDA_NAME ($AWS_REGION)"
VPC_JSON=$(aws lambda get-function-configuration \
  --function-name "$LAMBDA_NAME" \
  --region "$AWS_REGION" \
  --query 'VpcConfig' --output json)

SUBNETS=$(echo "$VPC_JSON" | python3 -c "import sys,json;print(' '.join(json.load(sys.stdin).get('SubnetIds',[])))")
LAMBDA_SGS=$(echo "$VPC_JSON" | python3 -c "import sys,json;print(' '.join(json.load(sys.stdin).get('SecurityGroupIds',[])))")
VPC_ID=$(echo "$VPC_JSON" | python3 -c "import sys,json;print(json.load(sys.stdin).get('VpcId',''))")

if [[ -z "$VPC_ID" || -z "$SUBNETS" || -z "$LAMBDA_SGS" ]]; then
  echo "❌ Lambda has no VpcConfig. Attach it to a VPC first, then rerun." >&2
  exit 1
fi

LAMBDA_SG=$(echo "$LAMBDA_SGS" | awk '{print $1}')
echo "   VPC:     $VPC_ID"
echo "   Subnets: $SUBNETS"
echo "   Lambda SG (source): $LAMBDA_SG"

# --- 1) Endpoint security group ---------------------------------------------
ENDPOINT_SG_NAME="secretsmanager-endpoint-sg"

ENDPOINT_SG=$(aws ec2 describe-security-groups \
  --region "$AWS_REGION" \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=$ENDPOINT_SG_NAME" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [[ "$ENDPOINT_SG" == "None" || -z "$ENDPOINT_SG" ]]; then
  echo "➕ Creating SG: $ENDPOINT_SG_NAME"
  ENDPOINT_SG=$(aws ec2 create-security-group \
    --region "$AWS_REGION" \
    --group-name "$ENDPOINT_SG_NAME" \
    --description "Allow 443 from Lambda SG to Secrets Manager VPC endpoint" \
    --vpc-id "$VPC_ID" \
    --query 'GroupId' --output text)
else
  echo "✔ SG exists: $ENDPOINT_SG"
fi

echo "➕ Ensuring ingress 443 from $LAMBDA_SG"
aws ec2 authorize-security-group-ingress \
  --region "$AWS_REGION" \
  --group-id "$ENDPOINT_SG" \
  --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,UserIdGroupPairs=[{GroupId=$LAMBDA_SG}]" \
  >/dev/null 2>&1 || echo "   (rule already present)"

# --- 2) Interface endpoint ---------------------------------------------------
SERVICE_NAME="com.amazonaws.${AWS_REGION}.secretsmanager"

EXISTING=$(aws ec2 describe-vpc-endpoints \
  --region "$AWS_REGION" \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=service-name,Values=$SERVICE_NAME" \
  --query 'VpcEndpoints[0].VpcEndpointId' --output text 2>/dev/null || echo "None")

if [[ "$EXISTING" != "None" && -n "$EXISTING" ]]; then
  echo "✔ Endpoint already exists: $EXISTING"
else
  echo "➕ Creating interface endpoint for $SERVICE_NAME"
  EXISTING=$(aws ec2 create-vpc-endpoint \
    --region "$AWS_REGION" \
    --vpc-id "$VPC_ID" \
    --service-name "$SERVICE_NAME" \
    --vpc-endpoint-type Interface \
    --subnet-ids $SUBNETS \
    --security-group-ids "$ENDPOINT_SG" \
    --private-dns-enabled \
    --query 'VpcEndpoint.VpcEndpointId' --output text)
  echo "   Created: $EXISTING"
fi

echo "⏳ Waiting for endpoint to become available..."
for _ in {1..30}; do
  STATE=$(aws ec2 describe-vpc-endpoints \
    --region "$AWS_REGION" \
    --vpc-endpoint-ids "$EXISTING" \
    --query 'VpcEndpoints[0].State' --output text)
  if [[ "$STATE" == "available" ]]; then break; fi
  sleep 5
done
echo "   State: $STATE"

echo ""
echo "✅ Done. Test from the Lambda by invoking it; the ConnectTimeoutError"
echo "   against secretsmanager.${AWS_REGION}.amazonaws.com should be gone."
