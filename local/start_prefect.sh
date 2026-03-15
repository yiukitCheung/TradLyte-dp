#!/bin/bash
# Navigate to prefect_medallion directory
cd "$(dirname "$0")"

# Activate virtual environment
source .local_dp/bin/activate

# Load environment variables from parent directory
set -a
source ../.env
set +a

# Start Prefect server
echo "🚀 Starting Prefect server..."
echo "   UI will be available at: http://localhost:4200"
echo "   Press Ctrl+C to stop"
echo ""
prefect server start --host 127.0.0.1 --port 4200