#!/bin/bash
# Navigate to prefect_medallion directory
cd "$(dirname "$0")"

# Check if virtual environment exists in parent directory, otherwise use local
if [ -f "../.dp/bin/activate" ]; then
    source ../.dp/bin/activate
elif [ -f ".dp/bin/activate" ]; then
    source .dp/bin/activate
else
    echo "⚠️  Virtual environment not found. Please create one:"
    echo "   python3 -m venv .dp"
    echo "   source .dp/bin/activate"
    echo "   pip install -r requirements.txt"
    exit 1
fi

# Load environment variables from parent directory if exists, otherwise from local
if [ -f "../.env" ]; then
    set -a
    source ../.env
    set +a
elif [ -f ".env" ]; then
    set -a
    source .env
    set +a
else
    echo "⚠️  .env file not found. Please create one with required environment variables."
fi

# Start Prefect server
echo "🚀 Starting Prefect server..."
echo "   UI will be available at: http://localhost:4200"
echo "   Press Ctrl+C to stop"
echo ""
prefect server start --host 127.0.0.1 --port 4200