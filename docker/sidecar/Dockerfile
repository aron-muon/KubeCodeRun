# syntax=docker/dockerfile:1
# KubeCodeRun HTTP Sidecar — Multi-target Dockerfile.
#
# Produces two distinct container images via Docker build targets:
#
#   docker build --target sidecar-agent   → kubecoderun-sidecar-agent (default)
#   docker build --target sidecar-nsenter → kubecoderun-sidecar-nsenter
#
# sidecar-agent (default):
#   - Contains the executor-agent Go binary (copied to main container via init container)
#   - No nsenter, no setcap, no capabilities, no privilege escalation
#   - Compatible with GKE Sandbox (gVisor) and restricted Pod Security Standards
#
# sidecar-nsenter (legacy):
#   - Contains nsenter with file capabilities (setcap) for namespace entry
#   - Requires shareProcessNamespace, SYS_PTRACE/SYS_ADMIN/SYS_CHROOT capabilities,
#     and allowPrivilegeEscalation: true in the pod spec
#   - For clusters that do not support agent mode or need legacy behavior

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Executor agent build stage — statically compiled Go binary.
# This binary runs in the main (language) container via init container copy,
# providing HTTP-based code execution without nsenter.
################################
FROM golang:1.26-alpine AS agent-builder

WORKDIR /build
COPY executor-agent/ .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -trimpath -o /opt/executor-agent .

################################
# Python builder (common) — install Python dependencies and app code.
# Used by both agent and nsenter targets.
################################
FROM dhi.io/python:3.13-debian13-dev AS builder-common

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# Create data directory and arch lib dirs (for COPY compatibility)
RUN mkdir -p /lib/x86_64-linux-gnu /lib/aarch64-linux-gnu && \
    mkdir -p /mnt/data && chown 65532:65532 /mnt/data

WORKDIR /app

# Install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements.txt

# Copy application code
COPY main.py .

################################
# nsenter builder — extends common builder with nsenter + file capabilities.
# Only needed for the nsenter sidecar target.
################################
FROM builder-common AS builder-nsenter

# Install nsenter (util-linux) and setcap (libcap2-bin)
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    util-linux \
    libcap2-bin \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Add file capabilities to nsenter binary so non-root users can use it
# - cap_sys_ptrace: access /proc/<pid>/ns/ of other processes
# - cap_sys_admin: call setns() to enter namespaces
# - cap_sys_chroot: required for mount namespace operations
RUN setcap 'cap_sys_ptrace,cap_sys_admin,cap_sys_chroot+eip' /usr/bin/nsenter


################################
# Common runtime base — shared by both final targets.
# Contains Python runtime, sidecar app, and common configuration.
################################
FROM dhi.io/python:3.13-debian13 AS runtime-base

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

# Copy installed Python packages from builder
# DHI Python is installed in /opt/python, not /usr/local
COPY --from=builder-common /opt/python/lib/python3.13/site-packages /opt/python/lib/python3.13/site-packages
COPY --from=builder-common /opt/python/bin /opt/python/bin

# Copy data directory with correct ownership (DHI uses UID 65532)
COPY --from=builder-common /mnt/data /mnt/data

# Copy application code
COPY --from=builder-common /app /app

WORKDIR /app

# Place Python at the front of the path
ENV PATH="/opt/python/bin:/usr/bin:/bin"

# Expose sidecar port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=4).read()"]

# Version and runtime config
ARG VERSION=0.0.0-dev
ENV VERSION=${VERSION} \
    WORKING_DIR=/mnt/data \
    LANGUAGE=python \
    SIDECAR_PORT=8080 \
    MAX_EXECUTION_TIME=120 \
    PYTHONUNBUFFERED=1

# DHI images run as non-root (UID 65532) by default
CMD ["python", "main.py"]


################################
# TARGET: sidecar-agent (default)
#
# Agent mode sidecar — the recommended execution mode.
# Contains the executor-agent Go binary for init-container distribution.
# No nsenter, no capabilities, no privilege escalation needed.
#
# Kubernetes pod spec (agent mode):
#   - No shareProcessNamespace needed
#   - No capabilities needed (all dropped)
#   - allowPrivilegeEscalation: false for all containers
#   - Init container copies /opt/executor-agent to shared volume
#   - Main container runs executor-agent instead of sleep infinity
#   - Compatible with GKE Sandbox (gVisor) and restricted Pod Security Standards
#
# Build: docker build --target sidecar-agent -t kubecoderun-sidecar-agent .
################################
FROM runtime-base AS sidecar-agent

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Sidecar (Agent)" \
      org.opencontainers.image.description="HTTP sidecar for executing code in Kubernetes pods via executor agent" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Copy executor agent binary (distributed to main container via init container)
COPY --from=agent-builder /opt/executor-agent /opt/executor-agent

ENV EXECUTION_MODE=agent


################################
# TARGET: sidecar-nsenter (legacy)
#
# nsenter mode sidecar — for backward compatibility.
# Contains nsenter with file capabilities for namespace entry.
#
# Kubernetes pod spec (nsenter mode):
#   - shareProcessNamespace: true (so sidecar can see main container's processes)
#   - securityContext.capabilities.add: ["SYS_PTRACE", "SYS_ADMIN", "SYS_CHROOT"]
#   - securityContext.allowPrivilegeEscalation: true
#     (required for file capabilities to be honored)
#
# Build: docker build --target sidecar-nsenter -t kubecoderun-sidecar-nsenter .
################################
FROM runtime-base AS sidecar-nsenter

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Sidecar (nsenter)" \
      org.opencontainers.image.description="HTTP sidecar for executing code in Kubernetes pods via nsenter" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Copy nsenter with file capabilities from nsenter builder
COPY --from=builder-nsenter /usr/bin/nsenter /usr/bin/

# Copy /usr/bin/env from builder — nsenter mode uses /usr/bin/env -i for clean execution
COPY --from=builder-common /usr/bin/env /usr/bin/env

# Copy shared libraries needed by nsenter (libselinux, libpcre2) for both architectures
# Note: Only one arch directory will have content depending on build platform
COPY --from=builder-nsenter /lib/x86_64-linux-gnu /lib/x86_64-linux-gnu
COPY --from=builder-nsenter /lib/aarch64-linux-gnu /lib/aarch64-linux-gnu

ENV EXECUTION_MODE=nsenter
