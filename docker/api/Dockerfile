# syntax=docker/dockerfile:1
# KubeCodeRun API server with Docker Hardened Images!

ARG BUILD_DATE
ARG VERSION=0.0.0-dev
ARG VCS_REF

################################
# Builder stage - install dependencies with uv
################################
FROM dhi.io/python:3.13-debian13-dev AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install uv for fast dependency installation
RUN pip install --no-cache-dir uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# Use the system interpreter (don't download Python)
ENV UV_PYTHON_DOWNLOADS=0

# Version for hatch-vcs
ARG VERSION=0.0.0-dev
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

# Create runtime directories with correct ownership for DHI non-root user (UID 65532)
RUN mkdir -p /app/logs /app/data /app/ssl && \
    chown -R 65532:65532 /app/logs /app/data /app/ssl

################################
# Final stage - minimal runtime image
################################
FROM dhi.io/python:3.13-debian13 AS final

ARG BUILD_DATE
ARG VERSION=0.0.0-dev
ARG VCS_REF

LABEL maintainer="KubeCodeRun Contributors" \
    org.opencontainers.image.title="KubeCodeRun" \
    org.opencontainers.image.description="Secure API for executing code in isolated environments" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.source="https://github.com/aron-muon/KubeCodeRun" \
    org.opencontainers.image.licenses="Apache-2.0"

# Copy /usr/bin/env for execution patterns
COPY --from=builder /usr/bin/env /usr/bin/

# Copy runtime directories with correct ownership
COPY --chown=65532:65532 --from=builder /app/logs /app/logs
COPY --chown=65532:65532 --from=builder /app/data /app/data
COPY --chown=65532:65532 --from=builder /app/ssl /app/ssl

# Copy the virtual environment from builder
# DHI Python is at /opt/python, uv creates .venv with symlinks to system Python
COPY --from=builder --chown=65532:65532 /app/.venv /app/.venv
COPY --from=builder --chown=65532:65532 /app/src /app/src
COPY --from=builder --chown=65532:65532 /app/dashboard /app/dashboard

WORKDIR /app

# Place virtual environment executables at the front of the path
ENV PATH="/app/.venv/bin:/opt/python/bin:/usr/bin:/bin"

# Set environment variables
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Health check
HEALTHCHECK --interval=30s --timeout=15s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=10).read()"]

# Expose ports
EXPOSE 8000 443

# DHI images run as non-root (UID 65532) by default

# Default command - run the API server
CMD ["python", "-m", "src.main"]
