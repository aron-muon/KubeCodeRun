# Configuration Guide

This document provides comprehensive information about configuring the Code Interpreter API.

## Overview

The Code Interpreter API uses environment-based configuration with sensible defaults. All configuration options can be set via environment variables or a `.env` file.

## Quick Start

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your specific settings:

   ```bash
   # At minimum, change the API key
   API_KEY=your-secure-api-key-here
   ```

3. Validate your configuration:
   ```bash
   python config_manager.py validate
   ```

## Configuration Sections

### API Configuration

Controls the basic API server settings.

| Variable     | Default   | Description                               |
| ------------ | --------- | ----------------------------------------- |
| `API_HOST`   | `0.0.0.0` | Host to bind the API server               |
| `API_PORT`   | `8000`    | Port for the API server                   |
| `API_DEBUG`  | `false`   | Enable debug mode (disable in production) |
| `API_RELOAD` | `false`   | Enable auto-reload for development        |

### SSL/HTTPS Configuration

Configures SSL/TLS support for secure HTTPS connections.

#### Docker Deployments

| Variable         | Default  | Description                                              |
| ---------------- | -------- | -------------------------------------------------------- |
| `ENABLE_HTTPS`   | `false`  | Enable HTTPS/SSL support                                 |
| `HTTPS_PORT`     | `443`    | HTTPS server port                                        |
| `SSL_CERTS_PATH` | `./ssl`  | Host path to directory containing `cert.pem` and `key.pem` |
| `SSL_REDIRECT`   | `false`  | Redirect HTTP traffic to HTTPS                           |

> **Note:** When using Docker, the certificate files are automatically mapped to `/app/ssl/` inside the container. You only need to set `SSL_CERTS_PATH` to point to your certificates directory on the host.

#### Non-Docker Deployments

| Variable         | Default  | Description                                              |
| ---------------- | -------- | -------------------------------------------------------- |
| `ENABLE_HTTPS`   | `false`  | Enable HTTPS/SSL support                                 |
| `HTTPS_PORT`     | `443`    | HTTPS server port                                        |
| `SSL_CERT_FILE`  | -        | Absolute path to SSL certificate file (.pem)             |
| `SSL_KEY_FILE`   | -        | Absolute path to SSL private key file (.pem)             |
| `SSL_CA_CERTS`   | -        | Path to CA certificates file (optional)                  |
| `SSL_REDIRECT`   | `false`  | Redirect HTTP traffic to HTTPS                           |

**HTTPS Setup (Docker):**

1. **Generate or obtain SSL certificates**:

   ```bash
   # For development (self-signed certificate)
   mkdir ssl
   openssl req -x509 -newkey rsa:4096 -nodes -out ssl/cert.pem -keyout ssl/key.pem -days 365

   # For production, use certificates from a trusted CA
   ```

2. **Configure HTTPS in .env**:

   ```bash
   ENABLE_HTTPS=true
   HTTPS_PORT=443
   SSL_REDIRECT=true  # Optional: redirect HTTP to HTTPS
   
   # If using the default ./ssl directory, no additional config needed.
   # If your certs are elsewhere, set the path:
   # SSL_CERTS_PATH=/path/to/your/ssl/certs
   ```

   The directory must contain files named `cert.pem` and `key.pem`.

3. **Deploy with Docker Compose**:
   ```bash
   docker-compose up -d
   ```

**HTTPS Setup (Non-Docker):**

```bash
ENABLE_HTTPS=true
HTTPS_PORT=443
SSL_CERT_FILE=/absolute/path/to/cert.pem
SSL_KEY_FILE=/absolute/path/to/key.pem
SSL_REDIRECT=true
```

**Security Notes:**

- Use certificates from trusted Certificate Authorities in production
- Keep private keys secure and never commit them to version control
- Consider using Let's Encrypt for free SSL certificates
- Enable `SSL_REDIRECT` to automatically redirect HTTP to HTTPS

### Authentication Configuration

Manages API key authentication and security.

| Variable             | Default        | Description                                      |
| -------------------- | -------------- | ------------------------------------------------ |
| `API_KEY`            | `test-api-key` | Primary API key (CHANGE IN PRODUCTION)           |
| `API_KEYS`           | -              | Additional API keys (comma-separated)            |
| `API_KEY_HEADER`     | `x-api-key`    | HTTP header name for API key                     |
| `API_KEY_CACHE_TTL`  | `300`          | API key validation cache TTL (seconds)           |
| `MASTER_API_KEY`     | -              | Master API key for admin operations (CLI, admin) |
| `RATE_LIMIT_ENABLED` | `true`         | Enable per-key rate limiting for Redis keys      |

