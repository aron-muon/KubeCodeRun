"""Configuration validation utilities."""

import logging
from typing import Any, Dict, List

import redis
from minio.error import S3Error

from ..config import settings

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when configuration validation fails."""

    pass


class ConfigValidator:
    """Validates application configuration and external service connectivity."""

    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def validate_all(self) -> bool:
        """Validate all configuration settings and external services."""
        self.errors.clear()
        self.warnings.clear()

        # Validate basic configuration
        self._validate_api_config()
        self._validate_security_config()
        self._validate_resource_limits()
        self._validate_file_config()

        # Validate external services
        self._validate_redis_connection()
        self._validate_minio_connection()
        self._validate_kubernetes_config()

        # Log results
        if self.warnings:
            for warning in self.warnings:
                logger.warning(f"Configuration warning: {warning}")

        if self.errors:
            for error in self.errors:
                logger.error(f"Configuration error: {error}")
            return False

        return True

    def _validate_api_config(self):
        """Validate API configuration."""
        # Check API key strength
        if len(settings.api_key) < 16:
            self.errors.append("API key must be at least 16 characters long")

        if settings.api_key == "test-api-key":
            self.warnings.append("Using default API key - change this in production")

        # Validate additional API keys
        if settings.api_keys:
            for key in settings.api_keys:
                if len(key) < 16:
                    self.errors.append(f"Additional API key too short: {key[:8]}...")

    def _validate_security_config(self):
        """Validate security configuration."""
        # Check file extensions
        if not settings.allowed_file_extensions:
            self.warnings.append("No allowed file extensions configured")

        # Validate security settings
        if not settings.enable_network_isolation:
            self.warnings.append("Network isolation is disabled - security risk")

        if not settings.enable_filesystem_isolation:
            self.warnings.append("Filesystem isolation is disabled - security risk")

    def _validate_resource_limits(self):
        """Validate resource limit configuration."""
        # Check critical limit conflicts
        if settings.max_total_file_size_mb < settings.max_file_size_mb:
            self.errors.append("Total file size limit is less than individual file size limit")

    def _validate_file_config(self):
        """Validate file handling configuration."""
        # Validate file extensions format
        for ext in settings.allowed_file_extensions:
            if not ext.startswith("."):
                self.errors.append(f"File extension must start with dot: {ext}")

    def _validate_redis_connection(self):
        """Validate Redis connection."""
        try:
            mode = settings.redis_mode
            ssl_kwargs = settings.redis.get_ssl_kwargs()

            if mode == "cluster":
                from redis.cluster import RedisCluster as SyncCluster

                if settings.redis.url:
                    client = SyncCluster.from_url(
                        settings.redis.url,
                        decode_responses=True,
                        socket_timeout=settings.redis_socket_timeout,
                        ssl=settings.redis.ssl,
                        **ssl_kwargs,
                    )
                else:
                    nodes = settings.redis.parse_nodes(settings.redis_cluster_nodes)
                    if not nodes:
                        self.errors.append(
                            "REDIS_CLUSTER_NODES or REDIS_URL required for cluster mode"
                        )
                        return
                    client = SyncCluster(
                        host=nodes[0][0],
                        port=nodes[0][1],
                        decode_responses=True,
                        socket_timeout=settings.redis_socket_timeout,
                        ssl=settings.redis.ssl,
                        **ssl_kwargs,
                    )
                client.ping()
                client.close()
            elif mode == "sentinel":
                from redis.sentinel import Sentinel as SyncSentinel

                nodes = settings.redis.parse_nodes(settings.redis_sentinel_nodes)
                if not nodes:
                    self.errors.append("REDIS_SENTINEL_NODES required for sentinel mode")
                    return
                sentinel = SyncSentinel(nodes, socket_timeout=settings.redis_socket_timeout, **ssl_kwargs)
                master = sentinel.master_for(
                    settings.redis_sentinel_master,
                    db=settings.redis_sentinel_db,
                    socket_timeout=settings.redis_socket_timeout,
                )
                master.ping()
                master.close()
            else:
                client = redis.from_url(
                    settings.get_redis_url(),
                    socket_timeout=settings.redis_socket_timeout,
                    socket_connect_timeout=settings.redis_socket_connect_timeout,
                    **ssl_kwargs,
                )
                client.ping()
                client.close()

        except redis.ConnectionError as e:
            # Treat as warning in development mode to allow startup without Redis
            if settings.api_debug:
                self.warnings.append(f"Cannot connect to Redis: {e}")
            else:
                self.errors.append(f"Cannot connect to Redis: {e}")
        except redis.AuthenticationError as e:
            self.errors.append(f"Redis authentication failed: {e}")
        except Exception as e:
            # Treat as warning in development mode
            if settings.api_debug:
                self.warnings.append(f"Redis validation error: {e}")
            else:
                self.errors.append(f"Redis validation error: {e}")

    def _validate_minio_connection(self):
        """Validate MinIO/S3 connection."""
        try:
            # Use the minio config's create_client method which handles IAM vs static credentials
            client = settings.minio.create_client()

            # Test connection by checking if our specific bucket exists
            # This only requires s3:ListBucket on the specific bucket, not s3:ListAllMyBuckets
            bucket_exists = client.bucket_exists(settings.minio_bucket)

            if not bucket_exists:
                self.warnings.append(f"MinIO bucket '{settings.minio_bucket}' does not exist - will be created")

        except S3Error as e:
            # Treat as warning in development mode to allow startup without MinIO
            if settings.api_debug:
                self.warnings.append(f"MinIO S3 error: {e}")
            else:
                self.errors.append(f"MinIO S3 error: {e}")
        except Exception as e:
            # Treat as warning in development mode
            if settings.api_debug:
                self.warnings.append(f"MinIO validation error: {e}")
            else:
                self.errors.append(f"MinIO validation error: {e}")

    def _validate_kubernetes_config(self):
        """Validate Kubernetes configuration."""
        try:
            # Check if Kubernetes settings are configured
            if settings.pod_pool_enabled:
                # Validate resource limits are reasonable
                if settings.k8s_memory_limit:
                    # Parse memory limit (e.g., "512Mi", "1Gi")
                    try:
                        mem_str = settings.k8s_memory_limit
                        if mem_str.endswith("Gi"):
                            mem_mb = int(mem_str[:-2]) * 1024
                        elif mem_str.endswith("Mi"):
                            mem_mb = int(mem_str[:-2])
                        else:
                            mem_mb = int(mem_str) // (1024 * 1024)

                        if mem_mb < 64:
                            self.warnings.append(f"Kubernetes memory limit {mem_str} may be too low")
                    except (ValueError, TypeError):
                        self.warnings.append(f"Invalid Kubernetes memory limit format: {settings.k8s_memory_limit}")

                # Validate image registry is set
                if not settings.k8s_image_registry:
                    self.warnings.append("Kubernetes image registry not configured")

        except Exception as e:
            self.warnings.append(f"Kubernetes config validation error: {e}")


def validate_configuration() -> bool:
    """Validate application configuration."""
    validator = ConfigValidator()
    return validator.validate_all()


def get_configuration_summary() -> dict[str, Any]:
    """Get a summary of current configuration for debugging."""
    return {
        "debug": settings.api_debug,
        "languages": len(settings.supported_languages),
        "max_execution_time": settings.max_execution_time,
        "max_memory_mb": settings.max_memory_mb,
    }
