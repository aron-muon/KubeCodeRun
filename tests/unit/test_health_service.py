"""Unit tests for the health check service."""

import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.health import (
    HealthCheckResult,
    HealthCheckService,
    HealthStatus,
    health_service,
)


class TestHealthStatus:
    """Tests for HealthStatus enum."""

    def test_health_status_values(self):
        """Test health status enum values."""
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.UNKNOWN.value == "unknown"


class TestHealthCheckResult:
    """Tests for HealthCheckResult."""

    def test_init_basic(self):
        """Test basic initialization."""
        result = HealthCheckResult(
            service="test",
            status=HealthStatus.HEALTHY,
        )

        assert result.service == "test"
        assert result.status == HealthStatus.HEALTHY
        assert result.response_time_ms is None
        assert result.details == {}
        assert result.error is None
        assert result.timestamp is not None

    def test_init_with_all_params(self):
        """Test initialization with all parameters."""
        result = HealthCheckResult(
            service="test",
            status=HealthStatus.DEGRADED,
            response_time_ms=150.5,
            details={"key": "value"},
            error="Some error",
        )

        assert result.service == "test"
        assert result.status == HealthStatus.DEGRADED
        assert result.response_time_ms == 150.5
        assert result.details == {"key": "value"}
        assert result.error == "Some error"

    def test_to_dict_basic(self):
        """Test to_dict with basic result."""
        result = HealthCheckResult(
            service="test",
            status=HealthStatus.HEALTHY,
        )

        data = result.to_dict()

        assert data["service"] == "test"
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "response_time_ms" not in data
        assert "details" not in data
        assert "error" not in data

    def test_to_dict_with_response_time(self):
        """Test to_dict with response time."""
        result = HealthCheckResult(
            service="test",
            status=HealthStatus.HEALTHY,
            response_time_ms=123.456,
        )

        data = result.to_dict()

        assert data["response_time_ms"] == 123.46  # Rounded to 2 decimals

    def test_to_dict_with_details(self):
        """Test to_dict with details."""
        result = HealthCheckResult(
            service="test",
            status=HealthStatus.HEALTHY,
            details={"version": "1.0"},
        )

        data = result.to_dict()

        assert data["details"] == {"version": "1.0"}

    def test_to_dict_with_error(self):
        """Test to_dict with error."""
        result = HealthCheckResult(
            service="test",
            status=HealthStatus.UNHEALTHY,
            error="Connection failed",
        )

        data = result.to_dict()

        assert data["error"] == "Connection failed"


class TestHealthCheckServiceInit:
    """Tests for HealthCheckService initialization."""

    def test_init(self):
        """Test service initialization."""
        service = HealthCheckService()

        assert service._redis_client is None
        assert service._minio_client is None
        assert service._kubernetes_manager is None
        assert service._last_check_time is None
        assert service._cached_results == {}
        assert service._cache_ttl_seconds == 30

    def test_set_kubernetes_manager(self):
        """Test setting kubernetes manager."""
        service = HealthCheckService()
        mock_manager = MagicMock()

        service.set_kubernetes_manager(mock_manager)

        assert service._kubernetes_manager == mock_manager


