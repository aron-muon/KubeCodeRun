"""Unit tests for the API key manager service."""

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.api_key import ApiKeyRecord, RateLimits, RateLimitStatus
from src.services.api_key_manager import ApiKeyManagerService


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis_mock = AsyncMock()
    redis_mock.hgetall = AsyncMock(return_value={})
    redis_mock.hset = AsyncMock(return_value=1)
    redis_mock.exists = AsyncMock(return_value=True)
    redis_mock.delete = AsyncMock(return_value=1)
    redis_mock.sadd = AsyncMock(return_value=1)
    redis_mock.srem = AsyncMock(return_value=1)
    redis_mock.smembers = AsyncMock(return_value=set())
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.setex = AsyncMock(return_value=True)
    redis_mock.incr = AsyncMock(return_value=1)
    redis_mock.expire = AsyncMock(return_value=True)
    redis_mock.hincrby = AsyncMock(return_value=1)

    # Mock pipeline
    pipeline_mock = AsyncMock()
    pipeline_mock.hset = MagicMock()
    pipeline_mock.sadd = MagicMock()
    pipeline_mock.delete = MagicMock()
    pipeline_mock.srem = MagicMock()
    pipeline_mock.incr = MagicMock()
    pipeline_mock.expire = MagicMock()
    pipeline_mock.hincrby = MagicMock()
    pipeline_mock.execute = AsyncMock(return_value=[True, True, True])
    redis_mock.pipeline = MagicMock(return_value=pipeline_mock)

    return redis_mock


@pytest.fixture
def api_key_manager(mock_redis):
    """Create an API key manager with mocked Redis."""
    with patch("src.services.api_key_manager.redis_pool") as mock_pool:
        mock_pool.key_prefix = ""
        return ApiKeyManagerService(redis_client=mock_redis)


class TestApiKeyManagerInit:
    """Tests for ApiKeyManagerService initialization."""

    def test_init_with_redis_client(self, mock_redis):
        """Test initialization with provided Redis client."""
        manager = ApiKeyManagerService(redis_client=mock_redis)
        assert manager._redis == mock_redis

    def test_init_without_redis_client(self):
        """Test initialization without Redis client."""
        manager = ApiKeyManagerService()
        assert manager._redis is None

    def test_redis_property_with_provided_client(self, api_key_manager, mock_redis):
        """Test redis property returns provided client."""
        assert api_key_manager.redis == mock_redis

    def test_redis_property_lazy_init(self):
        """Test redis property initializes from pool when needed."""
        manager = ApiKeyManagerService()

        with patch("src.services.api_key_manager.redis_pool") as mock_pool:
            mock_client = AsyncMock()
            mock_pool.get_client.return_value = mock_client

            result = manager.redis

            assert result == mock_client
            mock_pool.get_client.assert_called_once()


class TestHashFunctions:
    """Tests for key hashing functions."""

    def test_hash_key(self, api_key_manager):
        """Test API key hashing."""
        api_key = "sk-test123"
        expected = hashlib.sha256(api_key.encode()).hexdigest()

        result = api_key_manager._hash_key(api_key)

        assert result == expected

    def test_short_hash(self, api_key_manager):
        """Test short hash generation."""
        full_hash = "abcdef1234567890abcdef1234567890"

        result = api_key_manager._short_hash(full_hash)

        assert result == "abcdef1234567890"
        assert len(result) == 16


class TestCreateKey:
    """Tests for key creation."""

    @pytest.mark.asyncio
    async def test_create_key_basic(self, api_key_manager, mock_redis):
        """Test basic key creation."""
        full_key, record = await api_key_manager.create_key(name="Test Key")

        assert full_key.startswith("sk-")
        assert len(full_key) > 10
        assert record.name == "Test Key"
        assert record.enabled is True
        assert record.key_prefix == full_key[:11]

        mock_redis.pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_key_with_rate_limits(self, api_key_manager, mock_redis):
        """Test key creation with rate limits."""
        rate_limits = RateLimits(hourly=100, daily=1000)

        full_key, record = await api_key_manager.create_key(
            name="Rate Limited Key",
            rate_limits=rate_limits,
        )

        assert record.rate_limits.hourly == 100
        assert record.rate_limits.daily == 1000

    @pytest.mark.asyncio
    async def test_create_key_with_metadata(self, api_key_manager, mock_redis):
        """Test key creation with metadata."""
        metadata = {"team": "engineering", "project": "test"}

        full_key, record = await api_key_manager.create_key(
            name="Metadata Key",
            metadata=metadata,
        )

        assert record.metadata == metadata


