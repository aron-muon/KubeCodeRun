"""Unit tests for core Redis pool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.pool import RedisPool


class TestRedisPoolInit:
    """Tests for RedisPool initialization."""

    def test_init(self):
        """Test pool initialization."""
        pool = RedisPool()

        assert pool._client is None
        assert pool._initialized is False
        assert pool._mode == "standalone"
        assert pool._key_prefix == ""


class TestRedisPoolInitialize:
    """Tests for _initialize method."""

    def test_initialize_already_initialized(self):
        """Test _initialize returns early if already initialized."""
        pool = RedisPool()
        pool._initialized = True
        pool._client = MagicMock()

        pool._initialize()

        # Should not modify the pool
        assert pool._initialized is True

    def test_initialize_creates_pool(self):
        """Test _initialize creates connection pool in standalone mode."""
        pool = RedisPool()

        mock_cfg = MagicMock()
        mock_cfg.mode = "standalone"
        mock_cfg.key_prefix = ""
        mock_cfg.get_ssl_kwargs.return_value = {}
        mock_cfg.get_url.return_value = "redis://localhost:6379/0"
        mock_cfg.max_connections = 20
        mock_cfg.socket_timeout = 5
        mock_cfg.socket_connect_timeout = 5

        with patch("src.config.settings") as mock_settings:
            mock_settings.redis = mock_cfg

            with patch("src.core.pool.redis.ConnectionPool") as mock_pool_cls:
                mock_pool_cls.from_url.return_value = MagicMock()

                with patch("src.core.pool.redis.Redis") as mock_redis:
                    mock_redis.return_value = MagicMock()
                    pool._initialize()

        assert pool._initialized is True
        assert pool._client is not None

    def test_initialize_fallback_on_error(self):
        """Test _initialize creates fallback client on error."""
        pool = RedisPool()

        mock_cfg = MagicMock()
        mock_cfg.mode = "standalone"
        mock_cfg.key_prefix = ""
        mock_cfg.get_ssl_kwargs.return_value = {}
        mock_cfg.get_url.side_effect = Exception("Connection failed")

        with patch("src.config.settings") as mock_settings:
            mock_settings.redis = mock_cfg

            with patch("src.core.pool.redis.from_url") as mock_from_url:
                mock_from_url.return_value = MagicMock()
                pool._initialize()

        assert pool._initialized is True
        assert pool._client is not None


class TestGetClient:
    """Tests for get_client method."""

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


class TestKeyPrefix:
    """Tests for key_prefix property."""

    def test_key_prefix_default(self):
        """Test key_prefix returns empty string by default."""
        pool = RedisPool()
        pool._initialized = True
        pool._key_prefix = ""

        assert pool.key_prefix == ""

    def test_key_prefix_configured(self):
        """Test key_prefix returns configured value."""
        pool = RedisPool()
        pool._initialized = True
        pool._key_prefix = "myapp:"

        assert pool.key_prefix == "myapp:"


class TestPoolStats:
    """Tests for pool_stats property."""

    def test_pool_stats_not_initialized(self):
        """Test pool_stats when pool not initialized."""
        pool = RedisPool()

        stats = pool.pool_stats

        assert stats == {"initialized": False}

    def test_pool_stats_initialized(self):
        """Test pool_stats when pool is initialized."""
        pool = RedisPool()
        pool._initialized = True
        pool._mode = "standalone"
        pool._key_prefix = "test:"

        stats = pool.pool_stats

        assert stats["initialized"] is True
        assert stats["mode"] == "standalone"
        assert stats["key_prefix"] == "test:"


class TestClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close(self):
        """Test closing the pool."""
        pool = RedisPool()
        mock_client = AsyncMock(spec=[])  # No spec attrs, so no aclose
        mock_client.close = AsyncMock()
        pool._client = mock_client
        pool._initialized = True

        await pool.close()

        mock_client.close.assert_called_once()
        assert pool._client is None
        assert pool._initialized is False

    @pytest.mark.asyncio
    async def test_close_with_aclose(self):
        """Test closing the pool with aclose method (cluster mode)."""
        pool = RedisPool()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        pool._client = mock_client
        pool._initialized = True

        await pool.close()

        mock_client.aclose.assert_called_once()
        assert pool._client is None

    @pytest.mark.asyncio
    async def test_close_when_not_initialized(self):
        """Test closing when pool not initialized."""
        pool = RedisPool()
        pool._client = None
        pool._initialized = False

        # Should not raise
        await pool.close()

        assert pool._client is None
