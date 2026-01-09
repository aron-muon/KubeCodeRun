## KubeCodeRun

KubeCodeRun is a secure code interpreter API that executes code in isolated Kubernetes pods. It supports multiple languages (see `src/config/languages.py`) with sub-100ms latency for Python/JS via warm pod pools.

**Key Technologies:** Python 3.13+, FastAPI, Kubernetes, Redis (sessions), S3-compatible storage (files), uv (package manager)

## Common Commands

```bash
# Development
just install             # Install dependencies with uv
just run                 # Start dev server (uvicorn with reload)
just docker-up           # Start Redis + MinIO for local dev
just docker-down         # Stop local infrastructure

# Code Quality
just lint                # Run ruff linter on src/ and tests/
just format              # Format with ruff
just format-check        # Check formatting without changes
just typecheck           # Run ty type checking

# Testing
just test                # Run all tests
just test-unit           # Unit tests only (fast, no deps)
just test-integration    # Integration tests (requires K8s/Redis/MinIO)
just test-cov            # Tests with HTML coverage report

# Run a single test
uv run pytest tests/unit/test_session_service.py -v
uv run pytest tests/unit/test_session_service.py::test_specific_function -v

# Performance testing
uv run python scripts/perf_test.py
```

## Architecture

### Execution Model

Requests flow through `ExecutionOrchestrator` → `KubernetesManager`, which routes to either:
- **Pod Pool** (poolSize > 0): ~50-100ms latency for Python/JavaScript via pre-warmed pods
- **Job Executor** (poolSize = 0): 3-10s cold start for Go, Rust, etc.

Each pod uses a two-container sidecar pattern: a main container (language runtime) and an HTTP sidecar that executes code in the main container's namespace via `nsenter` with file capabilities for non-root execution.

### Key Service Layers

| Layer | Location | Purpose |
|-------|----------|---------|
| **API** | `src/api/` | FastAPI endpoints (exec, files, health, state, admin) |
| **Services** | `src/services/` | Business logic (session, file, state, orchestrator) |
| **Execution** | `src/services/execution/` | Code execution runner and output processing |
| **Kubernetes** | `src/services/kubernetes/` | Pod pool management, job execution, K8s client |
| **Config** | `src/config/` | Pydantic settings, language configs |
| **Models** | `src/models/` | Request/response Pydantic models |
| **Middleware** | `src/middleware/` | Security headers, metrics, authentication |

### Key Files

- `src/main.py` - FastAPI app entry point with lifespan management
- `src/services/orchestrator.py` - Coordinates execution, state, and files
- `src/services/kubernetes/manager.py` - Main Kubernetes integration point
- `src/services/kubernetes/pool.py` - Warm pod pool management per language
- `src/services/execution/runner.py` - Primary code execution service
- `docker/sidecar/main.py` - HTTP sidecar server for pod communication
- `helm-deployments/kubecoderun/templates/` - Kubernetes Helm chart templates

## Language-Specific Notes

- **Python**: Supports state persistence across executions via cloudpickle + lz4 compression
- **TypeScript**: Uses two-step compilation (`tsc` + `node`) instead of ts-node due to stdout capture issues with nsenter

## Testing Notes

- Unit tests mock Kubernetes/Redis/MinIO - no infrastructure needed
- Integration tests require running cluster + `docker-compose up -d` for Redis/MinIO
- Use `@pytest.mark.asyncio` for async tests
- Fixtures in `tests/conftest.py`

## Development notes

- Use conventional commit messages
- Work in atomic commit units, committing frequently
