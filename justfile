# Default recipe - show help
default: help

# Show available recipes
help:
    @echo "Available recipes:"
    @echo "  install          Install dependencies"
    @echo "  test             Run all tests"
    @echo "  test-unit        Run unit tests only"
    @echo "  test-integration Run integration tests only"
    @echo "  test-cov         Run tests with coverage report"
    @echo "  lint             Run ruff linter"
    @echo "  format           Format code with ruff"
    @echo "  format-check     Check code formatting without changes"
    @echo "  typecheck        Run ty type checking"
    @echo "  clean            Remove build artifacts and cache"
    @echo "  run              Run the development server"
    @echo "  docker-up        Start docker-compose services"
    @echo "  docker-down      Stop docker-compose services"

# Install dependencies
install:
    uv sync

# Run all tests
test:
    uv run pytest tests/ -v

# Run unit tests only
test-unit:
    uv run pytest tests/unit/ -v

# Run integration tests only
test-integration:
    uv run pytest tests/integration/ -v

# Run tests with coverage
test-cov:
    uv run pytest --cov=src --cov-report=html --cov-report=term tests/
    @echo "Coverage report generated in htmlcov/"

# Lint with ruff
lint:
    uv run ruff check src/ tests/

# Format with ruff
format:
    uv run ruff format src/ tests/

# Check formatting without changes
format-check:
    uv run ruff format --check src/ tests/

# Type checking with ty
typecheck:
    uv run ty check src/

# Clean build artifacts
clean:
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".ty_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    find . -type f -name ".coverage" -delete 2>/dev/null || true

# Run development server
run:
    uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Docker compose up
docker-up:
    docker-compose up -d

# Docker compose down
docker-down:
    docker-compose down