**Security Notes:**

- API keys should be at least 16 characters long
- Use cryptographically secure random keys in production
- Consider rotating API keys regularly
- The `MASTER_API_KEY` is required for admin dashboard and CLI key management

### Redis Configuration

Redis is used for session management and caching. Three deployment modes are supported:
**standalone** (default), **cluster**, and **sentinel** — all with optional TLS/SSL.

#### Connection Settings

| Variable                       | Default       | Description                                              |
| ------------------------------ | ------------- | -------------------------------------------------------- |
| `REDIS_MODE`                   | `standalone`  | Deployment mode: `standalone`, `cluster`, or `sentinel`  |
| `REDIS_HOST`                   | `localhost`   | Redis server hostname                                    |
| `REDIS_PORT`                   | `6379`        | Redis server port                                        |
| `REDIS_PASSWORD`               | -             | Redis password (if required)                             |
| `REDIS_DB`                     | `0`           | Redis database number (standalone/sentinel only)         |
| `REDIS_URL`                    | -             | Complete Redis URL (overrides individual settings)       |
| `REDIS_MAX_CONNECTIONS`        | `20`          | Maximum connections in pool                              |
| `REDIS_SOCKET_TIMEOUT`         | `5`           | Socket timeout (seconds)                                 |
| `REDIS_SOCKET_CONNECT_TIMEOUT` | `5`           | Connection timeout (seconds)                             |
| `REDIS_KEY_PREFIX`             | -             | Optional prefix prepended to every Redis key (e.g. `prod:`) |

**Example Redis URL:**

```
REDIS_URL=redis://password@localhost:6379/0
```

#### Redis Cluster Mode

Use `REDIS_MODE=cluster` when running against a Redis Cluster deployment (e.g. GCP Memorystore Cluster, AWS ElastiCache Cluster Mode).

| Variable               | Default | Description                                                                 |
| ---------------------- | ------- | --------------------------------------------------------------------------- |
| `REDIS_CLUSTER_NODES`  | -       | Comma-separated `host:port` pairs for cluster startup nodes                 |

> **Note:** `REDIS_DB` is ignored in cluster mode (Redis Cluster only supports database 0).

**Example:**

```bash
REDIS_MODE=cluster
REDIS_CLUSTER_NODES=node1:6379,node2:6379,node3:6379
REDIS_PASSWORD=your-cluster-password
```

#### Redis Sentinel Mode

Use `REDIS_MODE=sentinel` for high-availability setups with Redis Sentinel.

| Variable                   | Default    | Description                                                  |
| -------------------------- | ---------- | ------------------------------------------------------------ |
| `REDIS_SENTINEL_NODES`     | -          | Comma-separated `host:port` pairs for Sentinel instances     |
| `REDIS_SENTINEL_MASTER`    | `mymaster` | Name of the Sentinel-monitored master                        |
| `REDIS_SENTINEL_PASSWORD`  | -          | Password for authenticating to Sentinel instances             |

**Example:**

```bash
REDIS_MODE=sentinel
REDIS_SENTINEL_NODES=sentinel1:26379,sentinel2:26379,sentinel3:26379
REDIS_SENTINEL_MASTER=mymaster
REDIS_PASSWORD=your-redis-password
REDIS_SENTINEL_PASSWORD=your-sentinel-password
```

#### Redis TLS/SSL

Enable TLS for encrypted connections. Required by most managed Redis services (GCP Memorystore, AWS ElastiCache, Azure Cache for Redis).

| Variable                       | Default | Description                                                      |
| ------------------------------ | ------- | ---------------------------------------------------------------- |
| `REDIS_TLS_ENABLED`            | `false` | Enable TLS/SSL for Redis connections                             |
| `REDIS_TLS_CA_CERT_FILE`       | -       | Path to CA certificate for verifying the server                  |
| `REDIS_TLS_CERT_FILE`          | -       | Path to client TLS certificate (mutual TLS)                     |
| `REDIS_TLS_KEY_FILE`           | -       | Path to client TLS private key (mutual TLS)                     |
| `REDIS_TLS_INSECURE`           | `false` | Skip TLS certificate verification (NOT recommended)             |
| `REDIS_TLS_CHECK_HOSTNAME`     | `false` | Verify server hostname against certificate CN/SAN                |

