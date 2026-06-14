# SSM bastion for private RDS access

Your RDS instance is in a private VPC. CloudShell and your laptop cannot reach it directly. This bastion uses **AWS Systems Manager Session Manager** (no SSH keys, no public IP).

## One-time setup

From repo root, with AWS CLI configured for `ca-west-1`:

```bash
chmod +x cloud/infrastructure/common/setup_ssm_bastion.sh
chmod +x cloud/infrastructure/common/rds_port_forward.sh

AWS_REGION=ca-west-1 \
SOURCE_VPC_LAMBDA=dev-batch-daily-ohlcv-ingest-handler \
./cloud/infrastructure/common/setup_ssm_bastion.sh
```

If your IAM user cannot read Lambda config, pass VPC/subnet explicitly (from RDS console → Connectivity, or ask an admin):

```bash
AWS_REGION=ca-west-1 \
VPC_ID=vpc-xxxxxxxx \
SUBNET_ID=subnet-xxxxxxxx \
./cloud/infrastructure/common/setup_ssm_bastion.sh
```

This creates:

- EC2 instance `tradlyte-ssm-bastion` (t3.micro, private subnet)
- IAM role with `AmazonSSMManagedInstanceCore`
- Security group + RDS/Proxy ingress for bastion → Postgres
- SSM VPC interface endpoints (if missing)

Required IAM permissions: `ec2:*`, `iam:*` (role/profile), `lambda:GetFunctionConfiguration`, `rds:DescribeDBProxies`, `ssm:*`.

## Connect (every time)

### 1. Install Session Manager plugin (local Mac, once)

```bash
brew install --cask session-manager-plugin
```

CloudShell usually has this preinstalled.

### 2. Start tunnel (terminal 1 — keep open)

```bash
./cloud/infrastructure/common/rds_port_forward.sh
```

### 3. psql (terminal 2)

```bash
export AWS_REGION=ca-west-1
export RDS_SECRET_ARN="$(aws lambda get-function-configuration \
  --function-name dev-batch-scanner \
  --region ca-west-1 \
  --query 'Environment.Variables.RDS_SECRET_ARN' --output text)"

SECRET=$(aws secretsmanager get-secret-value \
  --secret-id "$RDS_SECRET_ARN" --region ca-west-1 \
  --query SecretString --output text)

export PGPASSWORD=$(echo "$SECRET" | jq -r '.password')
export PGUSER=$(echo "$SECRET" | jq -r '.username')
export PGDATABASE=$(echo "$SECRET" | jq -r '.database // .dbname // "condvest"')

psql "host=127.0.0.1 port=5433 dbname=$PGDATABASE user=$PGUSER sslmode=require"
```

### Example query

```sql
SELECT rank, symbol, confidence, metadata->>'market_cap' AS market_cap
FROM stock_picks
WHERE strategy_name = 'vegas_channel_short_term'
  AND scan_date = (SELECT MAX(scan_date) FROM stock_picks)
ORDER BY rank
LIMIT 15;
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Target not connected` | Wait 2–3 min after setup; check SSM agent Online in Systems Manager → Fleet Manager |
| Tunnel starts but psql times out | Secret `host` must be reachable from bastion (RDS or Proxy SG allows bastion SG) |
| `AccessDenied` on setup | Need EC2/IAM/Lambda read perms; ask account admin |
| Plugin missing | Install session-manager-plugin (see above) |

## Cost

- t3.micro ~ $7–8/month if left running 24/7
- Stop instance when not needed: `aws ec2 stop-instances --instance-ids i-xxx --region ca-west-1`
- Start again before port-forward: setup script auto-starts stopped bastion on re-run
