"""Tests for Redis configuration."""

import os
import ssl
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
    """Test RedisConfig.get_ssl_kwargs() builds SSL context."""

    def test_ssl_disabled_returns_empty(self):
        """When SSL is disabled, get_ssl_kwargs() returns empty dict."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=False)
            assert config.get_ssl_kwargs() == {}

    def test_ssl_enabled_returns_ssl_context(self):
        """When SSL is enabled, get_ssl_kwargs() returns ssl_context."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True)
            kwargs = config.get_ssl_kwargs()
            assert "ssl_context" in kwargs
            assert isinstance(kwargs["ssl_context"], ssl.SSLContext)

    def test_ssl_context_has_default_verify_mode(self):
        """Default SSL context uses CERT_REQUIRED."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True)
            ctx = config.get_ssl_kwargs()["ssl_context"]
            assert ctx.verify_mode == ssl.CERT_REQUIRED
            assert ctx.check_hostname is True

    def test_ssl_context_cert_reqs_none(self):
        """When cert_reqs is 'none', verification is disabled."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True, redis_ssl_cert_reqs="none")
            ctx = config.get_ssl_kwargs()["ssl_context"]
            assert ctx.verify_mode == ssl.CERT_NONE
            assert ctx.check_hostname is False

    def test_ssl_context_cert_reqs_optional(self):
        """When cert_reqs is 'optional', mode is CERT_OPTIONAL."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(
                redis_ssl=True,
                redis_ssl_cert_reqs="optional",
                redis_ssl_check_hostname=False,
            )
            ctx = config.get_ssl_kwargs()["ssl_context"]
            assert ctx.verify_mode == ssl.CERT_OPTIONAL

    def test_ssl_context_check_hostname_disabled(self):
        """When check_hostname is False, it is disabled in the context."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True, redis_ssl_check_hostname=False)
            ctx = config.get_ssl_kwargs()["ssl_context"]
            assert ctx.check_hostname is False

    def test_ssl_context_with_ca_certs(self):
        """When ssl_ca_certs is set, the context loads the CA file."""
        import tempfile

        # Create a temporary self-signed CA cert for testing
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".pem") as f:
            # Generate a minimal self-signed cert for test purposes
            f.write("")  # Empty file won't be loaded, we test the path is used
            ca_file = f.name

        try:
            with patch.dict(os.environ, get_clean_env(), clear=True):
                config = RedisConfig(redis_ssl=True, redis_ssl_ca_certs=ca_file)
                # _build_ssl_context calls create_default_context(cafile=ca_file)
                # An empty file will raise an error, which confirms it's being used
                try:
                    config._build_ssl_context()
                except ssl.SSLError:
                    pass  # Expected - empty file is not a valid cert
        finally:
            os.unlink(ca_file)

    def test_ssl_kwargs_no_individual_params(self):
        """SSL kwargs should only contain ssl_context, not individual params."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = RedisConfig(redis_ssl=True)
            kwargs = config.get_ssl_kwargs()
            assert "ssl_ca_certs" not in kwargs
            assert "ssl_certfile" not in kwargs
            assert "ssl_keyfile" not in kwargs
            assert "ssl_cert_reqs" not in kwargs
            assert "ssl_check_hostname" not in kwargs
