#!/usr/bin/env bash
# Remove generated TLS certificates from tests/tls-certs/.
#
# Usage:
#   cd tests/tls-certs && ./cleanup.sh
set -euo pipefail
cd "$(dirname "$0")"

rm -f ca.key ca.crt ca.srl ca-ext.cnf
rm -f redis.key redis.crt redis.csr redis-ext.cnf

echo "TLS certificates cleaned up."
