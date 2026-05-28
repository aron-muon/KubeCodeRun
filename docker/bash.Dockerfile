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
# Languages baked in:
#   bash, sh                         — apt: bash + coreutils + jq + grep + sed
#   python (py) + full pip stack     — apt: python3 + python-is-python3
#                                      pip: docker/requirements/python-*.txt
#                                      (same lib set as the dedicated python image —
#                                      xlsxwriter, reportlab, python-docx, python-pptx,
#                                      pdf2image, mammoth, openpyxl, scikit-learn,
#                                      scipy, plotly, opencv, numpy, pandas, matplotlib)
#   javascript (js)                  — apt: nodejs
#   typescript (ts)                  — npm: typescript globally
#   go                               — apt: golang-go
#   java                             — apt: default-jdk-headless
#   c, cpp                           — apt: gcc, g++
#   php                              — apt: php-cli
#   rust (rs)                        — apt: rustc, cargo
#   d                                — apt: ldc (uses gcc for linking)
#
# Deliberately NOT in the bash image:
#   fortran (f90)                    — gfortran-14 wants stock
#                                      gcc-14-base=14.2.0-19 but DHI ships
#                                      14.2.0-19dhi0 (custom hardened build).
#                                      Unable to install alongside.
#   r                                — r-base-core transitively requires
#                                      liblapack3 → libgfortran5 → same
#                                      DHI gcc version conflict.
#
# Both ARE still served by /exec for explicit `lang: f90` / `lang: r`
# callers — the dedicated fortran/r per-language images handle those
# (LC's bash_tool collapse doesn't reach for fortran/r in practice).
#
# Size: ~3-4 GB. Big, but unavoidable: LC's bash_tool reaches for
# python3 -c with any library a python user would use, and a partial
# python stack means non-deterministic "sometimes works, sometimes
# ModuleNotFoundError" behaviour (depending on whether the model picked
# the python image via lang=py or shelled out via lang=bash → python3).
# Matching the dedicated python image's lib set is the only way to make
# the two execution paths indistinguishable to the user.

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

# Two-step install: first apt (interpreters/compilers + build deps the
# pip step may need), then pip (full python stack from the same
# requirement files the dedicated python image uses, for behaviour
# parity between `lang=py` and `lang=bash → python3 -c "..."`).
#
# python-is-python3 makes `python` resolve to /usr/bin/python3, matching
# the executor.go Args for the "py" language (which calls `python {file}`).
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
    bc \
    ca-certificates \
    # --- python (interpreter; lib stack installed via pip below) ---
    # No apt python3-numpy/pandas/etc. — they pull liblapack3 +
    # libgfortran5 which can't be installed alongside DHI's custom
    # gcc-14-base. pip-installed numpy/scipy/etc. bundle their own
    # openblas in the manylinux wheels so we don't need apt's BLAS.
    python3 \
    python3-pip \
    python-is-python3 \
    # cryptography / lxml etc. wheels dlopen these at runtime.
    libxml2 libxslt1.1 libffi8 libssl3t64 \
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
    make \
    # --- php ---
    php-cli \
    # --- rust ---
    rustc \
    cargo \
    # --- d (ldc compiler) ---
    # NOTE: r-base-core and gfortran intentionally omitted — they conflict
    # with DHI's custom gcc-14-base (see comment at top of file). Their
    # dedicated per-language images still serve explicit `lang=r` / `lang=f90`.
    ldc \
    && npm install -g typescript \
    && npm cache clean --force \
    # DHI's hardened libzstd1+dhi0 and libffi8+dhi0 can't be dlopen()ed
    # — Python's _ssl.so (links libzstd) and _ctypes.so (links libffi)
    # both fail to import with
    # `ImportError: libX: shared object cannot be dlopen()ed`.
    # Force-downgrade to stock Debian versions so dlopen works.
    # --allow-downgrades is required because the DHI versions are held.
    # Without this fix:
    #   - pip can't reach PyPI (SSL load fails) → no pip install
    #   - pandas / numpy / cryptography all fail at runtime (no ctypes)
    && apt-get install -y --no-install-recommends --allow-downgrades \
        libzstd1=1.5.7+dfsg-1 \
        libffi8=3.4.8-2 \
    && mkdir -p /mnt/data && chown 65532:65532 /mnt/data

# Pip-install the full python lib stack — same requirement files the
# dedicated python image uses — so `python3 -c` inside bash_tool sees
# the same modules as a native `lang=py` call. Without this the user
# experience is "sometimes works, sometimes ModuleNotFoundError"
# depending on which tool path LC's agent picked.
#
# --break-system-packages: Debian's pip is PEP-668-locked; we override
# because this is a sandbox-builder image, not a user OS install.
# --no-cache-dir: shave ~500 MB off the final layer.
#
# IMPORTANT: pip install MUST happen BEFORE any apt-get autoremove +
# /var/lib/apt/lists cleanup. The Debian python3-pip package shells out
# to ca-certificates and a couple of apt-managed lib paths to do SSL
# verification on its first run; if autoremove fires between the apt
# RUN and this RUN it strips deps that python3-pip needs at runtime
# ("SSL module is not available" pip errors). Sequence is: apt install
# → pip install → apt cleanup, all in this stage.
COPY requirements/python-core.txt requirements/python-analysis.txt \
     requirements/python-visualization.txt requirements/python-documents.txt \
     requirements/python-utilities.txt /tmp/reqs/
# Skip `pip install --upgrade pip setuptools wheel` — Debian ships them
# pre-installed without RECORD files so pip refuses to uninstall them
# ("Cannot uninstall wheel 0.46.1 — no RECORD file"). The apt-installed
# versions are recent enough for the wheel-only requirement files.
RUN python3 -m pip install --break-system-packages --no-cache-dir \
        -r /tmp/reqs/python-core.txt \
        -r /tmp/reqs/python-analysis.txt \
        -r /tmp/reqs/python-visualization.txt \
        -r /tmp/reqs/python-documents.txt \
        -r /tmp/reqs/python-utilities.txt \
    && rm -rf /tmp/reqs /root/.cache \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/*

# yq — YAML/JSON/XML/TOML processor (static Go binary, no system deps)
ADD --chmod=755 https://github.com/mikefarah/yq/releases/download/v4.53.2/yq_linux_amd64 /usr/local/bin/yq

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
