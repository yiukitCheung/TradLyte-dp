#!/usr/bin/env bash
# Provision (or reuse) an SSM-managed EC2 bastion for private RDS access.
#
# The bastion sits in the same VPC/subnets as your batch Lambdas. You connect
# with Session Manager port forwarding — no public IP or SSH keys required.
#
# Usage:
#   AWS_REGION=ca-west-1 \
#   SOURCE_VPC_LAMBDA=dev-batch-daily-ohlcv-ingest-handler \
#   ./cloud/infrastructure/common/setup_ssm_bastion.sh
#
# Optional:
#   INSTANCE_TYPE=t3.micro
#   BASTION_NAME=tradlyte-ssm-bastion
#   RDS_SG=sg-0f54a5a73687acb96
#   RDS_PROXY_NAME=dev-rds-proxy-v2

set -euo pipefail
export AWS_PAGER=""

AWS_REGION="${AWS_REGION:-ca-west-1}"
SOURCE_VPC_LAMBDA="${SOURCE_VPC_LAMBDA:-dev-batch-daily-ohlcv-ingest-handler}"
VPC_ID="${VPC_ID:-}"
SUBNET_ID="${SUBNET_ID:-}"
SUBNET_IDS="${SUBNET_IDS:-}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.micro}"
BASTION_NAME="${BASTION_NAME:-tradlyte-ssm-bastion}"
BASTION_SG_NAME="${BASTION_SG_NAME:-tradlyte-ssm-bastion}"
IAM_ROLE_NAME="${IAM_ROLE_NAME:-tradlyte-ssm-bastion-role}"
INSTANCE_PROFILE_NAME="${INSTANCE_PROFILE_NAME:-tradlyte-ssm-bastion-profile}"
RDS_SG="${RDS_SG:-sg-0f54a5a73687acb96}"
RDS_PROXY_NAME="${RDS_PROXY_NAME:-dev-rds-proxy-v2}"
SSM_ENDPOINT_SG_NAME="${SSM_ENDPOINT_SG_NAME:-tradlyte-ssm-endpoints}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"

echo "============================================================"
echo "  TradLyte SSM bastion setup"
echo "============================================================"
echo "Region:            $AWS_REGION"
echo "Source Lambda:     ${SOURCE_VPC_LAMBDA:-<none>}"
echo "Bastion name tag:  $BASTION_NAME"

if [[ -n "$VPC_ID" && -n "$SUBNET_ID" ]]; then
  SUBNETS="${SUBNET_IDS:-$SUBNET_ID}"
  echo "VPC (env):         $VPC_ID"
  echo "Subnet (env):      $SUBNET_ID"
elif [[ -n "$VPC_ID" && -n "$SUBNET_IDS" ]]; then
  SUBNETS="$SUBNET_IDS"
  SUBNET_ID="$(echo "$SUBNETS" | awk '{print $1}')"
  echo "VPC (env):         $VPC_ID"
  echo "Subnets (env):     $SUBNETS"
else
  VPC_JSON="$(aws lambda get-function-configuration \
    --function-name "$SOURCE_VPC_LAMBDA" \
    --region "$AWS_REGION" \
    --query 'VpcConfig' \
    --output json)"

  SUBNETS="$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(' '.join(d.get('SubnetIds', [])))" "$VPC_JSON")"
  VPC_ID="$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('VpcId',''))" "$VPC_JSON")"
  SUBNET_ID="$(echo "$SUBNETS" | awk '{print $1}')"
fi

if [[ -z "$VPC_ID" || -z "$SUBNET_ID" ]]; then
  echo "❌ Could not resolve VPC/subnet." >&2
  echo "   Set VPC_ID + SUBNET_ID (or SUBNET_IDS), or grant lambda:GetFunctionConfiguration on $SOURCE_VPC_LAMBDA" >&2
  exit 1
fi

echo "VPC:               $VPC_ID"
echo "Subnet:            $SUBNET_ID"

ensure_iam_role() {
  if aws iam get-role --role-name "$IAM_ROLE_NAME" >/dev/null 2>&1; then
    echo "✔ IAM role exists: $IAM_ROLE_NAME"
  else
    echo "➕ Creating IAM role: $IAM_ROLE_NAME"
    aws iam create-role \
      --role-name "$IAM_ROLE_NAME" \
      --assume-role-policy-document '{
        "Version":"2012-10-17",
        "Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]
      }' >/dev/null
    aws iam attach-role-policy \
      --role-name "$IAM_ROLE_NAME" \
      --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  fi

  if aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null 2>&1; then
    echo "✔ Instance profile exists: $INSTANCE_PROFILE_NAME"
  else
    echo "➕ Creating instance profile: $INSTANCE_PROFILE_NAME"
    aws iam create-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null
    aws iam add-role-to-instance-profile \
      --instance-profile-name "$INSTANCE_PROFILE_NAME" \
      --role-name "$IAM_ROLE_NAME"
    sleep 10
  fi
}

