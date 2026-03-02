# syntax=docker/dockerfile:1
# C/C++ execution environment with Docker Hardened Images.
# Uses -dev variant because compilers and dev libraries must be available at runtime.

FROM dhi.io/debian-base:trixie-debian13-dev

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun C/C++ Environment" \
      org.opencontainers.image.description="Secure execution environment for C/C++ code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install compilers, development tools and scientific libraries
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    cmake \
    # Math and science libraries
    libgsl-dev \
    libblas-dev \
    liblapack-dev \
    # File handling libraries
    libzip-dev \
    zlib1g-dev \
    # JSON library
    nlohmann-json3-dev \
    # CSV library
    libcsv-dev \
    # Additional utilities
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /mnt/data && chown 65532:65532 /mnt/data

WORKDIR /mnt/data

USER 65532

# Sanitized environment
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "CC=gcc", \
    "CXX=g++", \
    "PKG_CONFIG_PATH=/usr/lib/x86_64-linux-gnu/pkgconfig"]
CMD ["sleep", "infinity"]
