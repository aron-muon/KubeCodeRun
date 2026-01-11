"""Pytest configuration and shared fixtures."""

import asyncio
import os
from datetime import UTC, datetime, timezone
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import redis.asyncio as redis
from minio import Minio

from docker import DockerClient

# Set test environment before importing config
# These match the docker-compose infrastructure settings
os.environ["API_KEY"] = "test-api-key-for-testing-12345"
os.environ["REDIS_HOST"] = "localhost"
os.environ["REDIS_PORT"] = "6379"
os.environ["MINIO_ENDPOINT"] = "localhost:9000"
os.environ["MINIO_ACCESS_KEY"] = "minioadmin"
os.environ["MINIO_SECRET_KEY"] = "minioadmin"
os.environ["MINIO_SECURE"] = "false"

from src.config import settings
from src.models import Session, SessionCreate, SessionStatus
from src.services.auth import AuthenticationService
from src.services.execution import CodeExecutionService
from src.services.file import FileService
from src.services.session import SessionService


# Note: event_loop fixture removed - asyncio_mode="auto" handles this


@pytest.fixture(scope="module")
def mock_redis():
    """Mock Redis client for testing."""
    mock_client = AsyncMock(spec=redis.Redis)

    # Mock common Redis operations
    mock_client.hset = AsyncMock(return_value=1)
    mock_client.hgetall = AsyncMock(return_value={})
    mock_client.expire = AsyncMock(return_value=True)
    mock_client.delete = AsyncMock(return_value=1)
    mock_client.exists = AsyncMock(return_value=True)
    mock_client.sadd = AsyncMock(return_value=1)
    mock_client.srem = AsyncMock(return_value=1)
    mock_client.smembers = AsyncMock(return_value=set())
    mock_client.incr = AsyncMock(return_value=1)
    mock_client.get = AsyncMock(return_value=None)
    mock_client.setex = AsyncMock(return_value=True)
    mock_client.ping = AsyncMock(return_value=True)
    mock_client.close = AsyncMock()
    mock_client.scan_iter = AsyncMock(return_value=iter([]))

    return mock_client


@pytest.fixture(scope="module")
def mock_minio():
    """Mock MinIO client for testing."""
    mock_client = MagicMock(spec=Minio)

    # Mock common MinIO operations
    mock_client.bucket_exists.return_value = True
    mock_client.make_bucket.return_value = None
    mock_client.presigned_put_object.return_value = "https://example.com/upload"
    mock_client.presigned_get_object.return_value = "https://example.com/download"
    mock_client.stat_object.return_value = MagicMock(size=1024)
    mock_client.put_object.return_value = None
    mock_client.get_object.return_value = MagicMock()
    mock_client.remove_object.return_value = None

    return mock_client


@pytest.fixture(scope="module")
def mock_docker():
    """Mock Docker client for testing."""
    mock_client = MagicMock(spec=DockerClient)
    mock_container = MagicMock()

    # Mock container operations
    mock_container.id = "test_container_id"
    mock_container.status = "running"
    mock_container.reload.return_value = None
    mock_container.exec_run.return_value = MagicMock(exit_code=0, output=b"test output")

    mock_client.containers.create.return_value = mock_container
    mock_client.containers.get.return_value = mock_container
    mock_client.images.pull.return_value = None
    mock_client.images.get.return_value = MagicMock()

    return mock_client


@pytest.fixture
async def session_service(mock_redis):
    """Create SessionService instance with mocked Redis."""
    service = SessionService(redis_client=mock_redis)
    yield service
    await service.close()


@pytest.fixture
def execution_service():
    """Create CodeExecutionService instance with mocked dependencies."""
    with patch("src.services.execution.KubernetesManager") as mock_kubernetes_manager:
        mock_manager = MagicMock()
        mock_kubernetes_manager.return_value = mock_manager

        # Mock kubernetes manager methods
        mock_manager.is_available.return_value = True
        mock_manager.acquire_pod = AsyncMock(return_value=(MagicMock(name="test-pod"), "pool_hit"))
        mock_manager.execute_code = AsyncMock(return_value=(MagicMock(), MagicMock(), "pool_hit"))
        mock_manager.destroy_pod = AsyncMock()
        mock_manager.get_pool_stats.return_value = {}

        service = CodeExecutionService()
        yield service


@pytest.fixture
def file_service(mock_minio, mock_redis):
    """Create FileService instance with mocked dependencies."""
    with (
        patch("src.services.file.Minio", return_value=mock_minio),
        patch("src.services.file.redis.Redis", return_value=mock_redis),
    ):
        service = FileService()
        yield service


@pytest.fixture
def auth_service(mock_redis):
    """Create AuthenticationService instance with mocked Redis."""
    service = AuthenticationService(redis_client=mock_redis)
    yield service


@pytest.fixture
def sample_session():
    """Create a sample session for testing."""
    return Session(
        session_id="test-session-123",
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(UTC),
        last_activity=datetime.now(UTC),
        expires_at=datetime.now(UTC),
        metadata={"entity_id": "test-entity"},
    )


@pytest.fixture
def sample_session_create():
    """Create a sample session creation request."""
    return SessionCreate(metadata={"entity_id": "test-entity", "user_id": "test-user"})


@pytest.fixture
def mock_settings():
    """Mock settings for testing."""
    with patch("src.config.settings") as mock_settings:
        mock_settings.redis_host = "localhost"
        mock_settings.redis_port = 6379
        mock_settings.redis_password = None
        mock_settings.redis_db = 0
        mock_settings.redis_url = None
        mock_settings.session_ttl_hours = 24
        mock_settings.session_cleanup_interval_minutes = 60
        mock_settings.pod_ttl_minutes = 5
        mock_settings.pod_cleanup_interval_minutes = 5
        mock_settings.minio_endpoint = "localhost:9000"
        mock_settings.minio_access_key = "test_key"
        mock_settings.minio_secret_key = "test_secret"
        mock_settings.minio_secure = False
        mock_settings.minio_bucket = "test-bucket"
        mock_settings.api_key = "test-api-key-12345"
        mock_settings.max_execution_time = 30
        mock_settings.max_file_size_mb = 10
        mock_settings.max_output_files = 10

        # Add helper methods
        mock_settings.get_session_ttl_minutes = lambda: mock_settings.session_ttl_hours * 60

        yield mock_settings


@pytest.fixture
def mock_pod_stats():
    """Mock pod statistics."""
    return {
        "memory_usage_mb": 128.5,
        "cpu_usage_percent": 15.2,
        "network_io": {"rx_bytes": 1024, "tx_bytes": 512},
    }


@pytest.fixture
def mock_execution_result():
    """Mock execution result."""
    return {
        "exit_code": 0,
        "stdout": "Hello, World!",
        "stderr": "",
        "execution_time_ms": 150,
        "memory_peak_mb": 64.2,
    }


# Async fixtures for services that need async initialization
@pytest_asyncio.fixture
async def async_session_service(mock_redis):
    """Async fixture for SessionService."""
    service = SessionService(redis_client=mock_redis)
    yield service
    await service.close()


@pytest_asyncio.fixture
async def async_file_service(mock_minio, mock_redis):
    """Async fixture for FileService."""
    with (
        patch("src.services.file.Minio", return_value=mock_minio),
        patch("src.services.file.redis.Redis", return_value=mock_redis),
    ):
        service = FileService()
        yield service
        await service.close()


@pytest_asyncio.fixture
async def async_auth_service(mock_redis):
    """Async fixture for AuthenticationService."""
    service = AuthenticationService(redis_client=mock_redis)
    yield service