ensure_bastion_sg() {
  local sg_id
  sg_id="$(aws ec2 describe-security-groups \
    --region "$AWS_REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=$BASTION_SG_NAME" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || echo "None")"

  if [[ -z "$sg_id" || "$sg_id" == "None" ]]; then
    echo "➕ Creating bastion SG: $BASTION_SG_NAME" >&2
    sg_id="$(aws ec2 create-security-group \
      --region "$AWS_REGION" \
      --group-name "$BASTION_SG_NAME" \
      --description "SSM bastion for dev RDS port-forwarding" \
      --vpc-id "$VPC_ID" \
      --query 'GroupId' \
      --output text)"
    aws ec2 create-tags \
      --region "$AWS_REGION" \
      --resources "$sg_id" \
      --tags "Key=Name,Value=$BASTION_SG_NAME" "Key=Purpose,Value=ssm-bastion"
  else
    echo "✔ Bastion SG exists: $sg_id" >&2
  fi

  echo "$sg_id"
}

authorize_bastion_to_rds() {
  local bastion_sg="$1"
  local target_sg="$2"
  local label="$3"

  local already
  already="$(aws ec2 describe-security-groups \
    --group-ids "$target_sg" \
    --region "$AWS_REGION" \
    --query "SecurityGroups[0].IpPermissions[?FromPort==\`5432\` && ToPort==\`5432\`].UserIdGroupPairs[?GroupId=='$bastion_sg'] | [0]" \
    --output text 2>/dev/null || echo "")"

  if [[ -n "$already" && "$already" != "None" ]]; then
    echo "✔ $label already allows bastion SG on 5432"
    return
  fi

  echo "➕ Authorizing bastion SG -> $label (tcp/5432)"
  aws ec2 authorize-security-group-ingress \
    --region "$AWS_REGION" \
    --group-id "$target_sg" \
    --ip-permissions "[{
      \"IpProtocol\":\"tcp\",
      \"FromPort\":5432,
      \"ToPort\":5432,
      \"UserIdGroupPairs\":[{\"GroupId\":\"$bastion_sg\",\"Description\":\"SSM bastion to Postgres\"}]
    }]" >/dev/null
}

ensure_ssm_vpc_endpoints() {
  local bastion_sg="$1"
  local endpoint_sg
  endpoint_sg="$(aws ec2 describe-security-groups \
    --region "$AWS_REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=$SSM_ENDPOINT_SG_NAME" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || echo "None")"

  if [[ -z "$endpoint_sg" || "$endpoint_sg" == "None" ]]; then
    echo "➕ Creating SSM endpoint SG: $SSM_ENDPOINT_SG_NAME"
    endpoint_sg="$(aws ec2 create-security-group \
      --region "$AWS_REGION" \
      --group-name "$SSM_ENDPOINT_SG_NAME" \
      --description "Interface endpoints for SSM Session Manager" \
      --vpc-id "$VPC_ID" \
      --query 'GroupId' \
      --output text)"
  else
    echo "✔ SSM endpoint SG exists: $endpoint_sg"
  fi

  aws ec2 authorize-security-group-ingress \
    --region "$AWS_REGION" \
    --group-id "$endpoint_sg" \
    --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,UserIdGroupPairs=[{GroupId=$bastion_sg}]" \
    >/dev/null 2>&1 || true

  local services=(ssm ssmmessages ec2messages)
  for svc in "${services[@]}"; do
    local service_name="com.amazonaws.${AWS_REGION}.${svc}"
    local existing
    existing="$(aws ec2 describe-vpc-endpoints \
      --region "$AWS_REGION" \
      --filters "Name=vpc-id,Values=$VPC_ID" "Name=service-name,Values=$service_name" \
      --query 'VpcEndpoints[?State!=`deleted`].VpcEndpointId | [0]' \
      --output text 2>/dev/null || echo "None")"

    if [[ -n "$existing" && "$existing" != "None" ]]; then
      echo "✔ VPC endpoint exists for $svc: $existing"
      continue
    fi

    echo "➕ Creating VPC endpoint for $service_name"
    aws ec2 create-vpc-endpoint \
      --region "$AWS_REGION" \
      --vpc-id "$VPC_ID" \
      --service-name "$service_name" \
      --vpc-endpoint-type Interface \
      --subnet-ids $SUBNETS \
      --security-group-ids "$endpoint_sg" \
      --private-dns-enabled \
      --query 'VpcEndpoint.VpcEndpointId' \
      --output text >/dev/null
  done
}

find_bastion_instance() {
  aws ec2 describe-instances \
    --region "$AWS_REGION" \
    --filters \
      "Name=tag:Name,Values=$BASTION_NAME" \
      "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text 2>/dev/null || echo "None"
}

launch_bastion() {
  local bastion_sg="$1"
  local existing
  existing="$(find_bastion_instance)"
  if [[ -n "$existing" && "$existing" != "None" ]]; then
    echo "✔ Bastion instance already exists: $existing" >&2
    if [[ "$(aws ec2 describe-instances --instance-ids "$existing" --region "$AWS_REGION" --query 'Reservations[0].Instances[0].State.Name' --output text)" == "stopped" ]]; then
      echo "▶ Starting stopped bastion..." >&2
      aws ec2 start-instances --instance-ids "$existing" --region "$AWS_REGION" >/dev/null
      aws ec2 wait instance-running --instance-ids "$existing" --region "$AWS_REGION"
    fi
    echo "$existing"
    return
  fi

  local ami_id
  ami_id="$(aws ssm get-parameter \
    --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
    --region "$AWS_REGION" \
    --query 'Parameter.Value' \
    --output text)"

  echo "➕ Launching bastion EC2 ($INSTANCE_TYPE, AMI $ami_id)" >&2
  local instance_id
  instance_id="$(aws ec2 run-instances \
    --region "$AWS_REGION" \
    --image-id "$ami_id" \
    --instance-type "$INSTANCE_TYPE" \
    --subnet-id "$SUBNET_ID" \
    --security-group-ids "$bastion_sg" \
    --iam-instance-profile "Name=$INSTANCE_PROFILE_NAME" \
    --metadata-options "HttpTokens=required,HttpPutResponseHopLimit=2" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$BASTION_NAME},{Key=Purpose,Value=ssm-bastion}]" \
    --query 'Instances[0].InstanceId' \
    --output text)"

  echo "⏳ Waiting for instance running..." >&2
  aws ec2 wait instance-running --instance-ids "$instance_id" --region "$AWS_REGION"
  echo "$instance_id"
}

wait_for_ssm_online() {
  local instance_id="$1"
  echo "⏳ Waiting for SSM agent (PingStatus=Online) on $instance_id ..."
  for _ in $(seq 1 36); do
    local status
    status="$(aws ssm describe-instance-information \
      --region "$AWS_REGION" \
      --filters "Key=InstanceIds,Values=$instance_id" \
      --query 'InstanceInformationList[0].PingStatus' \
      --output text 2>/dev/null || echo "None")"
    if [[ "$status" == "Online" ]]; then
      echo "✔ SSM Online"
      return 0
    fi
    sleep 10
  done
  echo "⚠️  SSM not Online yet. Wait a few minutes, then run rds_port_forward.sh" >&2
  return 1
}

ensure_iam_role
BASTION_SG="$(ensure_bastion_sg)"
BASTION_SG="$(echo "$BASTION_SG" | tr -d '[:space:]' | grep -Eo 'sg-[0-9a-f]+' | head -1)"
if [[ -z "$BASTION_SG" ]]; then
  echo "❌ Failed to resolve bastion security group id" >&2
  exit 1
fi
authorize_bastion_to_rds "$BASTION_SG" "$RDS_SG" "RDS SG ($RDS_SG)"

PROXY_SG="$(aws rds describe-db-proxies \
  --db-proxy-name "$RDS_PROXY_NAME" \
  --region "$AWS_REGION" \
  --query 'DBProxies[0].VpcSecurityGroupIds[0]' \
  --output text 2>/dev/null || echo "None")"
if [[ -n "$PROXY_SG" && "$PROXY_SG" != "None" ]]; then
  authorize_bastion_to_rds "$BASTION_SG" "$PROXY_SG" "RDS Proxy SG ($PROXY_SG)"
else
  echo "ℹ️  RDS proxy $RDS_PROXY_NAME not found — wired RDS SG only"
fi

ensure_ssm_vpc_endpoints "$BASTION_SG"
INSTANCE_ID="$(launch_bastion "$BASTION_SG")"
INSTANCE_ID="$(echo "$INSTANCE_ID" | tr -d '[:space:]' | grep -Eo 'i-[0-9a-f]+' | head -1)"
if [[ -z "$INSTANCE_ID" ]]; then
  echo "❌ Failed to resolve bastion instance id" >&2
  exit 1
fi
wait_for_ssm_online "$INSTANCE_ID" || true

echo ""
echo "============================================================"
echo "  ✅ SSM bastion ready"
echo "============================================================"
echo "Instance ID:  $INSTANCE_ID"
echo "Bastion SG:    $BASTION_SG"
echo ""
echo "Next — start a tunnel (keep terminal open):"
echo "  BASTION_INSTANCE_ID=$INSTANCE_ID \\"
echo "  ./cloud/infrastructure/common/rds_port_forward.sh"
echo ""
echo "Then connect with psql:"
echo "  PGHOST=127.0.0.1 PGPORT=5433 PGUSER=<user> PGDATABASE=condvest psql"
echo ""
