# syntax=docker/dockerfile:1
# PHP execution environment with Docker Hardened Images.
# Strategy: Copy PHP from DHI PHP image into DHI debian-base where equivs
# works correctly for installing dev dependencies needed to compile extensions.

ARG RUNNER_IMAGE=ghcr.io/aron-muon/kubecoderun-runner:latest
FROM ${RUNNER_IMAGE} AS runner

# Source for PHP binaries
FROM dhi.io/php:8.5.6-debian13-dev AS php-source

################################
# Main image based on debian-base (equivs works here for gcc-14-base conflict)
################################
FROM dhi.io/debian-base:trixie-debian13-dev

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun PHP Environment" \
      org.opencontainers.image.description="Secure execution environment for PHP code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Copy PHP installation from DHI PHP image
COPY --from=php-source /opt/php-8.5 /opt/php-8.5
# Copy shared libraries PHP depends on (avoids chasing individual packages)
COPY --from=php-source /usr/lib/x86_64-linux-gnu/libargon2.so* /usr/lib/x86_64-linux-gnu/
COPY --from=php-source /usr/lib/x86_64-linux-gnu/libsodium.so* /usr/lib/x86_64-linux-gnu/
COPY --from=php-source /usr/lib/x86_64-linux-gnu/libicu*.so* /usr/lib/x86_64-linux-gnu/
COPY --from=php-source /usr/lib/x86_64-linux-gnu/libonig.so* /usr/lib/x86_64-linux-gnu/

# Put PHP in PATH for build steps
ENV PATH="/opt/php-8.5/bin:${PATH}"

# Install build deps for PHP extensions (GD, zip) + runtime deps + Composer prereqs
# DHI ships gcc-14-base=14.2.0-19dhi0; equivs dummy satisfies stock deps needing =14.2.0-19
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends equivs && \
    printf 'Section: misc\nPriority: optional\nStandards-Version: 3.9.2\nPackage: gcc-14-base-dummy\nVersion: 14.2.0-19\nProvides: gcc-14-base (= 14.2.0-19)\nDescription: Satisfies gcc-14-base version constraint on DHI\n' > /tmp/gcc-14-base-dummy && \
    cd /tmp && equivs-build gcc-14-base-dummy && \
    dpkg -i gcc-14-base-dummy_14.2.0-19_all.deb && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    # Build deps for extensions
    make \
    gcc \
    autoconf \
    pkg-config \
    libpng-dev \
    libjpeg-dev \
    libfreetype-dev \
    libzip-dev \
    libonig-dev \
    libpcre2-dev \
    # Runtime deps for PHP binary (apt provides transitive deps for curl/ssl/xml/etc)
    libcurl4t64 \
    libssl3t64 \
    libxml2 \
    libsqlite3-0 \
    libreadline8t64 \
    libgmp10 \
    libzip5 \
    libonig5 \
    # Composer prereqs
    unzip \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* /tmp/*.deb /tmp/gcc-14-base-dummy

# Compile GD extension with JPEG/PNG/Freetype support
RUN ldconfig && \
    which php && php -v && \
    cd /tmp && \
    php_src_version=$(php -r 'echo PHP_VERSION;') && \
    PHP_INI_DIR=$(php --ini | grep "Scan for additional" | sed 's/.*: //' | tr -d ' "') && \
    mkdir -p "${PHP_INI_DIR}" && \
    curl -sSL "https://github.com/php/php-src/archive/refs/tags/php-${php_src_version}.tar.gz" | tar xz && \
    cd "php-src-php-${php_src_version}/ext/gd" && \
    phpize && \
    ./configure --with-jpeg --with-png --with-freetype && \
    make -j"$(nproc)" && make install && \
    echo "extension=gd.so" > "${PHP_INI_DIR}/20-gd.ini" && \
    cd /tmp && rm -rf php-src-*

# Compile zip extension
RUN cd /tmp && \
    php_src_version=$(php -r 'echo PHP_VERSION;') && \
    PHP_INI_DIR=$(php --ini | grep "Scan for additional" | sed 's/.*: //' | tr -d ' "') && \
    mkdir -p "${PHP_INI_DIR}" && \
    curl -sSL "https://github.com/php/php-src/archive/refs/tags/php-${php_src_version}.tar.gz" | tar xz && \
    cd "php-src-php-${php_src_version}/ext/zip" && \
    phpize && \
    ./configure && \
    make -j"$(nproc)" && make install && \
    echo "extension=zip.so" > "${PHP_INI_DIR}/20-zip.ini" && \
    cd /tmp && rm -rf php-src-*

# Install Composer with signature verification
RUN mkdir -p /usr/local/bin && \
    EXPECTED_CHECKSUM="$(php -r 'copy("https://composer.github.io/installer.sig", "php://stdout");')" && \
    php -r "copy('https://getcomposer.org/installer', 'composer-setup.php');" && \
    ACTUAL_CHECKSUM="$(php -r "echo hash_file('sha384', 'composer-setup.php');")" && \
    if [ "$EXPECTED_CHECKSUM" != "$ACTUAL_CHECKSUM" ]; then \
        echo 'ERROR: Invalid Composer installer checksum' >&2; \
        rm composer-setup.php; \
        exit 1; \
    fi && \
    php composer-setup.php --install-dir=/usr/local/bin --filename=composer && \
    rm composer-setup.php

# Create composer directory and install packages
RUN mkdir -p /opt/composer/global
ENV COMPOSER_HOME=/opt/composer/global

# Pre-install PHP packages globally
RUN --mount=type=cache,target=/opt/composer/global/cache \
    composer global require \
    league/csv \
    phpoffice/phpspreadsheet \
    league/flysystem \
    intervention/image \
    ramsey/uuid \
    nesbot/carbon \
    markrogoyski/math-php \
    guzzlehttp/guzzle \
    symfony/yaml \
    symfony/console \
    --optimize-autoloader && \
    # Auto-include Composer autoloader so packages work without manual require
    PHP_INI_DIR=$(php --ini | grep "Scan for additional" | sed 's/.*: //' | tr -d ' "') && \
    mkdir -p "${PHP_INI_DIR}" && \
    echo "auto_prepend_file=/opt/composer/global/vendor/autoload.php" > "${PHP_INI_DIR}/99-autoload.ini"

# Clean up build deps to reduce image size
RUN apt-get purge -y \
    make \
    gcc \
    autoconf \
    pkg-config \
    equivs \
    libpng-dev \
    libjpeg-dev \
    libfreetype-dev \
    libzip-dev \
    libonig-dev \
    libpcre2-dev \
    && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/* /etc/apt/preferences.d/no-stock-gcc && \
    ldconfig

# Copy runner binary for code execution
COPY --from=runner /runner /usr/local/bin/runner

RUN mkdir -p /mnt/data && chown 65532:65532 /mnt/data

WORKDIR /mnt/data

USER 65532

# Sanitized environment via env -i
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/opt/composer/global/vendor/bin:/opt/php-8.5/bin:/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "COMPOSER_HOME=/opt/composer/global", \
    "LANGUAGE=php"]
CMD ["/usr/local/bin/runner"]
