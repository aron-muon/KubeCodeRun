"""Integration tests for Redis Cluster with TLS.

Mirrors the user's production GCP Memorystore configuration:
- REDIS_MODE=cluster
- REDIS_TLS_ENABLED=true
- REDIS_TLS_CA_CERT_FILE=/path/to/ca.crt  (server verification)
- REDIS_TLS_CERT_FILE=""                   (no client cert / no mTLS)
- REDIS_TLS_KEY_FILE=""                    (no client key / no mTLS)
- REDIS_TLS_INSECURE=false                 (certificate chain verified)
- REDIS_TLS_CHECK_HOSTNAME not set         (defaults to false)
- REDIS_PASSWORD=""                         (no authentication)
- REDIS_CLUSTER_NODES not set              (falls back to host:port)
- REDIS_KEY_PREFIX=kubecoderun:

Requires a running TLS Redis Cluster on localhost:6380-6385.
Start with: docker compose -f docker-compose.redis-cluster-tls.yml up -d

Usage:
    uv run python -m pytest tests/integration/test_redis_cluster_tls.py -v
"""

import os
import ssl as ssl_mod
from pathlib import Path

import pytest
import redis as sync_redis
import redis.asyncio as async_redis
from redis.asyncio.cluster import RedisCluster as AsyncRedisCluster
from redis.cluster import ClusterNode, RedisCluster

# ── Configuration matching production ────────────────────────────────────

TLS_CLUSTER_HOST = os.environ.get("REDIS_TLS_CLUSTER_HOST", "127.0.0.1")
TLS_CLUSTER_PORT = int(os.environ.get("REDIS_TLS_CLUSTER_PORT", "6380"))

# CA cert path (relative to project root, same concept as production
# REDIS_TLS_CA_CERT_FILE=/app/api/cache/redis-ca.crt)
CERTS_DIR = Path(__file__).resolve().parent.parent / "tls-certs"
CA_CERT_FILE = str(CERTS_DIR / "ca.crt")

pytestmark = pytest.mark.integration


def _tls_kwargs_production() -> dict:
    """Build TLS kwargs matching production config.

    This mirrors what RedisConfig.get_tls_kwargs() produces with:
        REDIS_TLS_ENABLED=true
        REDIS_TLS_INSECURE=false
        REDIS_TLS_CHECK_HOSTNAME=false (default)
        REDIS_TLS_CA_CERT_FILE=/path/to/ca.crt
        REDIS_TLS_CERT_FILE=""  -> None
        REDIS_TLS_KEY_FILE=""   -> None
    """
    return {
        "ssl": True,
        "ssl_cert_reqs": ssl_mod.CERT_REQUIRED,
        "ssl_check_hostname": False,
        "ssl_ca_certs": CA_CERT_FILE,
    }


def _tls_cluster_available() -> bool:
    """Check if a TLS Redis Cluster is reachable."""
    try:
        rc = RedisCluster(
            startup_nodes=[ClusterNode(host=TLS_CLUSTER_HOST, port=TLS_CLUSTER_PORT)],
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=3,
            **_tls_kwargs_production(),
        )
        rc.ping()
        rc.close()
        return True
    except Exception:
        return False


skip_no_tls_cluster = pytest.mark.skipif(
    not _tls_cluster_available(),
    reason=f"TLS Redis Cluster not available at {TLS_CLUSTER_HOST}:{TLS_CLUSTER_PORT}",
)


# ── Synchronous TLS Cluster tests ────────────────────────────────────────


@skip_no_tls_cluster
class TestSyncTlsCluster:
    """Synchronous redis-py with TLS (same path as config_validator)."""

    def test_connect_single_startup_node_tls(self):
        """TLS cluster discovery from a single startup node."""
        rc = RedisCluster(
            startup_nodes=[ClusterNode(host=TLS_CLUSTER_HOST, port=TLS_CLUSTER_PORT)],
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
            **_tls_kwargs_production(),
        )
        assert rc.ping() is True
        node_info = rc.cluster_info(target_nodes=RedisCluster.RANDOM)
        assert node_info.get("cluster_state") == "ok"
        rc.close()

    def test_connect_no_password_tls(self):
        """TLS cluster with password=None (production has REDIS_PASSWORD='')."""
        rc = RedisCluster(
            startup_nodes=[ClusterNode(host=TLS_CLUSTER_HOST, port=TLS_CLUSTER_PORT)],
            password=None,
            decode_responses=True,
            socket_timeout=5,
            **_tls_kwargs_production(),
        )
        assert rc.ping() is True
        rc.close()

    def test_set_get_across_slots_tls(self):
        """SET/GET across cluster slots over TLS."""
        rc = RedisCluster(
            startup_nodes=[ClusterNode(host=TLS_CLUSTER_HOST, port=TLS_CLUSTER_PORT)],
            decode_responses=True,
            **_tls_kwargs_production(),
        )
        for i in range(10):
            key = f"test:tls:cluster:{i}"
            rc.set(key, f"value-{i}")
            assert rc.get(key) == f"value-{i}"
            rc.delete(key)
        rc.close()

    def test_key_prefix_operations_tls(self):
        """Operations with kubecoderun: prefix (matching production key_prefix)."""
        rc = RedisCluster(
            startup_nodes=[ClusterNode(host=TLS_CLUSTER_HOST, port=TLS_CLUSTER_PORT)],
            decode_responses=True,
            **_tls_kwargs_production(),
        )
        prefix = "kubecoderun:"
        key = f"{prefix}session:test-abc"
        rc.set(key, "session-data")
        assert rc.get(key) == "session-data"
        rc.delete(key)
        rc.close()


