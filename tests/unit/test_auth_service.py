"""Unit tests for Authentication Service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.api_key import KeyValidationResult, RateLimitStatus
from src.services.auth import AuthenticationService, get_auth_service


def create_rate_limit_status(period="minute", limit=10, used=5, remaining=5, is_exceeded=False):
    """Helper to create RateLimitStatus with defaults."""
    return RateLimitStatus(
        period=period,
        limit=limit,
        used=used,
        remaining=remaining,
        resets_at=datetime.now(UTC) + timedelta(minutes=1),
        is_exceeded=is_exceeded,
    )


@pytest.fixture
def mock_redis_client():
    """Create a mock Redis client."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.incr = AsyncMock()
    client.expire = AsyncMock()
    client.ping = AsyncMock()
    return client


@pytest.fixture
def auth_service(mock_redis_client):
    """Create an authentication service instance."""
    with patch("src.core.pool.redis_pool") as mock_pool:
        mock_pool.key_prefix = ""
        return AuthenticationService(mock_redis_client)


@pytest.fixture
def mock_api_key_manager():
    """Create a mock API key manager."""
    manager = MagicMock()
    manager.validate_key = AsyncMock()
    manager.check_rate_limits = AsyncMock()
    manager.increment_usage = AsyncMock()
    manager.increment_env_key_usage = AsyncMock()
    manager.get_rate_limit_status = AsyncMock()
    manager.list_keys = AsyncMock()
    return manager


class TestAuthenticationServiceInit:
    """Tests for AuthenticationService initialization."""

    def test_init_with_redis(self, mock_redis_client):
        """Test initialization with Redis client."""
        service = AuthenticationService(mock_redis_client)

        assert service.redis_client is mock_redis_client
        assert service._cache_ttl == 300

    def test_init_without_redis(self):
        """Test initialization without Redis client."""
        with patch("src.core.pool.redis_pool") as _mp:
            _mp.key_prefix = ""
            service = AuthenticationService(None)

        assert service.redis_client is None


class TestApiKeyManagerProperty:
    """Tests for api_key_manager lazy loading."""

    def test_lazy_loads_api_key_manager(self, auth_service):
        """Test that api_key_manager is lazy loaded."""
        assert auth_service._api_key_manager is None

        with patch("src.services.api_key_manager.ApiKeyManagerService") as mock_class:
            mock_instance = MagicMock()
            mock_class.return_value = mock_instance

            manager = auth_service.api_key_manager

            # Since the import happens inside the property, this actually creates a real instance
            # Just verify that the manager was created
            assert auth_service._api_key_manager is not None

    def test_caches_api_key_manager(self, auth_service, mock_api_key_manager):
        """Test that api_key_manager is cached."""
        auth_service._api_key_manager = mock_api_key_manager

        manager1 = auth_service.api_key_manager
        manager2 = auth_service.api_key_manager

        assert manager1 is manager2


class TestValidateApiKey:
    """Tests for validate_api_key method."""

    @pytest.mark.asyncio
    async def test_validate_api_key_valid(self, auth_service, mock_api_key_manager):
        """Test validating a valid API key."""
        mock_api_key_manager.validate_key.return_value = KeyValidationResult(
            is_valid=True, key_hash="hash123", is_env_key=True
        )
        auth_service._api_key_manager = mock_api_key_manager

        result = await auth_service.validate_api_key("valid-key")

        assert result is True

    @pytest.mark.asyncio
    async def test_validate_api_key_invalid(self, auth_service, mock_api_key_manager):
        """Test validating an invalid API key."""
        mock_api_key_manager.validate_key.return_value = KeyValidationResult(
            is_valid=False, error_message="Invalid key"
        )
        auth_service._api_key_manager = mock_api_key_manager

        result = await auth_service.validate_api_key("invalid-key")

        assert result is False

    @pytest.mark.asyncio
    async def test_validate_api_key_rate_limited(self, auth_service, mock_api_key_manager):
        """Test validating a rate-limited API key."""
        validation_result = KeyValidationResult(is_valid=True, key_hash="hash123", rate_limit_exceeded=True)
        mock_api_key_manager.validate_key.return_value = validation_result
        auth_service._api_key_manager = mock_api_key_manager

        result = await auth_service.validate_api_key("rate-limited-key")

        assert result is False


