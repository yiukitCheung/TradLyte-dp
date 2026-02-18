# 🎯 AWS Step Functions Pipeline Orchestration

## Overview

This directory contains the AWS Step Functions state machine that orchestrates the daily OHLCV data pipeline. The pipeline is fully automated and runs daily after market close.

## Architecture

```
                     Step Functions: condvest-daily-ohlcv-pipeline
┌────────────────────────────────────────────────────────────────────────────┐
│                                                                            │
│   ┌──────────────────────────────────────────────────────────────────┐    │
│   │              STAGE 1: PARALLEL FETCHERS                          │    │
│   │  ┌─────────────────────────┐   ┌─────────────────────────┐      │    │
│   │  │   Lambda: OHLCV Fetcher │   │  Lambda: Meta Fetcher   │      │    │
│   │  │   (2 retries)           │   │  (2 retries)            │      │    │
│   │  └───────────┬─────────────┘   └───────────┬─────────────┘      │    │
│   │              └─────────────┬───────────────┘                     │    │
│   └────────────────────────────┼─────────────────────────────────────┘    │
│                                ▼                                           │
│                          ┌─────────────────────┐                          │
│                          │  ✅ Pipeline Complete │                          │
│                          └─────────────────────┘                          │
│                                                                            │
│   ON FAILURE → SNS: condvest-pipeline-alerts → Email Notification         │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

## AWS Resources

| Resource | Name | Description |
|----------|------|-------------|
| **State Machine** | `condvest-daily-ohlcv-pipeline` | Main orchestration workflow |
| **IAM Role** | `condvest-pipeline-step-functions-role` | Permissions for Lambda/Batch/SNS |
| **EventBridge Rule** | `condvest-daily-pipeline-trigger` | Daily trigger at 21:05 UTC |
| **SNS Topic** | `condvest-pipeline-alerts` | Failure notifications |

## Schedule

| Component | Time (UTC) | Time (ET) |
|-----------|------------|-----------|
| Pipeline Trigger | 21:05 | 4:05 PM |
| Expected Completion | ~21:08 | ~4:08 PM |

**Total Duration:** ~2–5 minutes (fetchers only; no consolidator/resampler)

## Key Benefits

- **⚡ Parallel Execution:** OHLCV and Metadata fetchers run in parallel
- **🔄 Automatic Retries:** Lambda (2 retries each)
- **📧 Failure Alerts:** SNS notification with full error details on any failure
- **📊 Visual Monitoring:** AWS Console shows real-time execution graph
- **Resampling:** Done on-the-fly in backtester from raw 1d (consolidator/resampler Batch jobs archived)

## Files

| File | Description |
|------|-------------|
| `state_machine_definition.json` | Step Functions ASL definition |
| `deploy_step_functions.sh` | Deployment script for state machine + IAM + EventBridge |
| `README.md` | This documentation |

## Manual Operations

### Trigger Pipeline Manually

```bash
aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:ca-west-1:471112909340:stateMachine:condvest-daily-ohlcv-pipeline" \
  --name "manual-$(date +%Y%m%d%H%M%S)" \
  --region ca-west-1
```

### Check Pipeline Status

```bash
# List recent executions
aws stepfunctions list-executions \
  --state-machine-arn "arn:aws:states:ca-west-1:471112909340:stateMachine:condvest-daily-ohlcv-pipeline" \
  --max-results 5 \
  --region ca-west-1

# Get execution details
aws stepfunctions describe-execution \
  --execution-arn "arn:aws:states:ca-west-1:471112909340:execution:condvest-daily-ohlcv-pipeline:EXECUTION_NAME" \
  --region ca-west-1
```

### Disable/Enable Daily Schedule

```bash
# Disable (pause the pipeline)
aws events disable-rule \
  --name condvest-daily-pipeline-trigger \
  --region ca-west-1

# Enable (resume the pipeline)
aws events enable-rule \
  --name condvest-daily-pipeline-trigger \
  --region ca-west-1
```

## Monitoring

### AWS Console

1. **Step Functions Console:** Visual execution graph showing each stage's progress/status
2. **CloudWatch Logs:** Detailed logs from Lambda and Batch jobs
3. **EventBridge Console:** View scheduled triggers and invocation history

### SNS Alerts

The `condvest-pipeline-alerts` SNS topic sends notifications when any stage fails. To receive alerts:

1. Go to AWS SNS Console → Topics → `condvest-pipeline-alerts`
2. Click "Create subscription"
3. Protocol: Email
4. Endpoint: Your email address
5. Confirm the subscription email

## Retry Logic

| Stage | Component | Max Retries | Backoff |
|-------|-----------|-------------|---------|
| 1 | Lambda Fetchers | 2 | 3s exponential |
| 2 | Batch Consolidator | 1 | 60s interval |
| 3 | Batch Resamplers | 0 | N/A (fail fast) |

## Troubleshooting

### Pipeline Failed - How to Investigate

1. Go to **Step Functions Console** → Select the failed execution
2. Click on the **red (failed) state** in the visual graph
3. Expand **"Error"** to see the error message
4. Check **CloudWatch Logs** for detailed stack traces:
   - Lambda: `/aws/lambda/daily-ohlcv-fetcher`
   - Batch: `/aws/batch/job` (search for job ID)

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Lambda timeout | Too many symbols to fetch | Check Polygon API rate limits |
| Batch job FAILED | Container error | Check CloudWatch logs for job ID |
| Consolidator error | Schema mismatch | Verify data.parquet schema matches date files |
| SNS not received | No subscription | Add email subscription to SNS topic |

## Deployment

To deploy or update the Step Functions pipeline:

```bash
cd /path/to/data_pipeline/aws_lambda_architecture/batch_layer/infrastructure/orchestration
./deploy_step_functions.sh
```

The script will:
1. Create/update the IAM role with required permissions
2. Create/update the Step Functions state machine
3. Create/update the EventBridge schedule rule

---

**Last Updated:** December 10, 2025