# ── Asynchronous TLS Cluster tests ───────────────────────────────────────


@skip_no_tls_cluster
class TestAsyncTlsCluster:
    """Async redis-py with TLS (same path as RedisPool._init_cluster)."""

    @pytest.mark.asyncio
    async def test_async_connect_tls(self):
        """Async TLS cluster client connects and pings."""
        from redis.backoff import ExponentialBackoff
        from redis.exceptions import ConnectionError, TimeoutError
        from redis.retry import Retry

        rc = AsyncRedisCluster(
            startup_nodes=[
                async_redis.cluster.ClusterNode(host=TLS_CLUSTER_HOST, port=TLS_CLUSTER_PORT),
            ],
            password=None,
            decode_responses=True,
            max_connections=20,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            retry=Retry(ExponentialBackoff(), retries=3),
            retry_on_error=[ConnectionError, TimeoutError],
            **_tls_kwargs_production(),
        )
        assert await rc.ping() is True
        await rc.aclose()

    @pytest.mark.asyncio
    async def test_async_set_get_tls(self):
        """Async SET/GET over TLS cluster."""
        rc = AsyncRedisCluster(
            startup_nodes=[
                async_redis.cluster.ClusterNode(host=TLS_CLUSTER_HOST, port=TLS_CLUSTER_PORT),
            ],
            decode_responses=True,
            **_tls_kwargs_production(),
        )
        for i in range(10):
            key = f"test:async:tls:{i}"
            await rc.set(key, f"tls-value-{i}")
            val = await rc.get(key)
            assert val == f"tls-value-{i}"
            await rc.delete(key)
        await rc.aclose()

    @pytest.mark.asyncio
    async def test_async_prefixed_operations_tls(self):
        """Async operations with production-like key prefix over TLS."""
        rc = AsyncRedisCluster(
            startup_nodes=[
                async_redis.cluster.ClusterNode(host=TLS_CLUSTER_HOST, port=TLS_CLUSTER_PORT),
            ],
            decode_responses=True,
            **_tls_kwargs_production(),
        )
        prefix = "kubecoderun:"
        keys = [f"{prefix}session:{i}" for i in range(5)]
        for key in keys:
            await rc.set(key, "data")
            assert await rc.get(key) == "data"
        for key in keys:
            await rc.delete(key)
        await rc.aclose()


# ── RedisPool with TLS Cluster ───────────────────────────────────────────