class TestGetKey:
    """Tests for retrieving keys."""

    @pytest.mark.asyncio
    async def test_get_key_exists(self, api_key_manager, mock_redis):
        """Test getting an existing key."""
        key_hash = "abc123"
        mock_redis.hgetall.return_value = {
            b"key_hash": b"abc123",
            b"key_prefix": b"sk-test",
            b"name": b"Test Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"true",
            b"rate_limits": b"{}",
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"api",
        }

        record = await api_key_manager.get_key(key_hash)

        assert record is not None
        assert record.key_prefix == "sk-test"
        assert record.name == "Test Key"
        assert record.enabled is True

    @pytest.mark.asyncio
    async def test_get_key_not_exists(self, api_key_manager, mock_redis):
        """Test getting a non-existent key."""
        mock_redis.hgetall.return_value = {}

        record = await api_key_manager.get_key("nonexistent")

        assert record is None


class TestListKeys:
    """Tests for listing keys."""

    @pytest.mark.asyncio
    async def test_list_keys_empty(self, api_key_manager, mock_redis):
        """Test listing keys when empty."""
        mock_redis.smembers.return_value = set()

        records = await api_key_manager.list_keys()

        assert records == []

    @pytest.mark.asyncio
    async def test_list_keys_with_results(self, api_key_manager, mock_redis):
        """Test listing keys with results."""
        mock_redis.smembers.return_value = {b"hash1", b"hash2"}

        # Mock get_key calls
        async def mock_hgetall(key):
            return {
                b"key_hash": key.split(":")[-1].encode(),
                b"key_prefix": b"sk-test",
                b"name": b"Test Key",
                b"created_at": datetime.now(UTC).isoformat().encode(),
                b"enabled": b"true",
                b"rate_limits": b"{}",
                b"metadata": b"{}",
                b"usage_count": b"0",
                b"source": b"api",
            }

        mock_redis.hgetall.side_effect = mock_hgetall

        records = await api_key_manager.list_keys(include_env_keys=False)

        assert len(records) == 2


class TestUpdateKey:
    """Tests for updating keys."""

    @pytest.mark.asyncio
    async def test_update_key_enable_disable(self, api_key_manager, mock_redis):
        """Test enabling/disabling a key."""
        # Mock existing key
        mock_redis.hgetall.return_value = {
            b"key_hash": b"abc123",
            b"key_prefix": b"sk-test",
            b"name": b"Test Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"true",
            b"rate_limits": b"{}",
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"api",
        }

        result = await api_key_manager.update_key("abc123", enabled=False)

        assert result is True
        mock_redis.hset.assert_called()
        mock_redis.delete.assert_called()  # Cache invalidation

    @pytest.mark.asyncio
    async def test_update_key_not_exists(self, api_key_manager, mock_redis):
        """Test updating a non-existent key."""
        mock_redis.hgetall.return_value = {}

        result = await api_key_manager.update_key("nonexistent", enabled=False)

        assert result is False

    @pytest.mark.asyncio
    async def test_update_key_rate_limits(self, api_key_manager, mock_redis):
        """Test updating rate limits."""
        mock_redis.hgetall.return_value = {
            b"key_hash": b"abc123",
            b"key_prefix": b"sk-test",
            b"name": b"Test Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"true",
            b"rate_limits": b"{}",
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"api",
        }

        new_limits = RateLimits(hourly=500)
        result = await api_key_manager.update_key("abc123", rate_limits=new_limits)

        assert result is True

    @pytest.mark.asyncio
    async def test_update_key_name(self, api_key_manager, mock_redis):
        """Test updating key name."""
        mock_redis.hgetall.return_value = {
            b"key_hash": b"abc123",
            b"key_prefix": b"sk-test",
            b"name": b"Test Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"true",
            b"rate_limits": b"{}",
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"api",
        }

        result = await api_key_manager.update_key("abc123", name="New Name")

        assert result is True


