# syntax=docker/dockerfile:1
# R execution environment with BuildKit optimizations.

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder stage - compile R packages
################################
FROM r-base:4.5.2 AS builder

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install build dependencies for R packages
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libcurl4-openssl-dev \
    libssl-dev \
    libxml2-dev \
    libfontconfig1-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libfreetype6-dev \
    libpng-dev \
    libtiff5-dev \
    libjpeg-dev \
    libcairo2-dev \
    libxt-dev \
    libx11-dev \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install all R packages using Posit Package Manager
# - amd64: Downloads pre-compiled binaries (~5 min)
# - arm64: Compiles from source but single layer avoids redundant dependency builds
RUN R -e "options(repos = c(CRAN = 'https://packagemanager.posit.co/cran/__linux__/trixie/latest')); \
    install.packages(c( \
        'dplyr', 'tidyr', 'data.table', 'magrittr', \
        'ggplot2', 'lattice', 'scales', 'Cairo', \
        'readr', 'readxl', 'writexl', 'jsonlite', 'xml2', \
        'MASS', 'survival', 'lubridate', 'stringr', 'glue' \
    ))"

################################
# Final stage - minimal runtime image
################################
FROM r-base:4.5.2 AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun R Environment" \
      org.opencontainers.image.description="Secure execution environment for R code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install ONLY runtime dependencies (no -dev packages)
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libcurl4 \
    libssl3 \
    libxml2 \
    libfontconfig1 \
    libharfbuzz0b \
    libfribidi0 \
    libfreetype6 \
    libpng16-16 \
    libtiff6 \
    libjpeg62-turbo \
    libcairo2 \
    libxt6 \
    libx11-6 \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy installed R packages from builder
COPY --from=builder /usr/local/lib/R/site-library /usr/local/lib/R/site-library

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
    "R_LIBS_USER=/usr/local/lib/R/site-library"]
CMD ["R", "--version"]