class TestCheckAllServices:
    """Tests for check_all_services."""

    @pytest.mark.asyncio
    async def test_check_all_services_uses_cache(self):
        """Test that cached results are used when valid."""
        service = HealthCheckService()
        service._last_check_time = datetime.now(UTC)
        service._cached_results = {"redis": HealthCheckResult("redis", HealthStatus.HEALTHY)}

        results = await service.check_all_services(use_cache=True)

        assert results == service._cached_results

    @pytest.mark.asyncio
    async def test_check_all_services_no_cache(self):
        """Test check without cache."""
        service = HealthCheckService()

        with (
            patch.object(service, "check_redis", new_callable=AsyncMock) as mock_redis,
            patch.object(service, "check_minio", new_callable=AsyncMock) as mock_minio,
            patch.object(service, "check_kubernetes", new_callable=AsyncMock) as mock_k8s,
        ):
            mock_redis.return_value = HealthCheckResult("redis", HealthStatus.HEALTHY)
            mock_minio.return_value = HealthCheckResult("minio", HealthStatus.HEALTHY)
            mock_k8s.return_value = HealthCheckResult("kubernetes", HealthStatus.HEALTHY)

            results = await service.check_all_services(use_cache=False)

            assert "redis" in results
            assert "minio" in results
            assert "kubernetes" in results
            mock_redis.assert_called_once()
            mock_minio.assert_called_once()
            mock_k8s.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_all_services_handles_exception(self):
        """Test handling of exceptions during checks."""
        service = HealthCheckService()

        with (
            patch.object(service, "check_redis", new_callable=AsyncMock) as mock_redis,
            patch.object(service, "check_minio", new_callable=AsyncMock) as mock_minio,
            patch.object(service, "check_kubernetes", new_callable=AsyncMock) as mock_k8s,
        ):
            mock_redis.side_effect = Exception("Redis error")
            mock_minio.return_value = HealthCheckResult("minio", HealthStatus.HEALTHY)
            mock_k8s.return_value = HealthCheckResult("kubernetes", HealthStatus.HEALTHY)

            results = await service.check_all_services(use_cache=False)

            assert results["redis"].status == HealthStatus.UNHEALTHY
            assert "Redis error" in results["redis"].error

    @pytest.mark.asyncio
    async def test_check_all_services_with_pod_pool(self):
        """Test check with pod pool enabled."""
        service = HealthCheckService()
        service._kubernetes_manager = MagicMock()

        with (
            patch.object(service, "check_redis", new_callable=AsyncMock) as mock_redis,
            patch.object(service, "check_minio", new_callable=AsyncMock) as mock_minio,
            patch.object(service, "check_kubernetes", new_callable=AsyncMock) as mock_k8s,
            patch.object(service, "check_pod_pool", new_callable=AsyncMock) as mock_pool,
            patch("src.services.health.settings") as mock_settings,
        ):
            mock_settings.pod_pool_enabled = True
            mock_redis.return_value = HealthCheckResult("redis", HealthStatus.HEALTHY)
            mock_minio.return_value = HealthCheckResult("minio", HealthStatus.HEALTHY)
            mock_k8s.return_value = HealthCheckResult("kubernetes", HealthStatus.HEALTHY)
            mock_pool.return_value = HealthCheckResult("pod_pool", HealthStatus.HEALTHY)

            results = await service.check_all_services(use_cache=False)

            assert "pod_pool" in results
            mock_pool.assert_called_once()


class TestCheckRedis:
    """Tests for Redis health check."""

    @pytest.mark.asyncio
    async def test_check_redis_healthy(self):
        """Test Redis health check when healthy."""
        service = HealthCheckService()
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True
        mock_redis.set.return_value = True
        mock_redis.get.return_value = mock_redis.set.call_args  # Returns same value
        mock_redis.delete.return_value = True
        mock_redis.info.return_value = {
            "redis_version": "7.0.0",
            "connected_clients": 10,
            "used_memory": 1024 * 1024 * 50,  # 50MB
            "keyspace_hits": 1000,
            "keyspace_misses": 100,
            "uptime_in_seconds": 3600,
        }

        service._redis_client = mock_redis

        # Mock the get to return the same value as set
        async def mock_get(key):
            return mock_redis._test_value

        async def mock_set(key, value, **kwargs):
            mock_redis._test_value = value
            return True

        mock_redis.set.side_effect = mock_set
        mock_redis.get.side_effect = mock_get

        result = await service.check_redis()

        assert result.status == HealthStatus.HEALTHY
        assert result.service == "redis"
        assert result.response_time_ms is not None
        assert result.details.get("version") == "7.0.0"

    @pytest.mark.asyncio
    async def test_check_redis_degraded(self):
        """Test Redis health check when degraded (slow response)."""
        service = HealthCheckService()
        mock_redis = AsyncMock()

        async def slow_operation(*args, **kwargs):
            await asyncio.sleep(1.1)  # Over 1 second
            return True

        mock_redis.ping = slow_operation
        mock_redis.set.return_value = True
        mock_redis.delete.return_value = True
        mock_redis.info.return_value = {}

        async def mock_get(key):
            return mock_redis._test_value

        async def mock_set(key, value, **kwargs):
            mock_redis._test_value = value
            return True

        mock_redis.set.side_effect = mock_set
        mock_redis.get.side_effect = mock_get

        service._redis_client = mock_redis

        result = await service.check_redis()

        assert result.status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_check_redis_unhealthy(self):
        """Test Redis health check when unhealthy."""
        service = HealthCheckService()
        mock_redis = AsyncMock()
        mock_redis.ping.side_effect = Exception("Connection refused")

        service._redis_client = mock_redis

        result = await service.check_redis()

        assert result.status == HealthStatus.UNHEALTHY
        assert "Connection refused" in result.error


