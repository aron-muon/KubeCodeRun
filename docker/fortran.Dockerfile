# syntax=docker/dockerfile:1.4
# Fortran execution environment
FROM debian:trixie-slim

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Fortran Environment" \
      org.opencontainers.image.description="Secure execution environment for Fortran code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"
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
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with UID/GID 1001
RUN groupadd -g 1001 codeuser && \
    useradd -r -u 1001 -g codeuser codeuser

# Set working directory and ensure ownership
WORKDIR /mnt/data
RUN chown -R codeuser:codeuser /mnt/data

# Switch to non-root user
USER codeuser

# Set environment variables
ENV FORTRAN_COMPILER=gfortran \
    FC=gfortran \
    F77=gfortran \
    F90=gfortran \
    F95=gfortran

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env","-i","PATH=/usr/local/bin:/usr/bin:/bin","HOME=/tmp","TMPDIR=/tmp","FORTRAN_COMPILER=gfortran","FC=gfortran","F77=gfortran","F90=gfortran","F95=gfortran"]
CMD ["gfortran", "--version"]
