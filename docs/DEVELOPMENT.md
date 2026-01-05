# Development Guide

This document provides detailed instructions for setting up the development environment, installing dependencies, and running tests.

## Setup & Installation

### Prerequisites

- Python 3.11+
- Kubernetes cluster (1.24+) or Docker for local development
- Redis
- MinIO (or S3-compatible storage)
- Helm 3.x (for Kubernetes deployment)

### Installation Steps

1. **Clone the repository**

   ```bash
   git clone https://github.com/usnavy13/LibreCodeInterpreter.git
   cd LibreCodeInterpreter
   ```

2. **Create a virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**

   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Start infrastructure services**

   ```bash
   docker-compose up -d
   ```

6. **Run the API server**
   ```bash
   uvicorn src.main:app --reload
   ```

## Testing

For detailed testing instructions, please refer to [TESTING.md](TESTING.md).

### Quick Commands

```bash
# Run unit tests
pytest tests/unit/

# Run integration tests (requires Docker/Redis/MinIO)
pytest tests/integration/

# Run all tests with coverage
pytest --cov=src tests/
```

## Building Container Images

The API requires language-specific execution images and the HTTP sidecar image.

```bash
# Build individual language images (from project root)
docker build -f docker/python.Dockerfile -t librecodeinterpreter/python:latest .
docker build -f docker/nodejs.Dockerfile -t librecodeinterpreter/nodejs:latest .
docker build -f docker/go.Dockerfile -t librecodeinterpreter/go:latest .
docker build -f docker/java.Dockerfile -t librecodeinterpreter/java:latest .
docker build -f docker/rust.Dockerfile -t librecodeinterpreter/rust:latest .
docker build -f docker/php.Dockerfile -t librecodeinterpreter/php:latest .
docker build -f docker/c-cpp.Dockerfile -t librecodeinterpreter/c-cpp:latest .
docker build -f docker/r.Dockerfile -t librecodeinterpreter/r:latest .
docker build -f docker/fortran.Dockerfile -t librecodeinterpreter/fortran:latest .
docker build -f docker/d.Dockerfile -t librecodeinterpreter/d:latest .

# Build the HTTP sidecar image
docker build -t librecodeinterpreter/sidecar:latest docker/sidecar/
```

For more details on Kubernetes pod management, see [ARCHITECTURE.md](ARCHITECTURE.md).
