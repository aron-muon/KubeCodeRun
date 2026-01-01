# Multi-stage build for Code Interpreter API.
FROM python:3.11-slim as builder

# Set build arguments
ARG BUILD_DATE
ARG VERSION=1.0.0
ARG VCS_REF

# Add metadata
LABEL maintainer="LibreCodeInterpreter Contributors" \
    org.opencontainers.image.title="LibreCodeInterpreter" \
    org.opencontainers.image.description="Secure API for executing code in isolated environments" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.source="https://github.com/LibreCodeInterpreter/LibreCodeInterpreter" \
    org.opencontainers.image.licenses="Apache-2.0"

# Install system dependencies for building
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim as production

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user with explicit UID 1000 for consistent volume permissions
# The docker group GID 988 matches the host's docker group for socket access
RUN groupadd -r -g 1000 appuser && useradd -r -u 1000 -g appuser appuser && \
    groupadd -g 988 docker && usermod -aG docker appuser

# Set working directory
WORKDIR /app

# Copy Python packages from builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY src/ ./src/
COPY dashboard/ ./dashboard/

COPY .env.example .

# Create necessary directories with correct ownership
RUN mkdir -p /app/logs /app/data /app/ssl && \
    chown -R 1000:1000 /app

# Switch to non-root user
USER appuser

# Set environment variables
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Health check - try HTTPS first, then common HTTP ports
HEALTHCHECK --interval=30s --timeout=15s --start-period=10s --retries=3 \
    CMD curl -f -k https://localhost:443/health 2>/dev/null || curl -f http://localhost:8000/health 2>/dev/null || curl -f http://localhost:80/health || exit 1

# Expose ports
EXPOSE 8000 443

# Default command - use Python to run main.py which handles HTTP/HTTPS logic
CMD ["python", "-m", "src.main"]