#!/bin/bash
# Script to run Prefect flows
# Usage: ./run_flow.sh [bronze|silver|gold]

cd "$(dirname "$0")"

# Activate virtual environment
source ../.dp/bin/activate

# Load environment variables
set -a
source ../.env
set +a

# Get flow name from argument or default to bronze
FLOW=${1:-bronze}

case $FLOW in
    bronze)
        echo "🔄 Running Bronze Pipeline (data extraction)..."
        python flows/make_bronze_pipeline.py
        ;;
    silver)
        echo "🔄 Running Silver Pipeline (data processing)..."
        python flows/make_silver_pipeline.py
        ;;
    gold)
        echo "🔄 Running Gold Pipeline (analytics)..."
        python flows/make_gold_pipeline.py
        ;;
    *)
        echo "❌ Unknown flow: $FLOW"
        echo "Usage: ./run_flow.sh [bronze|silver|gold]"
        exit 1
        ;;
esac

