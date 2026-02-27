"""Configuration management for the Code Interpreter API.

This module provides a unified Settings class that maintains full backward
compatibility with the original flat config.py while organizing settings
into logical groups.

Usage:
    from src.config import settings

    # Access grouped settings
    settings.api.host
    settings.kubernetes.namespace
    settings.redis.get_url()

    # Or use the backward-compatible flat access
    settings.api_host
    settings.k8s_namespace
    settings.get_redis_url()
"""

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Import grouped configurations
from .api import APIConfig
from .kubernetes import KubernetesConfig
from .languages import (
    LANGUAGES,
    LanguageConfig,
    get_execution_command,
    get_file_extension,
    get_image_for_language,
    get_language,
    get_supported_languages,
    get_user_id_for_language,
    is_supported_language,
    uses_stdin,
)
from .logging import LoggingConfig
from .minio import MinIOConfig
from .redis import RedisConfig
from .resources import ResourcesConfig
from .security import SecurityConfig


class Settings(BaseSettings):
    """Application settings with environment variable support.

    This class provides both:
    1. Grouped access via nested configs (settings.api.host)
    2. Flat access for backward compatibility (settings.api_host)
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

    # ========================================================================
    # BACKWARD COMPATIBILITY - All original flat fields preserved
    # ========================================================================

    # API Configuration
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_debug: bool = Field(default=False)
    api_reload: bool = Field(default=False)

    # SSL/HTTPS Configuration
    enable_https: bool = Field(default=False)
    https_port: int = Field(default=443, ge=1, le=65535)
    ssl_cert_file: str | None = Field(default=None)
    ssl_key_file: str | None = Field(default=None)
    ssl_redirect: bool = Field(default=False)
    ssl_ca_certs: str | None = Field(default=None)

    # Authentication Configuration
    api_key: str = Field(default="test-api-key", min_length=16)
    api_keys: str | None = Field(default=None)
    api_key_header: str = Field(default="x-api-key")
    api_key_cache_ttl: int = Field(default=300, ge=60)

    # API Key Management Configuration
    master_api_key: str | None = Field(
        default=None,
        description="Master API key for admin operations (CLI key management)",
    )
    rate_limit_enabled: bool = Field(default=True, description="Enable per-key rate limiting for Redis-managed keys")

    # Redis Configuration
    redis_mode: Literal["standalone", "cluster", "sentinel"] = Field(
        default="standalone",
        description="Redis deployment mode: standalone, cluster, or sentinel",
    )
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password: str | None = Field(default=None)
    redis_db: int = Field(default=0, ge=0, le=15)
    redis_url: str | None = Field(default=None)
    redis_max_connections: int = Field(default=20, ge=1)
    redis_socket_timeout: int = Field(default=5, ge=1)
    redis_socket_connect_timeout: int = Field(default=5, ge=1)

    # Redis Cluster
    redis_cluster_nodes: str | None = Field(
        default=None,
        description="Comma-separated host:port pairs for Redis Cluster startup nodes",
    )

    # Redis Sentinel
    redis_sentinel_nodes: str | None = Field(
        default=None,
        description="Comma-separated host:port pairs for Sentinel instances",
    )
    redis_sentinel_master: str = Field(
        default="mymaster",
        description="Name of the Sentinel-monitored master",
    )
    redis_sentinel_password: str | None = Field(
        default=None,
        description="Password for authenticating to Sentinel instances",
    )

    # Redis Key Prefix
    redis_key_prefix: str = Field(
        default="",
        description="Optional prefix prepended to every Redis key (e.g. 'prod:', 'kubecoderun:')",
    )

    # Redis TLS/SSL
    redis_tls_enabled: bool = Field(
        default=False,
        description="Enable TLS/SSL for Redis connections",
    )
    redis_tls_cert_file: str | None = Field(
        default=None,
        description="Path to client TLS certificate (mutual TLS)",
    )
    redis_tls_key_file: str | None = Field(
        default=None,
        description="Path to client TLS private key (mutual TLS)",
    )
    redis_tls_ca_cert_file: str | None = Field(
        default=None,
        description="Path to CA certificate for verifying the server",
    )
    redis_tls_insecure: bool = Field(
        default=False,
        description="Skip TLS certificate verification (NOT recommended for production)",
    )
    redis_tls_check_hostname: bool = Field(
        default=False,
        description="Enable TLS hostname verification (off by default for managed Redis / cluster)",
    )

    # MinIO/S3 Configuration
    minio_endpoint: str = Field(default="localhost:9000")
    minio_access_key: str | None = Field(default=None)
    minio_secret_key: str | None = Field(default=None)
    minio_secure: bool = Field(default=False)
    minio_bucket: str = Field(default="kubecoderun-files")
    minio_region: str = Field(default="us-east-1")
    minio_use_iam: bool = Field(
        default=False,
        description="Use IAM credentials instead of access key/secret key",
    )

    # Kubernetes Configuration
    k8s_namespace: str = Field(
        default="",
        description="Namespace for execution pods (empty = use API's namespace)",
    )
    k8s_service_account: str = Field(
        default="kubecoderun-executor",
        description="Service account for execution pods",
    )
    k8s_sidecar_image: str = Field(
        default="aronmuon/kubecoderun-sidecar-agent:latest",
        description="Sidecar container image for pod communication",
    )
    k8s_sidecar_port: int = Field(default=8080, ge=1, le=65535, description="Sidecar HTTP API port")
    k8s_sidecar_cpu_limit: str = Field(default="500m", description="Sidecar CPU limit (user code inherits this)")
    k8s_sidecar_memory_limit: str = Field(default="512Mi", description="Sidecar memory limit (user code inherits this)")
    k8s_sidecar_cpu_request: str = Field(default="100m", description="Sidecar CPU request")
    k8s_sidecar_memory_request: str = Field(default="256Mi", description="Sidecar memory request")
    k8s_cpu_limit: str = Field(default="1", description="CPU limit for execution pods")
    k8s_memory_limit: str = Field(default="512Mi", description="Memory limit for execution pods")
    k8s_cpu_request: str = Field(default="100m", description="CPU request for execution pods")
    k8s_memory_request: str = Field(default="128Mi", description="Memory request for execution pods")
    k8s_run_as_user: int = Field(default=65532, ge=1, description="UID to run containers as")
    k8s_execution_mode: Literal["agent", "nsenter"] = Field(
        default="agent",
        description="Execution mode: 'agent' (no nsenter/capabilities, gVisor-safe) or 'nsenter' (legacy)",
    )
    k8s_executor_port: int = Field(
        default=9090,
        ge=1,
        le=65535,
        description="Port for the executor HTTP server inside the main container",
    )
    k8s_seccomp_profile_type: Literal["RuntimeDefault", "Unconfined"] = Field(
        default="RuntimeDefault",
        description="Seccomp profile type for execution pods",
    )
    k8s_job_ttl_seconds: int = Field(
        default=60,
        ge=10,
        le=3600,
        description="TTL for completed Jobs before cleanup",
    )
    k8s_job_deadline_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="Maximum execution time for Jobs",
    )
    k8s_image_registry: str = Field(
        default="aronmuon/kubecoderun",
        description="Container image registry prefix (images: {registry}-{language}:{tag})",
    )
    k8s_image_tag: str = Field(default="latest", description="Container image tag for execution pods")
    k8s_image_pull_policy: str = Field(
        default="Always",
        description="Image pull policy for execution pods (Always, IfNotPresent, Never)",
    )
    k8s_image_pull_secrets: str = Field(
        default="",
        description="Comma-separated list of secret names for pulling images from private registries",
    )

    # GKE Sandbox (gVisor) Configuration
    gke_sandbox_enabled: bool = Field(
        default=False,
        description="Enable GKE Sandbox (gVisor) for additional kernel isolation",
    )
    gke_sandbox_runtime_class: str = Field(
        default="gvisor",
        description="Runtime class name for sandboxed pods",
    )
    gke_sandbox_node_selector: str | None = Field(
        default=None,
        description="JSON string of node selector for sandbox-enabled nodes",
    )
    gke_sandbox_custom_tolerations: str | None = Field(
        default=None,
        description="JSON string of custom tolerations for node pool taints",
    )

    # Resource Limits - Execution
    max_execution_time: int = Field(default=30, ge=1, le=600)
    max_memory_mb: int = Field(default=512, ge=64, le=16384)
    max_cpus: float = Field(
        default=4.0,
        ge=0.5,
        le=16.0,
        description="Maximum CPU cores available to execution containers",
    )
    max_cpu_quota: int = Field(default=50000, ge=10000, le=100000)  # Deprecated, use max_cpus
    max_pids: int = Field(
        default=512,
        ge=64,
        le=4096,
        description="Per-container process limit (cgroup pids_limit). Prevents fork bombs.",
    )
    max_open_files: int = Field(default=1024, ge=64, le=4096)

    # Resource Limits - Files
    max_file_size_mb: int = Field(default=10, ge=1, le=500)
    max_total_file_size_mb: int = Field(default=50, ge=10, le=2000)
    max_files_per_session: int = Field(default=50, ge=1, le=500)
    max_output_files: int = Field(default=10, ge=1, le=100)
    max_filename_length: int = Field(default=255, ge=1, le=255)

    # Resource Limits - Sessions
    max_concurrent_executions: int = Field(default=10, ge=1, le=50)
    max_sessions_per_entity: int = Field(default=100, ge=1, le=1000)

    # Session Configuration
    session_ttl_hours: int = Field(default=24, ge=1, le=168)
    session_cleanup_interval_minutes: int = Field(default=10, ge=1, le=1440)
    session_id_length: int = Field(default=32, ge=16, le=64)
    enable_orphan_minio_cleanup: bool = Field(default=False)

    # Pod Configuration
    pod_ttl_minutes: int = Field(default=5, ge=1, le=1440)
    pod_cleanup_interval_minutes: int = Field(default=5, ge=1, le=60)

    # Pod Pool Configuration
    pod_pool_enabled: bool = Field(default=True)
    pod_pool_warmup_on_startup: bool = Field(default=True)

    # Per-language pool sizes (0 = on-demand only, no pre-warming)
    pod_pool_py: int = Field(default=5, ge=0, le=50, description="Python pool size")
    pod_pool_js: int = Field(default=2, ge=0, le=50, description="JavaScript pool size")
    pod_pool_ts: int = Field(default=0, ge=0, le=50, description="TypeScript pool size")
    pod_pool_go: int = Field(default=0, ge=0, le=50, description="Go pool size")
    pod_pool_java: int = Field(default=0, ge=0, le=50, description="Java pool size")
    pod_pool_c: int = Field(default=0, ge=0, le=50, description="C pool size")
    pod_pool_cpp: int = Field(default=0, ge=0, le=50, description="C++ pool size")
    pod_pool_php: int = Field(default=0, ge=0, le=50, description="PHP pool size")
    pod_pool_rs: int = Field(default=0, ge=0, le=50, description="Rust pool size")
    pod_pool_r: int = Field(default=0, ge=0, le=50, description="R pool size")
    pod_pool_f90: int = Field(default=0, ge=0, le=50, description="Fortran pool size")
    pod_pool_d: int = Field(default=0, ge=0, le=50, description="D pool size")

    # Pool Optimization Configuration
    pod_pool_parallel_batch: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Number of pods to start in parallel during warmup",
    )
    pod_pool_replenish_interval: int = Field(
        default=2, ge=1, le=30, description="Seconds between pool replenishment checks"
    )
    pod_pool_exhaustion_trigger: bool = Field(
        default=True,
        description="Trigger immediate replenishment when pool is exhausted",
    )

    # WAN Network Access Configuration
    # When enabled, execution pods can access the public internet
    # but are blocked from accessing host, other pods, and private networks
    enable_wan_access: bool = Field(
        default=False,
        description="Enable WAN-only network access for execution pods",
    )
    wan_network_name: str = Field(
        default="kubecoderun-wan",
        description="Network name for WAN-access pods",
    )
    wan_dns_servers: list[str] = Field(
        default_factory=lambda: ["8.8.8.8", "1.1.1.1", "8.8.4.4"],
        description="Public DNS servers for WAN-access pods",
    )

    # Pod Hardening Configuration
    pod_mask_host_info: bool = Field(
        default=True,
        description="Mask sensitive /proc paths to prevent host info leakage",
    )
    pod_generic_hostname: str = Field(
        default="sandbox",
        description="Generic hostname for execution pods",
    )

    # State Persistence Configuration - Python session state across executions
    state_persistence_enabled: bool = Field(
        default=True, description="Enable Python session state persistence via Redis"
    )
    state_ttl_seconds: int = Field(
        default=7200,
        ge=60,
        le=86400,
        description="TTL for persisted Python session state in Redis (seconds). Default: 2 hours",
    )
    state_max_size_mb: int = Field(default=50, ge=1, le=200, description="Maximum size for serialized state in MB")
    state_capture_on_error: bool = Field(
        default=False, description="Capture and persist state even when execution fails"
    )

    # State Archival Configuration - Hybrid Redis + MinIO storage
    state_archive_enabled: bool = Field(
        default=True, description="Enable archiving inactive states from Redis to MinIO"
    )
    state_archive_after_seconds: int = Field(
        default=3600,
        ge=300,
        le=86400,
        description="Archive state to MinIO after this many seconds of inactivity. Default: 1 hour",
    )
    state_archive_ttl_days: int = Field(
        default=7,
        ge=1,
        le=30,
        description="Keep archived states in MinIO for this many days. Default: 7 days",
    )
    state_archive_check_interval_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="How often to check for states to archive. Default: 5 minutes",
    )

    # Detailed Metrics Configuration
    detailed_metrics_enabled: bool = Field(
        default=True,
        description="Enable detailed per-key, per-language metrics tracking",
    )
    metrics_buffer_size: int = Field(
        default=10000,
        ge=1000,
        le=100000,
        description="Maximum number of recent metrics to buffer in memory",
    )
    metrics_archive_enabled: bool = Field(
        default=True,
        description="Enable archiving metrics to MinIO for long-term storage",
    )
    metrics_archive_retention_days: int = Field(
        default=90,
        ge=7,
        le=365,
        description="Keep archived metrics in MinIO for this many days",
    )

    # SQLite Metrics Configuration
    sqlite_metrics_enabled: bool = Field(
        default=True,
        description="Enable SQLite-based metrics storage for long-term analytics",
    )
    sqlite_metrics_db_path: str = Field(
        default="data/metrics.db",
        description="Path to SQLite metrics database file",
    )
    metrics_execution_retention_days: int = Field(
        default=90,
        ge=7,
        le=365,
        description="Retain individual execution records for this many days",
    )
    metrics_daily_retention_days: int = Field(
        default=365,
        ge=30,
        le=730,
        description="Retain daily aggregate records for this many days",
    )
    metrics_aggregation_interval_minutes: int = Field(
        default=60,
        ge=5,
        le=1440,
        description="How often to run metrics aggregation (minutes)",
    )

    # Security Configuration
    allowed_file_extensions: list[str] = Field(
        default_factory=lambda: [
            ".txt",
            ".py",
            ".js",
            ".ts",
            ".go",
            ".java",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".php",
            ".rs",
            ".r",
            ".f90",
            ".d",
            ".json",
            ".csv",
            ".xml",
            ".yaml",
            ".yml",
            ".md",
            ".sql",
            ".sh",
            ".bat",
            ".ps1",
            ".dockerfile",
            ".makefile",
        ]
    )
    blocked_file_patterns: list[str] = Field(default_factory=lambda: ["*.exe", "*.dll", "*.so", "*.dylib", "*.bin"])
    enable_network_isolation: bool = Field(default=True)
    enable_filesystem_isolation: bool = Field(default=True)

    # Language Configuration - now uses LANGUAGES from languages.py
    supported_languages: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _set_supported_languages(cls, data):
        """Initialize supported_languages with registry-prefixed images."""
        if isinstance(data, dict):
            if data.get("supported_languages"):
                return data

            registry = data.get("k8s_image_registry", "aronmuon/kubecoderun")
            tag = data.get("k8s_image_tag", "latest")
            data["supported_languages"] = {
                code: {
                    "image": (
                        f"{registry}-{lang.image.rsplit(':', 1)[0]}:{tag}"
                        if registry
                        else f"{lang.image.rsplit(':', 1)[0]}:{tag}"
                    ),
                    "timeout_multiplier": lang.timeout_multiplier,
                    "memory_multiplier": lang.memory_multiplier,
                }
                for code, lang in LANGUAGES.items()
            }
        return data

    # Logging Configuration
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    log_file: str | None = Field(default=None)
    log_max_size_mb: int = Field(default=100, ge=1)
    log_backup_count: int = Field(default=5, ge=1)
    enable_access_logs: bool = Field(default=True)
    enable_security_logs: bool = Field(default=True)

    # Health Check Configuration
    health_check_interval: int = Field(default=30, ge=10)
    health_check_timeout: int = Field(default=5, ge=1)

    # Development Configuration
    enable_cors: bool = Field(default=False)
    cors_origins: list[str] = Field(default_factory=list)
    enable_docs: bool = Field(default=True)

    # ========================================================================
    # VALIDATORS (preserved from original)
    # ========================================================================

    @field_validator("api_keys", mode="before")
    @classmethod
    def parse_api_keys(cls, v):
        """Parse comma-separated API keys into a list."""
        return [key.strip() for key in v.split(",") if key.strip()] if v else None

    @field_validator("redis_host", mode="before")
    @classmethod
    def sanitize_redis_host(cls, v):
        """Strip accidental URL scheme from Redis host."""
        if isinstance(v, str):
            for scheme in ("rediss://", "redis://"):
                if v.lower().startswith(scheme):
                    v = v[len(scheme):].rstrip("/")
                    break
        return v

    @field_validator("minio_endpoint")
    @classmethod
    def validate_minio_endpoint(cls, v):
        """Ensure MinIO endpoint doesn't include protocol."""
        if v.startswith(("http://", "https://")):
            raise ValueError("MinIO endpoint should not include protocol (use minio_secure instead)")
        return v

    # ========================================================================
    # GROUPED CONFIG ACCESS (new)
    # ========================================================================

    @property
    def api(self) -> APIConfig:
        """Access API configuration group."""
        return APIConfig(
            api_host=self.api_host,
            api_port=self.api_port,
            api_debug=self.api_debug,
            api_reload=self.api_reload,
            enable_https=self.enable_https,
            https_port=self.https_port,
            ssl_cert_file=self.ssl_cert_file,
            ssl_key_file=self.ssl_key_file,
            ssl_redirect=self.ssl_redirect,
            ssl_ca_certs=self.ssl_ca_certs,
            enable_cors=self.enable_cors,
            cors_origins=self.cors_origins,
            enable_docs=self.enable_docs,
        )

    @property
    def redis(self) -> RedisConfig:
        """Access Redis configuration group."""
        return RedisConfig(
            redis_mode=self.redis_mode,
            redis_host=self.redis_host,
            redis_port=self.redis_port,
            redis_password=self.redis_password,
            redis_db=self.redis_db,
            redis_url=self.redis_url,
            redis_max_connections=self.redis_max_connections,
            redis_socket_timeout=self.redis_socket_timeout,
            redis_socket_connect_timeout=self.redis_socket_connect_timeout,
            redis_cluster_nodes=self.redis_cluster_nodes,
            redis_sentinel_nodes=self.redis_sentinel_nodes,
            redis_sentinel_master=self.redis_sentinel_master,
            redis_sentinel_password=self.redis_sentinel_password,
            redis_key_prefix=self.redis_key_prefix,
            redis_tls_enabled=self.redis_tls_enabled,
            redis_tls_cert_file=self.redis_tls_cert_file,
            redis_tls_key_file=self.redis_tls_key_file,
            redis_tls_ca_cert_file=self.redis_tls_ca_cert_file,
            redis_tls_insecure=self.redis_tls_insecure,
            redis_tls_check_hostname=self.redis_tls_check_hostname,
        )

    @property
    def minio(self) -> MinIOConfig:
        """Access MinIO configuration group."""
        return MinIOConfig(
            minio_endpoint=self.minio_endpoint,
            minio_access_key=self.minio_access_key,
            minio_secret_key=self.minio_secret_key,
            minio_secure=self.minio_secure,
            minio_bucket=self.minio_bucket,
            minio_region=self.minio_region,
            minio_use_iam=self.minio_use_iam,
        )

    @property
    def security(self) -> SecurityConfig:
        """Access security configuration group."""
        return SecurityConfig(
            api_key=self.api_key,
            api_keys=self.api_keys if isinstance(self.api_keys, str) else None,
            api_key_header=self.api_key_header,
            api_key_cache_ttl=self.api_key_cache_ttl,
            allowed_file_extensions=self.allowed_file_extensions,
            blocked_file_patterns=self.blocked_file_patterns,
            enable_network_isolation=self.enable_network_isolation,
            enable_filesystem_isolation=self.enable_filesystem_isolation,
            enable_security_logs=self.enable_security_logs,
        )

    @property
    def resources(self) -> ResourcesConfig:
        """Access resources configuration group."""
        return ResourcesConfig(
            max_execution_time=self.max_execution_time,
            max_memory_mb=self.max_memory_mb,
            max_cpus=self.max_cpus,
            max_cpu_quota=self.max_cpu_quota,
            max_pids=self.max_pids,
            max_open_files=self.max_open_files,
            max_file_size_mb=self.max_file_size_mb,
            max_total_file_size_mb=self.max_total_file_size_mb,
            max_files_per_session=self.max_files_per_session,
            max_output_files=self.max_output_files,
            max_filename_length=self.max_filename_length,
            max_concurrent_executions=self.max_concurrent_executions,
            max_sessions_per_entity=self.max_sessions_per_entity,
            session_ttl_hours=self.session_ttl_hours,
            session_cleanup_interval_minutes=self.session_cleanup_interval_minutes,
            session_id_length=self.session_id_length,
            enable_orphan_minio_cleanup=self.enable_orphan_minio_cleanup,
        )

    @property
    def logging(self) -> LoggingConfig:
        """Access logging configuration group."""
        return LoggingConfig(
            log_level=self.log_level,
            log_format=self.log_format,
            log_file=self.log_file,
            log_max_size_mb=self.log_max_size_mb,
            log_backup_count=self.log_backup_count,
            enable_access_logs=self.enable_access_logs,
            health_check_interval=self.health_check_interval,
            health_check_timeout=self.health_check_timeout,
        )

    @property
    def kubernetes(self) -> KubernetesConfig:
        """Access Kubernetes configuration group."""
        import json

        # Parse JSON strings for node selector and tolerations
        sandbox_node_selector = None
        if self.gke_sandbox_node_selector:
            try:
                sandbox_node_selector = json.loads(self.gke_sandbox_node_selector)
            except json.JSONDecodeError:
                import logging

                logging.getLogger(__name__).warning(
                    "Invalid JSON in GKE_SANDBOX_NODE_SELECTOR, ignoring: %s",
                    self.gke_sandbox_node_selector,
                )

        custom_tolerations = None
        if self.gke_sandbox_custom_tolerations:
            try:
                custom_tolerations = json.loads(self.gke_sandbox_custom_tolerations)
            except json.JSONDecodeError:
                import logging

                logging.getLogger(__name__).warning(
                    "Invalid JSON in GKE_SANDBOX_CUSTOM_TOLERATIONS, ignoring: %s",
                    self.gke_sandbox_custom_tolerations,
                )

        return KubernetesConfig(
            namespace=self.k8s_namespace,
            service_account=self.k8s_service_account,
            sidecar_image=self.k8s_sidecar_image,
            sidecar_port=self.k8s_sidecar_port,
            sidecar_cpu_limit=self.k8s_sidecar_cpu_limit,
            sidecar_memory_limit=self.k8s_sidecar_memory_limit,
            sidecar_cpu_request=self.k8s_sidecar_cpu_request,
            sidecar_memory_request=self.k8s_sidecar_memory_request,
            cpu_limit=self.k8s_cpu_limit,
            memory_limit=self.k8s_memory_limit,
            cpu_request=self.k8s_cpu_request,
            memory_request=self.k8s_memory_request,
            run_as_user=self.k8s_run_as_user,
            execution_mode=self.k8s_execution_mode,
            executor_port=self.k8s_executor_port,
            seccomp_profile_type=self.k8s_seccomp_profile_type,
            job_ttl_seconds_after_finished=self.k8s_job_ttl_seconds,
            job_active_deadline_seconds=self.k8s_job_deadline_seconds,
            image_registry=self.k8s_image_registry,
            image_tag=self.k8s_image_tag,
            image_pull_policy=self.k8s_image_pull_policy,
            image_pull_secrets=self.k8s_image_pull_secrets,
            gke_sandbox_enabled=self.gke_sandbox_enabled,
            runtime_class_name=self.gke_sandbox_runtime_class,
            sandbox_node_selector=sandbox_node_selector,
            custom_tolerations=custom_tolerations,
        )

    def get_pool_configs(self):
        """Get pool configurations for all languages.

        Returns list of PoolConfig for all configured languages.
        """
        import json
        import os

        from ..services.kubernetes.models import PoolConfig

        # Parse GKE Sandbox configuration once
        sandbox_node_selector = None
        if self.gke_sandbox_node_selector:
            try:
                sandbox_node_selector = json.loads(self.gke_sandbox_node_selector)
            except json.JSONDecodeError:
                pass

        custom_tolerations = None
        if self.gke_sandbox_custom_tolerations:
            try:
                custom_tolerations = json.loads(self.gke_sandbox_custom_tolerations)
            except json.JSONDecodeError:
                pass

        configs = []
        languages = ["py", "js", "ts", "go", "java", "c", "cpp", "php", "rs", "r", "f90", "d"]

        pool_sizes = {
            "py": self.pod_pool_py,
            "js": self.pod_pool_js,
            "ts": self.pod_pool_ts,
            "go": self.pod_pool_go,
            "java": self.pod_pool_java,
            "c": self.pod_pool_c,
            "cpp": self.pod_pool_cpp,
            "php": self.pod_pool_php,
            "rs": self.pod_pool_rs,
            "r": self.pod_pool_r,
            "f90": self.pod_pool_f90,
            "d": self.pod_pool_d,
        }

        for lang in languages:
            lang_upper = lang.upper()
            pool_size = pool_sizes[lang]

            # Per-language image override (LANG_IMAGE_<LANG>)
            image = os.getenv(f"LANG_IMAGE_{lang_upper}") or self.kubernetes.get_image_for_language(lang)

            # Per-language resource limits (LANG_CPU_LIMIT_<LANG>, etc.)
            # Falls back to global sidecar defaults
            sidecar_cpu_limit = os.getenv(f"LANG_CPU_LIMIT_{lang_upper}") or self.k8s_sidecar_cpu_limit
            sidecar_memory_limit = os.getenv(f"LANG_MEMORY_LIMIT_{lang_upper}") or self.k8s_sidecar_memory_limit
            sidecar_cpu_request = os.getenv(f"LANG_CPU_REQUEST_{lang_upper}") or self.k8s_sidecar_cpu_request
            sidecar_memory_request = os.getenv(f"LANG_MEMORY_REQUEST_{lang_upper}") or self.k8s_sidecar_memory_request

            # Parse image pull secrets (comma-separated string -> list)
            pull_secrets = None
            if self.k8s_image_pull_secrets:
                pull_secrets = [s.strip() for s in self.k8s_image_pull_secrets.split(",") if s.strip()]

            configs.append(
                PoolConfig(
                    language=lang,
                    image=image,
                    pool_size=pool_size,
                    sidecar_image=self.k8s_sidecar_image,
                    cpu_limit=self.k8s_cpu_limit,
                    memory_limit=self.k8s_memory_limit,
                    sidecar_cpu_limit=sidecar_cpu_limit,
                    sidecar_memory_limit=sidecar_memory_limit,
                    sidecar_cpu_request=sidecar_cpu_request,
                    sidecar_memory_request=sidecar_memory_request,
                    image_pull_policy=self.k8s_image_pull_policy,
                    image_pull_secrets=pull_secrets,
                    execution_mode=self.k8s_execution_mode,
                    executor_port=self.k8s_executor_port,
                    seccomp_profile_type=self.k8s_seccomp_profile_type,
                    network_isolated=self.enable_network_isolation,
                    gke_sandbox_enabled=self.gke_sandbox_enabled,
                    runtime_class_name=self.gke_sandbox_runtime_class,
                    sandbox_node_selector=sandbox_node_selector,
                    custom_tolerations=custom_tolerations,
                )
            )

        return configs

    # ========================================================================
    # HELPER METHODS (preserved from original)
    # ========================================================================

    def validate_ssl_files(self) -> bool:
        """Validate that SSL files exist when HTTPS is enabled."""
        if not self.enable_https:
            return True
        if not self.ssl_cert_file or not self.ssl_key_file:
            return False
        return Path(self.ssl_cert_file).exists() and Path(self.ssl_key_file).exists()

    def get_redis_url(self) -> str:
        """Get Redis connection URL.

        Automatically uses ``rediss://`` when TLS is enabled.
        """
        if self.redis_url:
            return self.redis_url
        scheme = "rediss" if self.redis_tls_enabled else "redis"
        password_part = f":{self.redis_password}@" if self.redis_password else ""
        return f"{scheme}://{password_part}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    def get_valid_api_keys(self) -> list[str]:
        """Get all valid API keys including the primary key."""
        keys = [self.api_key]
        if self.api_keys:
            if isinstance(self.api_keys, list):
                keys.extend(self.api_keys)
            elif isinstance(self.api_keys, str):
                keys.extend([k.strip() for k in self.api_keys.split(",") if k.strip()])
        return list(set(keys))

    def get_language_config(self, language: str) -> dict[str, Any]:
        """Get configuration for a specific language."""
        return self.supported_languages.get(language, {})

    def get_image_for_language(self, code: str) -> str:
        """Get container image for a language."""
        config = self.get_language_config(code)
        if config and "image" in config:
            return config["image"]

        # Fallback to languages.py logic if not in settings
        from .languages import get_image_for_language as get_img

        return get_img(code, registry=self.k8s_image_registry, tag=self.k8s_image_tag)

    def get_execution_timeout(self, language: str) -> int:
        """Get execution timeout for a specific language."""
        multiplier = self.get_language_config(language).get("timeout_multiplier", 1.0)
        return int(self.max_execution_time * multiplier)

    def get_memory_limit(self, language: str) -> int:
        """Get memory limit for a specific language in MB."""
        multiplier = self.get_language_config(language).get("memory_multiplier", 1.0)
        return int(self.max_memory_mb * multiplier)

    def get_session_ttl_minutes(self) -> int:
        """Get session TTL in minutes for backward compatibility."""
        return self.session_ttl_hours * 60

    def is_file_allowed(self, filename: str) -> bool:
        """Check if a file is allowed based on extension and patterns."""
        extension = Path(filename).suffix.lower()

        if extension and extension not in self.allowed_file_extensions:
            return False

        import fnmatch

        return not any(fnmatch.fnmatch(filename.lower(), pattern.lower()) for pattern in self.blocked_file_patterns)


# Global settings instance
settings = Settings()

# Export everything needed for backward compatibility
__all__ = [
    "Settings",
    "settings",
    # Grouped configs
    "APIConfig",
    "RedisConfig",
    "MinIOConfig",
    "SecurityConfig",
    "ResourcesConfig",
    "LoggingConfig",
    "KubernetesConfig",
    # Language configuration
    "LANGUAGES",
    "LanguageConfig",
    "get_language",
    "get_supported_languages",
    "is_supported_language",
    "get_image_for_language",
    "get_user_id_for_language",
    "get_execution_command",
    "uses_stdin",
    "get_file_extension",
]
