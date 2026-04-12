"""Tests for MinIO configuration and client creation."""

import os
import tempfile
from unittest.mock import patch

import pytest

from src.config.minio import MinIOConfig

# Environment variables to clear for isolated tests
MINIO_ENV_VARS = [
    "MINIO_ENDPOINT",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "MINIO_SECURE",
    "MINIO_BUCKET",
    "MINIO_REGION",
    "MINIO_USE_IAM",
]

# AWS env vars that are also used as fallbacks for MinIO credentials
AWS_CREDENTIAL_VARS = [
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
]


def get_clean_env():
    """Return environment with MINIO_ and AWS credential vars removed."""
    return {k: v for k, v in os.environ.items() if not k.startswith("MINIO_") and k not in AWS_CREDENTIAL_VARS}


class TestMinIOConfigValidation:
    """Test MinIOConfig field validation."""

    def test_valid_static_credentials(self):
        """Test valid static credentials configuration."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = MinIOConfig(
                minio_endpoint="minio.example.com:9000",
                minio_access_key="minioadmin",
                minio_secret_key="minioadmin123",
                minio_secure=False,
                minio_use_iam=False,
            )
            assert config.endpoint == "minio.example.com:9000"
            assert config.access_key == "minioadmin"
            assert config.secret_key == "minioadmin123"
            assert config.use_iam is False

    def test_valid_iam_credentials(self):
        """Test valid IAM credentials configuration."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = MinIOConfig(
                minio_endpoint="s3.amazonaws.com",
                minio_secure=True,
                minio_use_iam=True,
            )
            assert config.endpoint == "s3.amazonaws.com"
            assert config.use_iam is True
            # access_key and secret_key not required when using IAM
            assert config.access_key is None
            assert config.secret_key is None

    def test_endpoint_rejects_protocol(self):
        """Test that endpoint with protocol is rejected."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            with pytest.raises(ValueError, match="should not include protocol"):
                MinIOConfig(
                    minio_endpoint="http://minio.example.com:9000",
                    minio_access_key="admin",
                    minio_secret_key="password123",
                )

            with pytest.raises(ValueError, match="should not include protocol"):
                MinIOConfig(
                    minio_endpoint="https://minio.example.com:9000",
                    minio_access_key="admin",
                    minio_secret_key="password123",
                )

    def test_requires_credentials_when_not_iam(self):
        """Test that credentials are required when use_iam is False."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            with pytest.raises(ValueError, match="access_key and secret_key are required"):
                MinIOConfig(
                    minio_endpoint="minio.example.com:9000",
                    minio_use_iam=False,
                )

    def test_access_key_minimum_length(self):
        """Test access_key minimum length validation."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            with pytest.raises(ValueError, match="at least 3 characters"):
                MinIOConfig(
                    minio_endpoint="minio.example.com:9000",
                    minio_access_key="ab",
                    minio_secret_key="password123",
                    minio_use_iam=False,
                )

    def test_secret_key_minimum_length(self):
        """Test secret_key minimum length validation."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            with pytest.raises(ValueError, match="at least 8 characters"):
                MinIOConfig(
                    minio_endpoint="minio.example.com:9000",
                    minio_access_key="admin",
                    minio_secret_key="short",
                    minio_use_iam=False,
                )


