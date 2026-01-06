# LibreCodeInterpreter Architecture

## Overview

LibreCodeInterpreter is a secure API for executing code in isolated Kubernetes pods. It uses a **Kubernetes-native architecture** with warm pod pools for low-latency execution and Jobs for cold-path languages.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         LibreCodeInterpreter API                             │
│                         (FastAPI Application)                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
            ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
            │   Redis     │  │    MinIO    │  │ Kubernetes  │
            │  (Sessions) │  │   (Files)   │  │   (Pods)    │
            └─────────────┘  └─────────────┘  └─────────────┘
                                                    │
                                    ┌───────────────┴───────────────┐
                                    ▼                               ▼
                            ┌─────────────────────┐       ┌─────────────────────┐
                            │     Pod Pool        │       │    Job Executor     │
                            │  (poolSize > 0)     │       │   (poolSize = 0)    │
                            │  Python, JS, etc.   │       │   Go, Rust, etc.    │
                            └─────────────────────┘       └─────────────────────┘
                                    │                               │
                                    ▼                               ▼
                            ┌─────────────────────────────────────────────┐
                            │              Execution Pods                  │
                            │  ┌─────────────────────────────────────┐    │
                            │  │  Main Container    │  HTTP Sidecar  │    │
                            │  │  (Language Runtime)│  (Executor)    │    │
                            │  └─────────────────────────────────────┘    │
                            └─────────────────────────────────────────────┘
```

## Execution Strategies

| Strategy | Cold Start | Use Case |
|----------|-----------|----------|
| **Warm Pod Pool** | 50-100ms | Languages with `pod_pool_<lang> > 0` |
| **Kubernetes Jobs** | 3-10s | Languages with `pod_pool_<lang> = 0` |

The warm pool approach achieves ~85% reduction in P99 latency compared to cold-start execution.

## Pod Design: Two-Container Sidecar Pattern

Each execution pod contains two containers that share process namespaces, enabling the sidecar to execute code using the main container's runtime environment.

### 1. Main Container (Language Runtime)
- Runs the language runtime (Python, Node.js, Go, etc.)
- Provides the execution environment (compilers, interpreters, libraries)
- Shares `/mnt/data` volume with sidecar
- Runs a sleep loop to keep the container alive

### 2. HTTP Sidecar (Executor)
- Lightweight FastAPI server (~50MB)
- Exposes REST API for code execution
- Uses `nsenter` to execute code in the main container's namespace
- Handles file transfers and state management

**Sidecar API Endpoints:**
```
POST /execute     - Execute code with optional state
POST /files       - Upload files to shared volume
GET  /files       - List files in working directory
GET  /files/{name} - Download file content
GET  /health      - Health check
```

### Namespace Sharing with nsenter

The pod uses `shareProcessNamespace: true`, allowing containers to see each other's processes. The sidecar uses Linux `nsenter` to execute code in the main container's mount namespace:

```
┌─────────────────────────────────────────────────────────────┐
│                      Execution Pod                          │
│  shareProcessNamespace: true                                │
│                                                             │
│  ┌─────────────────────┐    ┌─────────────────────────────┐│
│  │   Main Container    │    │      Sidecar Container      ││
│  │                     │    │                             ││
│  │  • Python/Node/Go   │◄───│  • Receives HTTP request    ││
│  │  • sleep infinity   │    │  • Writes code to /mnt/data ││
│  │  • PID 1 visible    │    │  • nsenter -m -t <PID> sh   ││
│  │    to sidecar       │    │  • Returns stdout/stderr    ││
│  └─────────────────────┘    └─────────────────────────────┘│
│           │                            │                    │
│           └────────────────────────────┘                    │
│                   Shared /mnt/data volume                   │
└─────────────────────────────────────────────────────────────┘
```

**How nsenter works:**
1. Sidecar finds the main container's PID (typically PID 7 after pause container)
2. Uses `nsenter -m -t <PID>` to enter the mount namespace
3. Executes shell commands using the main container's filesystem
4. Captures stdout/stderr and returns via HTTP

**nsenter Privilege Model:**

The sidecar runs as non-root (UID 1000) but requires Linux capabilities to use `nsenter`. Since capabilities for non-root users only populate the *bounding set* (not effective/permitted), we use **file capabilities** via `setcap` on the nsenter binary:

```dockerfile
# In sidecar Dockerfile
RUN setcap 'cap_sys_ptrace,cap_sys_admin,cap_sys_chroot+eip' /usr/bin/nsenter
```

This allows the non-root user to gain the required capabilities when executing nsenter, without running as root. The pod spec still requires `allowPrivilegeEscalation: true` for file capabilities to be honored. See [SECURITY.md](SECURITY.md) for full details.

**Per-Language Environment Setup:**

Since `nsenter -m` only enters the mount namespace (not the environment), the sidecar explicitly sets up PATH and environment variables for each language:

| Language | Key Environment Variables |
|----------|--------------------------|
| Python | `PATH=/usr/local/bin:/usr/bin:/bin`, `PYTHONUNBUFFERED=1` |
| Node.js | `PATH=/usr/local/bin:...`, `NODE_PATH=/usr/local/lib/node_modules` |
| Go | `PATH=/usr/local/go/bin:...`, `GOCACHE=/mnt/data/go-build` |
| Rust | `PATH=/usr/local/cargo/bin:...`, `CARGO_HOME=/usr/local/cargo` |
| Java | `PATH=/opt/java/openjdk/bin:...`, `CLASSPATH=/mnt/data` |
| TypeScript | Same as Node.js, uses `tsc` + `node` (not ts-node) |

**TypeScript Note:** TypeScript uses a two-step compilation (`tsc file.ts && node file.js`) instead of `ts-node` because ts-node has stdout capture issues when executed via nsenter.

## Core Components

### API Layer (`src/api/`)

| Module | Purpose |
|--------|---------|
| `exec.py` | Code execution endpoints (`POST /exec`) |
| `files.py` | File upload/download endpoints |
| `health.py` | Health and readiness checks |
| `state.py` | Session state management |
| `admin.py` | Admin dashboard API |
| `dashboard_metrics.py` | Dashboard metrics endpoints |

### Services Layer (`src/services/`)

| Service | Module | Responsibility |
|---------|--------|----------------|
| **SessionService** | `session.py` | Session lifecycle (create, get, delete) |
| **FileService** | `file.py` | File storage in MinIO |
| **CodeExecutionRunner** | `execution/runner.py` | Primary code execution service |
| **ExecutionOrchestrator** | `orchestrator.py` | Coordinates execution, state, and files |
| **KubernetesManager** | `kubernetes/` | Pod lifecycle and execution |
| **StateService** | `state.py` | Python state persistence in Redis |
| **HealthService** | `health.py` | Service health monitoring |

### Kubernetes Module (`src/services/kubernetes/`)

| Component | Module | Responsibility |
|-----------|--------|----------------|
| **KubernetesManager** | `manager.py` | Main entry point, coordinates pools and jobs |
| **PodPoolManager** | `pool.py` | Warm pod pool management per language |
| **JobExecutor** | `job_executor.py` | Job-based execution for cold languages |
| **Client** | `client.py` | Kubernetes client factory |

## Data Flow: Code Execution

```
1. Client Request
   │
   ▼
