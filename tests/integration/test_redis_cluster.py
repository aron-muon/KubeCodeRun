"""Integration test for Redis Cluster connectivity.

Requires a running Redis Cluster on localhost:7000-7005.
Start with: docker compose -f docker-compose.redis-cluster.yml up -d

Usage:
    uv run python -m pytest tests/integration/test_redis_cluster.py -v
"""

import asyncio
import os

import pytest
import redis as sync_redis
import redis.asyncio as async_redis
from redis.asyncio.cluster import RedisCluster as AsyncRedisCluster
from redis.cluster import ClusterNode, RedisCluster

# Only run when cluster is available
CLUSTER_HOST = os.environ.get("REDIS_CLUSTER_HOST", "127.0.0.1")
CLUSTER_PORT = int(os.environ.get("REDIS_CLUSTER_PORT", "7000"))

pytestmark = pytest.mark.integration


def _cluster_available() -> bool:
    """Check if a Redis Cluster is reachable."""
    try:
        rc = RedisCluster(
            startup_nodes=[ClusterNode(host=CLUSTER_HOST, port=CLUSTER_PORT)],
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        rc.ping()
        rc.close()
        return True
    except Exception:
        return False


skip_no_cluster = pytest.mark.skipif(
    not _cluster_available(),
    reason=f"Redis Cluster not available at {CLUSTER_HOST}:{CLUSTER_PORT}",
)


# ── Synchronous (validator path) ──────────────────────────────────────────


@skip_no_cluster
class TestSyncRedisCluster:
    """Tests using synchronous redis-py RedisCluster (same as config_validator)."""

    def test_connect_with_single_startup_node(self):
        """Cluster discovery works from a single startup node."""
        rc = RedisCluster(
            startup_nodes=[ClusterNode(host=CLUSTER_HOST, port=CLUSTER_PORT)],
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        assert rc.ping() is True
        # Verify the cluster is operational via a targeted node
        node_info = rc.cluster_info(target_nodes=RedisCluster.RANDOM)
        assert node_info.get("cluster_state") == "ok"
        rc.close()

    def test_connect_with_multiple_startup_nodes(self):
        """Cluster discovery works from multiple startup nodes."""
        nodes = [
            ClusterNode(host=CLUSTER_HOST, port=CLUSTER_PORT),
            ClusterNode(host=CLUSTER_HOST, port=CLUSTER_PORT + 1),
        ]
        rc = RedisCluster(
            startup_nodes=nodes,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        assert rc.ping() is True
        rc.close()

    def test_connect_with_no_password(self):
        """Cluster connects with password=None (no AUTH)."""
        rc = RedisCluster(
            startup_nodes=[ClusterNode(host=CLUSTER_HOST, port=CLUSTER_PORT)],
            password=None,
            decode_responses=True,
            socket_timeout=5,
        )
        assert rc.ping() is True
        rc.close()

    def test_empty_password_converted_to_none(self):
        """Our validator converts empty password to None to avoid spurious AUTH.

        Redis servers without requirepass accept AUTH with any string,
        so we can't observe the bug via an error.  Instead, verify that
        our Settings validator normalises empty password to None.
        """
        from src.config import Settings

        s = Settings(redis_password="")
        assert s.redis_password is None

        s2 = Settings(redis_password="  ")
        assert s2.redis_password is None

        s3 = Settings(redis_password="real-password")
        assert s3.redis_password == "real-password"

    def test_set_get_operations(self):
        """Basic SET/GET across cluster slots."""
        rc = RedisCluster(
            startup_nodes=[ClusterNode(host=CLUSTER_HOST, port=CLUSTER_PORT)],
            decode_responses=True,
        )
        # These keys hash to different slots
        for i in range(10):
            key = f"test:cluster:{i}"
            rc.set(key, f"value-{i}")
            assert rc.get(key) == f"value-{i}"
            rc.delete(key)
        rc.close()


# ── Asynchronous (pool path) ─────────────────────────────────────────────


@skip_no_cluster
class TestAsyncRedisCluster:
    """Tests using async redis-py RedisCluster (same as RedisPool._init_cluster)."""

    @pytest.mark.asyncio
    async def test_async_connect_and_ping(self):
        """Async cluster client connects and pings."""
        from redis.backoff import ExponentialBackoff
        from redis.exceptions import ConnectionError, TimeoutError
        from redis.retry import Retry

        rc = AsyncRedisCluster(
            startup_nodes=[
                async_redis.cluster.ClusterNode(host=CLUSTER_HOST, port=CLUSTER_PORT),
            ],
            password=None,
            decode_responses=True,
            max_connections=20,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            retry=Retry(ExponentialBackoff(), retries=3),
            retry_on_error=[ConnectionError, TimeoutError],
        )
        result = await rc.ping()
        assert result is True
        await rc.aclose()

    @pytest.mark.asyncio
    async def test_async_set_get(self):
        """Async SET/GET across cluster slots."""
        rc = AsyncRedisCluster(
            startup_nodes=[
                async_redis.cluster.ClusterNode(host=CLUSTER_HOST, port=CLUSTER_PORT),
            ],
            decode_responses=True,
        )
        for i in range(10):
            key = f"test:async:cluster:{i}"
            await rc.set(key, f"value-{i}")
            val = await rc.get(key)
            assert val == f"value-{i}"
            await rc.delete(key)
        await rc.aclose()


# ── RedisPool integration ────────────────────────────────────────────────


@skip_no_cluster
class TestRedisPoolClusterMode:
    """Test RedisPool with actual cluster backend."""

    @pytest.mark.asyncio
    async def test_pool_cluster_mode(self, monkeypatch):
        """RedisPool initializes in cluster mode and can SET/GET."""
        monkeypatch.setenv("REDIS_MODE", "cluster")
        monkeypatch.setenv("REDIS_HOST", CLUSTER_HOST)
        monkeypatch.setenv("REDIS_PORT", str(CLUSTER_PORT))
        monkeypatch.setenv("REDIS_PASSWORD", "")  # empty = no auth
        monkeypatch.setenv("REDIS_TLS_ENABLED", "false")
        monkeypatch.setenv("REDIS_CLUSTER_NODES", "")  # empty = fallback to host:port

        # Re-import to pick up new env
        from src.config import Settings

        settings_obj = Settings()
        cfg = settings_obj.redis

        # Verify our validators worked
        assert cfg.password is None, f"Expected None, got {cfg.password!r}"
        assert cfg.cluster_nodes is None, f"Expected None, got {cfg.cluster_nodes!r}"

        from src.core.pool import RedisPool

        pool = RedisPool()
        # Inject our test settings
        monkeypatch.setattr("src.core.pool.settings", settings_obj)
        pool._initialize()

        client = pool.get_client()
        assert isinstance(client, AsyncRedisCluster)

        # Test operations
        await client.set("test:pool:cluster", "works")
        val = await client.get("test:pool:cluster")
        assert val == "works"
        await client.delete("test:pool:cluster")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_pool_cluster_mode_with_explicit_nodes(self, monkeypatch):
        """RedisPool uses REDIS_CLUSTER_NODES when provided."""
        nodes_str = f"{CLUSTER_HOST}:{CLUSTER_PORT},{CLUSTER_HOST}:{CLUSTER_PORT + 1}"
        monkeypatch.setenv("REDIS_MODE", "cluster")
        monkeypatch.setenv("REDIS_CLUSTER_NODES", nodes_str)
        monkeypatch.setenv("REDIS_PASSWORD", "")
        monkeypatch.setenv("REDIS_TLS_ENABLED", "false")

        from src.config import Settings

        settings_obj = Settings()
        cfg = settings_obj.redis

        assert cfg.cluster_nodes == nodes_str
        assert cfg.password is None

        from src.core.pool import RedisPool

        pool = RedisPool()
        monkeypatch.setattr("src.core.pool.settings", settings_obj)
        pool._initialize()

        client = pool.get_client()
        result = await client.ping()
        assert result is True
        await client.aclose()


# ── Config Validator integration ─────────────────────────────────────────


@skip_no_cluster
class TestConfigValidatorClusterMode:
    """Test ConfigValidator._validate_redis_connection with real cluster."""

    def test_validator_cluster_succeeds(self, monkeypatch):
        """Config validator passes with a real cluster."""
        monkeypatch.setenv("REDIS_MODE", "cluster")
        monkeypatch.setenv("REDIS_HOST", CLUSTER_HOST)
        monkeypatch.setenv("REDIS_PORT", str(CLUSTER_PORT))
        monkeypatch.setenv("REDIS_PASSWORD", "")
        monkeypatch.setenv("REDIS_TLS_ENABLED", "false")
        monkeypatch.setenv("REDIS_CLUSTER_NODES", "")

        from src.config import Settings

        settings_obj = Settings()
        monkeypatch.setattr("src.utils.config_validator.settings", settings_obj)

        from src.utils.config_validator import ConfigValidator

        validator = ConfigValidator()
        validator._validate_redis_connection()

        assert not validator.errors, f"Unexpected errors: {validator.errors}"

    def test_validator_cluster_with_explicit_nodes(self, monkeypatch):
        """Config validator passes with explicit cluster nodes."""
        nodes_str = f"{CLUSTER_HOST}:{CLUSTER_PORT},{CLUSTER_HOST}:{CLUSTER_PORT + 1},{CLUSTER_HOST}:{CLUSTER_PORT + 2}"
        monkeypatch.setenv("REDIS_MODE", "cluster")
        monkeypatch.setenv("REDIS_CLUSTER_NODES", nodes_str)
        monkeypatch.setenv("REDIS_PASSWORD", "")
        monkeypatch.setenv("REDIS_TLS_ENABLED", "false")

        from src.config import Settings

        settings_obj = Settings()
        monkeypatch.setattr("src.utils.config_validator.settings", settings_obj)

        from src.utils.config_validator import ConfigValidator

        validator = ConfigValidator()
        validator._validate_redis_connection()

        assert not validator.errors, f"Unexpected errors: {validator.errors}"
