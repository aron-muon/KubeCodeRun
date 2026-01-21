# syntax=docker/dockerfile:1
# Rust execution environment with Docker Hardened Images.
#
# Pre-compiled crates from rust-Cargo.toml are available without recompilation.
# CARGO_NET_OFFLINE=true prevents runtime downloads (security hardening).
# Pure Rust crates NOT in rust-Cargo.toml will fail to compile.

################################
# Builder stage - compile crate dependencies
################################
FROM dhi.io/rust:1.92-debian13-dev AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Set cargo home explicitly (DHI image doesn't set CARGO_HOME by default)
ENV CARGO_HOME=/usr/local/cargo

# Build headers - required to compile crates with native C bindings
# These produce .rlib files that link against runtime libs below
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    libssl-dev \
    libfontconfig1-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

# Create a temporary project to pre-compile crate dependencies
WORKDIR /tmp/rust-cache

# Copy Cargo.toml for crate caching
COPY requirements/rust-Cargo.toml Cargo.toml

# Create minimal src/main.rs
RUN mkdir -p src && echo 'fn main() {}' > src/main.rs

# Build in release mode to pre-compile all dependencies
# This links native C libraries so they don't need headers at runtime
RUN cargo build --release

# Clean up source but keep compiled artifacts
RUN rm -rf /tmp/rust-cache/src /tmp/rust-cache/Cargo.toml /tmp/rust-cache/Cargo.lock

################################
# Final stage - runtime only
################################
FROM dhi.io/rust:1.92-debian13-dev AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Rust Environment" \
      org.opencontainers.image.description="Secure execution environment for Rust code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Runtime libraries only - no -dev packages (reduced attack surface)
# These are linked by the pre-compiled crates: image, plotters (freetype/fontconfig)
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libssl3t64 \
    libfontconfig1 \
    libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

# Copy entire cargo home (registry, config, env) and pre-compiled target
COPY --from=builder /usr/local/cargo /usr/local/cargo
COPY --from=builder --chown=65532:65532 /tmp/rust-cache/target /usr/local/cargo/target

# Create data directory with correct ownership for DHI non-root user (UID 65532)
RUN mkdir -p /mnt/data && chown 65532:65532 /mnt/data && \
    chown -R 65532:65532 /usr/local/cargo/target

WORKDIR /mnt/data

# DHI -dev images default to root; switch to non-root user (UID 65532)
USER 65532

# Sanitized environment via env -i
# CARGO_NET_OFFLINE=true prevents dependency confusion attacks (no runtime downloads)
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "CARGO_HOME=/usr/local/cargo", \
    "CARGO_TARGET_DIR=/usr/local/cargo/target", \
    "CARGO_NET_OFFLINE=true", \
    "RUSTUP_HOME=/usr/local/rustup"]
CMD ["sleep", "infinity"]