2. API Endpoint (/exec)
   │
   ▼
3. ExecutionOrchestrator
   ├── Validate request
   ├── Get/create session
   ├── Load state (Python only)
   ├── Mount files
   │
   ▼
4. KubernetesManager.execute_code()
   ├── Hot path: Acquire pod from pool
   │   └── PodPoolManager.acquire()
   │
   └── Cold path: Create Job
       └── JobExecutor.execute()
   │
   ▼
5. HTTP Sidecar
   ├── POST /execute
   ├── Run code in main container
   └── Return stdout/stderr/files
   │
   ▼
6. Response Processing
   ├── Save state (Python only)
   ├── Store generated files
   └── Destroy pod (pool replenishes)
   │
   ▼
7. Client Response
```

## State Persistence (Python)

Python sessions support state persistence across executions:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Execute   │ ──► │   Capture   │ ──► │    Save     │
│    Code     │     │   State     │     │  to Redis   │
└─────────────┘     └─────────────┘     └─────────────┘
                          │
                          ▼
                    cloudpickle + lz4
                    (compressed state)
```

**State Flow:**
1. Before execution: Load state from Redis (or MinIO archive)
2. Execute code with state restoration
3. After execution: Capture and save new state
4. Archive to MinIO after TTL expires

## Configuration

### Pod Pool Settings

```python
# Enable/disable pod pools
POD_POOL_ENABLED=true
POD_POOL_WARMUP_ON_STARTUP=true

# Per-language pool sizes (0 = use Jobs)
POD_POOL_PY=5      # Python: 5 warm pods
POD_POOL_JS=2      # JavaScript: 2 warm pods
POD_POOL_TS=0      # TypeScript: use Jobs
POD_POOL_GO=0      # Go: use Jobs
POD_POOL_JAVA=0    # Java: use Jobs
POD_POOL_RS=0      # Rust: use Jobs
POD_POOL_C=0       # C: use Jobs
POD_POOL_CPP=0     # C++: use Jobs
POD_POOL_PHP=0     # PHP: use Jobs
POD_POOL_R=0       # R: use Jobs
POD_POOL_F90=0     # Fortran: use Jobs
POD_POOL_D=0       # D: use Jobs

# Pool optimization settings
POD_POOL_PARALLEL_BATCH=5          # Pods to start in parallel during warmup
POD_POOL_REPLENISH_INTERVAL=2      # Seconds between pool replenishment checks
POD_POOL_EXHAUSTION_TRIGGER=true   # Trigger immediate replenishment when exhausted
```

