# syntax=docker/dockerfile:1
# Bash execution environment with EVERY supported language baked in.
#
# LibreChat @librechat/agents >= 3.1.74 collapsed `execute_code` and all
# per-language code-interpreter tools into a single `bash_tool` — the
# client no longer sends `lang: py | js | go | ...` to `/exec`; every
# request now arrives as `lang: bash`. To stay useful for those clients,
# the bash sandbox has to contain every interpreter and compiler so the
# model can `python3 -c "..."`, `node -e "..."`, `go run`, etc. from
# inside the shell.
#
# The dedicated per-language images still ship and are still served by
# `/exec` when a non-LC caller sends an explicit `lang: <code>`, so
# direct-API integrations are unaffected.
#
# Languages baked in (all 13):
#   bash, sh                         — apt: bash + coreutils + jq + grep + sed
#   python (py) + python data stack  — apt: python3 + python-is-python3 + numpy/pandas/etc.
#   javascript (js)                  — apt: nodejs
#   typescript (ts)                  — npm: typescript globally
#   go                               — apt: golang-go
#   java                             — apt: default-jdk-headless
#   c, cpp                           — apt: gcc, g++
#   php                              — apt: php-cli
#   rust (rs)                        — apt: rustc, cargo
#   r                                — apt: r-base-core
#   fortran (f90)                    — apt: gfortran
#   d                                — apt: ldc (uses gcc for linking)
#
# Size: ~1.5-2 GB vs ~100 MB for the bash-only image. Trade made deliberately
# so every LC bash_tool request stays inside the warm pod instead of
# erroring out on a missing interpreter.

# Global args — must be declared before any FROM so they can interpolate
# into the FROM lines. They lose scope inside the stages and are re-
# declared there as needed.
ARG RUNNER_IMAGE=ghcr.io/aron-muon/kubecoderun-runner:latest
ARG BASE_IMAGE=dhi.io/debian-base:trixie-debian13-dev

FROM ${RUNNER_IMAGE} AS runner

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Final stage - runtime image
################################
FROM ${BASE_IMAGE} AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun Bash Environment (all languages)" \
      org.opencontainers.image.description="LibreChat bash_tool sandbox — bash + every supported runtime (py, js, ts, go, java, c, cpp, php, rs, r, f90, d)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Single RUN to keep the layer count low. Order: shell utilities first, then
# the language families. `apt-get clean` + cache wipe at the end shaves
# ~50 MB off the final layer.
#
# python-is-python3 makes `python` resolve to /usr/bin/python3, matching
# the executor.go Args for the "py" language (which calls `python {file}`).
#
# python3-{numpy,pandas,matplotlib,openpyxl,pil} ship the data-analysis
# stack LLMs typically reach for via `python3 -c` inside bash_tool. Apt
# is preferred over pip here because (a) layered binary deps make the
# wheels reliable across base updates, and (b) we don't need bleeding-edge
# versions for inside-bash one-liners — the dedicated python image still
# serves `lang: "py"` calls that need pip.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    # --- bash + script utilities ---
    bash \
    coreutils \
    grep \
    sed \
    gawk \
    findutils \
    jq \
    ca-certificates \
    # --- python (interpreter + data-analysis stack) ---
    python3 \
    python3-pip \
    python-is-python3 \
    python3-numpy \
    python3-pandas \
    python3-matplotlib \
    python3-openpyxl \
    python3-pil \
    # --- node.js (js + ts) ---
    nodejs \
    npm \
    # --- go ---
    golang-go \
    # --- java ---
    default-jdk-headless \
    # --- c / c++ / linker (used by rust, fortran, d too) ---
    gcc \
    g++ \
    # --- php ---
    php-cli \
    # --- rust ---
    rustc \
    cargo \
    # --- r ---
    r-base-core \
    # --- fortran ---
    gfortran \
    # --- d (ldc compiler) ---
    ldc \
    && npm install -g typescript \
    && npm cache clean --force \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /root/.cache /tmp/* \
    && mkdir -p /mnt/data && chown 65532:65532 /mnt/data

WORKDIR /mnt/data

USER 65532

# Copy runner binary for code execution
COPY --from=runner /runner /usr/local/bin/runner

# Copy TypeScript runner script (used by executor.go for the "ts" language).
# Path is relative to the build context (docker/), matching nodejs.Dockerfile.
COPY scripts/ts-runner.js /opt/scripts/ts-runner.js

# Sanitized environment via env -i.
#
# HOME=/tmp is critical: rustc/cargo, go, R, javac, ldc, npm all write
# build artefacts / caches under $HOME. /mnt/data is the user-code
# working dir and shouldn't be polluted with .cache directories.
#
# GOCACHE/GOPATH and CARGO_HOME/RUSTUP_HOME are pinned under /tmp so
# multiple executions of go/rust on the same warm pod don't race over
# implicit defaults rooted at $HOME.
#
# JAVA_TOOL_OPTIONS=-Duser.home=/tmp keeps javac/java from trying to
# write to a non-existent home of the sandbox UID.
#
# PYTHON{UNBUFFERED,DONTWRITEBYTECODE} mirror the dedicated python image
# so stdout/stderr from `python3 -c` inside bash behaves identically.
#
# LANGUAGE=bash signals the runner's executor.go to drive the bash spec
# (i.e. write code to code.sh and exec `bash code.sh`). The model is
# free to shell out to any other interpreter from there.
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "GOCACHE=/tmp/go-cache", \
    "GOPATH=/tmp/go", \
    "CARGO_HOME=/tmp/cargo", \
    "RUSTUP_HOME=/tmp/rustup", \
    "JAVA_TOOL_OPTIONS=-Duser.home=/tmp", \
    "PYTHONUNBUFFERED=1", \
    "PYTHONDONTWRITEBYTECODE=1", \
    "MPLCONFIGDIR=/tmp/matplotlib", \
    "LANGUAGE=bash"]
CMD ["/usr/local/bin/runner"]
