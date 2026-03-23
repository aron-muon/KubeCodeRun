"""Tests for immediate pool replenishment (GitHub issue #30).

When all pool pods are terminated externally, the pool should replenish
immediately rather than waiting for the next polling interval.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.kubernetes.models import (
    PodHandle,
    PodStatus,
    PoolConfig,
    PooledPod,
)
from src.services.kubernetes.pool import PodPool


@pytest.fixture
def pool_config():
    return PoolConfig(
        language="python",
        image="python:3.11",
        pool_size=3,
        sidecar_image="sidecar:latest",
    )


@pytest.fixture
def pod_pool(pool_config):
    with patch("src.services.kubernetes.pool.get_current_namespace", return_value="test-ns"):
        pool = PodPool(pool_config, namespace="test-ns")
        return pool


def _make_pooled_pod(uid, language="python"):
    handle = PodHandle(
        name=f"pool-{language}-{uid}",
        namespace="test-ns",
        uid=uid,
        language=language,
        status=PodStatus.WARM,
        labels={},
    )
    handle.pod_ip = "10.0.0.1"
    return PooledPod(handle=handle, language=language)


class TestReplenishEvent:
    """Tests for the _replenish_needed event mechanism."""

    def test_signal_replenish_sets_event(self, pod_pool):
        """Test that _signal_replenish sets the event."""
        assert not pod_pool._replenish_needed.is_set()
        pod_pool._signal_replenish()
        assert pod_pool._replenish_needed.is_set()

    @pytest.mark.asyncio
    async def test_replenish_loop_wakes_on_event(self, pod_pool):
        """Test that the replenish loop wakes immediately when event is signaled."""
        pod_pool._running = True
        replenish_called = asyncio.Event()

        async def mock_create():
            replenish_called.set()
            pod_pool._running = False
            return None

        with patch.object(pod_pool, "_create_warm_pod", side_effect=mock_create):
            # Start the loop
            loop_task = asyncio.create_task(pod_pool._replenish_loop())

            # Signal replenishment immediately
            pod_pool._signal_replenish()

            # The loop should wake up and call _create_warm_pod within <1s
            try:
                await asyncio.wait_for(replenish_called.wait(), timeout=1.0)
            except TimeoutError:
                pod_pool._running = False
                pytest.fail("Replenish loop did not wake up within 1 second")
            finally:
                pod_pool._running = False
                loop_task.cancel()
                try:
                    await loop_task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_replenish_loop_creates_all_needed_pods(self, pod_pool):
        """Test that replenish creates all needed pods, not just 3."""
        pod_pool._running = True
        create_count = 0

        async def mock_create():
            nonlocal create_count
            create_count += 1
            # Stop after first batch
            if create_count >= pod_pool.pool_size:
                pod_pool._running = False
            return None

        with patch.object(pod_pool, "_create_warm_pod", side_effect=mock_create):
            pod_pool._signal_replenish()
            try:
                await asyncio.wait_for(pod_pool._replenish_loop(), timeout=2.0)
            except TimeoutError:
                pod_pool._running = False

        # Should have tried to create pool_size (3) pods, not just 3 max
        assert create_count == pod_pool.pool_size


class TestAcquireTriggersReplenish:
    """Tests for acquire triggering replenishment."""

    @pytest.mark.asyncio
    async def test_acquire_stale_pod_signals_replenish(self, pod_pool):
        """When acquire gets a stale UID (pod removed by health check),
        it should signal replenishment and retry."""
        # Put a stale UID in the queue (not in _pods)
        await pod_pool._available.put("stale-uid")

        # Also put a valid pod
        valid_pod = _make_pooled_pod("valid-uid")
        pod_pool._pods["valid-uid"] = valid_pod
        await pod_pool._available.put("valid-uid")

        result = await pod_pool.acquire("session-123", timeout=5)

        # Should have skipped stale and acquired the valid pod
        assert result is not None
        assert result.uid == "valid-uid"
        # Replenish event should have been signaled for the stale entry
        assert pod_pool._replenish_needed.is_set()

    @pytest.mark.asyncio
    async def test_acquire_timeout_signals_replenish(self, pod_pool):
        """When acquire times out, it should signal replenishment."""
        result = await pod_pool.acquire("session-123", timeout=0.1)

        assert result is None
        assert pod_pool._replenish_needed.is_set()

    @pytest.mark.asyncio
    async def test_acquire_retries_past_stale_entries(self, pod_pool):
        """Acquire should retry past multiple stale entries."""
        # Put multiple stale UIDs
        await pod_pool._available.put("stale-1")
        await pod_pool._available.put("stale-2")

        # Then a valid pod
        valid_pod = _make_pooled_pod("valid-uid")
        pod_pool._pods["valid-uid"] = valid_pod
        await pod_pool._available.put("valid-uid")

        result = await pod_pool.acquire("session-123", timeout=5)

        assert result is not None
        assert result.uid == "valid-uid"


class TestHealthCheckTriggersReplenish:
    """Tests for health check triggering immediate replenishment."""

    @pytest.mark.asyncio
    async def test_health_check_signals_replenish_on_removal(self, pod_pool):
        """When health check removes unhealthy pods, it should signal
        immediate replenishment."""
        pod_pool._running = True
        pooled_pod = _make_pooled_pod("unhealthy-uid")
        pooled_pod.health_check_failures = 2  # One more failure triggers removal
        pod_pool._pods["unhealthy-uid"] = pooled_pod
        iteration = 0

        async def mock_sleep(_):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                pod_pool._running = False

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 500  # Unhealthy
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            with patch.object(pod_pool, "_delete_pod", new_callable=AsyncMock):
                with patch("asyncio.sleep", side_effect=mock_sleep):
                    await pod_pool._health_check_loop()

        # Pod should have been removed
        assert "unhealthy-uid" not in pod_pool._pods
        # Replenish should have been signaled
        assert pod_pool._replenish_needed.is_set()

    @pytest.mark.asyncio
    async def test_health_check_no_signal_when_all_healthy(self, pod_pool):
        """Health check should NOT signal replenish when all pods are healthy."""
        pod_pool._running = True
        pooled_pod = _make_pooled_pod("healthy-uid")
        pod_pool._pods["healthy-uid"] = pooled_pod
        iteration = 0

        async def mock_sleep(_):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                pod_pool._running = False

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200  # Healthy
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            with patch("asyncio.sleep", side_effect=mock_sleep):
                await pod_pool._health_check_loop()

        # Replenish should NOT have been signaled
        assert not pod_pool._replenish_needed.is_set()


class TestEndToEndPoolRecovery:
    """End-to-end test simulating the issue #30 scenario."""

    @pytest.mark.asyncio
    async def test_pool_recovers_after_all_pods_terminated(self, pod_pool):
        """Simulate: all pods terminated externally, health check detects,
        replenish loop creates replacements immediately."""
        # Set up pool with pods
        pod1 = _make_pooled_pod("pod-1")
        pod2 = _make_pooled_pod("pod-2")
        pod3 = _make_pooled_pod("pod-3")
        pod_pool._pods = {"pod-1": pod1, "pod-2": pod2, "pod-3": pod3}

        # Simulate health check removing all pods (they were terminated externally)
        for uid in list(pod_pool._pods.keys()):
            del pod_pool._pods[uid]

        # Signal replenish (as health check would)
        pod_pool._signal_replenish()

        # Verify the event is set for immediate wakeup
        assert pod_pool._replenish_needed.is_set()

        # Verify replenish loop would create 3 pods (pool_size)
        pod_pool._running = True
        create_count = 0

        async def mock_create():
            nonlocal create_count
            create_count += 1
            if create_count >= 3:
                pod_pool._running = False
            return None

        with patch.object(pod_pool, "_create_warm_pod", side_effect=mock_create):
            try:
                await asyncio.wait_for(pod_pool._replenish_loop(), timeout=2.0)
            except TimeoutError:
                pod_pool._running = False

        assert create_count == 3
