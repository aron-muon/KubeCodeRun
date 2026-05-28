# syntax=docker/dockerfile:1
# Fortran execution environment with Docker Hardened Images.
#
# DHI ships gcc-14-base=14.2.0-19dhi0 which conflicts with stock Debian's
# gfortran → libgfortran5 → gcc-14-base (= 14.2.0-19) dependency chain.
# Solution: use equivs to create a dummy package satisfying the version
# constraint, then install gfortran-12 normally via apt.

ARG RUNNER_IMAGE=ghcr.io/aron-muon/kubecoderun-runner:latest
FROM ${RUNNER_IMAGE} AS runner

FROM dhi.io/debian-base:trixie-debian13-dev

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

# Install gfortran-12 via equivs dummy package to resolve DHI gcc-14-base conflict
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends equivs && \
    printf 'Section: misc\nPriority: optional\nStandards-Version: 3.9.2\nPackage: gcc-14-base-dummy\nVersion: 14.2.0-19\nProvides: gcc-14-base (= 14.2.0-19)\nDescription: Satisfies gcc-14-base version constraint on DHI\n' > /tmp/gcc-14-base-dummy && \
    cd /tmp && equivs-build gcc-14-base-dummy && \
    dpkg -i gcc-14-base-dummy_14.2.0-19_all.deb && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    gfortran-12 \
    cmake \
    make \
    && apt-get purge -y equivs && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/* /tmp/*.deb /tmp/gcc-14-base-dummy

# Create symlink so 'gfortran' command works
RUN mkdir -p /usr/local/bin && ln -sf /usr/bin/gfortran-12 /usr/local/bin/gfortran

RUN mkdir -p /mnt/data && chown 65532:65532 /mnt/data

WORKDIR /mnt/data

USER 65532

# Sanitized environment via env -i
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "FORTRAN_COMPILER=gfortran", \
    "FC=gfortran", \
    "F77=gfortran", \
    "F90=gfortran", \
    "F95=gfortran", \
    "LANGUAGE=f90"]
# Copy runner binary for code execution
COPY --from=runner /runner /usr/local/bin/runner

CMD ["/usr/local/bin/runner"]
