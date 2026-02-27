"""Connection pool management.

This module provides centralized connection pools for external services,
allowing efficient resource sharing across the application.

Supported Redis deployment modes:
- **standalone** (default): Single Redis server with ``ConnectionPool``.
- **cluster**: Redis Cluster via ``RedisCluster``.
- **sentinel**: Redis Sentinel via ``Sentinel`` for HA failover.

All modes support optional TLS/SSL for managed services such as
GCP Memorystore, AWS ElastiCache, and Azure Cache for Redis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import redis.asyncio as redis
import structlog
from redis.asyncio.cluster import RedisCluster
from redis.asyncio.sentinel import Sentinel
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, TimeoutError
from redis.retry import Retry

from ..config import settings

if TYPE_CHECKING:
    from ..config.redis import RedisConfig

logger = structlog.get_logger(__name__)


class RedisPool:
    """Centralized async Redis connection pool.

    Provides a shared connection pool for all services that need Redis,
    avoiding the overhead of multiple separate pools.  Supports standalone,
    cluster, and sentinel modes with optional TLS.

    Usage:
        client = redis_pool.get_client()
        await client.set("key", "value")
    """

    def __init__(self) -> None:
        self._pool: redis.ConnectionPool | None = None
        self._client: redis.Redis | RedisCluster | None = None
        self._sentinel: Sentinel | None = None
        self._initialized: bool = False
        self._mode: str = "standalone"
        self._key_prefix: str = ""

    def _initialize(self) -> None:
        """Initialize the connection pool lazily based on the configured mode."""
        if self._initialized:
            return

        try:
            redis_cfg = settings.redis
            self._mode = redis_cfg.mode
            self._key_prefix = redis_cfg.key_prefix
            tls_kwargs = redis_cfg.get_tls_kwargs()
            max_conns = redis_cfg.max_connections
            socket_timeout = float(redis_cfg.socket_timeout)
            socket_connect_timeout = float(redis_cfg.socket_connect_timeout)

            if self._mode == "cluster":
                self._init_cluster(redis_cfg, tls_kwargs, max_conns, socket_timeout, socket_connect_timeout)
            elif self._mode == "sentinel":
                self._init_sentinel(redis_cfg, tls_kwargs, max_conns, socket_timeout, socket_connect_timeout)
            else:
                self._init_standalone(redis_cfg, tls_kwargs, max_conns, socket_timeout, socket_connect_timeout)

            self._initialized = True
        except Exception as e:
            logger.error(
                "Failed to initialize Redis pool",
                error=str(e),
                mode=self._mode,
            )
            raise

    # -- Mode-specific initialisers -------------------------------------------

    def _init_standalone(
        self,
        cfg: RedisConfig,
        tls_kwargs: dict,
        max_conns: int,
        socket_timeout: float,
        socket_connect_timeout: float,
    ) -> None:
        redis_url = cfg.get_url()
        self._pool = redis.ConnectionPool.from_url(
            redis_url,
            max_connections=max_conns,
            decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            retry_on_timeout=True,
            **tls_kwargs,
        )
        self._client = redis.Redis(connection_pool=self._pool)
        logger.info(
            "Redis standalone connection pool initialized",
            max_connections=max_conns,
            tls=cfg.tls_enabled,
            url=redis_url.split("@")[-1],
        )

    def _init_cluster(
        self,
        cfg: RedisConfig,
        tls_kwargs: dict,
        max_conns: int,
        socket_timeout: float,
        socket_connect_timeout: float,
    ) -> None:
        if cfg.cluster_nodes:
            startup_nodes = [redis.cluster.ClusterNode(host=h, port=p) for h, p in cfg.parse_nodes(cfg.cluster_nodes)]
        else:
            startup_nodes = [redis.cluster.ClusterNode(host=cfg.host, port=cfg.port)]

        self._client = RedisCluster(
            startup_nodes=startup_nodes,
            password=cfg.password,
            decode_responses=True,
            max_connections=max_conns,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            retry=Retry(ExponentialBackoff(), retries=3),
            retry_on_error=[ConnectionError, TimeoutError],
            **tls_kwargs,
        )
        logger.info(
            "Redis cluster connection initialized",
            startup_nodes=[
                f"{h}:{p}"
                for h, p in (cfg.parse_nodes(cfg.cluster_nodes) if cfg.cluster_nodes else [(cfg.host, cfg.port)])
            ],
            tls=cfg.tls_enabled,
        )

    def _init_sentinel(
        self,
        cfg: RedisConfig,
        tls_kwargs: dict,
        max_conns: int,
        socket_timeout: float,
        socket_connect_timeout: float,
    ) -> None:
        if cfg.sentinel_nodes:
            sentinel_hosts = cfg.parse_nodes(cfg.sentinel_nodes)
        else:
            sentinel_hosts = [(cfg.host, 26379)]

        self._sentinel = Sentinel(
            sentinels=sentinel_hosts,
            password=cfg.sentinel_password,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            **tls_kwargs,
        )
        self._client = self._sentinel.master_for(
            service_name=cfg.sentinel_master,
            password=cfg.password,
            decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            max_connections=max_conns,
            retry_on_timeout=True,
            **tls_kwargs,
        )
        logger.info(
            "Redis sentinel connection initialized",
            sentinel_nodes=[f"{h}:{p}" for h, p in sentinel_hosts],
            master=cfg.sentinel_master,
            tls=cfg.tls_enabled,
        )

    # -- Public API -----------------------------------------------------------

    def get_client(self) -> redis.Redis | RedisCluster:
        """Get an async Redis client from the shared pool.

        Returns:
            Async Redis client instance connected to the shared pool.
            For cluster mode this is a ``RedisCluster`` instance which
            exposes the same command interface.
        """
        if not self._initialized:
            self._initialize()
        assert self._client is not None, "Redis client not initialized"
        return self._client

    @property
    def key_prefix(self) -> str:
        """Return the configured Redis key prefix (may be empty)."""
        if not self._initialized:
            self._initialize()
        return self._key_prefix

    def make_key(self, key: str) -> str:
        """Prepend the configured key prefix to *key*.

        Returns *key* unchanged when no prefix is configured.
        """
        prefix = self.key_prefix
        if prefix:
            return f"{prefix}{key}"
        return key

    @property
    def pool_stats(self) -> dict:
        """Get connection pool statistics."""
        if not self._pool and self._mode == "standalone":
            return {"initialized": self._initialized, "mode": self._mode}

        stats: dict = {"initialized": self._initialized, "mode": self._mode}

        if self._key_prefix:
            stats["key_prefix"] = self._key_prefix

        if self._pool:
            stats["max_connections"] = self._pool.max_connections

        return stats

    async def close(self) -> None:
        """Close the connection pool and release all connections."""
        if self._client:
            await self._client.close()
            logger.info("Redis connection pool closed", mode=self._mode)
        self._pool = None
        self._client = None
        self._sentinel = None
        self._initialized = False
        self._mode = "standalone"
        self._key_prefix = ""


# Global Redis pool instance
redis_pool = RedisPool()