class TestMinIOClientCreation:
    """Test MinIOConfig.create_client() method."""

    def test_create_client_with_static_credentials(self):
        """Test client creation with static access_key/secret_key."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = MinIOConfig(
                minio_endpoint="minio.example.com:9000",
                minio_access_key="minioadmin",
                minio_secret_key="minioadmin123",
                minio_secure=False,
                minio_use_iam=False,
            )

            client = config.create_client()

            # Verify client was created with correct settings
            assert client is not None
            # Check the host attribute directly
            assert client._base_url.host == "minio.example.com:9000"
            assert client._base_url.is_https is False

    def test_create_client_with_static_credentials_https(self):
        """Test client creation with HTTPS enabled."""
        with patch.dict(os.environ, get_clean_env(), clear=True):
            config = MinIOConfig(
                minio_endpoint="minio.example.com:443",
                minio_access_key="minioadmin",
                minio_secret_key="minioadmin123",
                minio_secure=True,
                minio_use_iam=False,
            )

            client = config.create_client()

            assert client._base_url.is_https is True

    def test_create_client_with_iam_no_irsa(self):
        """Test client creation with IAM but no IRSA (EC2 instance profile)."""
        # Build clean env without MINIO_ and AWS IRSA vars
        clean_env = get_clean_env()
        clean_env.pop("AWS_WEB_IDENTITY_TOKEN_FILE", None)
        clean_env.pop("AWS_ROLE_ARN", None)

        with patch.dict(os.environ, clean_env, clear=True):
            config = MinIOConfig(
                minio_endpoint="s3.amazonaws.com",
                minio_secure=True,
                minio_use_iam=True,
                minio_region="us-east-1",
            )

            client = config.create_client()

            # Should use IamAwsProvider
            assert client is not None
            assert client._provider is not None

    def test_create_client_with_irsa(self):
        """Test client creation with IRSA (EKS web identity)."""
        # Create a temporary token file
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".token") as f:
            f.write("mock-jwt-token-for-testing")
            token_file = f.name

        try:
            clean_env = get_clean_env()
            clean_env["AWS_WEB_IDENTITY_TOKEN_FILE"] = token_file
            clean_env["AWS_ROLE_ARN"] = "arn:aws:iam::123456789:role/test-role"

            with patch.dict(os.environ, clean_env, clear=True):
                config = MinIOConfig(
                    minio_endpoint="s3.amazonaws.com",
                    minio_secure=True,
                    minio_use_iam=True,
                    minio_region="us-east-1",
                )

                client = config.create_client()

                # Verify client was created successfully
                assert client is not None
                assert client._provider is not None
        finally:
            os.unlink(token_file)

    def test_irsa_jwt_provider_returns_dict(self):
        """Test that IRSA JWT provider returns dict with access_token key.

        This is a critical test - the WebIdentityProvider expects a dict,
        not a plain string. Returning a string causes:
        'str' object has no attribute 'get'
        """
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".token") as f:
            f.write("  test-jwt-token-with-whitespace  \n")
            token_file = f.name

        try:
            clean_env = get_clean_env()
            clean_env["AWS_WEB_IDENTITY_TOKEN_FILE"] = token_file
            clean_env["AWS_ROLE_ARN"] = "arn:aws:iam::123456789:role/test-role"

            with patch.dict(os.environ, clean_env, clear=True):
                config = MinIOConfig(
                    minio_endpoint="s3.amazonaws.com",
                    minio_secure=True,
                    minio_use_iam=True,
                    minio_region="us-east-1",
                )

                # Get the JWT provider function from the credentials provider
                client = config.create_client()
                provider = client._provider

                # Call the JWT provider function directly
                jwt_result = provider._jwt_provider_func()

                # CRITICAL: Must be a dict with access_token key
                assert isinstance(jwt_result, dict), (
                    f"JWT provider must return dict, got {type(jwt_result)}. "
                    "Returning a string causes 'str' object has no attribute 'get'"
                )
                assert "access_token" in jwt_result, "JWT provider dict must contain 'access_token' key"
                # Token should be stripped of whitespace
                assert jwt_result["access_token"] == "test-jwt-token-with-whitespace"
        finally:
            os.unlink(token_file)

    def test_irsa_includes_role_arn(self):
        """Test that IRSA configuration includes role_arn parameter."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".token") as f:
            f.write("mock-token")
            token_file = f.name

        try:
            role_arn = "arn:aws:iam::123456789:role/my-test-role"
            clean_env = get_clean_env()
            clean_env["AWS_WEB_IDENTITY_TOKEN_FILE"] = token_file
            clean_env["AWS_ROLE_ARN"] = role_arn

            with patch.dict(os.environ, clean_env, clear=True):
                config = MinIOConfig(
                    minio_endpoint="s3.amazonaws.com",
                    minio_secure=True,
                    minio_use_iam=True,
                    minio_region="us-west-2",
                )

                client = config.create_client()
                provider = client._provider

                # Verify role_arn is set on the provider
                assert provider._role_arn == role_arn
        finally:
            os.unlink(token_file)

    def test_irsa_uses_correct_sts_endpoint(self):
        """Test that IRSA uses region-specific STS endpoint."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".token") as f:
            f.write("mock-token")
            token_file = f.name

        try:
            clean_env = get_clean_env()
            clean_env["AWS_WEB_IDENTITY_TOKEN_FILE"] = token_file
            clean_env["AWS_ROLE_ARN"] = "arn:aws:iam::123456789:role/test"

            with patch.dict(os.environ, clean_env, clear=True):
                config = MinIOConfig(
                    minio_endpoint="s3.eu-west-1.amazonaws.com",
                    minio_secure=True,
                    minio_use_iam=True,
                    minio_region="eu-west-1",
                )

                client = config.create_client()
                provider = client._provider

                # Verify STS endpoint uses the correct region
                assert "eu-west-1" in provider._sts_endpoint
        finally:
            os.unlink(token_file)

    def test_irsa_uses_govcloud_sts_endpoint(self):
        """Test that IRSA uses correct STS endpoint for GovCloud regions."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".token") as f:
            f.write("mock-token")
            token_file = f.name

        try:
            clean_env = get_clean_env()
            clean_env["AWS_WEB_IDENTITY_TOKEN_FILE"] = token_file
            clean_env["AWS_ROLE_ARN"] = "arn:aws-us-gov:iam::123456789:role/test"

            with patch.dict(os.environ, clean_env, clear=True):
                config = MinIOConfig(
                    minio_endpoint="s3.us-gov-west-1.amazonaws.com",
                    minio_secure=True,
                    minio_use_iam=True,
                    minio_region="us-gov-west-1",
                )

                client = config.create_client()
                provider = client._provider

                # Verify STS endpoint uses GovCloud region
                assert "us-gov-west-1" in provider._sts_endpoint
                assert "amazonaws.com" in provider._sts_endpoint
        finally:
            os.unlink(token_file)


