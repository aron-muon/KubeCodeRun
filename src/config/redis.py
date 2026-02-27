"""Redis configuration.

Supports three deployment modes:
- **standalone** (default): Single Redis instance.
- **cluster**: Redis Cluster with automatic slot routing.
- **sentinel**: Redis Sentinel for high-availability failover.

TLS/SSL is supported in all modes and is required for most managed Redis
services such as GCP Memorystore, AWS ElastiCache, and Azure Cache for Redis.
"""

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RedisConfig(BaseSettings):
    """Redis connection settings.

    Supports standalone, cluster, and sentinel modes with optional TLS.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
    )

    # -- Connection mode -------------------------------------------------------
    mode: Literal["standalone", "cluster", "sentinel"] = Field(
        default="standalone",
        alias="redis_mode",
        description="Redis deployment mode: standalone, cluster, or sentinel",
    )

    # -- Basic connection (standalone / single-entry for cluster & sentinel) ---
    host: str = Field(default="localhost", alias="redis_host")
    port: int = Field(default=6379, ge=1, le=65535, alias="redis_port")
    password: str | None = Field(default=None, alias="redis_password")
    db: int = Field(default=0, ge=0, le=15, alias="redis_db")
    url: str | None = Field(default=None, alias="redis_url")
    max_connections: int = Field(default=20, ge=1, alias="redis_max_connections")
    socket_timeout: int = Field(default=5, ge=1, alias="redis_socket_timeout")
    socket_connect_timeout: int = Field(default=5, ge=1, alias="redis_socket_connect_timeout")

    # -- Cluster mode ----------------------------------------------------------
    cluster_nodes: str | None = Field(
        default=None,
        alias="redis_cluster_nodes",
        description=(
            "Comma-separated list of host:port pairs for Redis Cluster startup nodes. "
            "Example: 'node1:6379,node2:6379,node3:6379'"
        ),
    )

    # -- Sentinel mode ---------------------------------------------------------
    sentinel_nodes: str | None = Field(
        default=None,
        alias="redis_sentinel_nodes",
        description=(
            "Comma-separated list of host:port pairs for Sentinel instances. "
            "Example: 'sentinel1:26379,sentinel2:26379,sentinel3:26379'"
        ),
    )
    sentinel_master: str = Field(
        default="mymaster",
        alias="redis_sentinel_master",
        description="Name of the Sentinel-monitored master.",
    )
    sentinel_password: str | None = Field(
        default=None,
        alias="redis_sentinel_password",
        description="Password for authenticating to Sentinel instances (if different from Redis password).",
    )

    # -- Key prefix ------------------------------------------------------------
    key_prefix: str = Field(
        default="",
        alias="redis_key_prefix",
        description=(
            "Optional prefix prepended to every Redis key. "
            "Useful for sharing a single Redis instance across multiple environments "
            "or applications (e.g. 'prod:', 'staging:', 'kubecoderun:'). "
            "Must end with a separator like ':' if you want one."
        ),
    )

    # -- TLS / SSL -------------------------------------------------------------
    tls_enabled: bool = Field(
        default=False,
        alias="redis_tls_enabled",
        description="Enable TLS/SSL for Redis connections.",
    )
    tls_cert_file: str | None = Field(
        default=None,
        alias="redis_tls_cert_file",
        description="Path to client TLS certificate file (mutual TLS).",
    )
    tls_key_file: str | None = Field(
        default=None,
        alias="redis_tls_key_file",
        description="Path to client TLS private key file (mutual TLS).",
    )
    tls_ca_cert_file: str | None = Field(
        default=None,
        alias="redis_tls_ca_cert_file",
        description="Path to CA certificate file for verifying the server.",
    )
    tls_insecure: bool = Field(
        default=False,
        alias="redis_tls_insecure",
        description="Skip TLS certificate verification (NOT recommended for production).",
    )
    tls_check_hostname: bool = Field(
        default=False,
        alias="redis_tls_check_hostname",
        description=(
            "Enable TLS hostname verification. Disabled by default because "
            "managed Redis services (GCP Memorystore, AWS ElastiCache) and "
            "Redis Cluster mode expose node IPs that typically do not match "
            "the certificate CN/SAN entries. The certificate chain is still "
            "verified against the CA when tls_insecure is False."
        ),
    )

    # -- Validators ------------------------------------------------------------

    @field_validator("host", mode="before")
    @classmethod
    def _sanitize_host(cls, v: str) -> str:
        """Strip an accidental URL scheme from the host value.

        Users sometimes set ``REDIS_HOST=rediss://hostname`` instead of just
        ``REDIS_HOST=hostname``.  This validator normalises the value so that
        downstream code always receives a plain hostname or IP.
        """
        if isinstance(v, str):
            for scheme in ("rediss://", "redis://"):
                if v.lower().startswith(scheme):
                    v = v[len(scheme) :]
                    # Drop any trailing slash left over
                    v = v.rstrip("/")
                    break
        return v

    @field_validator("password", "sentinel_password", mode="before")
    @classmethod
    def _empty_string_to_none(cls, v: str | None) -> str | None:
        """Convert empty strings to ``None``.

        Kubernetes ConfigMaps and Helm values often set ``REDIS_PASSWORD: ""``
        which pydantic-settings reads as ``""`` rather than ``None``.  Passing
        an empty password to redis-py causes it to send ``AUTH ""`` which
        fails when the server has no authentication configured.
        """
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @field_validator("cluster_nodes", "sentinel_nodes", mode="before")
    @classmethod
    def _empty_nodes_to_none(cls, v: str | None) -> str | None:
        """Convert empty/whitespace-only node lists to ``None``.

        Helm values default to ``clusterNodes: ""`` which renders in the
        ConfigMap as an empty string.  This validator treats it the same
        as "not set" so the code falls back to ``host:port``.
        """
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    # -- Helpers ---------------------------------------------------------------

    def get_url(self) -> str:
        """Get Redis connection URL (standalone mode only).

        For cluster/sentinel modes the URL is not used; startup nodes are
        provided separately. This method honours an explicit ``url`` and
        automatically switches between the ``redis://`` and ``rediss://``
        scheme based on the ``tls_enabled`` flag.
        """
        if self.url:
            return self.url
        scheme = "rediss" if self.tls_enabled else "redis"
        password_part = f":{self.password}@" if self.password else ""
        return f"{scheme}://{password_part}{self.host}:{self.port}/{self.db}"

    def get_tls_kwargs(self) -> dict:
        """Build keyword arguments for redis-py SSL/TLS configuration.

        Returns an empty dict when TLS is disabled so callers can safely
        unpack the result: ``redis.Redis(**config.get_tls_kwargs())``.
        """
        if not self.tls_enabled:
            return {}

        import ssl

        kwargs: dict = {"ssl": True}

        if self.tls_insecure:
            kwargs["ssl_cert_reqs"] = ssl.CERT_NONE
            kwargs["ssl_check_hostname"] = False
        else:
            kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
            # Hostname checking is off by default because managed Redis
            # services (GCP Memorystore, AWS ElastiCache) and Redis
            # Cluster node discovery return IPs that do not match the
            # certificate CN/SAN.  The certificate chain is still fully
            # validated against the CA.
            kwargs["ssl_check_hostname"] = self.tls_check_hostname

        if self.tls_ca_cert_file:
            kwargs["ssl_ca_certs"] = self.tls_ca_cert_file
        if self.tls_cert_file:
            kwargs["ssl_certfile"] = self.tls_cert_file
        if self.tls_key_file:
            kwargs["ssl_keyfile"] = self.tls_key_file

        return kwargs

    def parse_nodes(self, raw: str) -> list[tuple[str, int]]:
        """Parse a comma-separated ``host:port`` string into a list of tuples."""
        nodes: list[tuple[str, int]] = []
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                h, p = entry.rsplit(":", 1)
                nodes.append((h.strip(), int(p.strip())))
            else:
                nodes.append((entry, self.port))
        return nodes
