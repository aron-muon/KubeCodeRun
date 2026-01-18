# syntax=docker/dockerfile:1
# PHP execution environment with BuildKit optimizations.

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

################################
# Builder stage - compile extensions and install packages
################################
FROM php:8.4-cli-trixie AS builder

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install build dependencies and compile PHP extensions
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libzip-dev \
    libpng-dev \
    libjpeg-dev \
    libfreetype6-dev \
    libonig-dev \
    libxml2-dev \
    unzip \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j"$(nproc)" \
        xml \
        zip \
        gd \
        mbstring \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Composer with signature verification
# See: https://getcomposer.org/doc/faqs/how-to-install-composer-programmatically.md
RUN EXPECTED_CHECKSUM="$(php -r 'copy("https://composer.github.io/installer.sig", "php://stdout");')" && \
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

# Set composer home directory
ENV COMPOSER_HOME=/opt/composer/global

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
    --optimize-autoloader

################################
# Final stage - minimal runtime image
################################
FROM php:8.4-cli-trixie AS final

ARG BUILD_DATE
ARG VERSION
ARG VCS_REF

LABEL org.opencontainers.image.title="KubeCodeRun PHP Environment" \
      org.opencontainers.image.description="Secure execution environment for PHP code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

# Enable pipefail for safer pipe operations
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install ONLY runtime dependencies (no -dev packages)
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libzip5 \
    libpng16-16 \
    libjpeg62-turbo \
    libfreetype6 \
    libonig5 \
    libxml2 \
    unzip \
    git \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled PHP extensions from builder
COPY --from=builder /usr/local/lib/php/extensions/ /usr/local/lib/php/extensions/
COPY --from=builder /usr/local/etc/php/conf.d/ /usr/local/etc/php/conf.d/

# Copy Composer
COPY --from=builder /usr/local/bin/composer /usr/local/bin/composer

# Copy pre-installed composer packages
COPY --from=builder /opt/composer/global /opt/composer/global

# Create non-root user with UID/GID 1001
RUN groupadd -g 1001 codeuser && \
    useradd -r -u 1001 -g codeuser codeuser && \
    mkdir -p /mnt/data && chown codeuser:codeuser /mnt/data && \
    chown -R codeuser:codeuser /opt/composer/global

WORKDIR /mnt/data

# Switch to non-root user
USER codeuser

# Default command with sanitized environment
ENTRYPOINT ["/usr/bin/env", "-i", \
    "PATH=/opt/composer/global/vendor/bin:/usr/local/bin:/usr/bin:/bin", \
    "HOME=/tmp", \
    "TMPDIR=/tmp", \
    "COMPOSER_HOME=/opt/composer/global", \
    "PHP_INI_SCAN_DIR=/usr/local/etc/php/conf.d"]
CMD ["php", "-a"]