class TestMinIOConfigFromEnvironment:
    """Test MinIOConfig loading from environment variables."""

    def test_loads_from_env_with_aliases(self):
        """Test that config loads from MINIO_* environment variables."""
        clean_env = get_clean_env()
        clean_env.update(
            {
                "MINIO_ENDPOINT": "env-minio.example.com:9000",
                "MINIO_ACCESS_KEY": "env-access-key",
                "MINIO_SECRET_KEY": "env-secret-key",
                "MINIO_SECURE": "true",
                "MINIO_BUCKET": "my-bucket",
                "MINIO_REGION": "eu-central-1",
                "MINIO_USE_IAM": "false",
            }
        )

        with patch.dict(os.environ, clean_env, clear=True):
            config = MinIOConfig()

            assert config.endpoint == "env-minio.example.com:9000"
            assert config.access_key == "env-access-key"
            assert config.secret_key == "env-secret-key"
            assert config.secure is True
            assert config.bucket == "my-bucket"
            assert config.region == "eu-central-1"
            assert config.use_iam is False

    def test_loads_iam_mode_from_env(self):
        """Test that IAM mode is correctly loaded from environment."""
        clean_env = get_clean_env()
        clean_env.update(
            {
                "MINIO_ENDPOINT": "s3.amazonaws.com",
                "MINIO_SECURE": "true",
                "MINIO_USE_IAM": "true",
                "MINIO_REGION": "us-east-1",
            }
        )

        with patch.dict(os.environ, clean_env, clear=True):
            config = MinIOConfig()

            assert config.use_iam is True
            assert config.access_key is None
            assert config.secret_key is None

    def test_loads_from_aws_env_vars_as_fallback(self):
        """Test that AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are accepted as fallbacks."""
        clean_env = get_clean_env()
        clean_env.update(
            {
                "MINIO_ENDPOINT": "s3.example.com:9000",
                "AWS_ACCESS_KEY_ID": "aws-access-key",
                "AWS_SECRET_ACCESS_KEY": "aws-secret-key-value",
                "MINIO_USE_IAM": "false",
            }
        )

        with patch.dict(os.environ, clean_env, clear=True):
            config = MinIOConfig()

            assert config.access_key == "aws-access-key"
            assert config.secret_key == "aws-secret-key-value"

    def test_minio_vars_take_precedence_over_aws_vars(self):
        """Test that MINIO_* env vars take precedence over AWS_* env vars."""
        clean_env = get_clean_env()
        clean_env.update(
            {
                "MINIO_ENDPOINT": "minio.example.com:9000",
                "MINIO_ACCESS_KEY": "minio-key",
                "MINIO_SECRET_KEY": "minio-secret-value",
                "AWS_ACCESS_KEY_ID": "aws-key",
                "AWS_SECRET_ACCESS_KEY": "aws-secret-value",
                "MINIO_USE_IAM": "false",
            }
        )

        with patch.dict(os.environ, clean_env, clear=True):
            config = MinIOConfig()

            assert config.access_key == "minio-key"
            assert config.secret_key == "minio-secret-value"
