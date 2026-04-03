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


class TestSessionTTLValidator:
    """Tests for session_ttl_hours validation."""

    def test_accepts_zero_infinite_ttl(self):
        """Test that 0 (no expiry) is accepted."""
        settings = Settings(session_ttl_hours=0)
        assert settings.session_ttl_hours == 0
        assert settings.get_session_ttl_minutes() == 0

    def test_accepts_normal_ttl(self):
        """Test that a normal TTL value is accepted."""
        settings = Settings(session_ttl_hours=24)
        assert settings.session_ttl_hours == 24
        assert settings.get_session_ttl_minutes() == 1440

    def test_accepts_max_ttl(self):
        """Test that the max TTL (1 year) is accepted."""
        settings = Settings(session_ttl_hours=8760)
        assert settings.session_ttl_hours == 8760

    def test_rejects_negative_ttl(self):
        """Test that negative TTL is rejected."""
        with pytest.raises(ValidationError):
            Settings(session_ttl_hours=-1)

    def test_rejects_over_max_ttl(self):
        """Test that TTL over 8760 (1 year) is rejected."""
        with pytest.raises(ValidationError):
            Settings(session_ttl_hours=8761)

    def test_default_is_zero(self):
        """Test that the default TTL is 0 (infinite)."""
        settings = Settings()
        assert settings.session_ttl_hours == 0