class TestRevokeKey:
    """Tests for revoking keys."""

    @pytest.mark.asyncio
    async def test_revoke_key_exists(self, api_key_manager, mock_redis):
        """Test revoking an existing key."""
        mock_redis.exists.return_value = True

        result = await api_key_manager.revoke_key("abc123")

        assert result is True
        mock_redis.pipeline.assert_called()

    @pytest.mark.asyncio
    async def test_revoke_key_not_exists(self, api_key_manager, mock_redis):
        """Test revoking a non-existent key."""
        mock_redis.exists.return_value = False

        result = await api_key_manager.revoke_key("nonexistent")

        assert result is False


class TestFindKeyByPrefix:
    """Tests for finding keys by prefix."""

    @pytest.mark.asyncio
    async def test_find_key_by_prefix_found(self, api_key_manager, mock_redis):
        """Test finding a key by prefix."""
        mock_redis.smembers.return_value = {b"hash1", b"hash2"}

        def mock_hgetall(key):
            if "hash1" in key:
                return {
                    b"key_hash": b"hash1",
                    b"key_prefix": b"sk-target",
                    b"name": b"Target Key",
                    b"created_at": datetime.now(UTC).isoformat().encode(),
                    b"enabled": b"true",
                    b"rate_limits": b"{}",
                    b"metadata": b"{}",
                    b"usage_count": b"0",
                    b"source": b"api",
                }
            return {
                b"key_hash": b"hash2",
                b"key_prefix": b"sk-other",
                b"name": b"Other Key",
                b"created_at": datetime.now(UTC).isoformat().encode(),
                b"enabled": b"true",
                b"rate_limits": b"{}",
                b"metadata": b"{}",
                b"usage_count": b"0",
                b"source": b"api",
            }

        mock_redis.hgetall.side_effect = mock_hgetall

        result = await api_key_manager.find_key_by_prefix("sk-target")

        assert result == "hash1"

    @pytest.mark.asyncio
    async def test_find_key_by_prefix_not_found(self, api_key_manager, mock_redis):
        """Test finding a non-existent prefix."""
        mock_redis.smembers.return_value = set()

        result = await api_key_manager.find_key_by_prefix("sk-nonexistent")

        assert result is None


class TestValidateKey:
    """Tests for key validation."""

    @pytest.mark.asyncio
    async def test_validate_key_empty(self, api_key_manager):
        """Test validating empty key."""
        result = await api_key_manager.validate_key("")

        assert result.is_valid is False
        assert "required" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_validate_key_none(self, api_key_manager):
        """Test validating None key."""
        result = await api_key_manager.validate_key(None)

        assert result.is_valid is False

    @pytest.mark.asyncio
    async def test_validate_key_cached_valid(self, api_key_manager, mock_redis):
        """Test validation with cached valid result."""
        mock_redis.get.return_value = b"1"
        mock_redis.hgetall.return_value = {
            b"key_hash": b"abc123",
            b"key_prefix": b"sk-test",
            b"name": b"Test Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"true",
            b"rate_limits": b"{}",
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"api",
        }

        result = await api_key_manager.validate_key("sk-test123")

        assert result.is_valid is True
        assert result.is_env_key is False

    @pytest.mark.asyncio
    async def test_validate_key_cached_env(self, api_key_manager, mock_redis):
        """Test validation with cached env key result."""
        mock_redis.get.return_value = b"env"

        result = await api_key_manager.validate_key("sk-test123")

        assert result.is_valid is True
        assert result.is_env_key is True

    @pytest.mark.asyncio
    async def test_validate_key_redis_key_enabled(self, api_key_manager, mock_redis):
        """Test validating an enabled Redis-managed key."""
        mock_redis.get.return_value = None  # No cache
        mock_redis.hgetall.return_value = {
            b"key_hash": b"abc123",
            b"key_prefix": b"sk-test",
            b"name": b"Test Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"true",
            b"rate_limits": b"{}",
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"api",
        }

        result = await api_key_manager.validate_key("sk-test123")

        assert result.is_valid is True
        mock_redis.setex.assert_called()  # Cache should be set

    @pytest.mark.asyncio
    async def test_validate_key_redis_key_disabled(self, api_key_manager, mock_redis):
        """Test validating a disabled Redis-managed key."""
        mock_redis.get.return_value = None
        mock_redis.hgetall.return_value = {
            b"key_hash": b"abc123",
            b"key_prefix": b"sk-test",
            b"name": b"Test Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"false",
            b"rate_limits": b"{}",
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"api",
        }

        result = await api_key_manager.validate_key("sk-test123")

        assert result.is_valid is False
        assert "disabled" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_validate_key_env_var_fallback(self, api_key_manager, mock_redis):
        """Test validation falls back to env var."""
        mock_redis.get.return_value = None
        mock_redis.hgetall.return_value = {}  # Not in Redis

        with patch("src.services.api_key_manager.settings") as mock_settings:
            mock_settings.api_key = "env-api-key"
            mock_settings.get_valid_api_keys.return_value = []

            result = await api_key_manager.validate_key("env-api-key")

            assert result.is_valid is True
            assert result.is_env_key is True

    @pytest.mark.asyncio
    async def test_validate_key_additional_env_keys(self, api_key_manager, mock_redis):
        """Test validation with additional env keys."""
        mock_redis.get.return_value = None
        mock_redis.hgetall.return_value = {}

        with patch("src.services.api_key_manager.settings") as mock_settings:
            mock_settings.api_key = "primary-key"
            mock_settings.get_valid_api_keys.return_value = ["additional-key-1", "additional-key-2"]

            result = await api_key_manager.validate_key("additional-key-2")

            assert result.is_valid is True
            assert result.is_env_key is True

    @pytest.mark.asyncio
    async def test_validate_key_invalid(self, api_key_manager, mock_redis):
        """Test validating an invalid key."""
        mock_redis.get.return_value = None
        mock_redis.hgetall.return_value = {}

        with patch("src.services.api_key_manager.settings") as mock_settings:
            mock_settings.api_key = "real-api-key"
            mock_settings.get_valid_api_keys.return_value = []

            result = await api_key_manager.validate_key("invalid-key")

            assert result.is_valid is False
            assert "invalid" in result.error_message.lower()


