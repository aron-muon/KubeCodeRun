# syntax=docker/dockerfile:1
# Java execution environment with Docker Hardened Images!

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder stage - download and verify JARs
################################
FROM dhi.io/eclipse-temurin:25.0-jdk-debian13-dev AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy dependency manifest with checksums
COPY requirements/java-deps.txt /build/java-deps.txt

# Download JARs and verify SHA-256 checksums
# hadolint ignore=SC2086
RUN set -eux; \
    mkdir -p /build/lib; \
    while IFS= read -r line; do \
        # Skip comments and blank lines
        case "$line" in \
            \#*|"") continue ;; \
        esac; \
        set -- $line; \
        url=$1; \
        expected_sha=$2; \
        filename=${url##*/}; \
        echo "Downloading $filename..."; \
        wget -q -O "/build/lib/$filename" "$url"; \
        actual_sha="$(sha256sum "/build/lib/$filename")"; \
        actual_sha="${actual_sha%% *}"; \
        if [ "$actual_sha" != "$expected_sha" ]; then \
            echo "ERROR: Checksum mismatch for $filename"; \
            echo "  Expected: $expected_sha"; \
            echo "  Actual:   $actual_sha"; \
            exit 1; \
        fi; \
        echo "  Verified: $filename"; \
    done < /build/java-deps.txt

################################
# Final stage
################################
FROM dhi.io/eclipse-temurin:25.0-jdk-debian13-dev AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Java Environment" \
      org.opencontainers.image.description="Secure execution environment for Java code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Copy verified JARs from builder
COPY --from=builder /build/lib /opt/java/lib

# Create data directory with correct ownership for DHI non-root user (UID 65532)
RUN mkdir -p /mnt/data && chown 65532:65532 /mnt/data

WORKDIR /mnt/data

# DHI -dev images default to root; switch to non-root user (UID 65532)
USER 65532

# Sanitized environment via env -i
# DHI eclipse-temurin installs Java to /usr/local/bin
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "CLASSPATH=/mnt/data:/opt/java/lib/*", \
    "JAVA_OPTS=-Xmx512m -Xms128m"]
CMD ["sleep", "infinity"]
