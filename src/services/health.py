"""Health check service for monitoring system dependencies."""

# Standard library imports
import asyncio
import time
from datetime import UTC, datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional

# Third-party imports
import redis.asyncio as redis
import structlog
from minio import Minio
from minio.error import S3Error

# Local application imports
from ..config import settings

if TYPE_CHECKING:
    from .kubernetes import KubernetesManager


logger = structlog.get_logger(__name__)


class HealthStatus(str, Enum):
    """Health check status enumeration."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class HealthCheckResult:
    """Health check result container."""

    def __init__(
        self,
        service: str,
        status: HealthStatus,
        response_time_ms: float | None = None,
        details: dict[str, Any] | None = None,
        error: str | None = None,
    ):
        self.service = service
        self.status = status
        self.response_time_ms = response_time_ms
        self.details = details or {}
        self.error = error
        self.timestamp = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "service": self.service,
            "status": self.status.value,
            "timestamp": self.timestamp.isoformat(),
        }

        if self.response_time_ms is not None:
            result["response_time_ms"] = round(self.response_time_ms, 2)

        if self.details:
            result["details"] = self.details

        if self.error:
            result["error"] = self.error

        return result


class HealthCheckService:
    """Service for performing health checks on system dependencies."""

    def __init__(self):
        """Initialize health check service."""
        self._redis_client: redis.Redis | None = None
        self._minio_client: Minio | None = None
        self._kubernetes_manager: KubernetesManager | None = None
        self._last_check_time: datetime | None = None
        self._cached_results: dict[str, HealthCheckResult] = {}
        self._cache_ttl_seconds = 30  # Cache results for 30 seconds

    def set_kubernetes_manager(self, manager: "KubernetesManager") -> None:
        """Set Kubernetes manager reference for health checks."""
        self._kubernetes_manager = manager

    async def check_all_services(self, use_cache: bool = True) -> dict[str, HealthCheckResult]:
        """Perform health checks on all services."""
        now = datetime.now(UTC)

        # Check if we can use cached results
        if (
            use_cache
            and self._last_check_time
            and (now - self._last_check_time).total_seconds() < self._cache_ttl_seconds
        ):
            return self._cached_results

        logger.info("Performing health checks on all services")

        # Run all health checks concurrently
        tasks = [
            self.check_redis(),
            self.check_minio(),
            self.check_kubernetes(),
        ]
        service_names = ["redis", "minio", "kubernetes"]

        # Add pod pool check if kubernetes manager is configured
        if self._kubernetes_manager and settings.pod_pool_enabled:
            tasks.append(self.check_pod_pool())
            service_names.append("pod_pool")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        health_results = {}

        for i, result in enumerate(results):
            service_name = service_names[i]
            if isinstance(result, Exception):
                logger.error(f"Health check failed for {service_name}", error=str(result))
                health_results[service_name] = HealthCheckResult(
                    service=service_name,
                    status=HealthStatus.UNHEALTHY,
                    error=str(result),
                )
            else:
                health_results[service_name] = result

        # Cache results
        self._cached_results = health_results
        self._last_check_time = now

        return health_results

    async def check_redis(self) -> HealthCheckResult:
        """Check Redis connectivity and performance."""
        start_time = time.time()

        try:
            # Use shared connection pool
            from ..core.pool import redis_pool

            if not self._redis_client:
                self._redis_client = redis_pool.get_client()

            # Test basic connectivity
            await self._redis_client.ping()

            # Test read/write operations
            test_key = redis_pool.make_key("health_check:test")
            test_value = f"test_{int(time.time())}"

            await self._redis_client.set(test_key, test_value, ex=60)
            retrieved_value = await self._redis_client.get(test_key)
            await self._redis_client.delete(test_key)

            if retrieved_value != test_value:
                raise Exception("Redis read/write test failed")

            # Get Redis info
            info = await self._redis_client.info()

            response_time = (time.time() - start_time) * 1000

            # Determine status based on response time and memory usage
            status = HealthStatus.HEALTHY
            if response_time > 1000:  # > 1 second
                status = HealthStatus.DEGRADED

            memory_usage_mb = info.get("used_memory", 0) / (1024 * 1024)
            max_memory_mb = info.get("maxmemory", 0) / (1024 * 1024) if info.get("maxmemory", 0) > 0 else None

            details = {
                "version": info.get("redis_version", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
                "memory_usage_mb": round(memory_usage_mb, 2),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
                "uptime_seconds": info.get("uptime_in_seconds", 0),
            }

            if max_memory_mb:
                details["max_memory_mb"] = round(max_memory_mb, 2)
                details["memory_usage_percent"] = round((memory_usage_mb / max_memory_mb) * 100, 2)

            return HealthCheckResult(
                service="redis",
                status=status,
                response_time_ms=response_time,
                details=details,
            )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            logger.error(
                "Redis health check failed",
                error=str(e),
                response_time_ms=response_time,
            )

            return HealthCheckResult(
                service="redis",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=str(e),
            )

    async def check_minio(self) -> HealthCheckResult:
        """Check MinIO/S3 connectivity and performance."""
        start_time = time.time()

        try:
            # Create MinIO client if not exists
            # Uses the config's create_client method which handles IAM vs static credentials
            if not self._minio_client:
                self._minio_client = settings.minio.create_client()

            loop = asyncio.get_event_loop()

            # Check if our bucket exists (doesn't require s3:ListAllMyBuckets permission)
            bucket_exists = await loop.run_in_executor(None, self._minio_client.bucket_exists, settings.minio_bucket)

            if not bucket_exists:
                # Try to create the bucket
                await loop.run_in_executor(None, self._minio_client.make_bucket, settings.minio_bucket)
                logger.info(f"Created missing bucket: {settings.minio_bucket}")

            # Test read/write operations
            test_object = f"health_check/test_{int(time.time())}.txt"
            test_content = b"health check test content"

            # Create a BytesIO object for the upload
            from io import BytesIO

            test_data = BytesIO(test_content)

            # Upload test object
            await loop.run_in_executor(
                None,
                self._minio_client.put_object,
                settings.minio_bucket,
                test_object,
                test_data,
                len(test_content),
            )

            # Download test object
            response = await loop.run_in_executor(
                None, self._minio_client.get_object, settings.minio_bucket, test_object
            )

            downloaded_content = response.read()
            response.close()
            response.release_conn()

            # Clean up test object
            await loop.run_in_executor(
                None,
                self._minio_client.remove_object,
                settings.minio_bucket,
                test_object,
            )

            if downloaded_content != test_content:
                raise Exception("MinIO read/write test failed")

            response_time = (time.time() - start_time) * 1000

            # Determine status based on response time
            status = HealthStatus.HEALTHY
            if response_time > 2000:  # > 2 seconds
                status = HealthStatus.DEGRADED

            details = {
                "endpoint": settings.minio_endpoint,
                "bucket": settings.minio_bucket,
                "bucket_exists": bucket_exists,
                "secure": settings.minio_secure,
            }

            return HealthCheckResult(
                service="minio",
                status=status,
                response_time_ms=response_time,
                details=details,
            )

        except S3Error as e:
            response_time = (time.time() - start_time) * 1000
            logger.error(
                "MinIO health check failed",
                error=str(e),
                response_time_ms=response_time,
            )

            return HealthCheckResult(
                service="minio",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=f"S3 Error: {e.message if hasattr(e, 'message') else str(e)}",
            )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            logger.error(
                "MinIO health check failed",
                error=str(e),
                response_time_ms=response_time,
            )

            return HealthCheckResult(
                service="minio",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=str(e),
            )

    async def check_kubernetes(self) -> HealthCheckResult:
        """Check Kubernetes API connectivity and status."""
        start_time = time.time()

        try:
            # Check if Kubernetes manager is configured
            if not self._kubernetes_manager:
                return HealthCheckResult(
                    service="kubernetes",
                    status=HealthStatus.UNKNOWN,
                    error="Kubernetes manager not configured",
                )

            # Test connectivity by getting pool stats
            pool_stats = self._kubernetes_manager.get_pool_stats()

            response_time = (time.time() - start_time) * 1000

            # Determine status based on pool health
            status = HealthStatus.HEALTHY
            if response_time > 3000:  # > 3 seconds
                status = HealthStatus.DEGRADED

            # Get namespace info
            namespace = self._kubernetes_manager.namespace or "default"

            details = {
                "namespace": namespace,
                "pool_enabled": settings.pod_pool_enabled,
                "sidecar_image": settings.k8s_sidecar_image,
                "total_languages_configured": len(pool_stats),
                "pool_stats": pool_stats,
            }

            return HealthCheckResult(
                service="kubernetes",
                status=status,
                response_time_ms=response_time,
                details=details,
            )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            logger.error(
                "Kubernetes health check failed",
                error=str(e),
                response_time_ms=response_time,
            )

            return HealthCheckResult(
                service="kubernetes",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=str(e),
            )

    async def check_pod_pool(self) -> HealthCheckResult:
        """Check pod pool health and statistics."""
        start_time = time.time()

        try:
            if not self._kubernetes_manager:
                return HealthCheckResult(
                    service="pod_pool",
                    status=HealthStatus.UNKNOWN,
                    error="Kubernetes manager not configured",
                )

            # Get pool statistics
            stats = self._kubernetes_manager.get_pool_stats()

            response_time = (time.time() - start_time) * 1000

            # Calculate totals
            total_available = sum(s.get("available", 0) for s in stats.values())
            total_in_use = sum(s.get("in_use", 0) for s in stats.values())
            total_creating = sum(s.get("creating", 0) for s in stats.values())

            # Determine status
            status = HealthStatus.HEALTHY
            if total_available == 0 and total_in_use == 0:
                status = HealthStatus.DEGRADED  # Pool is empty

            # Per-language breakdown
            language_stats = {}
            for lang, s in stats.items():
                language_stats[lang] = {
                    "available": s.get("available", 0),
                    "in_use": s.get("in_use", 0),
                    "creating": s.get("creating", 0),
                    "target_size": s.get("target_size", 0),
                }

            details = {
                "enabled": True,
                "architecture": "kubernetes-pods",
                "total_available": total_available,
                "total_in_use": total_in_use,
                "total_creating": total_creating,
                "languages": language_stats,
            }

            return HealthCheckResult(
                service="pod_pool",
                status=status,
                response_time_ms=response_time,
                details=details,
            )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            logger.error("Pod pool health check failed", error=str(e))

            return HealthCheckResult(
                service="pod_pool",
                status=HealthStatus.UNHEALTHY,
                response_time_ms=response_time,
                error=str(e),
            )

    def get_overall_status(self, service_results: dict[str, HealthCheckResult]) -> HealthStatus:
        """Determine overall system health status."""
        if not service_results:
            return HealthStatus.UNKNOWN

        statuses = [result.status for result in service_results.values()]

        # If any service is unhealthy, overall status is unhealthy
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY

        # If any service is degraded, overall status is degraded
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED

        # If all services are healthy, overall status is healthy
        if all(status == HealthStatus.HEALTHY for status in statuses):
            return HealthStatus.HEALTHY

        return HealthStatus.UNKNOWN

    async def close(self) -> None:
        """Close all client connections."""
        try:
            # Close Redis connection with timeout
            if self._redis_client:
                try:
                    await asyncio.wait_for(self._redis_client.close(), timeout=2.0)
                except TimeoutError:
                    logger.warning("Redis connection close timed out during shutdown")
                except Exception as e:
                    logger.warning(f"Error closing Redis connection during shutdown: {e}")

            logger.info("Closed health check service connections")

        except Exception as e:
            logger.error("Error closing health check service connections", error=str(e))


# Global health check service instance
health_service = HealthCheckService()