class TestRateLimits:
    """Tests for rate limiting."""

    @pytest.mark.asyncio
    async def test_check_rate_limits_no_key(self, api_key_manager, mock_redis):
        """Test rate limits for non-existent key."""
        mock_redis.hgetall.return_value = {}

        is_allowed, status = await api_key_manager.check_rate_limits("nonexistent")

        assert is_allowed is True
        assert status is None

    @pytest.mark.asyncio
    async def test_check_rate_limits_unlimited(self, api_key_manager, mock_redis):
        """Test rate limits for unlimited key."""
        mock_redis.hgetall.return_value = {
            b"key_hash": b"abc123",
            b"key_prefix": b"sk-test",
            b"name": b"Test Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"true",
            b"rate_limits": b"{}",  # Empty = unlimited
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"api",
        }

        is_allowed, status = await api_key_manager.check_rate_limits("abc123")

        assert is_allowed is True
        assert status is None

    @pytest.mark.asyncio
    async def test_check_rate_limits_exceeded(self, api_key_manager, mock_redis):
        """Test rate limits when exceeded."""
        mock_redis.hgetall.return_value = {
            b"key_hash": b"abc123",
            b"key_prefix": b"sk-test",
            b"name": b"Test Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"true",
            b"rate_limits_per_second": b"",
            b"rate_limits_per_minute": b"",
            b"rate_limits_hourly": b"10",
            b"rate_limits_daily": b"",
            b"rate_limits_monthly": b"",
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"api",
        }
        mock_redis.get.return_value = b"15"  # Over limit

        is_allowed, status = await api_key_manager.check_rate_limits("abc123")

        assert is_allowed is False
        assert status is not None
        assert status.is_exceeded is True
        assert status.period == "hourly"

    @pytest.mark.asyncio
    async def test_check_rate_limits_under_limit(self, api_key_manager, mock_redis):
        """Test rate limits when under limit."""
        mock_redis.hgetall.return_value = {
            b"key_hash": b"abc123",
            b"key_prefix": b"sk-test",
            b"name": b"Test Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"true",
            b"rate_limits_per_second": b"",
            b"rate_limits_per_minute": b"",
            b"rate_limits_hourly": b"100",
            b"rate_limits_daily": b"",
            b"rate_limits_monthly": b"",
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"api",
        }
        mock_redis.get.return_value = b"10"  # Under limit

        is_allowed, status = await api_key_manager.check_rate_limits("abc123")

        assert is_allowed is True
        assert status is None

    @pytest.mark.asyncio
    async def test_get_rate_limit_status_no_key(self, api_key_manager, mock_redis):
        """Test getting rate limit status for non-existent key."""
        mock_redis.hgetall.return_value = {}

        statuses = await api_key_manager.get_rate_limit_status("nonexistent")

        assert statuses == []

    @pytest.mark.asyncio
    async def test_get_rate_limit_status_with_limits(self, api_key_manager, mock_redis):
        """Test getting rate limit status."""
        mock_redis.hgetall.return_value = {
            b"key_hash": b"abc123",
            b"key_prefix": b"sk-test",
            b"name": b"Test Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"true",
            b"rate_limits_per_second": b"",
            b"rate_limits_per_minute": b"",
            b"rate_limits_hourly": b"100",
            b"rate_limits_daily": b"1000",
            b"rate_limits_monthly": b"",
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"api",
        }
        mock_redis.get.return_value = b"50"

        statuses = await api_key_manager.get_rate_limit_status("abc123")

        assert len(statuses) == 5  # All periods
        hourly_status = next(s for s in statuses if s.period == "hourly")
        assert hourly_status.limit == 100
        assert hourly_status.used == 50


