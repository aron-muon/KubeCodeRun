# Sidecar container for Kubernetes pod execution.
FROM python:3.11-slim

# Install curl for health checks and util-linux for nsenter
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user (used when nsenter is not needed)
RUN groupadd -r -g 1000 appuser && useradd -r -u 1000 -g appuser appuser

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy sidecar code
COPY main.py .

# Create working directory
RUN mkdir -p /mnt/data && chown -R appuser:appuser /mnt/data /app

# Note: We run as root by default because nsenter requires elevated privileges
# to enter another container's namespace. Security is controlled via:
# - shareProcessNamespace: true (required for nsenter)
# - securityContext.capabilities.add: ["SYS_PTRACE", "SYS_ADMIN", "SYS_CHROOT"]
# - allowPrivilegeEscalation: true

# Environment defaults
ENV WORKING_DIR=/mnt/data \
    LANGUAGE=python \
    SIDECAR_PORT=8080 \
    MAX_EXECUTION_TIME=120 \
    PYTHONUNBUFFERED=1

# Expose sidecar port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run sidecar
CMD ["python", "main.py"]
