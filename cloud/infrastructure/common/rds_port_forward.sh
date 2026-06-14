#!/usr/bin/env bash
# Start an SSM port-forward session to RDS (or RDS Proxy) through the bastion.
#
# Usage:
#   ./cloud/infrastructure/common/rds_port_forward.sh
#
# Optional env:
#   AWS_REGION=ca-west-1
#   BASTION_INSTANCE_ID=i-xxxxxxxx   (auto-detected from tag if unset)
#   BASTION_NAME=tradlyte-ssm-bastion
#   RDS_SECRET_ARN=arn:aws:secretsmanager:...
#   LOCAL_PORT=5433
#   REMOTE_PORT=5432
#
# Requires: AWS CLI v2 + session-manager-plugin
#   macOS: brew install --cask session-manager-plugin
#   CloudShell: usually preinstalled; if not, see AWS docs.

set -euo pipefail
export AWS_PAGER=""

AWS_REGION="${AWS_REGION:-ca-west-1}"
BASTION_NAME="${BASTION_NAME:-tradlyte-ssm-bastion}"
LOCAL_PORT="${LOCAL_PORT:-5433}"
REMOTE_PORT="${REMOTE_PORT:-5432}"

if ! command -v session-manager-plugin >/dev/null 2>&1; then
  echo "❌ session-manager-plugin not found." >&2
  echo "   Install: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html" >&2
  exit 1
fi

if [[ -z "${BASTION_INSTANCE_ID:-}" ]]; then
  BASTION_INSTANCE_ID="$(aws ec2 describe-instances \
    --region "$AWS_REGION" \
    --filters \
      "Name=tag:Name,Values=$BASTION_NAME" \
      "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text)"
fi

if [[ -z "$BASTION_INSTANCE_ID" || "$BASTION_INSTANCE_ID" == "None" ]]; then
  echo "❌ No running bastion found. Run setup_ssm_bastion.sh first." >&2
  exit 1
fi

if [[ -z "${RDS_SECRET_ARN:-}" ]]; then
  RDS_SECRET_ARN="$(aws lambda get-function-configuration \
    --function-name dev-batch-scanner \
    --region "$AWS_REGION" \
    --query 'Environment.Variables.RDS_SECRET_ARN' \
    --output text 2>/dev/null || echo "")"
fi

if [[ -z "$RDS_SECRET_ARN" || "$RDS_SECRET_ARN" == "None" ]]; then
  echo "❌ Set RDS_SECRET_ARN or ensure dev-batch-scanner has it configured." >&2
  exit 1
fi

SECRET_JSON="$(aws secretsmanager get-secret-value \
  --secret-id "$RDS_SECRET_ARN" \
  --region "$AWS_REGION" \
  --query SecretString \
  --output text)"

REMOTE_HOST="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['host'])" "$SECRET_JSON")"
PGUSER="$(python3 -c "import json,sys; s=json.loads(sys.argv[1]); print(s.get('username',''))" "$SECRET_JSON")"
PGDATABASE="$(python3 -c "import json,sys; s=json.loads(sys.argv[1]); print(s.get('database', s.get('dbname','condvest')))" "$SECRET_JSON")"

echo "============================================================"
echo "  RDS port forward (SSM)"
echo "============================================================"
echo "Bastion:      $BASTION_INSTANCE_ID"
echo "Remote host:  $REMOTE_HOST:$REMOTE_PORT"
echo "Local bind:   127.0.0.1:$LOCAL_PORT"
echo "Database:     $PGDATABASE"
echo "User:         $PGUSER"
echo ""
echo "Leave this session running. In another terminal:"
echo ""
echo "  export PGPASSWORD=\$(aws secretsmanager get-secret-value \\"
echo "    --secret-id \"$RDS_SECRET_ARN\" --region $AWS_REGION \\"
echo "    --query SecretString --output text | jq -r .password)"
echo "  psql \"host=127.0.0.1 port=$LOCAL_PORT dbname=$PGDATABASE user=$PGUSER sslmode=require\""
echo ""
echo "Press Ctrl+C to stop the tunnel."
echo "============================================================"

exec aws ssm start-session \
  --target "$BASTION_INSTANCE_ID" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"$REMOTE_HOST\"],\"portNumber\":[\"$REMOTE_PORT\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}" \
  --region "$AWS_REGION"
