"""Unit tests for configuration validator."""

from unittest.mock import MagicMock, patch

import pytest
import redis
from minio.error import S3Error

from src.utils.config_validator import (
    ConfigurationError,
    ConfigValidator,
    get_configuration_summary,
    validate_configuration,
)


class TestConfigurationError:
    """Tests for ConfigurationError exception."""

    def test_exception_creation(self):
        """Test creating ConfigurationError."""
        error = ConfigurationError("Test error")
        assert str(error) == "Test error"


class TestConfigValidatorInit:
    """Tests for ConfigValidator initialization."""

    def test_init(self):
        """Test validator initialization."""
        validator = ConfigValidator()

        assert validator.errors == []
        assert validator.warnings == []


class TestValidateApiConfig:
    """Tests for _validate_api_config method."""

    def test_api_key_too_short(self):
        """Test API key length validation."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.api_key = "short"
            mock_settings.api_keys = []

            validator._validate_api_config()

        assert any("16 characters" in e for e in validator.errors)

    def test_default_api_key_warning(self):
        """Test warning for default API key."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.api_key = "test-api-key"
            mock_settings.api_keys = []

            validator._validate_api_config()

        assert any("default API key" in w for w in validator.warnings)

    def test_additional_api_keys_too_short(self):
        """Test additional API key validation."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.api_key = "a" * 20
            mock_settings.api_keys = ["short"]

            validator._validate_api_config()

        assert any("Additional API key too short" in e for e in validator.errors)

    def test_valid_api_config(self):
        """Test valid API configuration."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.api_key = "valid-api-key-12345678"
            mock_settings.api_keys = ["another-valid-key-123"]

            validator._validate_api_config()

        assert len(validator.errors) == 0