class TestValidateApiKeyFull:
    """Tests for validate_api_key_full method."""

    @pytest.mark.asyncio
    async def test_validate_empty_key(self, auth_service):
        """Test validating empty API key."""
        result = await auth_service.validate_api_key_full("")

        assert result.is_valid is False
        assert "required" in result.error_message

    @pytest.mark.asyncio
    async def test_validate_valid_env_key(self, auth_service, mock_api_key_manager):
        """Test validating valid env key (no rate limit check)."""
        mock_api_key_manager.validate_key.return_value = KeyValidationResult(
            is_valid=True, key_hash="hash123", is_env_key=True
        )
        auth_service._api_key_manager = mock_api_key_manager

        result = await auth_service.validate_api_key_full("valid-env-key")

        assert result.is_valid is True
        assert result.is_env_key is True

    @pytest.mark.asyncio
    async def test_validate_valid_managed_key(self, auth_service, mock_api_key_manager):
        """Test validating valid managed key with rate limit check."""
        mock_api_key_manager.validate_key.return_value = KeyValidationResult(
            is_valid=True, key_hash="hash123", is_env_key=False
        )
        mock_api_key_manager.check_rate_limits.return_value = (True, None)
        auth_service._api_key_manager = mock_api_key_manager

        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.rate_limit_enabled = True

            result = await auth_service.validate_api_key_full("valid-managed-key")

        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_validate_rate_limited_key(self, auth_service, mock_api_key_manager):
        """Test validating rate-limited managed key."""
        mock_api_key_manager.validate_key.return_value = KeyValidationResult(
            is_valid=True, key_hash="hash123", is_env_key=False
        )
        rate_limit_status = create_rate_limit_status(period="minute", limit=10, used=15, remaining=0, is_exceeded=True)
        mock_api_key_manager.check_rate_limits.return_value = (False, rate_limit_status)
        auth_service._api_key_manager = mock_api_key_manager

        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.rate_limit_enabled = True

            result = await auth_service.validate_api_key_full("rate-limited-key")

        assert result.is_valid is True
        assert result.rate_limit_exceeded is True

    @pytest.mark.asyncio
    async def test_validate_invalid_key(self, auth_service, mock_api_key_manager):
        """Test validating invalid key."""
        mock_api_key_manager.validate_key.return_value = KeyValidationResult(
            is_valid=False, error_message="Key not found"
        )
        auth_service._api_key_manager = mock_api_key_manager

        result = await auth_service.validate_api_key_full("invalid-key")

        assert result.is_valid is False

    @pytest.mark.asyncio
    async def test_validate_exception_fallback(self, auth_service, mock_api_key_manager):
        """Test fallback validation when exception occurs."""
        mock_api_key_manager.validate_key.side_effect = Exception("Redis error")
        auth_service._api_key_manager = mock_api_key_manager

        with patch.object(auth_service, "_fallback_validation") as mock_fallback:
            mock_fallback.return_value = KeyValidationResult(is_valid=True, key_hash="hash", is_env_key=True)

            result = await auth_service.validate_api_key_full("test-key")

            mock_fallback.assert_called_once_with("test-key")


class TestFallbackValidation:
    """Tests for _fallback_validation method."""

    @pytest.mark.asyncio
    async def test_fallback_validates_env_key(self, auth_service):
        """Test fallback validation with matching env key."""
        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.api_key = "test-api-key"
            mock_settings.get_valid_api_keys.return_value = []

            result = await auth_service._fallback_validation("test-api-key")

        assert result.is_valid is True
        assert result.is_env_key is True

    @pytest.mark.asyncio
    async def test_fallback_validates_additional_key(self, auth_service):
        """Test fallback validation with additional valid key."""
        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.api_key = "primary-key"
            mock_settings.get_valid_api_keys.return_value = ["additional-key"]

            result = await auth_service._fallback_validation("additional-key")

        assert result.is_valid is True
        assert result.is_env_key is True

    @pytest.mark.asyncio
    async def test_fallback_rejects_invalid_key(self, auth_service):
        """Test fallback validation rejects invalid key."""
        with patch("src.services.auth.settings") as mock_settings:
            mock_settings.api_key = "valid-key"
            mock_settings.get_valid_api_keys.return_value = []

            result = await auth_service._fallback_validation("invalid-key")

        assert result.is_valid is False


