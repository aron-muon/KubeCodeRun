# KubeCodeRun

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![CI Status](https://github.com/aron-muon/KubeCodeRun/actions/workflows/lint.yml/badge.svg)](https://github.com/aron-muon/KubeCodeRun/actions/workflows/lint.yml)

A secure, open-source code interpreter API that provides sandboxed code execution in isolated Kubernetes pods. Compatible with LibreChat's Code Interpreter API.

## Quick Start

Get up and running in minutes with Kubernetes deployment.

### Prerequisites

- Kubernetes cluster (1.24+)
- Helm 3.x
- kubectl configured for your cluster

### Deployment

1. **Deploy with Helm**

   ```bash
   helm install kubecoderun ghcr.io/aron-muon/charts/kubecoderun:latest \
     --namespace kubecoderun \
     --create-namespace \
     --set replicaCount=2 \
     --set execution.languages.python.poolSize=5
   ```

2. **Access the API**

   ```bash
   # Port-forward for local access
   kubectl -n kubecoderun port-forward svc/kubecoderun 8000:8000
   ```

The API will be available at `http://localhost:8000`.
Visit `http://localhost:8000/docs` for the interactive API documentation.

### Local Development Infrastructure (Docker Compose)

For local development, use Docker Compose to run Redis and MinIO:

```bash
cp .env.example .env
docker compose up -d
```

> **Note:** This only starts Redis and MinIO. The API requires Kubernetes for code execution. After starting infrastructure, deploy to a local Kubernetes cluster (minikube, kind, etc.) or run the API locally with `KUBECONFIG` configured.

## Admin Dashboard

A built-in admin dashboard is available at `http://localhost:8000/admin-dashboard` for monitoring and management:
<img width="1449" height="1256" alt="image" src="docs/dashboard_image.png" />

- **Overview**: Real-time execution metrics, success rates, and performance graphs
- **API Keys**: Create, view, and manage API keys with rate limiting
- **System Health**: Monitor Redis, MinIO, Kubernetes, and pod pool status

The dashboard requires the master API key for authentication.

## Features

- **Multi-language Support**: Execute code in 12 languages - Python, JavaScript, TypeScript, Go, Java, C, C++, PHP, Rust, R, Fortran, and D
- **Sub-100ms Python Execution**: Warm pod pools with HTTP sidecar achieve ~50-100ms latency
- **Pod Pool**: Pre-warmed Kubernetes pods provide fast acquisition (vs 3-10s cold start with Jobs)
- **High Concurrency**: Kubernetes-native scaling supporting high concurrent requests
- **Secure Execution**: Isolated Kubernetes pods with comprehensive resource limits and network policies
- **File Management**: Upload, download, and manage files within execution sessions
- **Session Management**: Redis-based session handling with automatic cleanup
- **S3-Compatible Storage**: MinIO integration for persistent file storage
- **Authentication**: API key-based authentication for secure access
- **HTTPS/SSL Support**: Optional SSL/TLS encryption with automatic HTTP to HTTPS redirection
- **Health Monitoring**: Comprehensive health check endpoints for all dependencies
- **Metrics Collection**: Execution and API metrics for monitoring and debugging
- **Unicode Support**: Full Unicode filename support in file downloads
- **Structured Logging**: JSON-formatted logs with configurable levels and destinations
- **CORS Support**: Optional cross-origin resource sharing for web clients
- **Orphan Cleanup**: Automatic cleanup of orphaned storage objects

## Architecture

The KubeCodeRun is built with a focus on security, speed, and scalability. It uses a **Kubernetes-native architecture** with **FastAPI** for the web layer, **warm pod pools** for low-latency execution, and **Redis** for session management.

Key features include:

- **Warm Pod Pools**: Pre-warmed Kubernetes pods for hot-path languages (Python, JS)
- **Kubernetes Jobs**: Fallback for cold-path languages (Go, Rust, etc.)
- **HTTP Sidecar Pattern**: Communication with pods via lightweight HTTP API
- **Stateless Execution**: Each execution is isolated and ephemeral
- **Session Persistence**: Optional state persistence for Python sessions

For a deep dive into the system design, components, and request flows, see [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## API & Usage

The API provides endpoints for code execution, file management, and session state control.

- `POST /exec`: Execute code in one of the 12 supported languages.
- `POST /upload`: Upload files for processing.
- `GET /download`: Retrieve generated files.

Interactive documentation is available at `http://localhost:8000/docs` when the server is running.

For detailed information on all endpoints and specific language notes, see [ARCHITECTURE.md](docs/ARCHITECTURE.md#api-layer-srcapi).

## Supported Languages

We support 12 programming languages including Python, JavaScript, TypeScript, Go, Rust, and more. Each language has optimized execution paths and resource limits.

See the [Supported Languages table](docs/ARCHITECTURE.md#supported-languages) for details on versions and included libraries.

## Configuration

The service is highly configurable via environment variables.

| Category       | Description                                  |
| -------------- | -------------------------------------------- |
| **API**        | Host, port, and security settings.           |
| **Storage**    | Redis and MinIO/S3 connection details.       |
| **Resources**  | Per-execution memory, CPU, and time limits.  |
| **Pod Pools**  | Per-language pool sizing and warmup settings.|
| **Kubernetes** | Namespace, RBAC, and pod templates.          |

A full list of configuration options and a production checklist can be found in [CONFIGURATION.md](docs/CONFIGURATION.md).

## Development & Installation

For detailed instructions on setting up your local environment, running tests, and building custom images, please refer to the [Development Guide](docs/DEVELOPMENT.md).

Quick test command:

```bash
pytest tests/unit/
```

For comprehensive testing details, see [TESTING.md](docs/TESTING.md).

## Security

- All code execution happens in isolated Kubernetes pods
- Network policies deny all egress by default
- Both containers run as non-root (`runAsNonRoot: true`, `runAsUser: 1000`)
- Sidecar uses file capabilities (`setcap`) to grant `nsenter` binary-specific privileges without running as root
- Resource limits enforced via Kubernetes (CPU, memory, ephemeral storage)
- Pods destroyed immediately after execution (ephemeral)
- RBAC restricts API pod permissions to pod/job management only
- API key authentication protects all endpoints
- Input validation prevents injection attacks

Please see [SECURITY.md](docs/SECURITY.md) for our security policy and reporting instructions.

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to get started, our code of conduct, and the pull request process.

## Acknowledgments

This project was originally a fork of [@usnavy13](https://github.com/usnavy13)'s [LibreCodeInterpreter](https://github.com/usnavy13/LibreCodeInterpreter). Their work was foundational in developing the HTTP approach to running code execution.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
