#!/usr/bin/env bash
# Generate self-signed TLS certificates for Redis Cluster integration testing.
#
# Creates:
#   ca.key / ca.crt    — Certificate Authority (with keyUsage extensions for Python 3.14+)
#   redis.key / redis.crt — Server cert signed by the CA (SANs for localhost + docker IPs)
#
# Usage:
#   cd tests/tls-certs && ./generate.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "Generating CA key + certificate..."
cat > ca-ext.cnf << 'EOF'
[req]
default_bits = 4096
prompt = no
distinguished_name = dn
x509_extensions = v3_ca

[dn]
C = PT
ST = Lisboa
L = Lisboa
O = NOS Testing
CN = Redis Test CA

[v3_ca]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
EOF

openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -out ca.crt -config ca-ext.cnf 2>/dev/null

echo "Generating server key + certificate..."
cat > redis-ext.cnf << 'EOF'
[req]
default_bits = 2048
prompt = no
distinguished_name = dn
req_extensions = v3_req

[dn]
C = PT
ST = Lisboa
L = Lisboa
O = NOS Testing
CN = redis-node

[v3_req]
subjectAltName = @alt_names
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth, clientAuth

[alt_names]
DNS.1 = redis-tls-node-0
DNS.2 = redis-tls-node-1
DNS.3 = redis-tls-node-2
DNS.4 = redis-tls-node-3
DNS.5 = redis-tls-node-4
DNS.6 = redis-tls-node-5
DNS.7 = localhost
IP.1 = 127.0.0.1
IP.2 = 172.17.0.1
IP.3 = 172.18.0.1
IP.4 = 172.19.0.1
IP.5 = 172.20.0.1
IP.6 = 172.21.0.1
IP.7 = 172.22.0.1
IP.8 = 172.23.0.1
IP.9 = 172.24.0.1
IP.10 = 172.25.0.1
EOF

openssl genrsa -out redis.key 2048 2>/dev/null
openssl req -new -key redis.key -out redis.csr -config redis-ext.cnf 2>/dev/null
openssl x509 -req -in redis.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out redis.crt -days 3650 -sha256 \
  -extfile redis-ext.cnf -extensions v3_req 2>/dev/null

# Redis needs world-readable key files (containers run as redis user)
chmod 644 redis.key ca.key

echo "Verifying certificate chain..."
openssl verify -CAfile ca.crt redis.crt

echo "Done. Certificates generated in $(pwd)/"
