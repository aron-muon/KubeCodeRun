# Development Guide

This document provides detailed instructions for setting up the development environment, installing dependencies, and running tests.

## Setup & Installation

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [just](https://github.com/casey/just) (command runner)
- Kubernetes cluster (1.24+) or Docker for local development
- Redis
- MinIO (or S3-compatible storage)
- Helm 3.x (for Kubernetes deployment)

### Installation Steps

1. **Clone the repository**

   ```bash
   git clone https://github.com/aron-muon/KubeCodeRun.git
   cd KubeCodeRun
   ```

2. **Install dependencies**

   ```bash
   just install
   ```

   This creates a virtual environment in `.venv` and installs all dependencies.

3. **Set up environment variables**

   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

4. **Start infrastructure services**

   ```bash
   just docker-up
   # Or: docker-compose up -d
   ```

5. **Run the API server**
   ```bash
   just run
   ```

## Development Tools

This project uses modern Python tooling:

| Tool | Purpose | Command |
|------|---------|---------|
| **just** | Task runner | `just <recipe>` |
| **uv** | Package management (via just) | `just install` |
| **ruff** | Linting & formatting | `just lint`, `just format` |
| **ty** | Type checking | `just typecheck` |

### Available just recipes

```bash
just help           # Show all available recipes
just install        # Install dependencies
just run            # Start dev server
just lint           # Run ruff linter
just format         # Format code with ruff
just typecheck      # Run ty type checker
just test           # Run all tests
just test-unit      # Run unit tests only
just test-cov       # Run tests with coverage
just clean          # Remove build artifacts
just docker-up      # Start Redis + MinIO
just docker-down    # Stop infrastructure
```

## Testing

For detailed testing instructions, please refer to [TESTING.md](TESTING.md).

### Quick Commands

```bash
# Run unit tests
just test-unit

# Run integration tests (requires Docker/Redis/MinIO)
just test-integration

# Run all tests with coverage
just test-cov
```

## Building Container Images

The API requires language-specific execution images. Each language image embeds the runner binary via a multi-stage Docker build (`COPY --from=runner /runner /usr/local/bin/runner`).

```bash
# Build the runner binary first (used as a build stage by language images)
docker build -t kubecoderun/runner:latest docker/runner/

# Build individual language images (from project root)
docker build -f docker/python.Dockerfile -t kubecoderun/python:latest .
docker build -f docker/nodejs.Dockerfile -t kubecoderun/nodejs:latest .
docker build -f docker/go.Dockerfile -t kubecoderun/go:latest .
docker build -f docker/java.Dockerfile -t kubecoderun/java:latest .
docker build -f docker/rust.Dockerfile -t kubecoderun/rust:latest .
docker build -f docker/php.Dockerfile -t kubecoderun/php:latest .
docker build -f docker/c-cpp.Dockerfile -t kubecoderun/c-cpp:latest .
docker build -f docker/r.Dockerfile -t kubecoderun/r:latest .
docker build -f docker/fortran.Dockerfile -t kubecoderun/fortran:latest .
docker build -f docker/d.Dockerfile -t kubecoderun/d:latest .
```

For more details on Kubernetes pod management, see [ARCHITECTURE.md](ARCHITECTURE.md).
