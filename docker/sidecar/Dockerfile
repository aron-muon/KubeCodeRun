# syntax=docker/dockerfile:1
# BuildKit syntax for cache mounts and other optimizations

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder STAGE
################################
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install dependencies into a virtual environment
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    uv venv /app/.venv && \
    uv pip install --python /app/.venv/bin/python -r requirements.txt

# Copy application code
COPY main.py .

################################
# Production STAGE
################################
FROM python:3.13-slim-trixie AS production

# Add metadata
ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Sidecar" \
      org.opencontainers.image.description="HTTP sidecar for executing code in Kubernetes pods via nsenter" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Install runtime dependencies
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      util-linux \
      libcap2-bin; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

# Create non-root user (used when nsenter is not needed)
RUN groupadd -r -g 1000 appuser && useradd -r -u 1000 -g appuser appuser

WORKDIR /app

# Copy the application from the builder
COPY --from=builder /app /app

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Create working directory
RUN mkdir -p /mnt/data && chown -R appuser:appuser /mnt/data /app

# Add file capabilities to nsenter binary so non-root users can use it
# This allows the binary to gain these capabilities when executed:
# - cap_sys_ptrace: access /proc/<pid>/ns/ of other processes
# - cap_sys_admin: call setns() to enter namespaces
# - cap_sys_chroot: required for mount namespace operations
# The +eip flags mean: add to Effective, Inheritable, and Permitted sets
RUN setcap 'cap_sys_ptrace,cap_sys_admin,cap_sys_chroot+eip' /usr/bin/nsenter

# Kubernetes pod spec still requires:
# - shareProcessNamespace: true (so sidecar can see main container's processes)
# - securityContext.capabilities.add: ["SYS_PTRACE", "SYS_ADMIN", "SYS_CHROOT"]
#   (to allow the bounding set to include these caps)
# - securityContext.allowPrivilegeEscalation: true
#   (required for file capabilities to be honored)

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
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=4).read()"]

# Version passed as build arg
ARG VERSION=0.0.0-dev
ENV VERSION=${VERSION}

# Run as non-root user - file capabilities on nsenter allow this user
# to use nsenter with the required privileges
USER appuser

# Run sidecar
CMD ["python", "main.py"]
