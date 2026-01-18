# syntax=docker/dockerfile:1
# Rust execution environment with BuildKit optimizations
FROM rust:1.92.0-slim-trixie

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Rust Environment" \
      org.opencontainers.image.description="Secure execution environment for Rust code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    libssl-dev \
    libfontconfig1-dev \
    libfreetype6-dev \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a temporary project to pre-compile and cache crates
WORKDIR /tmp/rust-cache

# Copy Cargo.toml for crate caching
COPY requirements/rust-Cargo.toml Cargo.toml

# Create minimal src/main.rs (cargo init would fail since Cargo.toml exists)
RUN mkdir -p src && echo 'fn main() {}' > src/main.rs

# Fetch crate dependencies into cache
# cargo fetch downloads dependencies without compiling, which is faster and
# more reliable for cache warming (no system library dependencies needed)
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    cargo fetch

# Clean up the temporary project but keep the cargo cache
WORKDIR /
RUN rm -rf /tmp/rust-cache

# Create non-root user with UID/GID 1001
RUN groupadd -g 1001 codeuser && \
    useradd -r -u 1001 -g codeuser codeuser && \
    mkdir -p /mnt/data && chown codeuser:codeuser /mnt/data

WORKDIR /mnt/data

# Switch to non-root user
USER codeuser

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "CARGO_HOME=/usr/local/cargo", \
    "RUSTUP_HOME=/usr/local/rustup"]
CMD ["rustc", "--version"]
