"""Redis configuration."""

import ssl as _ssl
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
        """Get Redis connection URL.

        Returns a ``rediss://`` URL when SSL is enabled so that redis-py 7.x
        selects ``SSLConnection`` automatically (the ``ssl=True`` kwarg is no
        longer accepted by ``AbstractConnection.__init__()``).
        """
        if self.url:
            return self.url
        scheme = "rediss" if self.ssl else "redis"
        password_part = f":{self.password}@" if self.password else ""
        return f"{scheme}://{password_part}{self.host}:{self.port}/{self.db}"

    def _build_ssl_context(self) -> _ssl.SSLContext:
        """Build an ``ssl.SSLContext`` from the configured TLS fields.

        When ``ssl_ca_certs`` points to a custom CA file the context loads it
        explicitly so that self-signed / private-CA certificates are verified
        correctly.
        """
        cert_reqs_map = {
            "required": _ssl.CERT_REQUIRED,
            "optional": _ssl.CERT_OPTIONAL,
            "none": _ssl.CERT_NONE,
        }
        cert_reqs = cert_reqs_map.get(self.ssl_cert_reqs, _ssl.CERT_REQUIRED)

        if self.ssl_ca_certs:
            ctx = _ssl.create_default_context(cafile=self.ssl_ca_certs)
        else:
            ctx = _ssl.create_default_context()

        ctx.check_hostname = self.ssl_check_hostname and cert_reqs != _ssl.CERT_NONE
        ctx.verify_mode = cert_reqs

        if self.ssl_certfile:
            ctx.load_cert_chain(certfile=self.ssl_certfile, keyfile=self.ssl_keyfile)

        return ctx

    def get_ssl_kwargs(self) -> dict:
        """Get SSL kwargs for Redis client creation.

        Builds a proper ``ssl.SSLContext`` and passes it as ``ssl_context`` so
        that custom CA certificates (self-signed / private CA) are loaded into
        the context and verified correctly.

        Note: In redis-py 7.x the ``ssl=True`` keyword is no longer accepted by
        ``AbstractConnection.__init__()``.  SSL is instead enabled by using the
        ``rediss://`` URL scheme (see ``get_url()``).
        """
        if not self.ssl:
            return {}
        return {
            "ssl_context": self._build_ssl_context(),
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