class TestUsageTracking:
    """Tests for usage tracking."""

    @pytest.mark.asyncio
    async def test_increment_usage(self, api_key_manager, mock_redis):
        """Test incrementing usage counters."""
        await api_key_manager.increment_usage("abc123")

        mock_redis.pipeline.assert_called()

    @pytest.mark.asyncio
    async def test_increment_usage_error(self, api_key_manager, mock_redis):
        """Test increment usage handles errors."""
        pipeline_mock = mock_redis.pipeline.return_value
        pipeline_mock.execute.side_effect = Exception("Redis error")

        # Should not raise
        await api_key_manager.increment_usage("abc123")

    @pytest.mark.asyncio
    async def test_get_usage(self, api_key_manager, mock_redis):
        """Test getting usage statistics."""
        mock_redis.get.return_value = b"42"

        usage = await api_key_manager.get_usage("abc123")

        assert usage["hourly"] == 42
        assert "daily" in usage
        assert "monthly" in usage

    @pytest.mark.asyncio
    async def test_get_usage_handles_string_response(self, api_key_manager, mock_redis):
        """Test getting usage with string response."""
        mock_redis.get.return_value = "42"

        usage = await api_key_manager.get_usage("abc123")

        assert usage["hourly"] == 42


class TestPeriodKeyGeneration:
    """Tests for period key generation."""

    def test_get_second_key(self, api_key_manager):
        """Test second key generation."""
        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = api_key_manager._get_second_key(dt)
        assert result == "second:2024-01-15-10:30:45"

    def test_get_minute_key(self, api_key_manager):
        """Test minute key generation."""
        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = api_key_manager._get_minute_key(dt)
        assert result == "minute:2024-01-15-10:30"

    def test_get_hour_key(self, api_key_manager):
        """Test hour key generation."""
        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = api_key_manager._get_hour_key(dt)
        assert result == "hour:2024-01-15-10"

    def test_get_day_key(self, api_key_manager):
        """Test day key generation."""
        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = api_key_manager._get_day_key(dt)
        assert result == "day:2024-01-15"

    def test_get_month_key(self, api_key_manager):
        """Test month key generation."""
        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = api_key_manager._get_month_key(dt)
        assert result == "month:2024-01"


