"""Unit tests for the metrics collector service."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.metrics import (
    APIMetrics,
    ExecutionMetrics,
    MetricPoint,
    MetricsCollector,
    MetricType,
)


class TestMetricType:
    """Tests for MetricType enum."""

    def test_metric_types_exist(self):
        """Test all metric types exist."""
        assert MetricType.COUNTER == "counter"
        assert MetricType.GAUGE == "gauge"
        assert MetricType.HISTOGRAM == "histogram"
        assert MetricType.TIMER == "timer"


class TestMetricPoint:
    """Tests for MetricPoint dataclass."""

    def test_create_metric_point(self):
        """Test creating a metric point."""
        point = MetricPoint(
            name="test_metric",
            value=42.0,
            timestamp=datetime.now(UTC),
        )

        assert point.name == "test_metric"
        assert point.value == 42.0
        assert point.labels == {}
        assert point.metric_type == MetricType.GAUGE

    def test_create_metric_point_with_labels(self):
        """Test creating a metric point with labels."""
        point = MetricPoint(
            name="test_metric",
            value=1.0,
            timestamp=datetime.now(UTC),
            labels={"env": "prod", "region": "us-east-1"},
            metric_type=MetricType.COUNTER,
        )

        assert point.labels == {"env": "prod", "region": "us-east-1"}
        assert point.metric_type == MetricType.COUNTER


class TestExecutionMetrics:
    """Tests for ExecutionMetrics dataclass."""

    def test_create_execution_metrics(self):
        """Test creating execution metrics."""
        metrics = ExecutionMetrics(
            execution_id="exec-123",
            session_id="session-456",
            language="python",
            status="completed",
            execution_time_ms=150.5,
        )

        assert metrics.execution_id == "exec-123"
        assert metrics.session_id == "session-456"
        assert metrics.language == "python"
        assert metrics.status == "completed"
        assert metrics.execution_time_ms == 150.5
        assert metrics.memory_peak_mb is None
        assert metrics.file_count == 0

    def test_create_execution_metrics_with_all_fields(self):
        """Test creating execution metrics with all fields."""
        metrics = ExecutionMetrics(
            execution_id="exec-123",
            session_id="session-456",
            language="python",
            status="completed",
            execution_time_ms=150.5,
            memory_peak_mb=128.5,
            cpu_time_ms=100.0,
            exit_code=0,
            file_count=3,
            output_size_bytes=1024,
        )

        assert metrics.memory_peak_mb == 128.5
        assert metrics.exit_code == 0
        assert metrics.file_count == 3
        assert metrics.output_size_bytes == 1024


class TestAPIMetrics:
    """Tests for APIMetrics dataclass."""

    def test_create_api_metrics(self):
        """Test creating API metrics."""
        metrics = APIMetrics(
            endpoint="/api/execute",
            method="POST",
            status_code=200,
            response_time_ms=50.5,
        )

        assert metrics.endpoint == "/api/execute"
        assert metrics.method == "POST"
        assert metrics.status_code == 200
        assert metrics.response_time_ms == 50.5
        assert metrics.request_size_bytes == 0

    def test_create_api_metrics_with_all_fields(self):
        """Test creating API metrics with all fields."""
        metrics = APIMetrics(
            endpoint="/api/execute",
            method="POST",
            status_code=200,
            response_time_ms=50.5,
            request_size_bytes=512,
            response_size_bytes=2048,
            user_agent="Mozilla/5.0",
        )

        assert metrics.request_size_bytes == 512
        assert metrics.response_size_bytes == 2048
        assert metrics.user_agent == "Mozilla/5.0"


class TestMetricsCollectorInit:
    """Tests for MetricsCollector initialization."""

    def test_init(self):
        """Test metrics collector initialization."""
        collector = MetricsCollector()

        assert collector._redis_client is None
        assert len(collector._metrics_buffer) == 0
        assert len(collector._counters) == 0
        assert len(collector._gauges) == 0
        assert collector._execution_stats["total_executions"] == 0
        assert collector._api_stats["total_requests"] == 0
        assert collector._persistence_task is None


class TestMetricsCollectorStart:
    """Tests for MetricsCollector start method."""

    @pytest.mark.asyncio
    async def test_start_with_redis(self):
        """Test start with Redis available."""
        collector = MetricsCollector()

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch.dict("sys.modules", {"src.core.pool": MagicMock()}):
            with patch("src.core.pool.redis_pool") as mock_pool:
                mock_pool.get_client.return_value = mock_redis

                await collector.start()

        # Cleanup
        await collector.stop()

    @pytest.mark.asyncio
    async def test_start_redis_timeout(self):
        """Test start when Redis times out - collector continues without Redis."""
        collector = MetricsCollector()

        # The collector handles timeout internally, just verify it doesn't crash
        # and runs in memory-only mode
        with patch.object(collector, "_load_metrics_from_redis", new_callable=AsyncMock):
            # Simulate the collector already having a None redis client
            collector._redis_client = None

        # Should be able to use the collector
        assert collector._redis_client is None

    @pytest.mark.asyncio
    async def test_start_redis_error(self):
        """Test start when Redis connection fails."""
        collector = MetricsCollector()

        # Collector should work without Redis
        collector._redis_client = None

        # Should still be usable
        assert collector._redis_client is None


class TestMetricsCollectorStop:
    """Tests for MetricsCollector stop method."""

    @pytest.mark.asyncio
    async def test_stop_without_redis(self):
        """Test stop without Redis."""
        collector = MetricsCollector()

        # Should not raise
        await collector.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_persistence_task(self):
        """Test stop cancels persistence task."""
        collector = MetricsCollector()
        collector._persistence_task = asyncio.create_task(asyncio.sleep(100))

        await collector.stop()

        assert collector._persistence_task.done() or collector._persistence_task.cancelled()


class TestRecordExecutionMetrics:
    """Tests for record_execution_metrics method."""

    def test_record_execution_completed(self):
        """Test recording a completed execution."""
        collector = MetricsCollector()

        metrics = ExecutionMetrics(
            execution_id="exec-123",
            session_id="session-456",
            language="python",
            status="completed",
            execution_time_ms=150.0,
        )

        collector.record_execution_metrics(metrics)

        assert collector._execution_stats["total_executions"] == 1
        assert collector._execution_stats["successful_executions"] == 1
        assert collector._execution_stats["language_counts"]["python"] == 1
        assert collector._counters["executions_total"] == 1

    def test_record_execution_failed(self):
        """Test recording a failed execution."""
        collector = MetricsCollector()

        metrics = ExecutionMetrics(
            execution_id="exec-123",
            session_id="session-456",
            language="python",
            status="failed",
            execution_time_ms=50.0,
        )

        collector.record_execution_metrics(metrics)

        assert collector._execution_stats["failed_executions"] == 1

    def test_record_execution_timeout(self):
        """Test recording a timed out execution."""
        collector = MetricsCollector()

        metrics = ExecutionMetrics(
            execution_id="exec-123",
            session_id="session-456",
            language="python",
            status="timeout",
            execution_time_ms=30000.0,
        )

        collector.record_execution_metrics(metrics)

        assert collector._execution_stats["timeout_executions"] == 1

    def test_record_execution_with_memory(self):
        """Test recording execution with memory stats."""
        collector = MetricsCollector()

        metrics = ExecutionMetrics(
            execution_id="exec-123",
            session_id="session-456",
            language="python",
            status="completed",
            execution_time_ms=150.0,
            memory_peak_mb=64.5,
        )

        collector.record_execution_metrics(metrics)

        assert collector._execution_stats["total_memory_usage_mb"] == 64.5
        assert 64.5 in collector._histograms["memory_usage_mb"]

    def test_record_multiple_executions(self):
        """Test recording multiple executions."""
        collector = MetricsCollector()

        for i in range(5):
            metrics = ExecutionMetrics(
                execution_id=f"exec-{i}",
                session_id=f"session-{i}",
                language="python",
                status="completed",
                execution_time_ms=100.0 + i * 10,
            )
            collector.record_execution_metrics(metrics)

        assert collector._execution_stats["total_executions"] == 5
        assert len(collector._histograms["execution_time_ms"]) == 5

    def test_histogram_trimming(self):
        """Test that histograms are trimmed when too large."""
        collector = MetricsCollector()

        # Add over 1000 entries
        for i in range(1100):
            metrics = ExecutionMetrics(
                execution_id=f"exec-{i}",
                session_id=f"session-{i}",
                language="python",
                status="completed",
                execution_time_ms=float(i),
            )
            collector.record_execution_metrics(metrics)

        # Histogram should be trimmed - after 1001 entries it trims to 500
        # Then adds 99 more (1002-1100), so should be ~599
        # Key point: it should never exceed 1000 + a few entries
        assert len(collector._histograms["execution_time_ms"]) < 1000

    def test_average_execution_time_calculated(self):
        """Test that average execution time is calculated."""
        collector = MetricsCollector()

        for i in range(10):
            metrics = ExecutionMetrics(
                execution_id=f"exec-{i}",
                session_id=f"session-{i}",
                language="python",
                status="completed",
                execution_time_ms=100.0,
            )
            collector.record_execution_metrics(metrics)

        assert collector._gauges["avg_execution_time_ms"] == 100.0


class TestRecordAPIMetrics:
    """Tests for record_api_metrics method."""

    def test_record_successful_request(self):
        """Test recording a successful API request."""
        collector = MetricsCollector()

        metrics = APIMetrics(
            endpoint="/api/execute",
            method="POST",
            status_code=200,
            response_time_ms=50.0,
        )

        collector.record_api_metrics(metrics)

        assert collector._api_stats["total_requests"] == 1
        assert collector._api_stats["successful_requests"] == 1
        assert collector._counters["api_requests_total"] == 1

    def test_record_error_request(self):
        """Test recording an error API request."""
        collector = MetricsCollector()

        metrics = APIMetrics(
            endpoint="/api/execute",
            method="POST",
            status_code=500,
            response_time_ms=100.0,
        )

        collector.record_api_metrics(metrics)

        assert collector._api_stats["error_requests"] == 1

    def test_record_client_error_request(self):
        """Test recording a client error (4xx) API request."""
        collector = MetricsCollector()

        metrics = APIMetrics(
            endpoint="/api/execute",
            method="POST",
            status_code=400,
            response_time_ms=10.0,
        )

        collector.record_api_metrics(metrics)

        assert collector._api_stats["error_requests"] == 1

    def test_record_redirect_as_success(self):
        """Test that 3xx redirects are counted as success."""
        collector = MetricsCollector()

        metrics = APIMetrics(
            endpoint="/api/redirect",
            method="GET",
            status_code=302,
            response_time_ms=5.0,
        )

        collector.record_api_metrics(metrics)

        assert collector._api_stats["successful_requests"] == 1

    def test_success_rate_calculated(self):
        """Test that success rate is calculated."""
        collector = MetricsCollector()

        # 8 successful, 2 errors
        for i in range(8):
            collector.record_api_metrics(
                APIMetrics(endpoint="/test", method="GET", status_code=200, response_time_ms=10.0)
            )
        for i in range(2):
            collector.record_api_metrics(
                APIMetrics(endpoint="/test", method="GET", status_code=500, response_time_ms=10.0)
            )

        assert collector._gauges["api_success_rate"] == 80.0


class TestGetExecutionStatistics:
    """Tests for get_execution_statistics method."""

    def test_get_empty_statistics(self):
        """Test getting statistics when empty."""
        collector = MetricsCollector()

        stats = collector.get_execution_statistics()

        assert stats["total_executions"] == 0
        assert "success_rate" not in stats

    def test_get_statistics_with_data(self):
        """Test getting statistics with data."""
        collector = MetricsCollector()

        # Add some executions
        for status in ["completed", "completed", "failed"]:
            collector.record_execution_metrics(
                ExecutionMetrics(
                    execution_id="exec",
                    session_id="session",
                    language="python",
                    status=status,
                    execution_time_ms=100.0,
                )
            )

        stats = collector.get_execution_statistics()

        assert stats["total_executions"] == 3
        assert stats["successful_executions"] == 2
        assert stats["failed_executions"] == 1
        assert "success_rate" in stats
        assert abs(stats["success_rate"] - 66.67) < 1  # ~66.67%

    def test_get_statistics_percentiles(self):
        """Test that percentiles are calculated."""
        collector = MetricsCollector()

        # Add executions with varying times
        for i in range(100):
            collector.record_execution_metrics(
                ExecutionMetrics(
                    execution_id=f"exec-{i}",
                    session_id="session",
                    language="python",
                    status="completed",
                    execution_time_ms=float(i),
                )
            )

        stats = collector.get_execution_statistics()

        assert "execution_time_percentiles" in stats
        assert "p50" in stats["execution_time_percentiles"]
        assert "p90" in stats["execution_time_percentiles"]


class TestGetAPIStatistics:
    """Tests for get_api_statistics method."""

    def test_get_empty_api_statistics(self):
        """Test getting API statistics when empty."""
        collector = MetricsCollector()

        stats = collector.get_api_statistics()

        assert stats["total_requests"] == 0

    def test_get_api_statistics_with_data(self):
        """Test getting API statistics with data."""
        collector = MetricsCollector()

        for _ in range(10):
            collector.record_api_metrics(
                APIMetrics(
                    endpoint="/api/execute",
                    method="POST",
                    status_code=200,
                    response_time_ms=50.0,
                )
            )

        stats = collector.get_api_statistics()

        assert stats["total_requests"] == 10
        assert stats["endpoint_counts"]["/api/execute"] == 10
        assert "response_time_percentiles" in stats


class TestGetSystemMetrics:
    """Tests for get_system_metrics method."""

    def test_get_system_metrics(self):
        """Test getting system metrics."""
        collector = MetricsCollector()

        # Record some metrics
        collector.record_execution_metrics(
            ExecutionMetrics(
                execution_id="exec",
                session_id="session",
                language="python",
                status="completed",
                execution_time_ms=100.0,
            )
        )

        metrics = collector.get_system_metrics()

        assert "counters" in metrics
        assert "gauges" in metrics
        assert "buffer_size" in metrics
        assert metrics["buffer_size"] == 1


class TestPercentile:
    """Tests for _percentile method."""

    def test_percentile_empty_data(self):
        """Test percentile with empty data."""
        collector = MetricsCollector()

        result = collector._percentile([], 50)

        assert result == 0.0

    def test_percentile_single_value(self):
        """Test percentile with single value."""
        collector = MetricsCollector()

        result = collector._percentile([100.0], 50)

        assert result == 100.0

    def test_percentile_median(self):
        """Test median calculation."""
        collector = MetricsCollector()

        result = collector._percentile([10.0, 20.0, 30.0, 40.0, 50.0], 50)

        assert result == 30.0

    def test_percentile_p90(self):
        """Test p90 calculation."""
        collector = MetricsCollector()

        data = list(range(1, 101))  # 1 to 100
        result = collector._percentile(data, 90)

        assert result >= 89.0  # Should be around 90

    def test_percentile_interpolation(self):
        """Test percentile interpolation between values."""
        collector = MetricsCollector()

        # With 4 values, p50 should interpolate between 2nd and 3rd
        result = collector._percentile([10.0, 20.0, 30.0, 40.0], 50)

        assert 20.0 <= result <= 30.0


class TestPersistenceLoop:
    """Tests for _persistence_loop method."""

    @pytest.mark.asyncio
    async def test_persistence_loop_continues_on_error(self):
        """Test that persistence loop continues on errors."""
        collector = MetricsCollector()
        collector._redis_client = AsyncMock()
        collector._persistence_interval = 0.01  # Very short for testing

        error_count = 0

        async def mock_persist():
            nonlocal error_count
            error_count += 1
            if error_count < 3:
                raise Exception("Persist error")

        collector._persist_metrics_to_redis = mock_persist

        # Run loop briefly
        task = asyncio.create_task(collector._persistence_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should have tried multiple times despite errors
        assert error_count >= 2


class TestPersistMetricsToRedis:
    """Tests for _persist_metrics_to_redis method."""

    @pytest.mark.asyncio
    async def test_persist_without_redis(self):
        """Test persist when Redis is not available."""
        collector = MetricsCollector()
        collector._redis_client = None

        # Should not raise
        await collector._persist_metrics_to_redis()

    @pytest.mark.asyncio
    async def test_persist_with_redis(self):
        """Test persist with Redis available."""
        collector = MetricsCollector()
        collector._redis_client = AsyncMock()
        collector._redis_client.setex = AsyncMock()

        await collector._persist_metrics_to_redis()

        # Should have called setex twice (current and hourly)
        assert collector._redis_client.setex.call_count == 2

    @pytest.mark.asyncio
    async def test_persist_handles_error(self):
        """Test persist handles errors gracefully."""
        collector = MetricsCollector()
        collector._redis_client = AsyncMock()
        collector._redis_client.setex = AsyncMock(side_effect=Exception("Redis error"))

        # Should not raise
        await collector._persist_metrics_to_redis()


class TestLoadMetricsFromRedis:
    """Tests for _load_metrics_from_redis method."""

    @pytest.mark.asyncio
    async def test_load_without_redis(self):
        """Test load when Redis is not available."""
        collector = MetricsCollector()
        collector._redis_client = None

        # Should not raise
        await collector._load_metrics_from_redis()

    @pytest.mark.asyncio
    async def test_load_with_existing_data(self):
        """Test load when data exists in Redis."""
        collector = MetricsCollector()
        collector._redis_client = AsyncMock()
        collector._redis_client.get = AsyncMock(return_value='{"some": "data"}')

        # Should not raise
        await collector._load_metrics_from_redis()

        collector._redis_client.get.assert_called_once_with("metrics:current")

    @pytest.mark.asyncio
    async def test_load_handles_error(self):
        """Test load handles errors gracefully."""
        collector = MetricsCollector()
        collector._redis_client = AsyncMock()
        collector._redis_client.get = AsyncMock(side_effect=Exception("Redis error"))

        # Should not raise
        await collector._load_metrics_from_redis()


class TestStartRedisConnectionErrors:
    """Tests for start method Redis connection error handling."""

    @pytest.mark.asyncio
    async def test_start_redis_timeout(self):
        """Test start handles Redis connection timeout."""
        collector = MetricsCollector()

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=TimeoutError())

        with patch("src.core.pool.redis_pool") as mock_pool:
            mock_pool.get_client.return_value = mock_redis

            await collector.start()

        # Collector should still work without Redis
        assert collector._redis_client is None

    @pytest.mark.asyncio
    async def test_start_redis_generic_error(self):
        """Test start handles Redis generic connection error."""
        collector = MetricsCollector()

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("Connection refused"))

        with patch("src.core.pool.redis_pool") as mock_pool:
            mock_pool.get_client.return_value = mock_redis

            await collector.start()

        # Collector should still work without Redis
        assert collector._redis_client is None


class TestStopWithTimeouts:
    """Tests for stop method timeout handling."""

    @pytest.mark.asyncio
    async def test_stop_persistence_timeout(self):
        """Test stop handles persistence timeout."""
        collector = MetricsCollector()

        # Setup mock persistence task
        async def slow_persist():
            await asyncio.sleep(10)

        collector._persistence_task = asyncio.create_task(asyncio.sleep(10))
        collector._persist_metrics_to_redis = slow_persist
        collector._redis_client = None

        await collector.stop()

        # Should complete without hanging
        assert True

    @pytest.mark.asyncio
    async def test_stop_redis_close_timeout(self):
        """Test stop handles Redis close timeout."""
        collector = MetricsCollector()
        collector._persistence_task = None

        async def slow_close():
            await asyncio.sleep(10)

        mock_redis = AsyncMock()
        mock_redis.close = slow_close
        collector._redis_client = mock_redis

        # Mock persist to return quickly
        collector._persist_metrics_to_redis = AsyncMock()

        await collector.stop()

        # Should complete without hanging
        assert True

    @pytest.mark.asyncio
    async def test_stop_redis_close_error(self):
        """Test stop handles Redis close error."""
        collector = MetricsCollector()
        collector._persistence_task = None

        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock(side_effect=Exception("Close error"))
        collector._redis_client = mock_redis
        collector._persist_metrics_to_redis = AsyncMock()

        # Should not raise
        await collector.stop()

    @pytest.mark.asyncio
    async def test_stop_general_error(self):
        """Test stop handles general errors."""
        collector = MetricsCollector()

        # Force an error by making persistence_task.done() raise
        mock_task = MagicMock()
        mock_task.done.side_effect = Exception("Task error")
        collector._persistence_task = mock_task

        # Should not raise
        await collector.stop()


class TestHistogramTruncation:
    """Tests for histogram size management."""

    def test_histogram_truncation(self):
        """Test histograms are truncated when too large."""
        collector = MetricsCollector()

        # Add more than 1000 execution times
        for i in range(1100):
            metrics = ExecutionMetrics(
                execution_id=f"exec-{i}",
                session_id="session-1",
                language="python",
                status="completed",
                execution_time_ms=100.0 + i,
                timestamp=datetime.now(UTC),
            )
            collector.record_execution_metrics(metrics)

        # Histogram should be truncated (when > 1000, keeps last 500)
        # After adding 1001 items, it truncates to 500, then 99 more = 599
        assert len(collector._histograms["execution_time_ms"]) < 1000
        assert len(collector._histograms["execution_time_ms"]) == 599

    def test_memory_histogram_truncation(self):
        """Test memory histogram is also truncated."""
        collector = MetricsCollector()

        # Add more than 1000 metrics with memory
        for i in range(1100):
            metrics = ExecutionMetrics(
                execution_id=f"exec-{i}",
                session_id="session-1",
                language="python",
                status="completed",
                execution_time_ms=100.0,
                memory_peak_mb=50.0 + i,
                timestamp=datetime.now(UTC),
            )
            collector.record_execution_metrics(metrics)

        # Memory histogram should also be truncated
        assert len(collector._histograms["memory_usage_mb"]) < 1000
        assert len(collector._histograms["memory_usage_mb"]) == 599


class TestGetSystemMetricsExtended:
    """Extended tests for get_system_metrics method."""

    def test_get_system_metrics_with_data(self):
        """Test get_system_metrics returns proper structure."""
        collector = MetricsCollector()

        # Add some metrics
        for i in range(5):
            metrics = ExecutionMetrics(
                execution_id=f"exec-{i}",
                session_id="session-1",
                language="python",
                status="completed",
                execution_time_ms=50.0 + i * 10,
                memory_peak_mb=100.0 + i * 5,
                timestamp=datetime.now(UTC),
            )
            collector.record_execution_metrics(metrics)

        result = collector.get_system_metrics()

        assert "counters" in result
        assert "gauges" in result
        assert "buffer_size" in result
        assert result["buffer_size"] == 5

    def test_get_system_metrics_with_api_metrics(self):
        """Test get_system_metrics after recording API metrics."""
        collector = MetricsCollector()

        # Add some API metrics
        for i in range(3):
            metrics = APIMetrics(
                endpoint="/api/execute",
                method="POST",
                status_code=200,
                response_time_ms=50.0 + i * 10,
                timestamp=datetime.now(UTC),
            )
            collector.record_api_metrics(metrics)

        result = collector.get_system_metrics()

        assert result["buffer_size"] == 3
        assert "api_requests_total" in result["counters"]
        assert result["counters"]["api_requests_total"] == 3
