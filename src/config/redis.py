"""Redis configuration."""

from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RedisConfig(BaseSettings):
    """Redis connection settings."""

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
    )

    host: str = Field(default="localhost", alias="redis_host")
    port: int = Field(default=6379, ge=1, le=65535, alias="redis_port")
    password: str | None = Field(default=None, alias="redis_password")

    @field_validator("host", mode="before")
    @classmethod
    def _sanitize_host(cls, v: str) -> str:
        """Extract hostname from accidental URL in REDIS_HOST.

        Users sometimes set REDIS_HOST=redis://hostname:6380 or
        REDIS_HOST=rediss://hostname instead of just the hostname.
        """
        if isinstance(v, str) and v.startswith(("redis://", "rediss://")):
            parsed = urlparse(v)
            return parsed.hostname or "localhost"
        return v

    @field_validator("password", mode="before")
    @classmethod
    def _empty_to_none(cls, v: str | None) -> str | None:
        """Treat empty string as None (Helm/ConfigMap renders '' not null)."""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    db: int = Field(default=0, ge=0, le=15, alias="redis_db")
    url: str | None = Field(default=None, alias="redis_url")
    max_connections: int = Field(default=20, ge=1, alias="redis_max_connections")
    socket_timeout: int = Field(default=5, ge=1, alias="redis_socket_timeout")
    socket_connect_timeout: int = Field(default=5, ge=1, alias="redis_socket_connect_timeout")

    # Mode and key prefix
    mode: str = Field(default="standalone", alias="redis_mode")
    key_prefix: str = Field(default="", alias="redis_key_prefix")

    # TLS
    ssl: bool = Field(default=False, alias="redis_ssl")
    ssl_ca_certs: str | None = Field(default=None, alias="redis_ssl_ca_certs")
    ssl_certfile: str | None = Field(default=None, alias="redis_ssl_certfile")
    ssl_keyfile: str | None = Field(default=None, alias="redis_ssl_keyfile")
    ssl_cert_reqs: str = Field(default="required", alias="redis_ssl_cert_reqs")
    ssl_check_hostname: bool = Field(default=True, alias="redis_ssl_check_hostname")

    # Cluster
    cluster_nodes: str = Field(default="", alias="redis_cluster_nodes")

    # Sentinel
    sentinel_nodes: str = Field(default="", alias="redis_sentinel_nodes")
    sentinel_master: str = Field(default="mymaster", alias="redis_sentinel_master")
    sentinel_password: str | None = Field(default=None, alias="redis_sentinel_password")
    sentinel_db: int = Field(default=0, alias="redis_sentinel_db")

    def get_url(self) -> str:
        """Get Redis connection URL."""
        if self.url:
            return self.url
        password_part = f":{self.password}@" if self.password else ""
        return f"redis://{password_part}{self.host}:{self.port}/{self.db}"

    def get_ssl_kwargs(self) -> dict:
        """Get SSL kwargs for Redis client creation."""
        if not self.ssl:
            return {}
        return {
            "ssl": True,
            "ssl_ca_certs": self.ssl_ca_certs,
            "ssl_certfile": self.ssl_certfile,
            "ssl_keyfile": self.ssl_keyfile,
            "ssl_cert_reqs": self.ssl_cert_reqs,
            "ssl_check_hostname": self.ssl_check_hostname,
        }

    @staticmethod
    def parse_nodes(nodes_str: str) -> list[tuple[str, int]]:
        """Parse comma-separated host:port string into list of (host, port) tuples."""
        if not nodes_str:
            return []
        result = []
        for node in nodes_str.split(","):
            node = node.strip()
            if ":" in node:
                host, port = node.rsplit(":", 1)
                result.append((host, int(port)))
            else:
                result.append((node, 6379))
        return result
