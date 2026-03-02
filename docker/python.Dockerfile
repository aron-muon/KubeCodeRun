# syntax=docker/dockerfile:1
# Python execution environment with Docker Hardened Images.

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder stage - install packages with build tools
################################
FROM dhi.io/python:3.14-debian13-dev AS builder

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install build dependencies
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
    libssl-dev \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libopenjp2-7-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
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
# Runtime dependencies stage - install runtime libraries
################################
FROM dhi.io/python:3.14-debian13-dev AS runtime-deps

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install ONLY runtime dependencies (no -dev packages, no compilers)
# Create both arch lib dirs to ensure COPY works on either architecture
RUN mkdir -p /usr/lib/x86_64-linux-gnu /usr/lib/aarch64-linux-gnu && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    # Core System Utilities
    libgomp1 \
    liblz4-1 \
    # Image Processing (Pillow, OpenCV)
    ffmpeg \
    libjpeg62-turbo \
    libpng16-16t64 \
    libtiff6 \
    libwebp7 \
    libopenjp2-7 \
    liblcms2-2 \
    # XML/HTML Processing (lxml, beautifulsoup4)
    libxml2 \
    libxslt1.1 \
    # Cryptography (cryptography, PyOpenSSL)
    libffi8 \
    libssl3t64 \
    # Font Support (Matplotlib, WordCloud)
    libfreetype6 \
    fontconfig \
    # External Tools (Runtime executables)
    poppler-utils \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /mnt/data && chown 65532:65532 /mnt/data


################################
# Final stage - minimal runtime image
################################
FROM dhi.io/python:3.14-debian13 AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Python Environment" \
      org.opencontainers.image.description="Secure execution environment for Python code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Copy runtime libraries from runtime-deps stage
COPY --from=runtime-deps /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu
COPY --from=runtime-deps /usr/lib/aarch64-linux-gnu /usr/lib/aarch64-linux-gnu
COPY --from=runtime-deps /usr/bin/pdftotext /usr/bin/pdftoppm /usr/bin/pdfinfo /usr/bin/
COPY --from=runtime-deps /usr/bin/ffmpeg /usr/bin/ffprobe /usr/bin/

# Copy installed Python packages from builder
# DHI Python is installed in /opt/python, not /usr/local
COPY --from=builder /opt/python/lib/python3.14/site-packages /opt/python/lib/python3.14/site-packages
COPY --from=builder /opt/python/bin /opt/python/bin

# Copy /usr/bin/env for sidecar's /usr/bin/env -i execution pattern
# Copy sleep for the default CMD (keep container alive for sidecar)
COPY --from=runtime-deps /usr/bin/env /usr/bin/sleep /usr/bin/

# Create data directory - DHI images run as non-root (UID 65532) by default
COPY --from=runtime-deps /mnt/data /mnt/data

WORKDIR /mnt/data

# Sanitized environment via env -i
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/opt/python/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "PYTHONUNBUFFERED=1", \
    "PYTHONDONTWRITEBYTECODE=1", \
    "PYTHONPATH=/mnt/data", \
    "MPLCONFIGDIR=/tmp/matplotlib"]
CMD ["sleep", "infinity"]
