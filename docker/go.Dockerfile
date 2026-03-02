# syntax=docker/dockerfile:1
# Go execution environment using Docker Hardened Images.

################################
# Stage 1: Build and download dependencies
FROM dhi.io/golang:1.26-debian13-dev AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install build tools
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git \
    make \
    gcc \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy go.mod for pre-downloading
COPY requirements/go.mod /tmp/gosetup/go.mod

# Pre-download common Go packages (no cache mount - modules must persist in image)
RUN cd /tmp/gosetup && \
    go mod download && \
    rm -rf /tmp/gosetup

################################
# Stage 2: Prepare runtime directories
FROM dhi.io/golang:1.26-debian13-dev AS runtime-deps

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Create data and cache directories with correct ownership (DHI uses UID 65532)
RUN mkdir -p /mnt/data /mnt/data/go-build && chown -R 65532:65532 /mnt/data

################################
# Stage 3: Minimal runtime image
FROM dhi.io/golang:1.26-debian13 AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Go Environment" \
      org.opencontainers.image.description="Secure execution environment for Go code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Copy pre-downloaded Go modules from builder (chown to non-root user for write access)
COPY --from=builder --chown=65532:65532 /go/pkg/mod /go/pkg/mod

# Copy data directory with correct ownership
COPY --from=runtime-deps /mnt/data /mnt/data

# Copy env for ENTRYPOINT, sleep for default CMD
COPY --from=runtime-deps /usr/bin/env /usr/bin/sleep /usr/bin/

WORKDIR /mnt/data

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "GO111MODULE=on", \
    "GOPROXY=https://proxy.golang.org,direct", \
    "GOSUMDB=sum.golang.org", \
    "GOCACHE=/mnt/data/go-build", \
    "GOMODCACHE=/go/pkg/mod"]
CMD ["sleep", "infinity"]
