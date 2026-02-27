"""Unit tests for Settings validators.

Tests that our Settings class validates configuration values correctly.
"""

import logging

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


class TestKubernetesPropertyJsonParsing:
    """Tests for kubernetes property JSON parsing of GKE fields."""

    def test_valid_node_selector_json(self):
        """Test valid JSON for GKE_SANDBOX_NODE_SELECTOR is parsed."""
        settings = Settings(gke_sandbox_node_selector='{"pool": "sandbox"}')
        k8s = settings.kubernetes
        assert k8s.sandbox_node_selector == {"pool": "sandbox"}

    def test_invalid_node_selector_json_logs_warning(self, caplog):
        """Test invalid JSON for GKE_SANDBOX_NODE_SELECTOR logs a warning."""
        settings = Settings(gke_sandbox_node_selector="not-valid-json")
        with caplog.at_level(logging.WARNING):
            k8s = settings.kubernetes
        assert k8s.sandbox_node_selector is None
        assert "GKE_SANDBOX_NODE_SELECTOR" in caplog.text

    def test_valid_custom_tolerations_json(self):
        """Test valid JSON for GKE_SANDBOX_CUSTOM_TOLERATIONS is parsed."""
        settings = Settings(
            gke_sandbox_custom_tolerations='[{"key": "pool", "value": "sandbox"}]'
        )
        k8s = settings.kubernetes
        assert k8s.custom_tolerations == [{"key": "pool", "value": "sandbox"}]

    def test_invalid_custom_tolerations_json_logs_warning(self, caplog):
        """Test invalid JSON for GKE_SANDBOX_CUSTOM_TOLERATIONS logs a warning."""
        settings = Settings(gke_sandbox_custom_tolerations="[broken")
        with caplog.at_level(logging.WARNING):
            k8s = settings.kubernetes
        assert k8s.custom_tolerations is None
        assert "GKE_SANDBOX_CUSTOM_TOLERATIONS" in caplog.text

    def test_image_pull_policy_default_is_always(self):
        """Test that the default image_pull_policy is 'Always' (matches Settings)."""
        settings = Settings()
        k8s = settings.kubernetes
        assert k8s.image_pull_policy == "Always"
