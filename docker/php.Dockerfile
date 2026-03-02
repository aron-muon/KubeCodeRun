# syntax=docker/dockerfile:1
# PHP execution environment with Docker Hardened Images.

# PHP version configuration - single source of truth
ARG PHP_VERSION=8.5.3
ARG PHP_MAJOR=8.5
ARG DEBIAN_VERSION=debian13

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder stage - install Composer and packages
################################
FROM dhi.io/php:${PHP_VERSION}-${DEBIAN_VERSION}-dev AS builder

# Re-declare ARGs needed in this stage
ARG PHP_VERSION
ARG PHP_MAJOR

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# PHP paths in DHI image
# DHI installs PHP at /opt/php-<major.minor>, we create /opt/php symlink for version-agnostic paths
ENV PHP_VERSIONED_HOME=/opt/php-${PHP_MAJOR}
ENV PHP_HOME=/opt/php
ENV PHP_BIN=${PHP_VERSIONED_HOME}/bin/php
ENV PHP_CONFIG=${PHP_VERSIONED_HOME}/bin/php-config
ENV PHP_IZE=${PHP_VERSIONED_HOME}/bin/phpize
ENV PECL=${PHP_VERSIONED_HOME}/bin/pecl
ENV PHP_INI_DIR=${PHP_VERSIONED_HOME}/etc/conf.d

# Install build dependencies for PHP extensions and Composer packages
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    # Build tools
    gcc \
    g++ \
    make \
    autoconf \
    pkg-config \
    # GD dependencies
    libpng-dev \
    libjpeg-dev \
    libfreetype6-dev \
    # Zip dependencies
    libzip-dev \
    libpcre2-dev \
    # Other tools
    unzip \
    wget \
    ca-certificates \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install extensions:
# - zip via PECL
# - gd from PHP source (bundled extension, must compile)
# Use php-config --extension-dir to dynamically get the correct path
RUN set -eux; \
    # Update PECL channel
    $PECL channel-update pecl.php.net; \
    # Install zip via PECL
    $PECL install zip; \
    # Download PHP source for GD (bundled extension)
    wget -q "https://www.php.net/distributions/php-${PHP_VERSION}.tar.xz" -O /tmp/php.tar.xz; \
    cd /tmp && tar -xf php.tar.xz; \
    # Build GD extension
    cd /tmp/php-${PHP_VERSION}/ext/gd; \
    $PHP_IZE; \
    ./configure --with-php-config=$PHP_CONFIG --with-freetype --with-jpeg; \
    make -j"$(nproc)"; \
    make install; \
    # Clean up source
    rm -rf /tmp/php*; \
    # Create extension configuration dynamically
    EXT_DIR=$($PHP_CONFIG --extension-dir); \
    mkdir -p $PHP_INI_DIR; \
    echo "extension_dir=${EXT_DIR}" > $PHP_INI_DIR/extensions.ini; \
    echo "extension=zip.so" >> $PHP_INI_DIR/extensions.ini; \
    echo "extension=gd.so" >> $PHP_INI_DIR/extensions.ini; \
    # Create version-agnostic symlink: /opt/php -> /opt/php-<major.minor>
    ln -sf $PHP_VERSIONED_HOME /opt/php

# Install Composer with signature verification
# Create /usr/local/bin since DHI images don't have it
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

# Create composer directory structure
RUN mkdir -p /opt/composer/global

# Set composer home and PHP_INI_SCAN_DIR for extension loading
ENV COMPOSER_HOME=/opt/composer/global
ENV PHP_INI_SCAN_DIR=${PHP_INI_DIR}

# Verify extensions are loaded
RUN php -m | grep -E "^(gd|zip)$"

# Pre-install PHP packages globally with cache mount
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
    echo "auto_prepend_file=/opt/composer/global/vendor/autoload.php" >> $PHP_INI_DIR/autoload.ini

################################
# Runtime dependencies stage - install runtime libraries
################################
ARG PHP_VERSION
ARG DEBIAN_VERSION
FROM dhi.io/php:${PHP_VERSION}-${DEBIAN_VERSION}-dev AS runtime-deps

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install ONLY runtime dependencies (no -dev packages)
# Create both arch lib dirs to ensure COPY works on either architecture
RUN mkdir -p /usr/lib/x86_64-linux-gnu /usr/lib/aarch64-linux-gnu && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    # Runtime libraries for gd extension
    libpng16-16t64 \
    libjpeg62-turbo \
    libfreetype6 \
    # Runtime library for zip extension
    libzip5 \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /mnt/data && chown 65532:65532 /mnt/data

################################
# Final stage - minimal runtime image
################################
ARG PHP_VERSION
ARG PHP_MAJOR
ARG DEBIAN_VERSION
FROM dhi.io/php:${PHP_VERSION}-${DEBIAN_VERSION} AS final

# Re-declare ARGs needed in this stage (PHP_MAJOR used in COPY commands)
ARG PHP_MAJOR
ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun PHP Environment" \
      org.opencontainers.image.description="Secure execution environment for PHP code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Copy runtime libraries from runtime-deps stage
COPY --from=runtime-deps /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu
COPY --from=runtime-deps /usr/lib/aarch64-linux-gnu /usr/lib/aarch64-linux-gnu

# Copy PHP installation from builder
# /opt/php is a symlink to the versioned dir, provides version-agnostic paths
COPY --from=builder /opt/php-${PHP_MAJOR}/lib/php/extensions/ /opt/php-${PHP_MAJOR}/lib/php/extensions/
COPY --from=builder /opt/php-${PHP_MAJOR}/etc/conf.d/ /opt/php-${PHP_MAJOR}/etc/conf.d/
COPY --from=builder /opt/php /opt/php

# Copy pre-installed composer packages with correct ownership
COPY --from=builder --chown=65532:65532 /opt/composer/global /opt/composer/global

# Copy /usr/bin/env for sidecar's /usr/bin/env -i execution pattern
# Copy sleep for the default CMD (keep container alive for sidecar)
COPY --from=runtime-deps /usr/bin/env /usr/bin/sleep /usr/bin/

# Copy data directory with correct ownership - DHI images run as non-root (UID 65532)
COPY --from=runtime-deps /mnt/data /mnt/data

WORKDIR /mnt/data

# Sanitized environment via env -i
# Use /opt/php symlink for version-agnostic paths
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/opt/composer/global/vendor/bin:/opt/php/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "COMPOSER_HOME=/opt/composer/global", \
    "PHP_INI_SCAN_DIR=/opt/php/etc/conf.d"]
CMD ["sleep", "infinity"]
