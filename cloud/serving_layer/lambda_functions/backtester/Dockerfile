# AWS Lambda container image — Backtester API (ARM64 / Graviton)
# Build: docker buildx build --platform linux/arm64 -t backtester-lambda:latest -f Dockerfile .
# Run from repo root with: -f cloud/serving_layer/lambda_functions/backtester/Dockerfile

# Use default Lambda Python 3.10 image; build with --platform linux/arm64 (see build_push_backtester.sh).
FROM public.ecr.aws/lambda/python:3.10

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/var/task/shared

# Dependencies
COPY cloud/serving_layer/lambda_functions/backtester/requirements.backtester.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Shared analytics engine (package: analytics_core)
COPY cloud/shared /var/task/shared

# Handler at top level so Lambda handler = backtest_handler.lambda_handler
COPY cloud/serving_layer/lambda_functions/backtester/backtest_handler.py /var/task/backtest_handler.py

CMD ["backtest_handler.lambda_handler"]