class TestCheckMinio:
    """Tests for MinIO health check."""

    @pytest.mark.asyncio
    async def test_check_minio_healthy(self):
        """Test MinIO health check when healthy."""
        service = HealthCheckService()
        mock_minio = MagicMock()
        mock_minio.bucket_exists.return_value = True
        mock_minio.put_object.return_value = None

        mock_response = MagicMock()
        mock_response.read.return_value = b"health check test content"
        mock_response.close.return_value = None
        mock_response.release_conn.return_value = None
        mock_minio.get_object.return_value = mock_response
        mock_minio.remove_object.return_value = None

        service._minio_client = mock_minio

        with patch("src.services.health.settings") as mock_settings:
            mock_settings.minio_endpoint = "localhost:9000"
            mock_settings.minio_bucket = "test-bucket"
            mock_settings.minio_secure = False

            result = await service.check_minio()

            assert result.status == HealthStatus.HEALTHY
            assert result.service == "minio"

    @pytest.mark.asyncio
    async def test_check_minio_bucket_created(self):
        """Test MinIO creates bucket if not exists."""
        service = HealthCheckService()
        mock_minio = MagicMock()
        mock_minio.bucket_exists.return_value = False
        mock_minio.make_bucket.return_value = None
        mock_minio.put_object.return_value = None

        mock_response = MagicMock()
        mock_response.read.return_value = b"health check test content"
        mock_response.close.return_value = None
        mock_response.release_conn.return_value = None
        mock_minio.get_object.return_value = mock_response
        mock_minio.remove_object.return_value = None

        service._minio_client = mock_minio

        with patch("src.services.health.settings") as mock_settings:
            mock_settings.minio_endpoint = "localhost:9000"
            mock_settings.minio_bucket = "test-bucket"
            mock_settings.minio_secure = False

            result = await service.check_minio()

            mock_minio.make_bucket.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_minio_unhealthy(self):
        """Test MinIO health check when unhealthy."""
        service = HealthCheckService()
        mock_minio = MagicMock()
        mock_minio.bucket_exists.side_effect = Exception("Connection failed")

        service._minio_client = mock_minio

        with patch("src.services.health.settings") as mock_settings:
            mock_settings.minio_endpoint = "localhost:9000"
            mock_settings.minio_bucket = "test-bucket"
            mock_settings.minio_secure = False

            result = await service.check_minio()

            assert result.status == HealthStatus.UNHEALTHY
            assert "Connection failed" in result.error


class TestCheckKubernetes:
    """Tests for Kubernetes health check."""

    @pytest.mark.asyncio
    async def test_check_kubernetes_not_configured(self):
        """Test Kubernetes check when not configured."""
        service = HealthCheckService()

        result = await service.check_kubernetes()

        assert result.status == HealthStatus.UNKNOWN
        assert "not configured" in result.error.lower()

    @pytest.mark.asyncio
    async def test_check_kubernetes_healthy(self):
        """Test Kubernetes health check when healthy."""
        service = HealthCheckService()
        mock_manager = MagicMock()
        mock_manager.get_pool_stats.return_value = {"python": {"available": 5, "in_use": 2}}
        mock_manager.namespace = "default"

        service._kubernetes_manager = mock_manager

        with patch("src.services.health.settings") as mock_settings:
            mock_settings.pod_pool_enabled = True

            result = await service.check_kubernetes()

            assert result.status == HealthStatus.HEALTHY
            assert result.details.get("namespace") == "default"

    @pytest.mark.asyncio
    async def test_check_kubernetes_unhealthy(self):
        """Test Kubernetes health check when unhealthy."""
        service = HealthCheckService()
        mock_manager = MagicMock()
        mock_manager.get_pool_stats.side_effect = Exception("API unreachable")

        service._kubernetes_manager = mock_manager

        result = await service.check_kubernetes()

        assert result.status == HealthStatus.UNHEALTHY
        assert "API unreachable" in result.error


