"""Unit tests for core Redis pool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.pool import RedisPool


class TestRedisPoolInit:
    """Tests for RedisPool initialization."""

    def test_init(self):
        """Test pool initialization."""
        pool = RedisPool()

        assert pool._pool is None
        assert pool._client is None
        assert pool._initialized is False


class TestRedisPoolInitialize:
    """Tests for _initialize method."""

    def test_initialize_already_initialized(self):
        """Test _initialize returns early if already initialized."""
        pool = RedisPool()
        pool._initialized = True
        pool._pool = MagicMock()

        pool._initialize()

        # Should not modify the pool
        assert pool._initialized is True

    def test_initialize_creates_pool(self):
        """Test _initialize creates connection pool."""
        pool = RedisPool()

        with patch("src.core.pool.settings") as mock_settings:
            mock_settings.get_redis_url.return_value = "redis://localhost:6379/0"

            with patch("src.core.pool.redis.ConnectionPool") as mock_pool:
                mock_pool.from_url.return_value = MagicMock()

                with patch("src.core.pool.redis.Redis") as mock_redis:
                    mock_redis.return_value = MagicMock()

                    pool._initialize()

        assert pool._initialized is True
        assert pool._client is not None

    def test_initialize_raises_on_error(self):
        """Test _initialize propagates errors instead of silently falling back."""
        pool = RedisPool()

        with patch("src.core.pool.settings") as mock_settings:
            mock_settings.redis.mode = "standalone"
            mock_settings.redis.get_url.side_effect = Exception("Connection failed")
            mock_settings.redis.get_tls_kwargs.return_value = {}
            mock_settings.redis.key_prefix = ""
            mock_settings.redis.max_connections = 20
            mock_settings.redis.socket_timeout = 5
            mock_settings.redis.socket_connect_timeout = 5

            with pytest.raises(Exception, match="Connection failed"):
                pool._initialize()

        assert pool._initialized is False
        assert pool._client is None


class TestGetClient:
    """Tests for get_client method."""

    def test_init_cluster_does_not_pass_retry_on_timeout(self):
        """Test _init_cluster uses retry/retry_on_error instead of retry_on_timeout.

        RedisCluster (async) does not accept retry_on_timeout as a kwarg.
        """
        pool = RedisPool()

        with patch("src.core.pool.settings") as mock_settings:
            cfg = mock_settings.redis
            cfg.mode = "cluster"
            cfg.host = "redis-host"
            cfg.port = 6379
            cfg.password = None
            cfg.cluster_nodes = None
            cfg.key_prefix = ""
            cfg.tls_enabled = False
            cfg.max_connections = 20
            cfg.socket_timeout = 5
            cfg.socket_connect_timeout = 5
            cfg.get_tls_kwargs.return_value = {}

            with patch("src.core.pool.RedisCluster") as mock_cluster:
                mock_cluster.return_value = MagicMock()
                pool._initialize()

                mock_cluster.assert_called_once()
                call_kwargs = mock_cluster.call_args[1]
                assert "retry_on_timeout" not in call_kwargs, (
                    "RedisCluster does not accept retry_on_timeout"
                )
                assert "retry" in call_kwargs
                assert "retry_on_error" in call_kwargs

    def test_get_client_initializes_if_needed(self):
        """Test get_client initializes the pool if not initialized."""
        pool = RedisPool()

        with patch.object(pool, "_initialize") as mock_init:
            pool._initialized = False
            pool._client = MagicMock()

            mock_init.side_effect = lambda: setattr(pool, "_initialized", True)

            client = pool.get_client()

            mock_init.assert_called_once()

    def test_get_client_returns_client(self):
        """Test get_client returns the client."""
        pool = RedisPool()
        mock_client = MagicMock()
        pool._client = mock_client
        pool._initialized = True

        client = pool.get_client()

        assert client is mock_client


class TestPoolStats:
    """Tests for pool_stats property."""

    def test_pool_stats_not_initialized(self):
        """Test pool_stats when pool not initialized."""
        pool = RedisPool()
        pool._pool = None

        stats = pool.pool_stats

        assert stats == {"initialized": False, "mode": "standalone"}

    def test_pool_stats_initialized(self):
        """Test pool_stats when pool is initialized."""
        pool = RedisPool()
        mock_pool = MagicMock()
        mock_pool.max_connections = 20
        pool._pool = mock_pool
        pool._initialized = True

        stats = pool.pool_stats

        assert stats["initialized"] is True
        assert stats["mode"] == "standalone"
        assert stats["max_connections"] == 20


class TestClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close(self):
        """Test closing the pool."""
        pool = RedisPool()
        mock_client = AsyncMock()
        pool._client = mock_client
        pool._pool = MagicMock()
        pool._initialized = True

        await pool.close()

        mock_client.close.assert_called_once()
        assert pool._client is None
        assert pool._pool is None
        assert pool._initialized is False

    @pytest.mark.asyncio
    async def test_close_when_not_initialized(self):
        """Test closing when pool not initialized."""
        pool = RedisPool()
        pool._client = None
        pool._pool = None
        pool._initialized = False

        # Should not raise
        await pool.close()

        assert pool._client is None
