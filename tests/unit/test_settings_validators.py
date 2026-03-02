"""Unit tests for Settings validators.

Tests that our Settings class validates configuration values correctly.
"""

import pytest
from pydantic import ValidationError

from src.config import Settings


class TestSeccompProfileTypeValidator:
    """Tests for seccomp profile type validation."""

    def test_accepts_runtime_default(self):
        """Test that RuntimeDefault is accepted."""
        settings = Settings(k8s_seccomp_profile_type="RuntimeDefault")
        assert settings.k8s_seccomp_profile_type == "RuntimeDefault"

    def test_accepts_unconfined(self):
        """Test that Unconfined is accepted."""
        settings = Settings(k8s_seccomp_profile_type="Unconfined")
        assert settings.k8s_seccomp_profile_type == "Unconfined"

    def test_rejects_localhost(self):
        """Test that Localhost is rejected (requires localhostProfile path)."""
        with pytest.raises(ValidationError) as exc_info:
            Settings(k8s_seccomp_profile_type="Localhost")

        errors = exc_info.value.errors()
        assert any("seccomp_profile_type" in str(e) for e in errors)

    def test_rejects_invalid_type(self):
        """Test that arbitrary invalid types are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Settings(k8s_seccomp_profile_type="InvalidType")

        errors = exc_info.value.errors()
        assert any("seccomp_profile_type" in str(e) for e in errors)

    def test_default_is_runtime_default(self):
        """Test that the default seccomp profile type is RuntimeDefault."""
        settings = Settings()
        assert settings.k8s_seccomp_profile_type == "RuntimeDefault"


class TestRedisPasswordValidator:
    """Tests for empty-string-to-None password sanitization."""

    def test_empty_password_becomes_none(self):
        """Empty string REDIS_PASSWORD is converted to None."""
        settings = Settings(redis_password="")
        assert settings.redis_password is None

    def test_whitespace_password_becomes_none(self):
        """Whitespace-only REDIS_PASSWORD is converted to None."""
        settings = Settings(redis_password="  ")
        assert settings.redis_password is None

    def test_real_password_preserved(self):
        """Non-empty password is kept as-is."""
        settings = Settings(redis_password="s3cret")
        assert settings.redis_password == "s3cret"

    def test_none_password_stays_none(self):
        """None password stays None."""
        settings = Settings(redis_password=None)
        assert settings.redis_password is None

    def test_empty_sentinel_password_becomes_none(self):
        """Empty sentinel password is converted to None."""
        settings = Settings(redis_sentinel_password="")
        assert settings.redis_sentinel_password is None


class TestRedisClusterNodesValidator:
    """Tests for empty-string-to-None cluster/sentinel node sanitization."""

    def test_empty_cluster_nodes_becomes_none(self):
        """Empty REDIS_CLUSTER_NODES is converted to None."""
        settings = Settings(redis_cluster_nodes="")
        assert settings.redis_cluster_nodes is None

    def test_whitespace_cluster_nodes_becomes_none(self):
        """Whitespace-only REDIS_CLUSTER_NODES is converted to None."""
        settings = Settings(redis_cluster_nodes="   ")
        assert settings.redis_cluster_nodes is None

    def test_real_cluster_nodes_preserved(self):
        """Valid node list is kept."""
        settings = Settings(redis_cluster_nodes="node1:7000,node2:7001")
        assert settings.redis_cluster_nodes == "node1:7000,node2:7001"

    def test_empty_sentinel_nodes_becomes_none(self):
        """Empty REDIS_SENTINEL_NODES is converted to None."""
        settings = Settings(redis_sentinel_nodes="")
        assert settings.redis_sentinel_nodes is None

    def test_real_sentinel_nodes_preserved(self):
        """Valid sentinel node list is kept."""
        settings = Settings(redis_sentinel_nodes="sent1:26379,sent2:26379")
        assert settings.redis_sentinel_nodes == "sent1:26379,sent2:26379"


class TestRedisConfigValidators:
    """Tests for RedisConfig-level validators (password + nodes)."""

    def test_redis_config_empty_password_to_none(self):
        """RedisConfig also converts empty password to None."""
        from src.config.redis import RedisConfig

        cfg = RedisConfig(redis_password="")
        assert cfg.password is None

    def test_redis_config_empty_cluster_nodes_to_none(self):
        """RedisConfig also converts empty cluster nodes to None."""
        from src.config.redis import RedisConfig

        cfg = RedisConfig(redis_cluster_nodes="")
        assert cfg.cluster_nodes is None

    def test_redis_config_real_values_preserved(self):
        """Non-empty values pass through."""
        from src.config.redis import RedisConfig

        cfg = RedisConfig(redis_password="pass", redis_cluster_nodes="h:7000")
        assert cfg.password == "pass"
        assert cfg.cluster_nodes == "h:7000"
