# syntax=docker/dockerfile:1
# Fortran execution environment with Docker Hardened Images

FROM dhi.io/debian-base:trixie

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Fortran Environment" \
      org.opencontainers.image.description="Secure execution environment for Fortran code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install Fortran compiler and scientific libraries
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    gfortran \
    cmake \
    make \
    libblas-dev \
    liblapack-dev \
    libnetcdf-dev \
    libhdf5-dev \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /mnt/data && chown 65532:65532 /mnt/data

WORKDIR /mnt/data

USER 65532

# Sanitized environment via env -i (required for sidecar runtime env detection)
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "FORTRAN_COMPILER=gfortran", \
    "FC=gfortran", \
    "F77=gfortran", \
    "F90=gfortran", \
    "F95=gfortran"]
CMD ["sleep", "infinity"]
