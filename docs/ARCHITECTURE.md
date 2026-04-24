# KubeCodeRun Architecture

## Overview

KubeCodeRun is a secure API for executing code in isolated Kubernetes pods. It uses a **Kubernetes-native architecture** with warm pod pools for low-latency execution and Jobs for cold-path languages.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         KubeCodeRun API                             │
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
                            │  │  Single Container                   │    │
                            │  │  (Language Runtime + Runner Binary) │    │
                            │  └─────────────────────────────────────┘    │
                            └─────────────────────────────────────────────┘
```

## Execution Strategies

| Strategy | Cold Start | Use Case |
|----------|-----------|----------|
| **Warm Pod Pool** | 50-100ms | Languages with `pod_pool_<lang> > 0` |
| **Kubernetes Jobs** | 3-10s | Languages with `pod_pool_<lang> = 0` |

The warm pool approach achieves ~85% reduction in P99 latency compared to cold-start execution.

## Pod Design: Single Container with Embedded Runner

Each execution pod runs a single container that includes both the language runtime and an embedded Go binary called "runner". The runner serves HTTP on port 8080 and executes code via subprocess. No sidecar, no shared process namespace, no `nsenter`, and no elevated privileges are required.

### Container Layout
- Runs the language runtime (Python, Node.js, Go, etc.)
- Provides the execution environment (compilers, interpreters, libraries)
- Includes the runner binary at `/usr/local/bin/runner` (copied via multi-stage build)
- Uses `/mnt/data` as the working directory

### Runner Binary (Executor)
- Lightweight Go HTTP server (~10MB static binary)
- Exposes REST API for code execution on port 8080
- Executes code via subprocess in the same container
- Handles file transfers and state management
- Single source of truth for language execution commands (`docker/runner/executor.go`)

**Runner API Endpoints:**
```
POST /execute     - Execute code with optional state
POST /files       - Upload files to working directory
GET  /files       - List files in working directory
GET  /files/{name} - Download file content
GET  /health      - Health check
```

### How the Runner Works

The runner binary is embedded into each language container image via a multi-stage Docker build:

```dockerfile
COPY --from=runner /runner /usr/local/bin/runner
```

```
┌─────────────────────────────────────────────────────────────┐
│                      Execution Pod                          │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Single Container                        │   │
│  │                                                     │   │
│  │  • Language runtime (Python/Node/Go/...)            │   │
│  │  • Runner binary serves HTTP on :8080               │   │
│  │  • Receives request → writes code to /mnt/data      │   │
│  │  • Executes via subprocess → captures stdout/stderr │   │
│  │  • Returns results via HTTP response                │   │
│  │                                                     │   │
│  │  Working directory: /mnt/data                       │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Execution flow:**
1. Runner receives an HTTP request with code to execute
2. Writes code to a temporary file in `/mnt/data`
3. Executes the appropriate language command via subprocess (e.g., `python3 file.py`)
4. Captures stdout/stderr and returns via HTTP

**Advantages over the previous sidecar pattern:**
- **Zero elevated privileges**: No `SYS_PTRACE`, `SYS_ADMIN`, `SYS_CHROOT` capabilities, no `allowPrivilegeEscalation`, no `shareProcessNamespace`
- **Correct resource limits**: Container resource limits (CPU, memory) apply directly to user code since everything runs in a single cgroup
- **Simpler pod spec**: Single container, no shared volumes between containers
- **Compatible with hardened runtimes**: Works with any Kubernetes runtime (gVisor, Kata Containers, etc.)

**TypeScript Note:** TypeScript uses a two-step compilation (`tsc file.ts && node file.js`) instead of `ts-node`.

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
5. Runner Binary
   ├── POST /execute
   ├── Run code via subprocess
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

## File Persistence Across Executions

Pool pods are destroyed after every execution and their `/mnt/data`
volume is an ephemeral `emptyDir`. To keep uploaded files available
across multiple `POST /exec` calls on the same session, the orchestrator
re-hydrates every uploaded file associated with the execution session
from MinIO on each request — in addition to whatever the client lists
in `request.files`. Generated outputs (stored under `/outputs/…`) are
skipped to avoid collisions with files produced by earlier runs.

The number of files re-hydrated this way is returned to the client in
the `auto_mounted_files` field of `ExecResponse`, making pod rotations
(pool churn, OOMKills, evictions) observable without changing the
LibreChat-compatible response shape.

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
K8S_NAMESPACE=kubecoderun
K8S_IMAGE_REGISTRY=aronmuon/kubecoderun
K8S_IMAGE_TAG=latest
K8S_IMAGE_PULL_SECRETS=docker-privaterepo  # Comma-separated secret names for private registries
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
   - `runAsUser: 65532`
   - Resource limits enforced
   - Zero elevated privileges (no capabilities, no `allowPrivilegeEscalation`)
3. **Ephemeral Storage**: Pods destroyed after execution
4. **Non-root Execution**: Container runs as UID 65532
5. **Hardened Runtime Compatible**: No special capabilities required, works with gVisor, Kata Containers, etc.

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
├── api/                   # API server container
│   └── Dockerfile
├── runner/                # Embedded runner binary (Go)
│   ├── main.go            # HTTP server entry point
│   ├── executor.go        # Language execution commands
│   ├── files.go           # File handling
│   └── Dockerfile         # Builds the runner binary
└── *.Dockerfile           # Language execution environments

helm-deployments/
└── kubecoderun/  # Helm chart
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
helm install kubecoderun ./helm-deployments/kubecoderun \
  --namespace kubecoderun \
  --create-namespace \
  --set replicaCount=2 \
  --set execution.languages.python.poolSize=5
```

### Required Infrastructure

- **Kubernetes 1.24+**: Pod and Job execution
- **Redis 6+**: Session and state storage
- **MinIO/S3**: File storage and state archives