class TestRecordUsage:
    """Tests for record_usage method."""

    @pytest.mark.asyncio
    async def test_record_usage_env_key(self, auth_service, mock_api_key_manager):
        """Test recording usage for env key."""
        auth_service._api_key_manager = mock_api_key_manager

        await auth_service.record_usage("hash123", is_env_key=True)

        mock_api_key_manager.increment_env_key_usage.assert_called_once_with("hash123")

    @pytest.mark.asyncio
    async def test_record_usage_managed_key(self, auth_service, mock_api_key_manager):
        """Test recording usage for managed key."""
        auth_service._api_key_manager = mock_api_key_manager

        await auth_service.record_usage("hash123", is_env_key=False)

        mock_api_key_manager.increment_usage.assert_called_once_with("hash123")

    @pytest.mark.asyncio
    async def test_record_usage_handles_exception(self, auth_service, mock_api_key_manager):
        """Test that exceptions are handled gracefully."""
        mock_api_key_manager.increment_usage.side_effect = Exception("Redis error")
        auth_service._api_key_manager = mock_api_key_manager

        # Should not raise
        await auth_service.record_usage("hash123", is_env_key=False)


class TestGetRateLimitStatus:
    """Tests for get_rate_limit_status method."""

    @pytest.mark.asyncio
    async def test_get_rate_limit_status_success(self, auth_service, mock_api_key_manager):
        """Test getting rate limit status."""
        mock_status = [create_rate_limit_status(period="minute", limit=10, used=5, remaining=5)]
        mock_api_key_manager.get_rate_limit_status.return_value = mock_status
        auth_service._api_key_manager = mock_api_key_manager

        result = await auth_service.get_rate_limit_status("hash123")

        assert result == mock_status

    @pytest.mark.asyncio
    async def test_get_rate_limit_status_exception(self, auth_service, mock_api_key_manager):
        """Test getting rate limit status with exception."""
        mock_api_key_manager.get_rate_limit_status.side_effect = Exception("Error")
        auth_service._api_key_manager = mock_api_key_manager

        result = await auth_service.get_rate_limit_status("hash123")

        assert result == []


class TestSecureCompare:
    """Tests for _secure_compare method."""

    def test_secure_compare_equal(self, auth_service):
        """Test secure comparison with equal strings."""
        assert auth_service._secure_compare("test123", "test123") is True

    def test_secure_compare_not_equal(self, auth_service):
        """Test secure comparison with different strings."""
        assert auth_service._secure_compare("test123", "test456") is False


class TestHashKey:
    """Tests for _hash_key method."""

    def test_hash_key(self, auth_service):
        """Test key hashing."""
        hash1 = auth_service._hash_key("test-key")
        hash2 = auth_service._hash_key("test-key")

        assert hash1 == hash2
        assert len(hash1) == 16  # Truncated to 16 chars


class TestLogFailedAttempt:
    """Tests for _log_failed_attempt method."""

    @pytest.mark.asyncio
    async def test_log_failed_attempt(self, auth_service):
        """Test logging failed attempt."""
        # Should not raise
        await auth_service._log_failed_attempt("test-key")


class TestLogAuthenticationAttempt:
    """Tests for log_authentication_attempt method."""

    @pytest.mark.asyncio
    async def test_log_failed_attempt_with_redis(self, auth_service, mock_redis_client):
        """Test logging failed attempt stores in Redis."""
        request_info = {"client_ip": "127.0.0.1", "endpoint": "/api/v1/exec"}

        await auth_service.log_authentication_attempt("test-key", False, request_info)

        mock_redis_client.incr.assert_called_once()
        mock_redis_client.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_successful_attempt(self, auth_service, mock_redis_client):
        """Test logging successful attempt doesn't store in Redis."""
        request_info = {"client_ip": "127.0.0.1", "endpoint": "/api/v1/exec"}

        await auth_service.log_authentication_attempt("test-key", True, request_info)

        mock_redis_client.incr.assert_not_called()

    @pytest.mark.asyncio
    async def test_log_failed_attempt_no_redis(self):
        """Test logging failed attempt without Redis."""
        with patch("src.core.pool.redis_pool") as _mp:
            _mp.key_prefix = ""
            service = AuthenticationService(None)
        request_info = {"client_ip": "127.0.0.1", "endpoint": "/api/v1/exec"}

        # Should not raise
        await service.log_authentication_attempt("test-key", False, request_info)