> When `REDIS_TLS_ENABLED=true` the generated URL uses the `rediss://` scheme automatically.
>
> **Security note:** `REDIS_TLS_CHECK_HOSTNAME` is `false` by default because managed Redis services
> (GCP Memorystore, AWS ElastiCache) and Redis Cluster node discovery expose IP addresses
> that do not match certificate CN/SAN entries. The CA certificate chain is still fully
> validated. For environments where Redis hostnames match their certificates, set
> `REDIS_TLS_CHECK_HOSTNAME=true` for stronger TLS authentication.

**Example — GCP Memorystore with TLS:**

```bash
REDIS_HOST=10.0.0.3
REDIS_PORT=6378
REDIS_TLS_ENABLED=true
REDIS_TLS_CA_CERT_FILE=/etc/ssl/redis/server-ca.pem
```

**Example — GCP Memorystore Cluster:**

```bash
REDIS_MODE=cluster
REDIS_CLUSTER_NODES=10.0.0.3:6379,10.0.0.4:6379,10.0.0.5:6379
REDIS_TLS_ENABLED=true
REDIS_TLS_CA_CERT_FILE=/etc/ssl/redis/server-ca.pem
```

### MinIO/S3 Configuration

MinIO provides S3-compatible object storage for files.

| Variable           | Default                  | Description                                 |
| ------------------ | ------------------------ | ------------------------------------------- |
| `MINIO_ENDPOINT`   | `localhost:9000`         | MinIO server endpoint (no protocol)         |
| `MINIO_ACCESS_KEY` | (required)               | MinIO access key (required when not using IAM) |
| `MINIO_SECRET_KEY` | (required)               | MinIO secret key (required when not using IAM) |
| `MINIO_SECURE`     | `false`                  | Use HTTPS for MinIO connections             |
| `MINIO_BUCKET`     | `kubecoderun-files` | Bucket name for file storage                |
| `MINIO_REGION`     | `us-east-1`              | MinIO region                                |
| `MINIO_USE_IAM`    | `false`                  | Use IAM credentials instead of keys         |

### Kubernetes Configuration

Kubernetes is used for secure code execution in isolated pods.

