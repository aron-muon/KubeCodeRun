# syntax=docker/dockerfile:1
# R execution environment with Docker Hardened Images.
#
# DHI ships gcc-14-base=14.2.0-19dhi0 which conflicts with stock Debian's
# r-base-core → libopenblas → libgfortran5 → gcc-14-base (= 14.2.0-19).
# Solution: equivs dummy for gcc-14-base, install only r-base-core (NOT
# r-base or r-base-dev which pull gfortran-14 → gcc-14 and gir1.2-glib-2.0
# → libglib2.0-0t64, both of which conflict with DHI packages).
#
# R packages are installed as pre-compiled binaries from Posit Package
# Manager (PPM), eliminating the need for r-base-dev/compilation headers.

ARG RUNNER_IMAGE=ghcr.io/aron-muon/kubecoderun-runner:latest
FROM ${RUNNER_IMAGE} AS runner

FROM dhi.io/debian-base:trixie-debian13-dev

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun R Environment" \
      org.opencontainers.image.description="Secure execution environment for R code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install r-base-core via equivs dummy to resolve DHI gcc-14-base conflict.
# Only r-base-core — NOT r-base (pulls gir1.2-glib-2.0 DHI conflict) or
# r-base-dev (pulls gfortran-14 → gcc-14 DHI conflict).
# Runtime shared libraries for R packages are also installed here.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends equivs && \
    printf 'Section: misc\nPriority: optional\nStandards-Version: 3.9.2\nPackage: gcc-14-base-dummy\nVersion: 14.2.0-19\nProvides: gcc-14-base (= 14.2.0-19)\nDescription: Satisfies gcc-14-base version constraint on DHI\n' > /tmp/gcc-14-base-dummy && \
    cd /tmp && equivs-build gcc-14-base-dummy && \
    dpkg -i gcc-14-base-dummy_14.2.0-19_all.deb && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    r-base-core \
    # Runtime shared libs needed by common R packages
    libcurl4t64 libssl3t64 libxml2 \
    libfontconfig1 libharfbuzz0b libfribidi0 \
    libfreetype6 libpng16-16t64 libtiff6 libjpeg62-turbo \
    libcairo2 libxt6t64 libx11-6 \
    && apt-get purge -y equivs && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/* /tmp/*.deb /tmp/gcc-14-base-dummy

# Install R packages as pre-compiled binaries from Posit Package Manager
RUN Rscript -e "options(repos = c(CRAN = 'https://packagemanager.posit.co/cran/__linux__/bookworm/latest')); \
    install.packages(c( \
        'dplyr', 'tidyr', 'data.table', 'magrittr', \
        'ggplot2', 'lattice', 'scales', 'Cairo', \
        'readr', 'readxl', 'writexl', 'jsonlite', 'xml2', \
        'MASS', 'survival', 'lubridate', 'stringr', 'glue' \
    ), lib='/usr/local/lib/R/site-library')"

RUN mkdir -p /mnt/data && chown 65532:65532 /mnt/data

WORKDIR /mnt/data

USER 65532

ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "R_LIBS_USER=/usr/local/lib/R/site-library", \
    "LANGUAGE=r"]
# Copy runner binary for code execution
COPY --from=runner /runner /usr/local/bin/runner

CMD ["/usr/local/bin/runner"]