class TestValidateSecurityConfig:
    """Tests for _validate_security_config method."""

    def test_no_file_extensions_warning(self):
        """Test warning when no file extensions configured."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.allowed_file_extensions = []
            mock_settings.enable_network_isolation = True
            mock_settings.enable_filesystem_isolation = True

            validator._validate_security_config()

        assert any("No allowed file extensions" in w for w in validator.warnings)

    def test_network_isolation_disabled_warning(self):
        """Test warning when network isolation disabled."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.allowed_file_extensions = [".txt"]
            mock_settings.enable_network_isolation = False
            mock_settings.enable_filesystem_isolation = True

            validator._validate_security_config()

        assert any("Network isolation" in w for w in validator.warnings)

    def test_filesystem_isolation_disabled_warning(self):
        """Test warning when filesystem isolation disabled."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.allowed_file_extensions = [".txt"]
            mock_settings.enable_network_isolation = True
            mock_settings.enable_filesystem_isolation = False

            validator._validate_security_config()

        assert any("Filesystem isolation" in w for w in validator.warnings)


class TestValidateResourceLimits:
    """Tests for _validate_resource_limits method."""

    def test_invalid_file_size_limits(self):
        """Test error when total file size less than individual."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.max_file_size_mb = 100
            mock_settings.max_total_file_size_mb = 50

            validator._validate_resource_limits()

        assert any("Total file size limit" in e for e in validator.errors)

    def test_valid_file_size_limits(self):
        """Test valid file size limits."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.max_file_size_mb = 50
            mock_settings.max_total_file_size_mb = 100

            validator._validate_resource_limits()

        assert len(validator.errors) == 0


class TestValidateFileConfig:
    """Tests for _validate_file_config method."""

    def test_extension_missing_dot(self):
        """Test error when extension doesn't start with dot."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.allowed_file_extensions = ["txt", ".csv"]

            validator._validate_file_config()

        assert any("must start with dot" in e for e in validator.errors)

    def test_valid_extensions(self):
        """Test valid extensions."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.allowed_file_extensions = [".txt", ".csv", ".json"]

            validator._validate_file_config()

        assert len(validator.errors) == 0


class TestValidateRedisConnection:
    """Tests for _validate_redis_connection method."""

    def test_redis_connection_success(self):
        """Test successful Redis connection."""
        validator = ConfigValidator()

        mock_client = MagicMock()
        mock_client.ping.return_value = True

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.get_redis_url.return_value = "redis://localhost:6379"
            mock_settings.redis_socket_timeout = 5
            mock_settings.redis_socket_connect_timeout = 5
            mock_settings.redis_max_connections = 10

            with patch("src.utils.config_validator.redis.from_url", return_value=mock_client):
                validator._validate_redis_connection()

        assert len(validator.errors) == 0

    def test_redis_connection_error_debug_mode(self):
        """Test Redis connection error in debug mode."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.get_redis_url.return_value = "redis://localhost:6379"
            mock_settings.redis_socket_timeout = 5
            mock_settings.redis_socket_connect_timeout = 5
            mock_settings.redis_max_connections = 10
            mock_settings.api_debug = True

            with patch(
                "src.utils.config_validator.redis.from_url", side_effect=redis.ConnectionError("Connection failed")
            ):
                validator._validate_redis_connection()

        assert any("Cannot connect to Redis" in w for w in validator.warnings)
        assert len(validator.errors) == 0

    def test_redis_connection_error_production_mode(self):
        """Test Redis connection error in production mode."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.get_redis_url.return_value = "redis://localhost:6379"
            mock_settings.redis_socket_timeout = 5
            mock_settings.redis_socket_connect_timeout = 5
            mock_settings.redis_max_connections = 10
            mock_settings.api_debug = False

            with patch(
                "src.utils.config_validator.redis.from_url", side_effect=redis.ConnectionError("Connection failed")
            ):
                validator._validate_redis_connection()

        assert any("Cannot connect to Redis" in e for e in validator.errors)

    def test_redis_authentication_error(self):
        """Test Redis authentication error.

        Note: redis.AuthenticationError inherits from redis.ConnectionError,
        so the exception may be caught by the ConnectionError handler first.
        We test that the error message is added appropriately.
        """
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.get_redis_url.return_value = "redis://localhost:6379"
            mock_settings.redis_socket_timeout = 5
            mock_settings.redis_socket_connect_timeout = 5
            mock_settings.redis_max_connections = 10
            mock_settings.api_debug = False

            with patch(
                "src.utils.config_validator.redis.from_url", side_effect=redis.AuthenticationError("Auth failed")
            ):
                validator._validate_redis_connection()

        # AuthenticationError inherits from ConnectionError, so it may be caught there
        # The important thing is that an error about connection is recorded
        assert any("Cannot connect to Redis" in e for e in validator.errors)


class TestValidateMinioConnection:
    """Tests for _validate_minio_connection method."""

    def test_minio_connection_success(self):
        """Test successful MinIO connection."""
        validator = ConfigValidator()

        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True

        mock_minio_config = MagicMock()
        mock_minio_config.create_client.return_value = mock_client

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.minio = mock_minio_config
            mock_settings.minio_bucket = "test-bucket"

            validator._validate_minio_connection()

        assert len(validator.errors) == 0
        assert len(validator.warnings) == 0

    def test_minio_bucket_not_exists(self):
        """Test MinIO bucket doesn't exist warning."""
        validator = ConfigValidator()

        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = False

        mock_minio_config = MagicMock()
        mock_minio_config.create_client.return_value = mock_client

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.minio = mock_minio_config
            mock_settings.minio_bucket = "test-bucket"

            validator._validate_minio_connection()

        assert any("does not exist" in w for w in validator.warnings)

    def test_minio_s3_error_debug_mode(self):
        """Test MinIO S3 error in debug mode."""
        validator = ConfigValidator()

        mock_minio_config = MagicMock()
        mock_minio_config.create_client.side_effect = S3Error("Error", "Test", "test", "test", "test", "test")

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.minio = mock_minio_config
            mock_settings.api_debug = True

            validator._validate_minio_connection()

        assert any("MinIO S3 error" in w for w in validator.warnings)


