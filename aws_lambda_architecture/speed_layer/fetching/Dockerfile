FROM python:3.10-slim

# Set metadata
LABEL maintainer="condvest-team"
LABEL description="Speed Layer WebSocket Service for Real-time Market Data"

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash speedlayer

# Set working directory
WORKDIR /app

# Copy speed layer requirements first (for better Docker caching)
COPY speed_layer/fetching/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy shared client modules
COPY shared/ ./shared/

# Copy speed layer fetching service
COPY speed_layer/fetching/streaming_fetcher.py ./fetching/streaming_fetcher.py

# Create directory for logs
RUN mkdir -p /app/logs && chown -R speedlayer:speedlayer /app

# Switch to non-root user
USER speedlayer

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Expose health check port
EXPOSE 8080

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Run the streaming fetcher service
CMD ["python", "fetching/streaming_fetcher.py"]