class TestCheckPodPool:
    """Tests for pod pool health check."""

    @pytest.mark.asyncio
    async def test_check_pod_pool_not_configured(self):
        """Test pod pool check when not configured."""
        service = HealthCheckService()

        result = await service.check_pod_pool()

        assert result.status == HealthStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_check_pod_pool_healthy(self):
        """Test pod pool health check when healthy."""
        service = HealthCheckService()
        mock_manager = MagicMock()
        mock_manager.get_pool_stats.return_value = {
            "python": {"available": 5, "in_use": 2, "creating": 0, "target_size": 5},
            "javascript": {"available": 3, "in_use": 1, "creating": 0, "target_size": 3},
        }

        service._kubernetes_manager = mock_manager

        result = await service.check_pod_pool()

        assert result.status == HealthStatus.HEALTHY
        assert result.details["total_available"] == 8
        assert result.details["total_in_use"] == 3

    @pytest.mark.asyncio
    async def test_check_pod_pool_degraded_empty(self):
        """Test pod pool health check when empty."""
        service = HealthCheckService()
        mock_manager = MagicMock()
        mock_manager.get_pool_stats.return_value = {
            "python": {"available": 0, "in_use": 0, "creating": 0},
        }

        service._kubernetes_manager = mock_manager

        result = await service.check_pod_pool()

        assert result.status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_check_pod_pool_unhealthy(self):
        """Test pod pool health check when error."""
        service = HealthCheckService()
        mock_manager = MagicMock()
        mock_manager.get_pool_stats.side_effect = Exception("Pool error")

        service._kubernetes_manager = mock_manager

        result = await service.check_pod_pool()

        assert result.status == HealthStatus.UNHEALTHY


class TestGetOverallStatus:
    """Tests for overall status determination."""

    def test_overall_status_empty(self):
        """Test overall status with no results."""
        service = HealthCheckService()

        status = service.get_overall_status({})

        assert status == HealthStatus.UNKNOWN

    def test_overall_status_all_healthy(self):
        """Test overall status when all healthy."""
        service = HealthCheckService()
        results = {
            "redis": HealthCheckResult("redis", HealthStatus.HEALTHY),
            "minio": HealthCheckResult("minio", HealthStatus.HEALTHY),
        }

        status = service.get_overall_status(results)

        assert status == HealthStatus.HEALTHY

    def test_overall_status_one_unhealthy(self):
        """Test overall status when one is unhealthy."""
        service = HealthCheckService()
        results = {
            "redis": HealthCheckResult("redis", HealthStatus.HEALTHY),
            "minio": HealthCheckResult("minio", HealthStatus.UNHEALTHY),
        }

        status = service.get_overall_status(results)

        assert status == HealthStatus.UNHEALTHY

    def test_overall_status_one_degraded(self):
        """Test overall status when one is degraded."""
        service = HealthCheckService()
        results = {
            "redis": HealthCheckResult("redis", HealthStatus.HEALTHY),
            "minio": HealthCheckResult("minio", HealthStatus.DEGRADED),
        }

        status = service.get_overall_status(results)

        assert status == HealthStatus.DEGRADED

    def test_overall_status_unknown(self):
        """Test overall status with unknown status."""
        service = HealthCheckService()
        results = {
            "redis": HealthCheckResult("redis", HealthStatus.UNKNOWN),
            "minio": HealthCheckResult("minio", HealthStatus.HEALTHY),
        }

        status = service.get_overall_status(results)

        assert status == HealthStatus.UNKNOWN


class TestClose:
    """Tests for closing service connections."""

    @pytest.mark.asyncio
    async def test_close_with_redis(self):
        """Test closing with Redis client."""
        service = HealthCheckService()
        mock_redis = AsyncMock()
        service._redis_client = mock_redis

        await service.close()

        mock_redis.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_without_redis(self):
        """Test closing without Redis client."""
        service = HealthCheckService()

        # Should not raise
        await service.close()

    @pytest.mark.asyncio
    async def test_close_redis_timeout(self):
        """Test closing when Redis close times out."""
        service = HealthCheckService()
        mock_redis = AsyncMock()

        async def slow_close():
            await asyncio.sleep(10)

        mock_redis.close.side_effect = slow_close

        service._redis_client = mock_redis

        # Should not raise, just log warning
        await service.close()

    @pytest.mark.asyncio
    async def test_close_redis_error(self):
        """Test closing when Redis close raises error."""
        service = HealthCheckService()
        mock_redis = AsyncMock()
        mock_redis.close.side_effect = Exception("Close error")

        service._redis_client = mock_redis

        # Should not raise
        await service.close()


class TestGlobalInstance:
    """Tests for the global health_service instance."""

    def test_global_instance_exists(self):
        """Test that global instance exists."""
        assert health_service is not None
        assert isinstance(health_service, HealthCheckService)
