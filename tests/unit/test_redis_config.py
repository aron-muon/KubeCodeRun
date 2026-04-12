"""Tests for Redis configuration."""

import os
from unittest.mock import patch

from src.config.redis import RedisConfig

REDIS_ENV_VARS = [
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_PASSWORD",
    "REDIS_DB",
    "REDIS_URL",
    "REDIS_SSL",
    "REDIS_SSL_CA_CERTS",
    "REDIS_SSL_CERTFILE",
    "REDIS_SSL_KEYFILE",
    "REDIS_SSL_CERT_REQS",
    "REDIS_SSL_CHECK_HOSTNAME",
    "REDIS_MODE",
    "REDIS_MAX_CONNECTIONS",
    "REDIS_SOCKET_TIMEOUT",
    "REDIS_SOCKET_CONNECT_TIMEOUT",
    "REDIS_KEY_PREFIX",
    "REDIS_CLUSTER_NODES",
    "REDIS_SENTINEL_NODES",
    "REDIS_SENTINEL_MASTER",
    "REDIS_SENTINEL_PASSWORD",
    "REDIS_SENTINEL_DB",
]


def get_clean_env():
    """Return environment with REDIS_ vars removed."""
    return {k: v for k, v in os.environ.items() if k not in REDIS_ENV_VARS}


class TestRedisGetUrl:
    """Test RedisConfig.get_url() builds URLs from individual fields."""

    def test_get_url_returns_explicit_url(self):
        """When REDIS_URL is set, get_url() returns it directly."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_url="redis://custom:6380/1")
            assert config.get_url() == "redis://custom:6380/1"

    def test_get_url_builds_from_fields(self):
        """When REDIS_URL is not set, get_url() builds from host/port/db."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_host="my-redis", redis_port=6380, redis_db=2)
            assert config.get_url() == "redis://my-redis:6380/2"

    def test_get_url_builds_with_password(self):
        """When password is set, get_url() includes it in the URL."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(
                redis_host="my-redis",
                redis_port=6379,
                redis_password="secret",
                redis_db=0,
            )
            assert config.get_url() == "redis://:secret@my-redis:6379/0"

    def test_get_url_uses_rediss_scheme_when_ssl(self):
        """When SSL is enabled, get_url() uses rediss:// scheme."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_host="my-redis", redis_ssl=True)
            assert config.get_url().startswith("rediss://")

    def test_get_url_defaults(self):
        """Default get_url() returns redis://localhost:6379/0."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig()
            assert config.get_url() == "redis://localhost:6379/0"

    def test_get_url_from_env_vars(self):
        """get_url() builds from individual REDIS_* env vars."""
        clean_env = get_clean_env()
        clean_env.update(
            {
                "REDIS_HOST": "env-redis",
                "REDIS_PORT": "6380",
                "REDIS_PASSWORD": "env-pass",
                "REDIS_DB": "3",
            }
        )
        with patch.dict(os.environ, clean_env, clear=True):
            config = RedisConfig()
            assert config.get_url() == "redis://:env-pass@env-redis:6380/3"


class TestRedisGetSslKwargs:
    """Test RedisConfig.get_ssl_kwargs() returns correct SSL parameters."""

    def test_ssl_disabled_returns_empty(self):
        """When SSL is disabled, get_ssl_kwargs() returns empty dict."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=False)
            assert config.get_ssl_kwargs() == {}

    def test_ssl_enabled_returns_cert_reqs_and_hostname(self):
        """When SSL is enabled, always includes cert_reqs and check_hostname."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True)
            kwargs = config.get_ssl_kwargs()
            assert kwargs["ssl_cert_reqs"] == "required"
            assert kwargs["ssl_check_hostname"] is True

    def test_ssl_omits_none_ca_certs(self):
        """When ssl_ca_certs is not set, it is omitted from kwargs."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True)
            kwargs = config.get_ssl_kwargs()
            assert "ssl_ca_certs" not in kwargs

    def test_ssl_includes_ca_certs_when_set(self):
        """When ssl_ca_certs is set, it is included in kwargs."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True, redis_ssl_ca_certs="/etc/ssl/ca.crt")
            kwargs = config.get_ssl_kwargs()
            assert kwargs["ssl_ca_certs"] == "/etc/ssl/ca.crt"

    def test_ssl_omits_none_certfile(self):
        """When ssl_certfile is not set, it is omitted from kwargs."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True)
            kwargs = config.get_ssl_kwargs()
            assert "ssl_certfile" not in kwargs
            assert "ssl_keyfile" not in kwargs

    def test_ssl_includes_client_cert_when_set(self):
        """When ssl_certfile is set, both cert and key are included."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(
                redis_ssl=True,
                redis_ssl_certfile="/etc/ssl/client.crt",
                redis_ssl_keyfile="/etc/ssl/client.key",
            )
            kwargs = config.get_ssl_kwargs()
            assert kwargs["ssl_certfile"] == "/etc/ssl/client.crt"
            assert kwargs["ssl_keyfile"] == "/etc/ssl/client.key"

    def test_ssl_cert_reqs_none(self):
        """When cert_reqs is 'none', it is passed through."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True, redis_ssl_cert_reqs="none")
            kwargs = config.get_ssl_kwargs()
            assert kwargs["ssl_cert_reqs"] == "none"

    def test_ssl_check_hostname_disabled(self):
        """When check_hostname is False, it is passed through."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True, redis_ssl_check_hostname=False)
            kwargs = config.get_ssl_kwargs()
            assert kwargs["ssl_check_hostname"] is False

    def test_ssl_kwargs_from_env(self):
        """SSL kwargs load correctly from environment variables."""
        clean_env = get_clean_env()
        clean_env.update(
            {
                "REDIS_SSL": "true",
                "REDIS_SSL_CA_CERTS": "/etc/ssl/redis-ca.pem",
                "REDIS_SSL_CERT_REQS": "required",
                "REDIS_SSL_CHECK_HOSTNAME": "true",
            }
        )
        with patch.dict(os.environ, clean_env, clear=True):
            config = RedisConfig()
            kwargs = config.get_ssl_kwargs()
            assert kwargs["ssl_ca_certs"] == "/etc/ssl/redis-ca.pem"
            assert kwargs["ssl_cert_reqs"] == "required"
            assert kwargs["ssl_check_hostname"] is True

    def test_ssl_kwargs_accepted_by_connection_pool(self):
        """Verify kwargs are compatible with redis-py ConnectionPool.from_url()."""
        import redis.asyncio as redis

        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True, redis_ssl_ca_certs="/etc/ssl/ca.crt")
            kwargs = config.get_ssl_kwargs()
            # Should not raise - validates that kwargs are accepted by redis-py
            pool = redis.ConnectionPool.from_url(
                "rediss://localhost:6379/0",
                decode_responses=True,
                **kwargs,
            )
            assert pool is not None
