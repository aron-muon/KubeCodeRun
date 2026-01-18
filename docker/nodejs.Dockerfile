# syntax=docker/dockerfile:1
# Node.js execution environment with BuildKit optimizations.

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder stage - install packages with native addons
################################
FROM node:25-alpine AS builder

# Install build tools needed for native addons
RUN apk add --no-cache \
    python3 \
    make \
    g++

# Copy package list
COPY requirements/nodejs.txt /tmp/nodejs.txt

# Install packages globally with cache mount
# hadolint ignore=SC2086
RUN --mount=type=cache,target=/root/.npm \
    packages="$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' /tmp/nodejs.txt)" && \
    if [ -n "$packages" ]; then npm install -g $packages; fi

################################
# Final stage - minimal runtime image
################################
FROM node:25-alpine AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Node.js Environment" \
      org.opencontainers.image.description="Secure execution environment for JavaScript/TypeScript code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Install only runtime dependencies (git for npm operations)
RUN apk add --no-cache git

# Copy pre-installed global packages from builder
COPY --from=builder /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=builder /usr/local/bin /usr/local/bin

# Create non-root user with UID/GID 1001
RUN addgroup -g 1001 codeuser && \
    adduser -u 1001 -G codeuser -D -H -S codeuser && \
    mkdir -p /mnt/data && chown codeuser:codeuser /mnt/data

WORKDIR /mnt/data

# Switch to non-root user
USER codeuser

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "NODE_ENV=sandbox", \
    "NODE_PATH=/usr/local/lib/node_modules"]
CMD ["node"]
