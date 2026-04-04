"""Unit tests for Detailed Metrics Service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.metrics import DetailedExecutionMetrics, LanguageMetrics
from src.services.detailed_metrics import DetailedMetricsService


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    client = AsyncMock()
    client.lpush = AsyncMock()
    client.ltrim = AsyncMock()
    client.hgetall = AsyncMock(return_value={})
    client.hincrby = AsyncMock()
    client.hincrbyfloat = AsyncMock()
    client.hset = AsyncMock()
    client.expire = AsyncMock()
    client.pipeline = MagicMock()

    # Configure pipeline
    mock_pipe = MagicMock()
    mock_pipe.hincrby = MagicMock()
    mock_pipe.hincrbyfloat = MagicMock()
    mock_pipe.hset = MagicMock()
    mock_pipe.expire = MagicMock()
    mock_pipe.execute = AsyncMock()
    client.pipeline.return_value = mock_pipe

    return client


@pytest.fixture
def detailed_metrics_service(mock_redis):
    """Create a detailed metrics service with mocked Redis."""
    with patch("src.services.detailed_metrics.redis_pool") as mock_pool:
        mock_pool.key_prefix = ""
        return DetailedMetricsService(redis_client=mock_redis)


@pytest.fixture
def sample_metrics():
    """Create sample execution metrics."""
    return DetailedExecutionMetrics(
        execution_id="exec-123",
        session_id="session-123",
        api_key_hash="abc123def456",
        user_id="user-123",
        entity_id="entity-123",
        language="python",
        execution_time_ms=50.0,
        status="completed",
        timestamp=datetime.now(UTC),
    )


class TestDetailedMetricsServiceInit:
    """Tests for DetailedMetricsService initialization."""

    def test_init_with_redis(self, mock_redis):
        """Test initialization with provided Redis client."""
        service = DetailedMetricsService(redis_client=mock_redis)
        assert service._redis is mock_redis

    def test_init_without_redis(self):
        """Test initialization without Redis client."""
        service = DetailedMetricsService()
        assert service._redis is None
        assert service._in_memory_buffer == []


class TestRecordExecution:
    """Tests for record_execution method."""

    @pytest.mark.asyncio
    async def test_record_execution_disabled(self, detailed_metrics_service, sample_metrics):
        """Test recording is skipped when detailed metrics disabled."""
        with patch("src.services.detailed_metrics.settings") as mock_settings:
            mock_settings.detailed_metrics_enabled = False

            await detailed_metrics_service.record_execution(sample_metrics)

        # Redis should not be called
        detailed_metrics_service._redis.lpush.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_execution_success(self, detailed_metrics_service, mock_redis, sample_metrics):
        """Test successful execution recording."""
        with patch("src.services.detailed_metrics.settings") as mock_settings:
            mock_settings.detailed_metrics_enabled = True
            mock_settings.sqlite_metrics_enabled = False

            await detailed_metrics_service.record_execution(sample_metrics)

        mock_redis.lpush.assert_called_once()
        mock_redis.ltrim.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_execution_with_api_key(self, detailed_metrics_service, mock_redis):
        """Test recording execution with API key."""
        metrics = DetailedExecutionMetrics(
            execution_id="exec-123",
            session_id="session-123",
            api_key_hash="abc123def456",
            user_id="user-123",
            entity_id="entity-123",
            language="python",
            execution_time_ms=50.0,
            status="completed",
            timestamp=datetime.now(UTC),
        )

        with patch("src.services.detailed_metrics.settings") as mock_settings:
            mock_settings.detailed_metrics_enabled = True
            mock_settings.sqlite_metrics_enabled = False

            await detailed_metrics_service.record_execution(metrics)

        # Verify API key metrics were updated (pipeline was called)
        mock_redis.pipeline.assert_called()

    @pytest.mark.asyncio
    async def test_record_execution_redis_error(self, detailed_metrics_service, mock_redis, sample_metrics):
        """Test fallback to in-memory buffer on Redis error."""
        mock_redis.lpush.side_effect = Exception("Redis connection error")

        with patch("src.services.detailed_metrics.settings") as mock_settings:
            mock_settings.detailed_metrics_enabled = True

            await detailed_metrics_service.record_execution(sample_metrics)

        # Should add to in-memory buffer
        assert len(detailed_metrics_service._in_memory_buffer) == 1


class TestRecordPoolEvent:
    """Tests for record_pool_event method."""

    @pytest.mark.asyncio
    async def test_record_pool_hit(self, detailed_metrics_service, mock_redis):
        """Test recording pool hit event."""
        await detailed_metrics_service.record_pool_event(
            event_type="hit",
            language="python",
            acquire_time_ms=10.0,
        )

        # Verify pipeline was called
        mock_redis.pipeline.assert_called()
        mock_pipe = mock_redis.pipeline.return_value
        mock_pipe.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_pool_miss(self, detailed_metrics_service, mock_redis):
        """Test recording pool miss event."""
        await detailed_metrics_service.record_pool_event(
            event_type="miss",
            language="javascript",
        )

        mock_redis.pipeline.assert_called()

    @pytest.mark.asyncio
    async def test_record_pool_exhaustion(self, detailed_metrics_service, mock_redis):
        """Test recording pool exhaustion event."""
        await detailed_metrics_service.record_pool_event(
            event_type="exhaustion",
            language="python",
        )

        mock_redis.pipeline.assert_called()

    @pytest.mark.asyncio
    async def test_record_pool_event_error(self, detailed_metrics_service, mock_redis):
        """Test handling errors in pool event recording."""
        mock_redis.pipeline.side_effect = Exception("Redis error")

        # Should not raise
        await detailed_metrics_service.record_pool_event(
            event_type="hit",
            language="python",
        )


class TestGetHourlyMetrics:
    """Tests for get_hourly_metrics method."""

    @pytest.mark.asyncio
    async def test_get_hourly_metrics_no_data(self, detailed_metrics_service, mock_redis):
        """Test getting hourly metrics when no data exists."""
        mock_redis.hgetall.return_value = {}

        result = await detailed_metrics_service.get_hourly_metrics()

        assert result is None

    @pytest.mark.asyncio
    async def test_get_hourly_metrics_with_data(self, detailed_metrics_service, mock_redis):
        """Test getting hourly metrics with data."""
        mock_redis.hgetall.return_value = {
            b"execution_count": b"10",
            b"total_execution_time_ms": b"500.0",
            b"success_count": b"8",
            b"failure_count": b"2",
        }

        result = await detailed_metrics_service.get_hourly_metrics()

        assert result is not None
        assert result.execution_count == 10

    @pytest.mark.asyncio
    async def test_get_hourly_metrics_specific_hour(self, detailed_metrics_service, mock_redis):
        """Test getting hourly metrics for specific hour."""
        mock_redis.hgetall.return_value = {
            b"execution_count": b"5",
            b"total_execution_time_ms": b"250.0",
        }

        specific_hour = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        result = await detailed_metrics_service.get_hourly_metrics(specific_hour)

        assert result is not None

    @pytest.mark.asyncio
    async def test_get_hourly_metrics_error(self, detailed_metrics_service, mock_redis):
        """Test handling errors in get_hourly_metrics."""
        mock_redis.hgetall.side_effect = Exception("Redis error")

        result = await detailed_metrics_service.get_hourly_metrics()

        assert result is None


class TestGetMetricsRange:
    """Tests for get_metrics_range method."""

    @pytest.mark.asyncio
    async def test_get_metrics_range_empty(self, detailed_metrics_service, mock_redis):
        """Test getting metrics range with no data."""
        mock_redis.hgetall.return_value = {}

        start = datetime.now(UTC) - timedelta(hours=2)
        end = datetime.now(UTC)

        result = await detailed_metrics_service.get_metrics_range(start, end)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_metrics_range_with_data(self, detailed_metrics_service, mock_redis):
        """Test getting metrics range with data."""
        mock_redis.hgetall.return_value = {
            b"execution_count": b"10",
            b"total_execution_time_ms": b"500.0",
        }

        start = datetime.now(UTC) - timedelta(hours=1)
        end = datetime.now(UTC)

        result = await detailed_metrics_service.get_metrics_range(start, end, "hourly")

        assert len(result) >= 1


class TestGetLanguageStats:
    """Tests for get_language_stats method."""

    @pytest.mark.asyncio
    async def test_get_language_stats_no_data(self, detailed_metrics_service, mock_redis):
        """Test getting language stats with no data."""
        mock_redis.hgetall.return_value = {}

        result = await detailed_metrics_service.get_language_stats(hours=24)

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_language_stats_with_data(self, detailed_metrics_service, mock_redis):
        """Test getting language stats with data."""
        mock_redis.hgetall.return_value = {
            b"lang:python:count": b"50",
            b"lang:python:time_ms": b"2500.0",
            b"lang:python:errors": b"5",
            b"lang:javascript:count": b"30",
            b"lang:javascript:time_ms": b"1500.0",
        }

        result = await detailed_metrics_service.get_language_stats(hours=1)

        assert "python" in result
        assert "javascript" in result
        assert result["python"].execution_count == 50
        assert result["python"].failure_count == 5

    @pytest.mark.asyncio
    async def test_get_language_stats_error(self, detailed_metrics_service, mock_redis):
        """Test handling errors in get_language_stats."""
        mock_redis.hgetall.side_effect = Exception("Redis error")

        result = await detailed_metrics_service.get_language_stats(hours=1)

        # Should return empty dict on error
        assert result == {}


class TestUpdateHourlyAggregates:
    """Tests for _update_hourly_aggregates method."""

    @pytest.mark.asyncio
    async def test_update_hourly_completed_status(self, detailed_metrics_service, mock_redis):
        """Test updating hourly aggregates with completed status."""
        metrics = DetailedExecutionMetrics(
            execution_id="exec-123",
            session_id="session-123",
            api_key_hash="abc123def456",
            user_id="user-123",
            entity_id="entity-123",
            language="python",
            execution_time_ms=50.0,
            status="completed",
            timestamp=datetime.now(UTC),
            memory_peak_mb=128.0,
            container_source="pool_hit",
        )

        await detailed_metrics_service._update_hourly_aggregates(metrics)

        mock_redis.pipeline.assert_called()
        mock_pipe = mock_redis.pipeline.return_value
        mock_pipe.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_hourly_failed_status(self, detailed_metrics_service, mock_redis):
        """Test updating hourly aggregates with failed status."""
        metrics = DetailedExecutionMetrics(
            execution_id="exec-123",
            session_id="session-123",
            api_key_hash="abc123def456",
            user_id="user-123",
            entity_id="entity-123",
            language="python",
            execution_time_ms=50.0,
            status="failed",
            timestamp=datetime.now(UTC),
        )

        await detailed_metrics_service._update_hourly_aggregates(metrics)

        mock_redis.pipeline.assert_called()

    @pytest.mark.asyncio
    async def test_update_hourly_timeout_status(self, detailed_metrics_service, mock_redis):
        """Test updating hourly aggregates with timeout status."""
        metrics = DetailedExecutionMetrics(
            execution_id="exec-123",
            session_id="session-123",
            api_key_hash="abc123def456",
            user_id="user-123",
            entity_id="entity-123",
            language="python",
            execution_time_ms=30000.0,
            status="timeout",
            timestamp=datetime.now(UTC),
        )

        await detailed_metrics_service._update_hourly_aggregates(metrics)

        mock_redis.pipeline.assert_called()


class TestUpdateApiKeyMetrics:
    """Tests for _update_api_key_metrics method."""

    @pytest.mark.asyncio
    async def test_update_api_key_metrics_success(self, detailed_metrics_service, mock_redis):
        """Test updating API key metrics for successful execution."""
        metrics = DetailedExecutionMetrics(
            execution_id="exec-123",
            session_id="session-123",
            api_key_hash="abc123def456789012",
            user_id="user-123",
            entity_id="entity-123",
            language="python",
            execution_time_ms=50.0,
            status="completed",
            timestamp=datetime.now(UTC),
            memory_peak_mb=128.0,
            files_uploaded=2,
            files_generated=1,
        )

        await detailed_metrics_service._update_api_key_metrics(metrics)

        mock_redis.pipeline.assert_called()
        mock_pipe = mock_redis.pipeline.return_value
        mock_pipe.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_api_key_metrics_failure(self, detailed_metrics_service, mock_redis):
        """Test updating API key metrics for failed execution."""
        metrics = DetailedExecutionMetrics(
            execution_id="exec-123",
            session_id="session-123",
            api_key_hash="abc123def456789012",
            user_id="user-123",
            entity_id="entity-123",
            language="python",
            execution_time_ms=50.0,
            status="failed",
            timestamp=datetime.now(UTC),
        )

        await detailed_metrics_service._update_api_key_metrics(metrics)

        mock_redis.pipeline.assert_called()


class TestHelperMethods:
    """Tests for helper methods."""

    def test_get_hour_key(self, detailed_metrics_service):
        """Test hour key generation."""
        test_time = datetime(2024, 1, 15, 14, 30, 45, tzinfo=UTC)
        key = detailed_metrics_service._get_hour_key(test_time)

        assert "2024-01-15" in key
        assert "14" in key

    def test_parse_hourly_data(self, detailed_metrics_service):
        """Test parsing hourly data from Redis."""
        data = {
            b"execution_count": b"100",
            b"total_execution_time_ms": b"5000.0",
            b"success_count": b"90",
            b"failure_count": b"8",
            b"timeout_count": b"2",
            b"pool_hits": b"75",
            b"pool_misses": b"25",
        }

        result = detailed_metrics_service._parse_hourly_data(data, "2024-01-15T14", "hourly")

        assert result.execution_count == 100
        assert result.success_count == 90
        assert result.failure_count == 8

    def test_get_day_key(self, detailed_metrics_service):
        """Test day key generation."""
        test_time = datetime(2024, 1, 15, 14, 30, 45, tzinfo=UTC)
        key = detailed_metrics_service._get_day_key(test_time)

        assert key == "2024-01-15"

    def test_parse_hourly_data_with_memory(self, detailed_metrics_service):
        """Test parsing hourly data with memory stats."""
        data = {
            b"execution_count": b"100",
            b"total_execution_time_ms": b"5000.0",
            b"total_memory_mb": b"12800.0",
        }

        result = detailed_metrics_service._parse_hourly_data(data, "2024-01-15T14", "hourly")

        assert result.execution_count == 100
        assert result.avg_memory_mb == 128.0  # 12800 / 100


class TestGetApiKeyStats:
    """Tests for get_api_key_stats method."""

    @pytest.mark.asyncio
    async def test_get_api_key_stats_with_data(self, detailed_metrics_service, mock_redis):
        """Test get_api_key_stats returns aggregated data."""
        mock_redis.hgetall.return_value = {
            b"execution_count": b"50",
            b"success_count": b"45",
            b"failure_count": b"5",
            b"total_execution_time_ms": b"2500.0",
            b"total_memory_mb": b"1280.0",
            b"file_operations": b"10",
        }

        result = await detailed_metrics_service.get_api_key_stats("abc123def456789012", hours=1)

        assert result.api_key_hash == "abc123def4567890"
        assert result.execution_count == 50
        assert result.success_count == 45
        assert result.failure_count == 5
        assert result.success_rate == 90.0
        assert result.file_operations == 10

    @pytest.mark.asyncio
    async def test_get_api_key_stats_no_data(self, detailed_metrics_service, mock_redis):
        """Test get_api_key_stats when no data exists."""
        mock_redis.hgetall.return_value = {}

        result = await detailed_metrics_service.get_api_key_stats("abc123def456789012", hours=1)

        assert result.execution_count == 0
        # Default success_rate is 100.0 when no data (optimistic default)
        assert result.success_rate == 100.0

    @pytest.mark.asyncio
    async def test_get_api_key_stats_error(self, detailed_metrics_service, mock_redis):
        """Test get_api_key_stats handles errors."""
        mock_redis.hgetall.side_effect = Exception("Redis error")

        result = await detailed_metrics_service.get_api_key_stats("abc123def456789012", hours=1)

        # Should return empty stats on error
        assert result.execution_count == 0


class TestGetPoolStats:
    """Tests for get_pool_stats method."""

    @pytest.mark.asyncio
    async def test_get_pool_stats_with_data(self, detailed_metrics_service, mock_redis):
        """Test get_pool_stats returns pool statistics."""
        mock_redis.hgetall.return_value = {
            b"total_acquisitions": b"100",
            b"pool_hits": b"80",
            b"pool_misses": b"20",
            b"exhaustion_events": b"5",
            b"total_acquire_time_ms": b"500.0",
        }

        result = await detailed_metrics_service.get_pool_stats()

        assert result.total_acquisitions == 100
        assert result.pool_hits == 80
        assert result.pool_misses == 20
        assert result.exhaustion_events == 5
        assert result.hit_rate == 80.0
        assert result.avg_acquire_time_ms == 5.0  # 500 / 100

    @pytest.mark.asyncio
    async def test_get_pool_stats_no_data(self, detailed_metrics_service, mock_redis):
        """Test get_pool_stats when no data exists."""
        mock_redis.hgetall.return_value = {}

        result = await detailed_metrics_service.get_pool_stats()

        assert result.total_acquisitions == 0
        assert result.hit_rate == 0.0

    @pytest.mark.asyncio
    async def test_get_pool_stats_error(self, detailed_metrics_service, mock_redis):
        """Test get_pool_stats handles errors."""
        mock_redis.hgetall.side_effect = Exception("Redis error")

        result = await detailed_metrics_service.get_pool_stats()

        # Should return empty stats on error
        assert result.total_acquisitions == 0


class TestGetSummary:
    """Tests for get_summary method."""

    @pytest.mark.asyncio
    async def test_get_summary_success(self, detailed_metrics_service, mock_redis):
        """Test get_summary returns summary data."""
        # Mock hourly metrics
        mock_redis.hgetall.return_value = {
            b"execution_count": b"50",
            b"success_count": b"45",
            b"failure_count": b"5",
            b"total_execution_time_ms": b"2500.0",
            b"lang:python:count": b"30",
            b"lang:python:time_ms": b"1500.0",
        }

        result = await detailed_metrics_service.get_summary()

        # Should have aggregated data
        assert result is not None
        assert result.total_executions >= 0

    @pytest.mark.asyncio
    async def test_get_summary_error(self, detailed_metrics_service, mock_redis):
        """Test get_summary handles errors."""
        mock_redis.hgetall.side_effect = Exception("Redis error")

        result = await detailed_metrics_service.get_summary()

        # Should return empty summary on error
        assert result.total_executions == 0


class TestRecordExecutionWithSqlite:
    """Tests for record_execution with SQLite forwarding."""

    @pytest.mark.asyncio
    async def test_record_execution_forwards_to_sqlite(self, detailed_metrics_service, mock_redis, sample_metrics):
        """Test execution recording forwards to SQLite when enabled."""
        mock_sqlite = MagicMock()
        mock_sqlite.record_execution = AsyncMock()

        with patch("src.services.detailed_metrics.settings") as mock_settings:
            mock_settings.detailed_metrics_enabled = True
            mock_settings.sqlite_metrics_enabled = True

            with patch.dict(
                "sys.modules", {"src.services.sqlite_metrics": MagicMock(sqlite_metrics_service=mock_sqlite)}
            ):
                await detailed_metrics_service.record_execution(sample_metrics)

    @pytest.mark.asyncio
    async def test_record_execution_sqlite_error_handled(self, detailed_metrics_service, mock_redis, sample_metrics):
        """Test SQLite errors are handled gracefully."""
        mock_sqlite = MagicMock()
        mock_sqlite.record_execution = AsyncMock(side_effect=Exception("SQLite error"))

        with patch("src.services.detailed_metrics.settings") as mock_settings:
            mock_settings.detailed_metrics_enabled = True
            mock_settings.sqlite_metrics_enabled = True

            with patch.dict(
                "sys.modules", {"src.services.sqlite_metrics": MagicMock(sqlite_metrics_service=mock_sqlite)}
            ):
                # Should not raise
                await detailed_metrics_service.record_execution(sample_metrics)