class TestResetTime:
    """Tests for reset time calculation."""

    def test_get_reset_time_per_second(self, api_key_manager):
        """Test per-second reset time."""
        now = datetime(2024, 1, 15, 10, 30, 45, 500000, tzinfo=UTC)
        result = api_key_manager._get_reset_time("per_second", now)
        expected = datetime(2024, 1, 15, 10, 30, 46, 0, tzinfo=UTC)
        assert result == expected

    def test_get_reset_time_per_minute(self, api_key_manager):
        """Test per-minute reset time."""
        now = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = api_key_manager._get_reset_time("per_minute", now)
        expected = datetime(2024, 1, 15, 10, 31, 0, tzinfo=UTC)
        assert result == expected

    def test_get_reset_time_hourly(self, api_key_manager):
        """Test hourly reset time."""
        now = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = api_key_manager._get_reset_time("hourly", now)
        expected = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_get_reset_time_daily(self, api_key_manager):
        """Test daily reset time."""
        now = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = api_key_manager._get_reset_time("daily", now)
        expected = datetime(2024, 1, 16, 0, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_get_reset_time_monthly(self, api_key_manager):
        """Test monthly reset time."""
        now = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = api_key_manager._get_reset_time("monthly", now)
        expected = datetime(2024, 2, 1, 0, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_get_reset_time_monthly_december(self, api_key_manager):
        """Test monthly reset time in December."""
        now = datetime(2024, 12, 15, 10, 30, 45, tzinfo=UTC)
        result = api_key_manager._get_reset_time("monthly", now)
        expected = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_get_reset_time_unknown(self, api_key_manager):
        """Test unknown period returns now."""
        now = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = api_key_manager._get_reset_time("unknown", now)
        assert result == now


class TestEnvKeyRecords:
    """Tests for environment key record management."""

    @pytest.mark.asyncio
    async def test_ensure_env_key_records(self, api_key_manager, mock_redis):
        """Test ensuring env key records exist."""
        mock_redis.hgetall.return_value = {}  # No existing record

        with patch("src.services.api_key_manager.settings") as mock_settings:
            mock_settings.api_key = "env-api-key-12345"
            mock_settings.get_valid_api_keys.return_value = []

            records = await api_key_manager.ensure_env_key_records()

            assert len(records) == 1
            assert records[0].source == "environment"

    @pytest.mark.asyncio
    async def test_ensure_env_key_records_skip_test_key(self, api_key_manager, mock_redis):
        """Test that test-api-key is skipped."""
        with patch("src.services.api_key_manager.settings") as mock_settings:
            mock_settings.api_key = "test-api-key"
            mock_settings.get_valid_api_keys.return_value = []

            records = await api_key_manager.ensure_env_key_records()

            assert records == []

    @pytest.mark.asyncio
    async def test_get_env_key_records(self, api_key_manager, mock_redis):
        """Test getting env key records."""
        mock_redis.smembers.return_value = {b"hash1"}
        mock_redis.hgetall.return_value = {
            b"key_hash": b"hash1",
            b"key_prefix": b"env-key",
            b"name": b"Environment Key",
            b"created_at": datetime.now(UTC).isoformat().encode(),
            b"enabled": b"true",
            b"rate_limits": b"{}",
            b"metadata": b"{}",
            b"usage_count": b"0",
            b"source": b"environment",
        }

        records = await api_key_manager.get_env_key_records()

        assert len(records) == 1
        assert records[0].source == "environment"

    @pytest.mark.asyncio
    async def test_get_env_key_records_handles_error(self, api_key_manager, mock_redis):
        """Test get_env_key_records handles errors."""
        mock_redis.smembers.side_effect = Exception("Redis error")

        records = await api_key_manager.get_env_key_records()

        assert records == []

    @pytest.mark.asyncio
    async def test_increment_env_key_usage(self, api_key_manager, mock_redis):
        """Test incrementing env key usage."""
        mock_redis.exists.return_value = True

        await api_key_manager.increment_env_key_usage("abc123")

        mock_redis.pipeline.assert_called()

    @pytest.mark.asyncio
    async def test_increment_env_key_usage_creates_record(self, api_key_manager, mock_redis):
        """Test increment creates record if missing."""
        mock_redis.exists.return_value = False

        with patch.object(api_key_manager, "ensure_env_key_records", new_callable=AsyncMock):
            await api_key_manager.increment_env_key_usage("abc123")


class TestCacheValidation:
    """Tests for validation caching."""

    @pytest.mark.asyncio
    async def test_cache_validation(self, api_key_manager, mock_redis):
        """Test caching validation result."""
        await api_key_manager._cache_validation("short_hash", "1")

        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_validation_error(self, api_key_manager, mock_redis):
        """Test cache validation handles errors."""
        mock_redis.setex.side_effect = Exception("Redis error")

        # Should not raise
        await api_key_manager._cache_validation("short_hash", "1")


class TestGlobalInstance:
    """Tests for global instance management."""

    @pytest.mark.asyncio
    async def test_get_api_key_manager(self):
        """Test getting global API key manager."""
        from src.services.api_key_manager import _api_key_manager, get_api_key_manager

        with patch("src.services.api_key_manager.redis_pool") as mock_pool:
            mock_client = AsyncMock()
            mock_pool.get_client.return_value = mock_client

            # Reset global instance for test
            import src.services.api_key_manager as module

            module._api_key_manager = None

            with patch.object(ApiKeyManagerService, "ensure_env_key_records", new_callable=AsyncMock):
                manager = await get_api_key_manager()

                assert manager is not None
                assert isinstance(manager, ApiKeyManagerService)

            # Reset for other tests
            module._api_key_manager = None
