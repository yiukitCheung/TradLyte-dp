#!/bin/bash
# Authorize the scanner Fargate compute environment's security group into the
# RDS Proxy security group on port 5432. Idempotent — safe to re-run.
#
# Why this exists:
#   The scanner Batch tasks share the same VPC + subnets as the OHLCV planner
#   Lambdas and dev-rds-proxy-v2, but each component has its own SG. The proxy
#   SG only allows the SGs explicitly authorized on it, so the scanner SG must
#   be added before tasks can open a 5432 connection. Without this rule the
#   container fails with `psycopg2.OperationalError: connection ... timed out`
#   even though the proxy hostname / Secrets Manager / IAM are all correct.
#
# Usage:
#   ./wire_scanner_to_rds_proxy.sh
#   AWS_REGION=ca-west-1 PROXY_NAME=dev-rds-proxy-v2 \
#     COMPUTE_ENV=dev-batch-scanner-fargate ./wire_scanner_to_rds_proxy.sh

set -euo pipefail
export AWS_PAGER=""

AWS_REGION="${AWS_REGION:-ca-west-1}"
PROXY_NAME="${PROXY_NAME:-dev-rds-proxy-v2}"
COMPUTE_ENV="${COMPUTE_ENV:-dev-batch-scanner-fargate}"

echo "============================================================"
echo "  Wire scanner SG -> RDS Proxy SG (port 5432)"
echo "============================================================"
echo "Region:          $AWS_REGION"
echo "RDS Proxy:       $PROXY_NAME"
echo "Compute Env:     $COMPUTE_ENV"

PROXY_SG=$(aws rds describe-db-proxies \
    --db-proxy-name "$PROXY_NAME" \
    --region "$AWS_REGION" \
    --query 'DBProxies[0].VpcSecurityGroupIds[0]' \
    --output text)

if [ -z "$PROXY_SG" ] || [ "$PROXY_SG" = "None" ]; then
    echo "ERROR: could not resolve RDS proxy SG for $PROXY_NAME" >&2
    exit 1
fi

SCANNER_SG=$(aws batch describe-compute-environments \
    --compute-environments "$COMPUTE_ENV" \
    --region "$AWS_REGION" \
    --query 'computeEnvironments[0].computeResources.securityGroupIds[0]' \
    --output text)

if [ -z "$SCANNER_SG" ] || [ "$SCANNER_SG" = "None" ]; then
    echo "ERROR: could not resolve scanner compute environment SG for $COMPUTE_ENV" >&2
    exit 1
fi

echo "Proxy SG:        $PROXY_SG"
echo "Scanner SG:      $SCANNER_SG"

ALREADY_AUTHORIZED=$(aws ec2 describe-security-groups \
    --group-ids "$PROXY_SG" \
    --region "$AWS_REGION" \
    --query "SecurityGroups[0].IpPermissions[?FromPort==\`5432\` && ToPort==\`5432\`].UserIdGroupPairs[?GroupId=='$SCANNER_SG'] | [0]" \
    --output text)

if [ -n "$ALREADY_AUTHORIZED" ] && [ "$ALREADY_AUTHORIZED" != "None" ]; then
    echo ""
    echo "Rule already in place. Nothing to do."
    exit 0
fi

echo ""
echo "Authorizing $SCANNER_SG -> $PROXY_SG (tcp/5432)..."

aws ec2 authorize-security-group-ingress \
    --region "$AWS_REGION" \
    --group-id "$PROXY_SG" \
    --ip-permissions "[{
        \"IpProtocol\":\"tcp\",
        \"FromPort\":5432,
        \"ToPort\":5432,
        \"UserIdGroupPairs\":[{
            \"GroupId\":\"$SCANNER_SG\",
            \"Description\":\"Allow $COMPUTE_ENV Fargate tasks to reach RDS Proxy\"
        }]
    }]" \
    --output json >/dev/null

echo "Done. Verify:"
echo ""
echo "  aws ec2 describe-security-groups --group-ids $PROXY_SG \\"
echo "    --region $AWS_REGION \\"
echo "    --query 'SecurityGroups[0].IpPermissions[?FromPort==\`5432\`].UserIdGroupPairs[].GroupId' \\"
echo "    --output table"
