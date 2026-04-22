#!/usr/bin/env bash
# shellcheck source=../common/pip_for_lambda.sh
#
# Install dependencies into a Lambda deployment directory using **Linux x86_64 manylinux wheels**
# matching AWS Lambda (Python 3.11 default). Safe to run from macOS: pip downloads Linux wheels.
#
# NEVER fall back to `pip install` without --platform (that installs macOS/arm64 wheels and causes
# Runtime.ImportModuleError on Lambda, e.g. `No module named 'psycopg2._psycop'` or broken pyarrow).
#
# Optional env:
#   LAMBDA_PYTHON_VERSION   default 3.11 (must match Lambda runtime)
#   LAMBDA_MANYLINUX_PLATFORM  default manylinux2014_x86_64 (use manylinux2014_aarch64 for arm64 Lambda)

pip_for_lambda_x86_64() {
    local req_file="$1"
    local target_dir="$2"
    local pyver="${LAMBDA_PYTHON_VERSION:-3.11}"
    local plat="${LAMBDA_MANYLINUX_PLATFORM:-manylinux2014_x86_64}"
    local PIP_CMD
    PIP_CMD=$(command -v pip3 || command -v pip || echo "pip3")

    echo "📥 pip install → $target_dir"
    echo "   (platform=$plat python=$pyver) — Lambda wheels only, no fallback"

    if ! $PIP_CMD install -r "$req_file" -t "$target_dir" \
        --platform "$plat" \
        --only-binary=:all: \
        --python-version "$pyver" \
        --implementation cp \
        --no-cache-dir \
        --upgrade; then
        echo ""
        echo "❌ Lambda bundle install failed."
        echo "   Do not use a plain pip install without --platform: it installs host OS wheels (e.g. macOS)"
        echo "   and breaks psycopg2/pyarrow on Lambda (ImportError: psycopg2._psycopg, etc.)."
        echo "   Try: upgrade pip (pip install -U pip), or run this deploy inside amazonlinux:2023 / CI Linux."
        exit 1
    fi
}