@skip_no_tls_cluster
class TestRedisPoolTlsCluster:
    """Test RedisPool with TLS cluster backend — mirrors production config."""

    @pytest.mark.asyncio
    async def test_pool_tls_cluster_production_config(self, monkeypatch):
        """RedisPool initializes with the exact production configuration.

        Env vars set here match the user's Helm values:
            REDIS_MODE: "cluster"
            REDIS_HOST: <host>
            REDIS_PORT: "6380"
            REDIS_PASSWORD: ""
            REDIS_DB: "0"
            REDIS_MAX_CONNECTIONS: "20"
            REDIS_SOCKET_TIMEOUT: "5"
            REDIS_SOCKET_CONNECT_TIMEOUT: "5"
            REDIS_KEY_PREFIX: "kubecoderun:"
            REDIS_TLS_ENABLED: "true"
            REDIS_TLS_CA_CERT_FILE: <ca cert path>
            REDIS_TLS_CERT_FILE: ""
            REDIS_TLS_KEY_FILE: ""
            REDIS_TLS_INSECURE: "false"
        """
        # Set env vars exactly as Helm renders them in production
        monkeypatch.setenv("REDIS_MODE", "cluster")
        monkeypatch.setenv("REDIS_HOST", TLS_CLUSTER_HOST)
        monkeypatch.setenv("REDIS_PORT", str(TLS_CLUSTER_PORT))
        monkeypatch.setenv("REDIS_PASSWORD", "")  # empty -> None via validator
        monkeypatch.setenv("REDIS_DB", "0")
        monkeypatch.setenv("REDIS_MAX_CONNECTIONS", "20")
        monkeypatch.setenv("REDIS_SOCKET_TIMEOUT", "5")
        monkeypatch.setenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5")
        monkeypatch.setenv("REDIS_KEY_PREFIX", "kubecoderun:")
        monkeypatch.setenv("REDIS_TLS_ENABLED", "true")
        monkeypatch.setenv("REDIS_TLS_CA_CERT_FILE", CA_CERT_FILE)
        monkeypatch.setenv("REDIS_TLS_CERT_FILE", "")  # no client cert
        monkeypatch.setenv("REDIS_TLS_KEY_FILE", "")  # no client key
        monkeypatch.setenv("REDIS_TLS_INSECURE", "false")
        monkeypatch.setenv("REDIS_CLUSTER_NODES", "")  # empty -> None, fallback to host:port

        from src.config import Settings

        settings_obj = Settings()
        cfg = settings_obj.redis

        # Verify validators worked correctly
        assert cfg.mode == "cluster"
        assert cfg.host == TLS_CLUSTER_HOST
        assert cfg.port == TLS_CLUSTER_PORT
        assert cfg.password is None, f"Expected None, got {cfg.password!r}"
        assert cfg.cluster_nodes is None, f"Expected None, got {cfg.cluster_nodes!r}"
        assert cfg.tls_enabled is True
        assert cfg.tls_ca_cert_file == CA_CERT_FILE
        assert cfg.tls_cert_file is None or cfg.tls_cert_file == ""
        assert cfg.tls_key_file is None or cfg.tls_key_file == ""
        assert cfg.tls_insecure is False
        assert cfg.tls_check_hostname is False  # default
        assert cfg.key_prefix == "kubecoderun:"

        # Verify TLS kwargs
        tls_kwargs = cfg.get_tls_kwargs()
        assert tls_kwargs["ssl"] is True
        assert tls_kwargs["ssl_cert_reqs"] == ssl_mod.CERT_REQUIRED
        assert tls_kwargs["ssl_check_hostname"] is False
        assert tls_kwargs["ssl_ca_certs"] == CA_CERT_FILE
        assert "ssl_certfile" not in tls_kwargs  # no client cert
        assert "ssl_keyfile" not in tls_kwargs  # no client key

        # Initialize pool
        from src.core.pool import RedisPool

        pool = RedisPool()
        monkeypatch.setattr("src.core.pool.settings", settings_obj)
        pool._initialize()

        client = pool.get_client()
        assert isinstance(client, AsyncRedisCluster)
        assert pool.key_prefix == "kubecoderun:"

        # Test operations with prefix
        full_key = pool.make_key("session:test-tls")
        assert full_key == "kubecoderun:session:test-tls"

        await client.set(full_key, "tls-session-data")
        val = await client.get(full_key)
        assert val == "tls-session-data"
        await client.delete(full_key)

        await pool.close()

    @pytest.mark.asyncio
    async def test_pool_tls_cluster_without_key_prefix(self, monkeypatch):
        """RedisPool works in TLS cluster mode without key prefix."""
        monkeypatch.setenv("REDIS_MODE", "cluster")
        monkeypatch.setenv("REDIS_HOST", TLS_CLUSTER_HOST)
        monkeypatch.setenv("REDIS_PORT", str(TLS_CLUSTER_PORT))
        monkeypatch.setenv("REDIS_PASSWORD", "")
        monkeypatch.setenv("REDIS_KEY_PREFIX", "")
        monkeypatch.setenv("REDIS_TLS_ENABLED", "true")
        monkeypatch.setenv("REDIS_TLS_CA_CERT_FILE", CA_CERT_FILE)
        monkeypatch.setenv("REDIS_TLS_INSECURE", "false")
        monkeypatch.setenv("REDIS_CLUSTER_NODES", "")

        from src.config import Settings
        from src.core.pool import RedisPool

        settings_obj = Settings()
        pool = RedisPool()
        monkeypatch.setattr("src.core.pool.settings", settings_obj)
        pool._initialize()

        client = pool.get_client()
        assert pool.key_prefix == ""
        assert pool.make_key("mykey") == "mykey"

        await client.set("test:no-prefix:tls", "ok")
        assert await client.get("test:no-prefix:tls") == "ok"
        await client.delete("test:no-prefix:tls")
        await pool.close()


# ── ConfigValidator with TLS Cluster ─────────────────────────────────────


