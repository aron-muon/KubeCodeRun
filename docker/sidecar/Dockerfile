# syntax=docker/dockerfile:1
# KubeCodeRun HTTP sidecar with Docker Hardened Images.

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder stage - install Python dependencies and runtime tools
################################
FROM dhi.io/python:3.13-debian13-dev AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# Install runtime dependencies and set up nsenter with file capabilities
# - util-linux: provides nsenter for entering container namespaces
# - libcap2-bin: provides setcap for setting file capabilities
# Create both arch lib dirs to ensure COPY works on either architecture
RUN mkdir -p /lib/x86_64-linux-gnu /lib/aarch64-linux-gnu && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    util-linux \
    libcap2-bin \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /mnt/data && chown 65532:65532 /mnt/data

# Add file capabilities to nsenter binary so non-root users can use it
# - cap_sys_ptrace: access /proc/<pid>/ns/ of other processes
# - cap_sys_admin: call setns() to enter namespaces
# - cap_sys_chroot: required for mount namespace operations
RUN setcap 'cap_sys_ptrace,cap_sys_admin,cap_sys_chroot+eip' /usr/bin/nsenter

WORKDIR /app

# Install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements.txt

# Copy application code
COPY main.py .

################################
# Final stage - minimal runtime image
################################
FROM dhi.io/python:3.13-debian13 AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Sidecar" \
      org.opencontainers.image.description="HTTP sidecar for executing code in Kubernetes pods via nsenter" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Copy nsenter with file capabilities from builder
COPY --from=builder /usr/bin/nsenter /usr/bin/

# Copy shared libraries needed by nsenter (libselinux, libpcre2) for both architectures
# Note: Only one arch directory will have content depending on build platform
COPY --from=builder /lib/x86_64-linux-gnu /lib/x86_64-linux-gnu
COPY --from=builder /lib/aarch64-linux-gnu /lib/aarch64-linux-gnu

# Copy installed Python packages from builder
# DHI Python is installed in /opt/python, not /usr/local
COPY --from=builder /opt/python/lib/python3.13/site-packages /opt/python/lib/python3.13/site-packages
COPY --from=builder /opt/python/bin /opt/python/bin

# Copy /usr/bin/env for execution patterns, sleep for CMD
COPY --from=builder /usr/bin/env /usr/bin/sleep /usr/bin/

# Copy data directory with correct ownership (DHI uses UID 65532)
COPY --from=builder /mnt/data /mnt/data

# Copy application code
COPY --from=builder /app /app

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

# Kubernetes pod spec still requires:
# - shareProcessNamespace: true (so sidecar can see main container's processes)
# - securityContext.capabilities.add: ["SYS_PTRACE", "SYS_ADMIN", "SYS_CHROOT"]
#   (to allow the bounding set to include these caps)
# - securityContext.allowPrivilegeEscalation: true
#   (required for file capabilities to be honored)

# DHI images run as non-root (UID 65532) by default
# File capabilities on nsenter allow this user to use nsenter with required privileges

# Run sidecar
CMD ["python", "main.py"]
