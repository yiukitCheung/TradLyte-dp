#!/usr/bin/env bash
# Provision RDS Proxy for serving Lambda read traffic.
#
# Usage:
#   AWS_REGION=ca-west-1 \
#   DB_INSTANCE_ID=dev-condvest-db \
#   RDS_SECRET_ARN=arn:aws:secretsmanager:...:secret:... \
#   RDS_PROXY_ROLE_ARN=arn:aws:iam::123456789012:role/dev-rds-proxy-role \
#   SOURCE_LAMBDA_FOR_VPC=dev-serving-api \
#   ./cloud/serving_layer/infrastructure/serving_api/create_rds_proxy.sh

set -euo pipefail
export AWS_PAGER=""

AWS_REGION="${AWS_REGION:-ca-west-1}"
DB_PROXY_NAME="${DB_PROXY_NAME:-dev-rds-proxy}"
DB_INSTANCE_ID="${DB_INSTANCE_ID:-}"
RDS_SECRET_ARN="${RDS_SECRET_ARN:-}"
RDS_PROXY_ROLE_ARN="${RDS_PROXY_ROLE_ARN:-}"
SOURCE_LAMBDA_FOR_VPC="${SOURCE_LAMBDA_FOR_VPC:-dev-serving-api}"
PROXY_SG_NAME="${PROXY_SG_NAME:-dev-rds-proxy-sg}"

if [[ -z "$DB_INSTANCE_ID" || -z "$RDS_SECRET_ARN" || -z "$RDS_PROXY_ROLE_ARN" ]]; then
  echo "❌ DB_INSTANCE_ID, RDS_SECRET_ARN and RDS_PROXY_ROLE_ARN are required."
  exit 1
fi

echo "🚀 Creating/ensuring RDS Proxy: $DB_PROXY_NAME"
echo "Region: $AWS_REGION"

VPC_JSON="$(aws lambda get-function-configuration \
  --function-name "$SOURCE_LAMBDA_FOR_VPC" \
  --region "$AWS_REGION" \
  --query 'VpcConfig' --output json)"

VPC_ID="$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('VpcId',''))" "$VPC_JSON")"
SUBNET_IDS="$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(' '.join(d.get('SubnetIds', [])))" "$VPC_JSON")"
LAMBDA_SG="$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print((d.get('SecurityGroupIds') or [''])[0])" "$VPC_JSON")"

if [[ -z "$VPC_ID" || -z "$SUBNET_IDS" || -z "$LAMBDA_SG" ]]; then
  echo "❌ Unable to derive VPC/subnets/SG from $SOURCE_LAMBDA_FOR_VPC"
  exit 1
fi

PROXY_SG_ID="$(aws ec2 describe-security-groups \
  --region "$AWS_REGION" \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=$PROXY_SG_NAME" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")"

if [[ "$PROXY_SG_ID" == "None" || -z "$PROXY_SG_ID" ]]; then
  PROXY_SG_ID="$(aws ec2 create-security-group \
    --region "$AWS_REGION" \
    --group-name "$PROXY_SG_NAME" \
    --description "Security group for RDS Proxy ($DB_PROXY_NAME)" \
    --vpc-id "$VPC_ID" \
    --query 'GroupId' --output text)"
  echo "➕ Created proxy SG: $PROXY_SG_ID"
else
  echo "✔ Proxy SG exists: $PROXY_SG_ID"
fi

echo "➕ Ensuring Lambda SG can reach proxy on 5432"
aws ec2 authorize-security-group-ingress \
  --region "$AWS_REGION" \
  --group-id "$PROXY_SG_ID" \
  --ip-permissions "IpProtocol=tcp,FromPort=5432,ToPort=5432,UserIdGroupPairs=[{GroupId=$LAMBDA_SG}]" \
  >/dev/null 2>&1 || true

DB_SG_ID="$(aws rds describe-db-instances \
  --db-instance-identifier "$DB_INSTANCE_ID" \
  --region "$AWS_REGION" \
  --query 'DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId' \
  --output text)"

if [[ -n "$DB_SG_ID" && "$DB_SG_ID" != "None" ]]; then
  echo "➕ Ensuring proxy SG can reach DB SG on 5432"
  aws ec2 authorize-security-group-ingress \
    --region "$AWS_REGION" \
    --group-id "$DB_SG_ID" \
    --ip-permissions "IpProtocol=tcp,FromPort=5432,ToPort=5432,UserIdGroupPairs=[{GroupId=$PROXY_SG_ID}]" \
    >/dev/null 2>&1 || true
fi

EXISTING_PROXY="$(aws rds describe-db-proxies \
  --region "$AWS_REGION" \
  --query "DBProxies[?DBProxyName=='$DB_PROXY_NAME'].DBProxyName | [0]" \
  --output text 2>/dev/null || echo "None")"

if [[ -z "$EXISTING_PROXY" || "$EXISTING_PROXY" == "None" ]]; then
  echo "➕ Creating RDS Proxy"
  aws rds create-db-proxy \
    --db-proxy-name "$DB_PROXY_NAME" \
    --engine-family POSTGRESQL \
    --auth "AuthScheme=SECRETS,SecretArn=$RDS_SECRET_ARN,IAMAuth=DISABLED" \
    --role-arn "$RDS_PROXY_ROLE_ARN" \
    --vpc-subnet-ids $SUBNET_IDS \
    --vpc-security-group-ids "$PROXY_SG_ID" \
    --require-tls \
    --idle-client-timeout 1800 \
    --debug-logging \
    --region "$AWS_REGION" >/dev/null
else
  echo "✔ RDS Proxy already exists: $DB_PROXY_NAME"
fi

echo "⏳ Waiting for proxy availability"
for _ in {1..40}; do
  STATUS="$(aws rds describe-db-proxies --db-proxy-name "$DB_PROXY_NAME" --region "$AWS_REGION" --query 'DBProxies[0].Status' --output text)"
  [[ "$STATUS" == "available" ]] && break
  sleep 5
done
echo "Proxy status: $STATUS"

echo "➕ Ensuring DB instance target registration: $DB_INSTANCE_ID"
aws rds register-db-proxy-targets \
  --db-proxy-name "$DB_PROXY_NAME" \
  --target-group-name default \
  --db-instance-identifiers "$DB_INSTANCE_ID" \
  --region "$AWS_REGION" >/dev/null 2>&1 || true

PROXY_ENDPOINT="$(aws rds describe-db-proxies \
  --db-proxy-name "$DB_PROXY_NAME" \
  --region "$AWS_REGION" \
  --query 'DBProxies[0].Endpoint' --output text)"

echo "✅ RDS Proxy ready."
echo "Endpoint: $PROXY_ENDPOINT"
echo ""
echo "Update your RDS secret host field to this endpoint for serving Lambda reads."
