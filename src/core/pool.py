"""Connection pool management.

This module provides centralized connection pools for external services,
allowing efficient resource sharing across the application.

Supports three Redis modes:
- standalone: Traditional single-node Redis (default)
- cluster: Redis Cluster with automatic sharding
- sentinel: Redis Sentinel for high availability
"""

import redis.asyncio as redis
import structlog

logger = structlog.get_logger(__name__)


class RedisPool:
    """Centralized async Redis connection pool.

    Provides a shared connection pool for all services that need Redis,
    avoiding the overhead of multiple separate pools.

    Supports standalone, cluster, and sentinel modes. The returned client
    shares the same command API across all modes.

    Usage:
        client = redis_pool.get_client()
        await client.set("key", "value")
    """

    def __init__(self):
        self._client = None
        self._initialized = False
        self._mode = "standalone"
        self._key_prefix = ""

    def _initialize(self) -> None:
        """Initialize the connection pool lazily."""
        if self._initialized:
            return

        from ..config import settings

        cfg = settings.redis
        self._mode = cfg.mode
        self._key_prefix = cfg.key_prefix
        ssl_kwargs = cfg.get_ssl_kwargs()

        try:
            if self._mode == "cluster":
                from redis.asyncio.cluster import ClusterNode, RedisCluster

                nodes = cfg.parse_nodes(cfg.cluster_nodes)
                if cfg.url:
                    self._client = RedisCluster.from_url(cfg.url, decode_responses=True, **ssl_kwargs)
                elif nodes:
                    self._client = RedisCluster(
                        host=nodes[0][0],
                        port=nodes[0][1],
                        startup_nodes=[ClusterNode(h, p) for h, p in nodes],
                        decode_responses=True,
                        max_connections=cfg.max_connections,
                        socket_timeout=float(cfg.socket_timeout),
                        socket_connect_timeout=float(cfg.socket_connect_timeout),
                        **ssl_kwargs,
                    )
                else:
                    raise ValueError("REDIS_CLUSTER_NODES required for cluster mode")
            elif self._mode == "sentinel":
                from redis.asyncio.sentinel import Sentinel

                nodes = cfg.parse_nodes(cfg.sentinel_nodes)
                if not nodes:
                    raise ValueError("REDIS_SENTINEL_NODES required for sentinel mode")
                sentinel_kwargs = {"socket_timeout": float(cfg.socket_timeout)}
                if cfg.sentinel_password:
                    sentinel_kwargs["password"] = cfg.sentinel_password
                sentinel_kwargs.update(ssl_kwargs)
                sentinel = Sentinel(nodes, sentinel_kwargs=sentinel_kwargs)
                conn_kwargs = {
                    "db": cfg.sentinel_db,
                    "decode_responses": True,
                    "socket_timeout": float(cfg.socket_timeout),
                    "socket_connect_timeout": float(cfg.socket_connect_timeout),
                }
                if cfg.password:
                    conn_kwargs["password"] = cfg.password
                conn_kwargs.update(ssl_kwargs)
                self._client = sentinel.master_for(cfg.sentinel_master, **conn_kwargs)
            else:
                # standalone (default, current behavior)
                redis_url = cfg.get_url()
                pool = redis.ConnectionPool.from_url(
                    redis_url,
                    max_connections=cfg.max_connections,
                    decode_responses=True,
                    socket_timeout=float(cfg.socket_timeout),
                    socket_connect_timeout=float(cfg.socket_connect_timeout),
                    retry_on_timeout=True,
                    **ssl_kwargs,
                )
                self._client = redis.Redis(connection_pool=pool)

            self._initialized = True
            logger.info(
                "Redis pool initialized",
                mode=self._mode,
                key_prefix=self._key_prefix or "(none)",
            )
        except Exception as e:
            logger.error("Failed to initialize Redis pool", mode=self._mode, error=str(e))
            # Create a fallback client
            self._client = redis.from_url("redis://localhost:6379/0", decode_responses=True)
            self._initialized = True

    @property
    def key_prefix(self) -> str:
        """Get the configured key prefix."""
        if not self._initialized:
            self._initialize()
        return self._key_prefix

    def get_client(self) -> redis.Redis:
        """Get an async Redis client from the shared pool.

        Returns:
            Async Redis client instance connected to the shared pool.
            For cluster mode, returns a RedisCluster instance (same command API).
        """
        if not self._initialized:
            self._initialize()
        assert self._client is not None, "Redis client not initialized"
        return self._client

    @property
    def pool_stats(self) -> dict:
        """Get connection pool statistics."""
        if not self._initialized:
            return {"initialized": False}

        return {
            "initialized": True,
            "mode": self._mode,
            "key_prefix": self._key_prefix,
        }

    async def close(self) -> None:
        """Close the connection pool and release all connections."""
        if self._client:
            if hasattr(self._client, "aclose"):
                await self._client.aclose()
            else:
                await self._client.close()
            logger.info("Redis pool closed")
        self._client = None
        self._initialized = False


# Global Redis pool instance
redis_pool = RedisPool()
