# syntax=docker/dockerfile:1
# R execution environment with Docker Hardened Images.
# Uses debian-base since there is no DHI R image.

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder stage - install R and compile packages
################################
FROM dhi.io/debian-base:trixie-debian13-dev AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install R and build dependencies for R packages
# init-system-helpers required FIRST to fix x11-common postinst failures
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    init-system-helpers \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    r-base \
    r-base-dev \
    gcc g++ make pkg-config \
    libcurl4-openssl-dev libssl-dev libxml2-dev \
    libfontconfig1-dev libharfbuzz-dev libfribidi-dev \
    libfreetype-dev libpng-dev libtiff-dev libjpeg-dev \
    libcairo2-dev libxt-dev libx11-dev \
    && rm -rf /var/lib/apt/lists/*

# Install R packages using Posit Package Manager
RUN R -e "options(repos = c(CRAN = 'https://packagemanager.posit.co/cran/__linux__/trixie/latest')); \
    install.packages(c( \
        'dplyr', 'tidyr', 'data.table', 'magrittr', \
        'ggplot2', 'lattice', 'scales', 'Cairo', \
        'readr', 'readxl', 'writexl', 'jsonlite', 'xml2', \
        'MASS', 'survival', 'lubridate', 'stringr', 'glue' \
    ))"

################################
# Final stage - runtime image
################################
FROM dhi.io/debian-base:trixie-debian13-dev AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun R Environment" \
      org.opencontainers.image.description="Secure execution environment for R code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install runtime dependencies (no -dev packages)
# init-system-helpers required FIRST to fix x11-common postinst failures
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    init-system-helpers \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    r-base-core \
    libcurl4t64 libssl3t64 libxml2 \
    libfontconfig1 libharfbuzz0b libfribidi0 \
    libfreetype6 libpng16-16t64 libtiff6 libjpeg62-turbo \
    libcairo2 libxt6t64 libx11-6 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed R packages from builder
COPY --from=builder /usr/local/lib/R/site-library /usr/local/lib/R/site-library

RUN mkdir -p /mnt/data && chown 65532:65532 /mnt/data

WORKDIR /mnt/data

USER 65532

ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "R_LIBS_USER=/usr/local/lib/R/site-library"]
CMD ["sleep", "infinity"]