class TestCheckRateLimit:
    """Tests for check_rate_limit method."""

    @pytest.mark.asyncio
    async def test_check_rate_limit_no_redis(self):
        """Test rate limit check without Redis."""
        with patch("src.core.pool.redis_pool") as _mp:
            _mp.key_prefix = ""
            service = AuthenticationService(None)

        result = await service.check_rate_limit("127.0.0.1")

        assert result is True

    @pytest.mark.asyncio
    async def test_check_rate_limit_no_failures(self, auth_service, mock_redis_client):
        """Test rate limit check with no failures."""
        mock_redis_client.get.return_value = None

        result = await auth_service.check_rate_limit("127.0.0.1")

        assert result is True

    @pytest.mark.asyncio
    async def test_check_rate_limit_under_limit(self, auth_service, mock_redis_client):
        """Test rate limit check under limit."""
        mock_redis_client.get.return_value = b"5"

        result = await auth_service.check_rate_limit("127.0.0.1")

        assert result is True

    @pytest.mark.asyncio
    async def test_check_rate_limit_exceeded(self, auth_service, mock_redis_client):
        """Test rate limit check exceeded."""
        mock_redis_client.get.return_value = b"15"

        result = await auth_service.check_rate_limit("127.0.0.1")

        assert result is False

    @pytest.mark.asyncio
    async def test_check_rate_limit_exception(self, auth_service, mock_redis_client):
        """Test rate limit check with exception."""
        mock_redis_client.get.side_effect = Exception("Redis error")

        result = await auth_service.check_rate_limit("127.0.0.1")

        assert result is True  # Allow on error


class TestGetAuthenticationStats:
    """Tests for get_authentication_stats method."""

    @pytest.mark.asyncio
    async def test_get_stats_no_redis(self):
        """Test getting stats without Redis."""
        with patch("src.core.pool.redis_pool") as _mp:
            _mp.key_prefix = ""
            service = AuthenticationService(None)

        result = await service.get_authentication_stats()

        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_stats_success(self, auth_service, mock_redis_client, mock_api_key_manager):
        """Test getting stats successfully."""

        async def scan_iter(**kwargs):
            yield b"auth_failures:127.0.0.1"

        mock_redis_client.scan_iter = scan_iter
        mock_redis_client.get.return_value = b"3"

        # Mock api key manager
        mock_api_key_manager.list_keys.return_value = []
        auth_service._api_key_manager = mock_api_key_manager

        result = await auth_service.get_authentication_stats()

        assert "total_recent_failures" in result
        assert "api_keys" in result

    @pytest.mark.asyncio
    async def test_get_stats_exception(self, auth_service, mock_redis_client):
        """Test getting stats with exception."""
        mock_redis_client.scan_iter = MagicMock(side_effect=Exception("Redis error"))

        result = await auth_service.get_authentication_stats()

        assert "error" in result


class TestGetAuthService:
    """Tests for get_auth_service function."""

    @pytest.mark.asyncio
    async def test_get_auth_service_creates_instance(self):
        """Test that get_auth_service creates instance."""
        # Reset global state
        import src.services.auth as auth_module

        auth_module._auth_service = None

        with patch("src.core.pool.redis_pool") as mock_pool:
            mock_client = AsyncMock()
            mock_pool.get_client.return_value = mock_client
            mock_client.ping = AsyncMock()

            service = await get_auth_service()

            assert service is not None

        # Clean up
        auth_module._auth_service = None

    @pytest.mark.asyncio
    async def test_get_auth_service_handles_redis_error(self):
        """Test that get_auth_service handles Redis errors."""
        import src.services.auth as auth_module

        auth_module._auth_service = None

        with patch("src.core.pool.redis_pool") as mock_pool:
            mock_pool.get_client.side_effect = Exception("Connection failed")

            service = await get_auth_service()

            assert service is not None
            assert service.redis_client is None

        auth_module._auth_service = None
