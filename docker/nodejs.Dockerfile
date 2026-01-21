# syntax=docker/dockerfile:1
# Node.js execution environment with BuildKit optimizations.
# Uses Docker Hardened Images (DHI) for security.

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder stage - install packages with native addons
################################
FROM dhi.io/node:25.4-debian13-dev AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install build tools needed for native addons
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy package list
COPY requirements/nodejs.txt /tmp/nodejs.txt

# Install packages globally with cache mount
# hadolint ignore=SC2086
RUN --mount=type=cache,target=/root/.npm \
    packages="$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' /tmp/nodejs.txt)" && \
    if [ -n "$packages" ]; then npm install -g $packages; fi && \
    # Create version-agnostic symlink: /opt/node -> /opt/nodejs/node-v<version>
    ln -sf /opt/nodejs/node-* /opt/node

################################
# Runtime dependencies stage
################################
FROM dhi.io/node:25.4-debian13-dev AS runtime-deps

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Create directories with correct ownership for DHI non-root user (UID 65532)
# and multi-arch library paths
RUN mkdir -p /usr/lib/x86_64-linux-gnu /usr/lib/aarch64-linux-gnu /mnt/data && \
    chown 65532:65532 /mnt/data && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

################################
# Final stage - minimal runtime image
################################
FROM dhi.io/node:25.4-debian13 AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Node.js Environment" \
      org.opencontainers.image.description="Secure execution environment for JavaScript/TypeScript code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Copy runtime libraries (multi-arch support)
COPY --from=runtime-deps /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu
COPY --from=runtime-deps /usr/lib/aarch64-linux-gnu /usr/lib/aarch64-linux-gnu

# Copy git binary and dependencies for npm operations
COPY --from=runtime-deps /usr/bin/git /usr/bin/git
COPY --from=runtime-deps /usr/lib/git-core /usr/lib/git-core

# Copy /usr/bin/env for npm package shebangs and ENTRYPOINT
# Copy sleep for the default CMD (keep container alive for sidecar)
COPY --from=runtime-deps /usr/bin/env /usr/bin/sleep /usr/bin/

# Copy TypeScript runner script for shell-less execution
COPY scripts/ts-runner.js /opt/scripts/ts-runner.js

# Copy Node.js installation from builder
# /opt/node is a symlink to the versioned dir, provides version-agnostic paths
COPY --from=builder /opt/nodejs /opt/nodejs
COPY --from=builder /opt/node /opt/node

# Copy data directory with correct ownership (DHI UID 65532)
COPY --from=runtime-deps /mnt/data /mnt/data

WORKDIR /mnt/data

# Sanitized environment via env -i (no shell needed)
# Use /opt/node symlink for version-agnostic paths
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/opt/node/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "NODE_ENV=sandbox", \
    "NODE_PATH=/opt/node/lib/node_modules"]
CMD ["sleep", "infinity"]
