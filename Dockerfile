# syntax=docker/dockerfile:1
# Keep this syntax directive! It's used to enable Docker BuildKit

################################
# Builder STAGE
################################
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# Disable Python downloads - use the system interpreter across both images
ENV UV_PYTHON_DOWNLOADS=0

# Set build arguments
ARG BUILD_DATE
ARG VERSION=0.0.0-dev
ARG VCS_REF

# Version for hatch-vcs
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

WORKDIR /app

# Install dependencies first (cached layer)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --locked --no-install-project --no-dev

# Copy application code and install project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

################################
# Production STAGE
################################
FROM python:3.13-slim-trixie AS production
# Must match the builder image for consistent Python paths

# Add metadata
ARG BUILD_DATE
ARG VERSION=0.0.0-dev
ARG VCS_REF

LABEL maintainer="KubeCodeRun Contributors" \
    org.opencontainers.image.title="KubeCodeRun" \
    org.opencontainers.image.description="Secure API for executing code in isolated environments" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.source="https://github.com/KubeCodeRun/KubeCodeRun" \
    org.opencontainers.image.licenses="Apache-2.0"

# Install runtime dependencies
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      catatonit \
      curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

# Create non-root user with explicit UID 1000 for consistent volume permissions
# The docker group GID 988 matches the host's docker group for socket access
RUN groupadd -r -g 1000 appuser && useradd -r -u 1000 -g appuser -d /app -s /usr/sbin/nologin appuser && \
    groupadd -g 988 docker && usermod -aG docker appuser

WORKDIR /app

# Copy the application from the builder
COPY --from=builder --chown=appuser:appuser /app /app

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Create necessary directories with correct ownership
RUN mkdir -p /app/logs /app/data /app/ssl && \
    chown -R appuser:appuser /app

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

# Default command
ENTRYPOINT ["/usr/bin/catatonit", "--"]
CMD ["python", "-m", "src.main"]
