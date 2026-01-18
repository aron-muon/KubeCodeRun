# syntax=docker/dockerfile:1
# Python execution environment with BuildKit optimizations.

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder stage - install packages with build tools
################################
FROM python:3.13-slim-trixie AS builder

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install build dependencies and runtime dependencies
# Build deps are needed for compiling native extensions
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    # Build tools (not needed in final image)
    gcc \
    g++ \
    make \
    pkg-config \
    python3-dev \
    # Development libraries (runtime libs installed in final stage)
    libxml2-dev \
    libxslt-dev \
    libffi-dev \
    libcairo2-dev \
    libpango1.0-dev \
    libgdk-pixbuf-2.0-dev \
    libssl-dev \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libopenjp2-7-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    tcl8.6-dev \
    tk8.6-dev \
    portaudio19-dev \
    libpulse-dev \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Configure pip and build tools
# PIP_NO_BUILD_ISOLATION=1: Use pre-installed build tools (setuptools, wheel) instead of
# downloading fresh copies for each package. This ensures consistent versions across all
# package builds and avoids compatibility issues with the pinned versions below.
ENV PIP_NO_BUILD_ISOLATION=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install pip and build tooling with compatible versions
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install \
    "pip<24.1" \
    "setuptools<70" \
    wheel \
    "packaging<24"

# Copy requirements files
COPY requirements/python-core.txt /tmp/python-core.txt
COPY requirements/python-analysis.txt /tmp/python-analysis.txt
COPY requirements/python-visualization.txt /tmp/python-visualization.txt
COPY requirements/python-documents.txt /tmp/python-documents.txt
COPY requirements/python-utilities.txt /tmp/python-utilities.txt

RUN --mount=type=cache,target=/root/.cache/pip \
     pip install \
     -r /tmp/python-core.txt \
     -r /tmp/python-analysis.txt \
     -r /tmp/python-visualization.txt \
     -r /tmp/python-documents.txt \
     -r /tmp/python-utilities.txt

################################
# Final stage - minimal runtime image
################################
FROM python:3.13-slim-trixie AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Python Environment" \
      org.opencontainers.image.description="Secure execution environment for Python code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install ONLY runtime dependencies (no -dev packages, no compilers)
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    # Runtime libraries (counterparts to -dev packages in builder)
    libxml2 \
    libxslt1.1 \
    libffi8 \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libssl3 \
    libjpeg62-turbo \
    libpng16-16 \
    libtiff6 \
    libopenjp2-7 \
    libfreetype6 \
    liblcms2-2 \
    libwebp7 \
    tcl8.6 \
    tk8.6 \
    python3-tk \
    libportaudio2 \
    libpulse0 \
    # External tools needed at runtime
    poppler-utils \
    tesseract-ocr \
    pandoc \
    ffmpeg \
    flac \
    antiword \
    unrtf \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

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
    "TMPDIR=/tmp", \
    "PYTHONUNBUFFERED=1", \
    "PYTHONDONTWRITEBYTECODE=1", \
    "PYTHONPATH=/mnt/data", \
    "MPLCONFIGDIR=/tmp/matplotlib"]
CMD ["sleep", "infinity"]