class TestValidateKubernetesConfig:
    """Tests for _validate_kubernetes_config method."""

    def test_kubernetes_disabled(self):
        """Test when pod pool is disabled."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.pod_pool_enabled = False

            validator._validate_kubernetes_config()

        assert len(validator.errors) == 0
        assert len(validator.warnings) == 0

    def test_kubernetes_no_sidecar_image(self):
        """Test warning when sidecar image not set."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.pod_pool_enabled = True
            mock_settings.k8s_sidecar_image = ""
            mock_settings.k8s_memory_limit = "512Mi"
            mock_settings.k8s_image_registry = "docker.io"

            validator._validate_kubernetes_config()

        assert any("sidecar image" in w for w in validator.warnings)

    def test_kubernetes_low_memory(self):
        """Test warning when memory limit is too low."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.pod_pool_enabled = True
            mock_settings.k8s_sidecar_image = "my-sidecar:latest"
            mock_settings.k8s_memory_limit = "32Mi"
            mock_settings.k8s_image_registry = "docker.io"

            validator._validate_kubernetes_config()

        assert any("may be too low" in w for w in validator.warnings)

    def test_kubernetes_memory_gi_format(self):
        """Test parsing Gi memory format."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.pod_pool_enabled = True
            mock_settings.k8s_sidecar_image = "my-sidecar:latest"
            mock_settings.k8s_memory_limit = "2Gi"
            mock_settings.k8s_image_registry = "docker.io"

            validator._validate_kubernetes_config()

        # 2Gi is above 64MB so no warning
        assert not any("may be too low" in w for w in validator.warnings)

    def test_kubernetes_no_image_registry(self):
        """Test warning when image registry not set."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.pod_pool_enabled = True
            mock_settings.k8s_sidecar_image = "my-sidecar:latest"
            mock_settings.k8s_memory_limit = "512Mi"
            mock_settings.k8s_image_registry = ""

            validator._validate_kubernetes_config()

        assert any("image registry" in w for w in validator.warnings)

    def test_kubernetes_invalid_memory_format(self):
        """Test warning for invalid memory format."""
        validator = ConfigValidator()

        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.pod_pool_enabled = True
            mock_settings.k8s_sidecar_image = "my-sidecar:latest"
            mock_settings.k8s_memory_limit = "invalid"
            mock_settings.k8s_image_registry = "docker.io"

            validator._validate_kubernetes_config()

        assert any("Invalid Kubernetes memory limit" in w for w in validator.warnings)


class TestValidateAll:
    """Tests for validate_all method."""

    def test_validate_all_success(self):
        """Test successful validation."""
        validator = ConfigValidator()

        # Mock all validation methods to do nothing
        with patch.object(validator, "_validate_api_config"):
            with patch.object(validator, "_validate_security_config"):
                with patch.object(validator, "_validate_resource_limits"):
                    with patch.object(validator, "_validate_file_config"):
                        with patch.object(validator, "_validate_redis_connection"):
                            with patch.object(validator, "_validate_minio_connection"):
                                with patch.object(validator, "_validate_kubernetes_config"):
                                    result = validator.validate_all()

        assert result is True

    def test_validate_all_with_errors(self):
        """Test validation returns False when a real check produces an error."""
        validator = ConfigValidator()

        # Trigger a real validation error: total file size < individual file size
        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.api_key = "valid-key-long-enough"
            mock_settings.api_keys = []
            mock_settings.allowed_file_extensions = [".py"]
            mock_settings.enable_network_isolation = True
            mock_settings.enable_filesystem_isolation = True
            mock_settings.max_file_size_mb = 100
            mock_settings.max_total_file_size_mb = 10  # Less than individual → error
            mock_settings.api_debug = True
            mock_settings.pod_pool_enabled = False

            result = validator.validate_all()

        assert result is False
        assert any("Total file size" in e for e in validator.errors)

    def test_validate_all_clears_previous(self):
        """Test that validate_all clears previous errors/warnings."""
        validator = ConfigValidator()
        validator.errors.append("Previous error")
        validator.warnings.append("Previous warning")

        with patch.object(validator, "_validate_api_config"):
            with patch.object(validator, "_validate_security_config"):
                with patch.object(validator, "_validate_resource_limits"):
                    with patch.object(validator, "_validate_file_config"):
                        with patch.object(validator, "_validate_redis_connection"):
                            with patch.object(validator, "_validate_minio_connection"):
                                with patch.object(validator, "_validate_kubernetes_config"):
                                    validator.validate_all()

        # Previous items should be cleared
        assert "Previous error" not in validator.errors
        assert "Previous warning" not in validator.warnings


class TestValidateConfiguration:
    """Tests for validate_configuration function."""

    def test_validate_configuration_function(self):
        """Test validate_configuration function."""
        with patch("src.utils.config_validator.ConfigValidator") as mock_cls:
            mock_validator = MagicMock()
            mock_validator.validate_all.return_value = True
            mock_cls.return_value = mock_validator

            result = validate_configuration()

        assert result is True
        mock_validator.validate_all.assert_called_once()


class TestGetConfigurationSummary:
    """Tests for get_configuration_summary function."""

    def test_get_summary(self):
        """Test getting configuration summary."""
        with patch("src.utils.config_validator.settings") as mock_settings:
            mock_settings.api_debug = True
            mock_settings.supported_languages = ["python", "javascript"]
            mock_settings.max_execution_time = 30
            mock_settings.max_memory_mb = 512

            summary = get_configuration_summary()

        assert summary["debug"] is True
        assert summary["languages"] == 2
        assert summary["max_execution_time"] == 30
        assert summary["max_memory_mb"] == 512