| Variable               | Default                                      | Description                              |
| ---------------------- | -------------------------------------------- | ---------------------------------------- |
| `K8S_NAMESPACE`        | `""` (uses API's namespace)                  | Namespace for execution pods             |
| `K8S_SIDECAR_IMAGE`    | `aronmuon/kubecoderun-sidecar-agent:latest` | HTTP sidecar image for pod communication |
| `K8S_IMAGE_REGISTRY`   | `aronmuon/kubecoderun`              | Registry prefix for language images      |
| `K8S_IMAGE_TAG`        | `latest`                                     | Image tag for language images            |
| `K8S_CPU_LIMIT`        | `1`                                          | CPU limit per execution pod              |
| `K8S_MEMORY_LIMIT`     | `512Mi`                                      | Memory limit per execution pod           |
| `K8S_CPU_REQUEST`      | `100m`                                       | CPU request per execution pod            |
| `K8S_MEMORY_REQUEST`   | `128Mi`                                      | Memory request per execution pod         |
| `K8S_EXECUTION_MODE`   | `agent`                                      | Execution mode: `agent` (default) or `nsenter` |
| `K8S_EXECUTOR_PORT`    | `9090`                                       | Port for the executor HTTP server inside the main container |
| `K8S_IMAGE_PULL_POLICY`| `Always`                                     | Image pull policy for execution pods (`Always`, `IfNotPresent`, `Never`) |
| `K8S_IMAGE_PULL_SECRETS`| `""`                                        | Comma-separated list of Kubernetes secret names for pulling images from private registries |

**Image Pull Secrets:**

When using private container registries, create Kubernetes secrets in the execution namespace and reference them via `K8S_IMAGE_PULL_SECRETS`:

```bash
# Create the secret
kubectl create secret docker-registry my-registry-secret \
  --docker-server=ghcr.io \
  --docker-username=<user> \
  --docker-password=<token> \
  -n <execution-namespace>

# Configure the API
K8S_IMAGE_PULL_SECRETS=my-registry-secret
# Multiple secrets: K8S_IMAGE_PULL_SECRETS=secret1,secret2
```

The secrets are applied to all dynamically created execution pods (both warm pool pods and on-demand Job pods).

**Execution Modes:**

- **`agent` (default):** A lightweight Go HTTP server runs inside the main container. The sidecar forwards execution requests via localhost. No `nsenter`, no capabilities, no privilege escalation. Compatible with GKE Sandbox (gVisor) and restricted Pod Security Standards.
- **`nsenter` (legacy):** The sidecar uses `nsenter` to enter the main container's mount namespace. Requires `shareProcessNamespace`, `SYS_PTRACE`/`SYS_ADMIN`/`SYS_CHROOT` capabilities, and `allowPrivilegeEscalation: true`. Use only on clusters that allow privilege escalation.

**Security Notes:**

- Both containers run with `runAsNonRoot: true` and `runAsUser: 65532`
- In agent mode: all capabilities are dropped, `allowPrivilegeEscalation: false` for all containers
- In nsenter mode: the sidecar uses file capabilities (`setcap`) on the `nsenter` binary to allow non-root namespace entry
- Network policies deny all egress by default
- Pods are destroyed immediately after execution
- See [SECURITY.md](SECURITY.md) for detailed explanation of the security model

#### Sidecar Container Images

The sidecar Dockerfile produces two distinct images via Docker build targets. Use the image that matches your configured `K8S_EXECUTION_MODE`:

| Build Target | Image Name | Execution Mode | Description |
|-------------|------------|---------------|-------------|
| `sidecar-agent` (default) | `kubecoderun-sidecar-agent` | `agent` | Contains executor-agent binary; no nsenter, no capabilities |
| `sidecar-nsenter` | `kubecoderun-sidecar-nsenter` | `nsenter` | Contains nsenter with file capabilities (setcap) |

**Building the images:**

```bash
# Agent mode sidecar (default, recommended):
docker build --target sidecar-agent \
  -t kubecoderun-sidecar-agent:latest \
  -f docker/sidecar/Dockerfile docker/sidecar/

# nsenter mode sidecar (legacy):
docker build --target sidecar-nsenter \
  -t kubecoderun-sidecar-nsenter:latest \
  -f docker/sidecar/Dockerfile docker/sidecar/

# Or use the build script (builds both automatically):
./scripts/build-images.sh sidecar-agent     # agent mode sidecar
./scripts/build-images.sh sidecar-nsenter   # nsenter mode sidecar
./scripts/build-images.sh                   # all images (both sidecars)
```

**Helm chart configuration:**

Update `values.yaml` to use the correct sidecar image for your execution mode:

```yaml
execution:
  executionMode: "agent"  # or "nsenter"
  sidecar:
    # For agent mode (default):
    repository: ghcr.io/your-org/kubecoderun-sidecar-agent
    # For nsenter mode:
    # repository: ghcr.io/your-org/kubecoderun-sidecar-nsenter
```

### GKE Sandbox (gVisor) Configuration

[GKE Sandbox](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/sandbox-pods) provides kernel-level isolation using gVisor to protect the host kernel from untrusted code. It is **only compatible with agent execution mode**.

| Variable                            | Default   | Description                                        |
| ----------------------------------- | --------- | -------------------------------------------------- |
| `GKE_SANDBOX_ENABLED`               | `false`   | Enable GKE Sandbox (gVisor) for execution pods     |
| `GKE_SANDBOX_RUNTIME_CLASS`         | `gvisor`  | RuntimeClass name for sandboxed pods               |
| `GKE_SANDBOX_NODE_SELECTOR`         | `{}`      | JSON node selector for sandbox nodes               |
| `GKE_SANDBOX_CUSTOM_TOLERATIONS`    | `[]`      | JSON array of custom tolerations for sandbox nodes  |

**Requirements:**

- `K8S_EXECUTION_MODE=agent` (nsenter is **incompatible** with gVisor)
- GKE cluster with a sandbox-enabled node pool (`--sandbox type=gvisor`)
- At least two node pools — one with GKE Sandbox enabled, one without
- Container-Optimized OS with containerd (`cos_containerd`) node image

**Example configuration:**

```bash
K8S_EXECUTION_MODE=agent
GKE_SANDBOX_ENABLED=true
GKE_SANDBOX_RUNTIME_CLASS=gvisor
# Schedule on specific sandbox node pool:
GKE_SANDBOX_NODE_SELECTOR={"pool":"sandbox"}
GKE_SANDBOX_CUSTOM_TOLERATIONS=[{"key":"pool","value":"sandbox","operator":"Equal","effect":"NoSchedule"}]
```

**Key limitations of GKE Sandbox** (see [GKE docs](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/sandbox-pods#limitations)):

- Incompatible with `nsenter` execution mode, privileged containers, and `shareProcessNamespace` (all avoided in agent mode)
- Seccomp, AppArmor, and SELinux not applicable inside the sandbox
- HostPath volumes and port-forwarding not supported
- Container-level memory metrics not available (pod-level metrics are)

### Resource Limits

#### Execution Limits

| Variable             | Default | Description                                                      |
| -------------------- | ------- | ---------------------------------------------------------------- |
| `MAX_EXECUTION_TIME` | `30`    | Maximum code execution time (seconds)                            |
| `MAX_MEMORY_MB`      | `512`   | Maximum memory per execution (MB)                                |
| `MAX_CPU_QUOTA`      | `50000` | CPU quota (100000 = 1 CPU)                                       |
| `MAX_PIDS`           | `512`   | Per-container process limit (cgroup pids_limit, prevents fork bombs) |
| `MAX_OPEN_FILES`     | `1024`  | Maximum open files per container                                 |

#### File Limits

| Variable                 | Default | Description                              |
| ------------------------ | ------- | ---------------------------------------- |
| `MAX_FILE_SIZE_MB`       | `10`    | Maximum individual file size (MB)        |
| `MAX_TOTAL_FILE_SIZE_MB` | `50`    | Maximum total file size per session (MB) |
| `MAX_FILES_PER_SESSION`  | `50`    | Maximum files per session                |
| `MAX_OUTPUT_FILES`       | `10`    | Maximum output files per execution       |
| `MAX_FILENAME_LENGTH`    | `255`   | Maximum filename length                  |

#### Session Limits

| Variable                    | Default | Description                        |
| --------------------------- | ------- | ---------------------------------- |
| `MAX_CONCURRENT_EXECUTIONS` | `10`    | Maximum concurrent code executions |
| `MAX_SESSIONS_PER_ENTITY`   | `100`   | Maximum sessions per entity        |

### Session Configuration

| Variable                           | Default | Description                  |
| ---------------------------------- | ------- | ---------------------------- |
| `SESSION_TTL_HOURS`                | `24`    | Session time-to-live (hours) |
| `SESSION_CLEANUP_INTERVAL_MINUTES` | `10`    | Cleanup interval (minutes)   |
| `SESSION_ID_LENGTH`                | `32`    | Session ID length            |

### Pod Pool Configuration

Pre-warmed Kubernetes pods significantly reduce execution latency by eliminating cold start time.

| Variable                     | Default | Description                                |
| ---------------------------- | ------- | ------------------------------------------ |
| `POD_POOL_ENABLED`           | `true`  | Enable pod pooling                         |
| `POD_POOL_WARMUP_ON_STARTUP` | `true`  | Pre-warm pods at startup                   |
| `POD_POOL_PY`                | `5`     | Python pod pool size (0 = use Jobs)        |
| `POD_POOL_JS`                | `2`     | JavaScript pod pool size                   |
| `POD_POOL_TS`                | `0`     | TypeScript pool size (0 = use Jobs)        |
| `POD_POOL_GO`                | `0`     | Go pool size (0 = use Jobs)                |
| `POD_POOL_JAVA`              | `0`     | Java pool size (0 = use Jobs)              |
| `POD_POOL_RS`                | `0`     | Rust pool size (0 = use Jobs)              |
| `POD_POOL_C`                 | `0`     | C pool size (0 = use Jobs)                 |
| `POD_POOL_CPP`               | `0`     | C++ pool size (0 = use Jobs)               |
| `POD_POOL_PHP`               | `0`     | PHP pool size (0 = use Jobs)               |
| `POD_POOL_R`                 | `0`     | R pool size (0 = use Jobs)                 |
| `POD_POOL_F90`               | `0`     | Fortran pool size (0 = use Jobs)           |
| `POD_POOL_D`                 | `0`     | D pool size (0 = use Jobs)                 |

**Note:** Languages with `poolSize = 0` use Kubernetes Jobs for execution (3-10s cold start). Pods are destroyed immediately after execution and the pool is automatically replenished in the background.

### Pod Pool Optimization

Fine-tune the pod pool replenishment behavior for optimal performance.

| Variable                        | Default | Description                                    |
| ------------------------------- | ------- | ---------------------------------------------- |
| `POD_POOL_PARALLEL_BATCH`       | `5`     | Pods to start in parallel during warmup        |
| `POD_POOL_REPLENISH_INTERVAL`   | `2`     | Seconds between pool replenishment checks      |
| `POD_POOL_EXHAUSTION_TRIGGER`   | `true`  | Trigger immediate replenishment when exhausted |

### State Persistence Configuration (Python)

Python sessions can persist variables, functions, and objects across executions using the `session_id` parameter.

| Variable                    | Default | Description                          |
| --------------------------- | ------- | ------------------------------------ |
| `STATE_PERSISTENCE_ENABLED` | `true`  | Enable Python state persistence      |
| `STATE_TTL_SECONDS`         | `7200`  | Redis hot storage TTL (2 hours)      |
| `STATE_MAX_SIZE_MB`         | `50`    | Maximum serialized state size        |
| `STATE_CAPTURE_ON_ERROR`    | `false` | Save state even on execution failure |

### State Archival Configuration (Python)

Inactive states are automatically archived to MinIO for long-term storage.

| Variable                               | Default | Description                            |
| -------------------------------------- | ------- | -------------------------------------- |
| `STATE_ARCHIVE_ENABLED`                | `true`  | Enable MinIO cold storage archival     |
| `STATE_ARCHIVE_AFTER_SECONDS`          | `3600`  | Archive after this inactivity (1 hour) |
| `STATE_ARCHIVE_TTL_DAYS`               | `7`     | Keep archives for this many days       |
| `STATE_ARCHIVE_CHECK_INTERVAL_SECONDS` | `300`   | Archival check frequency (5 min)       |

### Security Configuration

| Variable                      | Default | Description                             |
| ----------------------------- | ------- | --------------------------------------- |
| `ENABLE_NETWORK_ISOLATION`    | `true`  | Enable network isolation for containers |
| `ENABLE_FILESYSTEM_ISOLATION` | `true`  | Enable filesystem isolation             |

### Logging Configuration

| Variable               | Default | Description                                 |
| ---------------------- | ------- | ------------------------------------------- |
| `LOG_LEVEL`            | `INFO`  | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FORMAT`           | `json`  | Log format (json or text)                   |
| `LOG_FILE`             | -       | Log file path (stdout if not set)           |
| `LOG_MAX_SIZE_MB`      | `100`   | Maximum log file size (MB)                  |
| `LOG_BACKUP_COUNT`     | `5`     | Number of log file backups                  |
| `ENABLE_ACCESS_LOGS`   | `true`  | Enable HTTP access logs                     |
| `ENABLE_SECURITY_LOGS` | `true`  | Enable security event logs                  |

### Health Check Configuration

| Variable                | Default | Description                     |
| ----------------------- | ------- | ------------------------------- |
| `HEALTH_CHECK_INTERVAL` | `30`    | Health check interval (seconds) |
| `HEALTH_CHECK_TIMEOUT`  | `5`     | Health check timeout (seconds)  |

### Development Configuration

| Variable       | Default | Description                            |
| -------------- | ------- | -------------------------------------- |
| `ENABLE_CORS`  | `false` | Enable CORS (for development)          |
| `CORS_ORIGINS` | -       | Allowed CORS origins (comma-separated) |
| `ENABLE_DOCS`  | `true`  | Enable API documentation endpoints     |

## Language-Specific Configuration

Each supported programming language has its own configuration for container images and resource multipliers:

### Supported Languages

- **Python** (`py`): `python:3.11-slim`
- **Node.js** (`js`): `node:18-alpine`
- **TypeScript** (`ts`): `node:18-alpine`
- **Go** (`go`): `golang:1.21-alpine`
- **Java** (`java`): `openjdk:11-jre-slim`
- **C** (`c`): `gcc:latest`
- **C++** (`cpp`): `gcc:latest`
- **PHP** (`php`): `php:8.2-cli-alpine`
- **Rust** (`rs`): `rust:1.70-slim`
- **R** (`r`): `r-base:latest`
- **Fortran** (`f90`): `gcc:latest`
- **D** (`d`): `dlang2/dmd-ubuntu:latest`

### Custom Language Images

You can override default images using environment variables. The format is `LANG_IMAGE_<CODE>` where `<CODE>` is the language code (py, js, ts, go, java, c, cpp, php, rs, r, f90, d):

```bash
LANG_IMAGE_PY=python:3.12-slim
LANG_IMAGE_JS=node:20-alpine
LANG_IMAGE_JAVA=openjdk:17-jre-slim
```

## Configuration Management Tools

### Command Line Tool

Use the configuration management script:

```bash
# Show configuration summary
python config_manager.py summary

# Validate configuration
python config_manager.py validate

# Check security settings
python config_manager.py security

# Generate complete .env template
python config_manager.py template

# Export configuration as JSON
python config_manager.py export
```

### Programmatic Access

```python
from src.config import settings
from src.utils.config_validator import validate_configuration

# Access configuration
print(f"API Port: {settings.api_port}")
print(f"Max Memory: {settings.max_memory_mb}MB")

# Validate configuration
if validate_configuration():
    print("Configuration is valid")
```

## Production Deployment Checklist

### Security

- [ ] Change default API key to a secure random value
- [ ] Enable network isolation (`ENABLE_NETWORK_ISOLATION=true`)
- [ ] Enable filesystem isolation (`ENABLE_FILESYSTEM_ISOLATION=true`)
- [ ] Deploy Kubernetes NetworkPolicy to deny egress
- [ ] Configure pod security context (non-root user)
- [ ] Review and adjust resource limits
- [ ] Choose execution mode (`K8S_EXECUTION_MODE=agent` recommended)
- [ ] Ensure sidecar image matches execution mode (`sidecar-agent` for agent, `sidecar-nsenter` for nsenter)
- [ ] Configure `K8S_IMAGE_PULL_SECRETS` if using private registries
- [ ] Enable GKE Sandbox for additional kernel isolation if running on GKE (`GKE_SANDBOX_ENABLED=true`)

### Performance

- [ ] Set appropriate memory limits based on expected workload
- [ ] Configure Redis connection pooling
- [ ] Set reasonable execution timeouts
- [ ] Configure log rotation
- [ ] Configure pod pool sizes based on language usage
- [ ] Review state persistence TTL settings

### State Persistence (Python)

- [ ] Configure `STATE_TTL_SECONDS` based on session patterns
- [ ] Set `STATE_MAX_SIZE_MB` limit appropriate for use case
- [ ] Enable state archival for long-term session resumption
- [ ] Configure archival TTL (`STATE_ARCHIVE_TTL_DAYS`)

### Monitoring

- [ ] Enable structured logging (`LOG_FORMAT=json`)
- [ ] Configure log aggregation
- [ ] Set up health check monitoring
- [ ] Enable security event logging

### Infrastructure

- [ ] Secure Redis with authentication
- [ ] Secure MinIO with proper access keys
- [ ] Configure Kubernetes RBAC for API service account
- [ ] Set up backup for Redis and MinIO data

## Troubleshooting

### Configuration Validation Errors

Run the validation tool to identify issues:

```bash
python config_manager.py validate
```

### Common Issues

1. **Redis Connection Failed**
   - Check Redis server is running
   - Verify host, port, and credentials
   - Check network connectivity

2. **MinIO Connection Failed**
   - Verify MinIO server is accessible
   - Check access key and secret key
   - Ensure bucket exists or can be created

3. **Kubernetes Connection Failed**
   - Verify Kubernetes cluster is accessible
   - Check kubeconfig or in-cluster authentication
   - Ensure API service account has required RBAC permissions

4. **Resource Limit Errors**
   - Check system resources available
   - Adjust limits based on hardware
   - Monitor resource usage

### Debug Mode

Enable debug mode for detailed logging:

```bash
API_DEBUG=true
LOG_LEVEL=DEBUG
```

**Warning:** Disable debug mode in production as it may expose sensitive information.

## Environment-Specific Configurations

### Development

```bash
API_DEBUG=true
API_RELOAD=true
ENABLE_CORS=true
ENABLE_DOCS=true
LOG_LEVEL=DEBUG
```

### Testing

```bash
API_DEBUG=false
ENABLE_DOCS=true
LOG_LEVEL=INFO
MAX_EXECUTION_TIME=10
MAX_MEMORY_MB=256
```

### Production

```bash
API_DEBUG=false
API_RELOAD=false
ENABLE_CORS=false
ENABLE_DOCS=false
LOG_LEVEL=INFO
LOG_FORMAT=json
ENABLE_SECURITY_LOGS=true
```