### Kubernetes Settings

```python
K8S_NAMESPACE=librecodeinterpreter
K8S_SIDECAR_IMAGE=aronmuon/librecodeinterpreter-sidecar:latest
K8S_IMAGE_REGISTRY=aronmuon/librecodeinterpreter
K8S_IMAGE_TAG=latest
K8S_CPU_LIMIT=1
K8S_MEMORY_LIMIT=512Mi
K8S_CPU_REQUEST=100m
K8S_MEMORY_REQUEST=128Mi
```

## Security Model

### Pod Isolation

Each execution pod is isolated via:

1. **Network Policy**: Deny all egress by default
2. **Security Context**:
   - `runAsNonRoot: true`
   - `runAsUser: 1000`
   - Resource limits enforced
   - Sidecar uses file capabilities (`setcap`) on nsenter binary for required privileges
3. **Ephemeral Storage**: Pods destroyed after execution
4. **Non-root Execution**: Both containers run as UID 1000
5. **Binary-specific Capabilities**: Only the `nsenter` binary has elevated capabilities; other processes cannot gain them

### RBAC Requirements

The API deployment needs these Kubernetes permissions:
- `pods`: create, delete, get, list, watch
- `jobs`: create, delete, get, list, watch

## Directory Structure

```
src/
├── api/                    # FastAPI route handlers
│   ├── exec.py            # Code execution endpoint
│   ├── files.py           # File management
│   ├── health.py          # Health checks
│   └── state.py           # State management
│
├── config/                 # Configuration
│   ├── __init__.py        # Settings (Pydantic)
│   ├── languages.py       # Language definitions
│   └── security.py        # Security settings
│
├── models/                 # Pydantic models
│   ├── execution.py       # Execution models
│   ├── session.py         # Session models
│   └── pool.py            # Pool models
│
├── services/               # Business logic
│   ├── execution/         # Execution service
│   │   ├── runner.py      # CodeExecutionRunner
│   │   └── output.py      # Output processing
│   │
│   ├── kubernetes/        # Kubernetes integration
│   │   ├── manager.py     # KubernetesManager
│   │   ├── pool.py        # PodPoolManager
│   │   ├── job_executor.py
│   │   ├── client.py      # K8s client factory
│   │   └── models.py      # PodHandle, etc.
│   │
│   ├── session.py         # SessionService
│   ├── file.py            # FileService
│   ├── state.py           # StateService
│   ├── health.py          # HealthService
│   └── orchestrator.py    # ExecutionOrchestrator
│
├── middleware/             # FastAPI middleware
│   ├── security.py        # Security middleware
│   ├── auth.py            # Authentication
│   ├── headers.py         # Security headers
│   └── metrics.py         # Request metrics
│
├── core/                   # Core utilities
│   ├── events.py          # Event handling
│   └── pool.py            # Pool abstractions
│
└── main.py                 # Application entry point

docker/
└── sidecar/               # HTTP sidecar container
    ├── main.py            # FastAPI sidecar server
    ├── Dockerfile
    └── requirements.txt

helm-deployments/
└── librecodeinterpreter/  # Helm chart
    ├── templates/
    │   ├── deployment.yaml
    │   ├── service.yaml
    │   ├── serviceaccount.yaml
    │   ├── role.yaml
    │   └── networkpolicy.yaml
    └── values.yaml
```

## Monitoring

### Health Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Basic liveness check |
| `GET /ready` | Readiness check |
| `GET /health/detailed` | Detailed health of all services |
| `GET /health/redis` | Redis connectivity |
| `GET /health/minio` | MinIO connectivity |
| `GET /health/kubernetes` | Kubernetes connectivity |
| `GET /metrics/pool` | Pod pool statistics |

### Metrics

The API exposes metrics for:
- Execution count by language and status
- Execution latency (P50, P95, P99)
- Pool hit/miss ratio
- Active sessions count

## Deployment

### Helm Installation

```bash
helm install librecodeinterpreter ./helm-deployments/librecodeinterpreter \
  --namespace librecodeinterpreter \
  --create-namespace \
  --set replicaCount=2 \
  --set execution.languages.python.poolSize=5
```

### Required Infrastructure

- **Kubernetes 1.24+**: Pod and Job execution
- **Redis 6+**: Session and state storage
- **MinIO/S3**: File storage and state archives
