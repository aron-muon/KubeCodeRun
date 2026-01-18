# syntax=docker/dockerfile:1
# D execution environment
FROM debian:trixie-slim

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun D Environment" \
      org.opencontainers.image.description="Secure execution environment for D (ldc2) code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install toolchain (ldc2) and basics
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates \
      git \
      build-essential \
      ldc \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with UID/GID 1001
RUN groupadd -g 1001 codeuser && \
    useradd -r -u 1001 -g codeuser codeuser && \
    mkdir -p /mnt/data && chown codeuser:codeuser /mnt/data

WORKDIR /mnt/data

# Switch to non-root user
USER codeuser

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp"]
CMD ["ldc2", "--version"]