@skip_no_tls_cluster
class TestConfigValidatorTlsCluster:
    """Test ConfigValidator._validate_redis_connection with TLS cluster."""

    def test_validator_tls_cluster_production_config(self, monkeypatch):
        """Config validator passes with production-like TLS cluster config."""
        monkeypatch.setenv("REDIS_MODE", "cluster")
        monkeypatch.setenv("REDIS_HOST", TLS_CLUSTER_HOST)
        monkeypatch.setenv("REDIS_PORT", str(TLS_CLUSTER_PORT))
        monkeypatch.setenv("REDIS_PASSWORD", "")
        monkeypatch.setenv("REDIS_TLS_ENABLED", "true")
        monkeypatch.setenv("REDIS_TLS_CA_CERT_FILE", CA_CERT_FILE)
        monkeypatch.setenv("REDIS_TLS_CERT_FILE", "")
        monkeypatch.setenv("REDIS_TLS_KEY_FILE", "")
        monkeypatch.setenv("REDIS_TLS_INSECURE", "false")
        monkeypatch.setenv("REDIS_CLUSTER_NODES", "")

        from src.config import Settings

        settings_obj = Settings()
        monkeypatch.setattr("src.utils.config_validator.settings", settings_obj)

        from src.utils.config_validator import ConfigValidator

        validator = ConfigValidator()
        validator._validate_redis_connection()

        assert not validator.errors, f"Unexpected errors: {validator.errors}"

    def test_validator_tls_cluster_bad_ca_cert_fails(self, monkeypatch):
        """Config validator fails when CA cert path is wrong."""
        monkeypatch.setenv("REDIS_MODE", "cluster")
        monkeypatch.setenv("REDIS_HOST", TLS_CLUSTER_HOST)
        monkeypatch.setenv("REDIS_PORT", str(TLS_CLUSTER_PORT))
        monkeypatch.setenv("REDIS_PASSWORD", "")
        monkeypatch.setenv("REDIS_TLS_ENABLED", "true")
        monkeypatch.setenv("REDIS_TLS_CA_CERT_FILE", "/nonexistent/ca.crt")
        monkeypatch.setenv("REDIS_TLS_INSECURE", "false")
        monkeypatch.setenv("REDIS_CLUSTER_NODES", "")

        from src.config import Settings

        settings_obj = Settings()
        monkeypatch.setattr("src.utils.config_validator.settings", settings_obj)

        from src.utils.config_validator import ConfigValidator

        validator = ConfigValidator()
        validator._validate_redis_connection()

        assert len(validator.errors) > 0, "Expected validation error for bad CA cert"


# ── RedisConfig TLS kwargs verification ──────────────────────────────────


@skip_no_tls_cluster
class TestRedisConfigTlsKwargs:
    """Verify RedisConfig.get_tls_kwargs() produces correct kwargs for production."""

    def test_production_tls_kwargs(self, monkeypatch):
        """get_tls_kwargs() output matches what RedisCluster needs for TLS."""
        monkeypatch.setenv("REDIS_MODE", "cluster")
        monkeypatch.setenv("REDIS_TLS_ENABLED", "true")
        monkeypatch.setenv("REDIS_TLS_CA_CERT_FILE", CA_CERT_FILE)
        monkeypatch.setenv("REDIS_TLS_CERT_FILE", "")
        monkeypatch.setenv("REDIS_TLS_KEY_FILE", "")
        monkeypatch.setenv("REDIS_TLS_INSECURE", "false")

        from src.config.redis import RedisConfig

        cfg = RedisConfig(
            redis_mode="cluster",
            redis_tls_enabled=True,
            redis_tls_ca_cert_file=CA_CERT_FILE,
            redis_tls_cert_file="",
            redis_tls_key_file="",
            redis_tls_insecure=False,
        )
        kwargs = cfg.get_tls_kwargs()

        assert kwargs["ssl"] is True
        assert kwargs["ssl_cert_reqs"] == ssl_mod.CERT_REQUIRED
        assert kwargs["ssl_check_hostname"] is False
        assert kwargs["ssl_ca_certs"] == CA_CERT_FILE
        # Empty string cert/key files should NOT be in kwargs
        assert "ssl_certfile" not in kwargs
        assert "ssl_keyfile" not in kwargs

    def test_tls_insecure_kwargs(self, monkeypatch):
        """get_tls_kwargs() with insecure mode skips cert verification."""
        from src.config.redis import RedisConfig

        cfg = RedisConfig(
            redis_mode="cluster",
            redis_tls_enabled=True,
            redis_tls_insecure=True,
        )
        kwargs = cfg.get_tls_kwargs()

        assert kwargs["ssl"] is True
        assert kwargs["ssl_cert_reqs"] == ssl_mod.CERT_NONE
        assert kwargs["ssl_check_hostname"] is False

    def test_tls_disabled_returns_empty(self):
        """get_tls_kwargs() returns empty dict when TLS is off."""
        from src.config.redis import RedisConfig

        cfg = RedisConfig(redis_tls_enabled=False)
        assert cfg.get_tls_kwargs() == {}
