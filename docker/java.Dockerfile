# syntax=docker/dockerfile:1
# Java execution environment with BuildKit optimizations.

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder stage - download and verify JARs
################################
FROM eclipse-temurin:25-jdk AS builder

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    && apt-get autoremove -y \
    && apt-get clean \
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
# Runtime stage - minimal image without download tools
################################
FROM eclipse-temurin:25-jdk

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Java Environment" \
      org.opencontainers.image.description="Secure execution environment for Java code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Ensure ownership of working directory
RUN chown -R codeuser:codeuser /mnt/data

# Switch to non-root user
USER codeuser

# Set environment variables with updated CLASSPATH
ENV JAVA_OPTS="-Xmx512m -Xms128m" \
    CLASSPATH="/mnt/data:/opt/java/lib/*"

# Default command with sanitized environment (include Java bin path)
ENTRYPOINT ["/usr/bin/env","-i","PATH=/opt/java/openjdk/bin:/usr/local/bin:/usr/bin:/bin","HOME=/tmp","TMPDIR=/tmp","CLASSPATH=/mnt/data:/opt/java/lib/*","JAVA_OPTS=-Xmx512m -Xms128m"]
CMD ["java", "--version"]
