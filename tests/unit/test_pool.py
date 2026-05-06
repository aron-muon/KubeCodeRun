"""Unit tests for Pod Pool Manager."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes.client import ApiException

from src.services.kubernetes.models import (
    ExecutionResult,
    FileData,
    PodHandle,
    PodStatus,
    PoolConfig,
    PooledPod,
)
from src.services.kubernetes.pool import PodPool, PodPoolManager


@pytest.fixture
def pool_config():
    """Create a pool configuration for testing."""
    return PoolConfig(
        language="python",
        image="python:3.11",
        pool_size=2,
        cpu_limit="1",
        memory_limit="512Mi",
    )


@pytest.fixture
def pod_pool(pool_config):
    """Create a pod pool instance."""
    with patch("src.services.kubernetes.pool.get_current_namespace", return_value="test-namespace"):
        pool = PodPool(pool_config, namespace="test-namespace")
        return pool


@pytest.fixture
def pod_handle():
    """Create a pod handle for testing."""
    handle = PodHandle(
        name="pool-python-abc123",
        namespace="test-namespace",
        uid="pod-uid-123",
        language="python",
        status=PodStatus.WARM,
        labels={},
    )
    handle.pod_ip = "10.0.0.1"
    return handle


@pytest.fixture
def pooled_pod(pod_handle):
    """Create a pooled pod for testing."""
    return PooledPod(
        handle=pod_handle,
        language="python",
    )


class TestPoolConfig:
    """Tests for PoolConfig dataclass."""

    def test_pool_config_default_network_isolated(self):
        """Test that network_isolated defaults to False."""
        config = PoolConfig(
            language="python",
            image="python:3.12",
            pool_size=5,
        )
        assert config.network_isolated is False

    def test_pool_config_with_network_isolated_true(self):
        """Test creating PoolConfig with network_isolated=True."""
        config = PoolConfig(
            language="go",
            image="golang:1.22",
            pool_size=2,
            network_isolated=True,
        )
        assert config.network_isolated is True

    def test_pool_config_with_network_isolated_false(self):
        """Test creating PoolConfig with explicit network_isolated=False."""
        config = PoolConfig(
            language="python",
            image="python:3.12",
            pool_size=3,
            network_isolated=False,
        )
        assert config.network_isolated is False


class TestPodPoolInit:
    """Tests for PodPool initialization."""

    def test_init_with_defaults(self, pool_config):
        """Test initialization with default namespace."""
        with patch("src.services.kubernetes.pool.get_current_namespace", return_value="default"):
            pool = PodPool(pool_config)

            assert pool.namespace == "default"
            assert pool.language == "python"
            assert pool.pool_size == 2

    def test_init_with_custom_namespace(self, pool_config):
        """Test initialization with custom namespace."""
        pool = PodPool(pool_config, namespace="custom-ns")

        assert pool.namespace == "custom-ns"

    def test_init_creates_queue(self, pod_pool):
        """Test that queue is created."""
        assert pod_pool._available is not None


class TestPodPoolGeneratePodName:
    """Tests for _generate_pod_name method."""

    def test_generate_pod_name(self, pod_pool):
        """Test generating pod name."""
        name = pod_pool._generate_pod_name()

        assert name.startswith("pool-python-")
        assert len(name) <= 63


class TestPodPoolGetHttpClient:
    """Tests for _get_http_client method."""

    @pytest.mark.asyncio
    async def test_creates_http_client(self, pod_pool):
        """Test that HTTP client is created."""
        client = await pod_pool._get_http_client()

        assert client is not None
        assert not client.is_closed

        await client.aclose()

    @pytest.mark.asyncio
    async def test_reuses_http_client(self, pod_pool):
        """Test that HTTP client is reused."""
        client1 = await pod_pool._get_http_client()
        client2 = await pod_pool._get_http_client()

        assert client1 is client2

        await client1.aclose()


class TestPodPoolStartStop:
    """Tests for start and stop methods."""

    @pytest.mark.asyncio
    async def test_start(self, pod_pool):
        """Test starting the pool."""
        with patch.object(pod_pool, "_resolve_owner_pod_uid", new_callable=AsyncMock, return_value="test-uid"), \
             patch.object(pod_pool, "_cleanup_orphaned_pods", new_callable=AsyncMock), \
             patch.object(pod_pool, "_warmup", new_callable=AsyncMock):
            await pod_pool.start()

            assert pod_pool._running is True
            assert pod_pool._owner_pod_uid == "test-uid"
            assert pod_pool._replenish_task is not None
            assert pod_pool._health_check_task is not None

            # Clean up
            await pod_pool.stop()

    @pytest.mark.asyncio
    async def test_start_already_running(self, pod_pool):
        """Test starting when already running."""
        pod_pool._running = True

        with patch.object(pod_pool, "_warmup", new_callable=AsyncMock) as mock_warmup:
            await pod_pool.start()

            mock_warmup.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop(self, pod_pool):
        """Test stopping the pool."""
        with patch.object(pod_pool, "_resolve_owner_pod_uid", new_callable=AsyncMock, return_value=None), \
             patch.object(pod_pool, "_cleanup_orphaned_pods", new_callable=AsyncMock), \
             patch.object(pod_pool, "_warmup", new_callable=AsyncMock):
            await pod_pool.start()

        await pod_pool.stop()

        assert pod_pool._running is False

    @pytest.mark.asyncio
    async def test_stop_deletes_pods(self, pod_pool, pooled_pod):
        """Test that stop deletes all pods."""
        pod_pool._pods[pooled_pod.handle.uid] = pooled_pod

        with patch.object(pod_pool, "_delete_pod", new_callable=AsyncMock) as mock_delete:
            await pod_pool.stop()

            mock_delete.assert_called_once()
            assert len(pod_pool._pods) == 0


class TestOrphanedPodCleanup:
    """Tests for _resolve_owner_pod_uid and _cleanup_orphaned_pods."""

    @pytest.mark.asyncio
    async def test_resolve_owner_pod_uid_no_hostname(self, pod_pool):
        """Returns None when HOSTNAME is not set."""
        with patch.dict("os.environ", {}, clear=True):
            result = await pod_pool._resolve_owner_pod_uid()
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_owner_pod_uid_no_core_api(self, pod_pool):
        """Returns None when k8s client is unavailable."""
        with patch.dict("os.environ", {"HOSTNAME": "my-pod-abc123"}), \
             patch("src.services.kubernetes.pool.get_core_api", return_value=None):
            result = await pod_pool._resolve_owner_pod_uid()
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_owner_pod_uid_success(self, pod_pool):
        """Returns the pod UID from the k8s API."""
        mock_pod = MagicMock()
        mock_pod.metadata.uid = "api-pod-uid-123"
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch.dict("os.environ", {"HOSTNAME": "my-pod-abc123"}), \
             patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            result = await pod_pool._resolve_owner_pod_uid()

        assert result == "api-pod-uid-123"

    @pytest.mark.asyncio
    async def test_cleanup_skipped_when_no_owner_uid(self, pod_pool):
        """Cleanup is skipped when owner UID could not be resolved."""
        pod_pool._owner_pod_uid = None
        mock_core_api = MagicMock()

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            await pod_pool._cleanup_orphaned_pods()

        mock_core_api.list_namespaced_pod.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_deletes_pods_with_dead_owner(self, pod_pool):
        """Pods whose owner is no longer alive are deleted."""
        pod_pool._owner_pod_uid = "current-uid"

        dead_pod = MagicMock()
        dead_pod.metadata.name = "pool-python-dead123"
        dead_pod.metadata.uid = "pool-pod-uid-1"
        dead_pod.metadata.labels = {
            "app.kubernetes.io/managed-by": "kubecoderun",
            "kubecoderun.io/type": "pool",
            "kubecoderun.io/language": "python",
            "kubecoderun.io/owner-pod-uid": "dead-owner-uid",
        }

        mock_pool_list = MagicMock()
        mock_pool_list.items = [dead_pod]

        # No live pod has the dead owner's UID
        mock_all_list = MagicMock()
        live_pod = MagicMock()
        live_pod.metadata.uid = "current-uid"
        mock_all_list.items = [live_pod]

        mock_core_api = MagicMock()
        mock_core_api.list_namespaced_pod.side_effect = [mock_pool_list, mock_all_list]

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api), \
             patch.object(pod_pool, "_delete_pod", new_callable=AsyncMock) as mock_delete:
            await pod_pool._cleanup_orphaned_pods()

        mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_spares_pods_with_live_owner(self, pod_pool):
        """Pods whose owner is still running are not deleted."""
        pod_pool._owner_pod_uid = "current-uid"

        live_pool_pod = MagicMock()
        live_pool_pod.metadata.name = "pool-python-live123"
        live_pool_pod.metadata.uid = "pool-pod-uid-2"
        live_pool_pod.metadata.labels = {
            "app.kubernetes.io/managed-by": "kubecoderun",
            "kubecoderun.io/type": "pool",
            "kubecoderun.io/language": "python",
            "kubecoderun.io/owner-pod-uid": "other-replica-uid",
        }

        mock_pool_list = MagicMock()
        mock_pool_list.items = [live_pool_pod]

        # other-replica-uid IS in the live set
        mock_all_list = MagicMock()
        other_pod = MagicMock()
        other_pod.metadata.uid = "other-replica-uid"
        mock_all_list.items = [other_pod]

        mock_core_api = MagicMock()
        mock_core_api.list_namespaced_pod.side_effect = [mock_pool_list, mock_all_list]

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api), \
             patch.object(pod_pool, "_delete_pod", new_callable=AsyncMock) as mock_delete:
            await pod_pool._cleanup_orphaned_pods()

        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_spares_own_pods(self, pod_pool):
        """Pods owned by this instance are never touched."""
        pod_pool._owner_pod_uid = "current-uid"

        own_pod = MagicMock()
        own_pod.metadata.name = "pool-python-mine"
        own_pod.metadata.uid = "pool-pod-uid-3"
        own_pod.metadata.labels = {
            "kubecoderun.io/owner-pod-uid": "current-uid",
        }

        mock_pool_list = MagicMock()
        mock_pool_list.items = [own_pod]

        mock_all_list = MagicMock()
        mock_all_list.items = []

        mock_core_api = MagicMock()
        mock_core_api.list_namespaced_pod.side_effect = [mock_pool_list, mock_all_list]

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api), \
             patch.object(pod_pool, "_delete_pod", new_callable=AsyncMock) as mock_delete:
            await pod_pool._cleanup_orphaned_pods()

        mock_delete.assert_not_called()


class TestPodPoolWarmup:
    """Tests for _warmup method."""

    @pytest.mark.asyncio
    async def test_warmup_creates_pods(self, pod_pool):
        """Test that warmup creates pods."""
        with patch.object(pod_pool, "_create_warm_pod", new_callable=AsyncMock) as mock_create:
            await pod_pool._warmup()

            # Should create pool_size pods
            assert mock_create.call_count == pod_pool.pool_size

    @pytest.mark.asyncio
    async def test_warmup_skips_if_enough_pods(self, pod_pool, pooled_pod):
        """Test that warmup skips if enough pods available."""
        # Add enough pods
        pod_pool._pods[pooled_pod.handle.uid] = pooled_pod
        pod_pool._pods["pod2"] = pooled_pod  # Add another

        with patch.object(pod_pool, "_create_warm_pod", new_callable=AsyncMock) as mock_create:
            await pod_pool._warmup()

            mock_create.assert_not_called()


class TestPodPoolCreateWarmPod:
    """Tests for _create_warm_pod method."""

    @pytest.mark.asyncio
    async def test_create_warm_pod_success(self, pod_pool):
        """Test successful pod creation."""
        mock_core_api = MagicMock()
        mock_pod = MagicMock()
        mock_pod.metadata.uid = "new-pod-uid"
        mock_core_api.create_namespaced_pod.return_value = mock_pod

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            with patch("src.services.kubernetes.pool.create_pod_manifest", return_value={}):
                with patch.object(pod_pool, "_wait_for_pod_ready", return_value=True):
                    result = await pod_pool._create_warm_pod()

        assert result is not None
        assert "new-pod-uid" in pod_pool._pods

    @pytest.mark.asyncio
    async def test_create_warm_pod_no_core_api(self, pod_pool):
        """Test pod creation when core API is not available."""
        with patch("src.services.kubernetes.pool.get_core_api", return_value=None):
            result = await pod_pool._create_warm_pod()

        assert result is None

    @pytest.mark.asyncio
    async def test_create_warm_pod_not_ready(self, pod_pool):
        """Test pod creation when pod doesn't become ready."""
        mock_core_api = MagicMock()
        mock_pod = MagicMock()
        mock_pod.metadata.uid = "new-pod-uid"
        mock_core_api.create_namespaced_pod.return_value = mock_pod

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            with patch("src.services.kubernetes.pool.create_pod_manifest", return_value={}):
                with patch.object(pod_pool, "_wait_for_pod_ready", return_value=False):
                    with patch.object(pod_pool, "_delete_pod", new_callable=AsyncMock):
                        result = await pod_pool._create_warm_pod()

        assert result is None

    @pytest.mark.asyncio
    async def test_create_warm_pod_api_exception(self, pod_pool):
        """Test pod creation with API exception."""
        mock_core_api = MagicMock()
        mock_core_api.create_namespaced_pod.side_effect = ApiException(status=500)

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            with patch("src.services.kubernetes.pool.create_pod_manifest", return_value={}):
                result = await pod_pool._create_warm_pod()

        assert result is None


class TestPodPoolWaitForPodReady:
    """Tests for _wait_for_pod_ready method."""

    @pytest.mark.asyncio
    async def test_wait_for_pod_ready_success(self, pod_pool, pod_handle):
        """Test waiting for pod to be ready."""
        mock_core_api = MagicMock()
        mock_pod = MagicMock()
        mock_pod.status.pod_ip = "10.0.0.1"
        mock_pod.status.phase = "Running"
        mock_container_status = MagicMock()
        mock_container_status.name = "main"
        mock_container_status.ready = True
        mock_pod.status.container_statuses = [mock_container_status]
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            result = await pod_pool._wait_for_pod_ready(pod_handle, timeout=5)

        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_pod_ready_no_core_api(self, pod_pool, pod_handle):
        """Test waiting when core API is not available."""
        with patch("src.services.kubernetes.pool.get_core_api", return_value=None):
            result = await pod_pool._wait_for_pod_ready(pod_handle, timeout=1)

        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_pod_ready_failed(self, pod_pool, pod_handle):
        """Test waiting when pod fails."""
        mock_core_api = MagicMock()
        mock_pod = MagicMock()
        mock_pod.status.pod_ip = "10.0.0.1"
        mock_pod.status.phase = "Failed"
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            result = await pod_pool._wait_for_pod_ready(pod_handle, timeout=5)

        assert result is False


class TestPodPoolDeletePod:
    """Tests for _delete_pod method."""

    @pytest.mark.asyncio
    async def test_delete_pod_success(self, pod_pool, pod_handle):
        """Test successful pod deletion."""
        mock_core_api = MagicMock()

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            await pod_pool._delete_pod(pod_handle)

        mock_core_api.delete_namespaced_pod.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_pod_no_core_api(self, pod_pool, pod_handle):
        """Test deletion when core API is not available."""
        with patch("src.services.kubernetes.pool.get_core_api", return_value=None):
            # Should not raise
            await pod_pool._delete_pod(pod_handle)

    @pytest.mark.asyncio
    async def test_delete_pod_not_found(self, pod_pool, pod_handle):
        """Test deletion when pod not found."""
        mock_core_api = MagicMock()
        mock_core_api.delete_namespaced_pod.side_effect = ApiException(status=404)

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            # Should not raise for 404
            await pod_pool._delete_pod(pod_handle)


class TestPodPoolAcquire:
    """Tests for acquire method."""

    @pytest.mark.asyncio
    async def test_acquire_success(self, pod_pool, pooled_pod):
        """Test successful pod acquisition."""
        pod_pool._pods[pooled_pod.handle.uid] = pooled_pod
        await pod_pool._available.put(pooled_pod.handle.uid)

        result = await pod_pool.acquire("session-123", timeout=5)

        assert result is not None
        assert result.status == PodStatus.EXECUTING
        assert pooled_pod.acquired is True

    @pytest.mark.asyncio
    async def test_acquire_timeout(self, pod_pool):
        """Test acquisition timeout."""
        result = await pod_pool.acquire("session-123", timeout=0.1)

        assert result is None

    @pytest.mark.asyncio
    async def test_acquire_pod_not_in_pool(self, pod_pool):
        """Test acquisition when pod is no longer in pool."""
        # Put a uid in the queue but not in _pods
        await pod_pool._available.put("missing-uid")

        result = await pod_pool.acquire("session-123", timeout=1)

        assert result is None


class TestPodPoolRelease:
    """Tests for release method."""

    @pytest.mark.asyncio
    async def test_release_with_destroy(self, pod_pool, pooled_pod):
        """Test releasing a pod with destruction."""
        pooled_pod.handle.session_id = "session-123"
        pod_pool._pods[pooled_pod.handle.uid] = pooled_pod
        pod_pool._session_pods["session-123"] = pooled_pod.handle.uid

        with patch.object(pod_pool, "_delete_pod", new_callable=AsyncMock) as mock_delete:
            await pod_pool.release(pooled_pod.handle, destroy=True)

            mock_delete.assert_called_once()
            assert pooled_pod.handle.uid not in pod_pool._pods

    @pytest.mark.asyncio
    async def test_release_without_destroy(self, pod_pool, pooled_pod):
        """Test releasing a pod back to pool."""
        pooled_pod.handle.session_id = "session-123"
        pooled_pod.acquired = True
        pod_pool._pods[pooled_pod.handle.uid] = pooled_pod
        pod_pool._session_pods["session-123"] = pooled_pod.handle.uid

        await pod_pool.release(pooled_pod.handle, destroy=False)

        assert pooled_pod.acquired is False
        assert pooled_pod.handle.status == PodStatus.WARM

    @pytest.mark.asyncio
    async def test_release_pod_not_in_pool(self, pod_pool, pod_handle):
        """Test releasing a pod that's not in pool."""
        # Should not raise
        await pod_pool.release(pod_handle, destroy=True)


class TestPodPoolExecute:
    """Tests for execute method."""

    @pytest.mark.asyncio
    async def test_execute_success(self, pod_pool, pod_handle):
        """Test successful code execution."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "exit_code": 0,
            "stdout": "Hello",
            "stderr": "",
            "execution_time_ms": 100,
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            result = await pod_pool.execute(pod_handle, "print('Hello')")

        assert result.exit_code == 0
        assert result.stdout == "Hello"

    @pytest.mark.asyncio
    async def test_execute_no_pod_ip(self, pod_pool, pod_handle):
        """Test execution without pod IP."""
        pod_handle.pod_ip = None

        result = await pod_pool.execute(pod_handle, "print('test')")

        assert result.exit_code == 1
        assert "Pod not ready" in result.stderr

    @pytest.mark.asyncio
    async def test_execute_with_files(self, pod_pool, pod_handle):
        """Test execution with file uploads."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "exit_code": 0,
            "stdout": "OK",
            "stderr": "",
            "execution_time_ms": 50,
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        files = [FileData(filename="test.py", content=b"print('test')")]

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            result = await pod_pool.execute(pod_handle, "exec(open('test.py').read())", files=files)

        assert result.exit_code == 0
        # File upload + execute = 2 calls
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_runner_error(self, pod_pool, pod_handle):
        """Test execution with runner error."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            result = await pod_pool.execute(pod_handle, "print('test')")

        assert result.exit_code == 1
        assert "Runner error" in result.stderr

    @pytest.mark.asyncio
    async def test_execute_timeout(self, pod_pool, pod_handle):
        """Test execution timeout."""
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            result = await pod_pool.execute(pod_handle, "import time; time.sleep(100)")

        assert result.exit_code == 124
        assert "timed out" in result.stderr

    @pytest.mark.asyncio
    async def test_execute_generic_exception(self, pod_pool, pod_handle):
        """Test execution with generic exception."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection error"))

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            result = await pod_pool.execute(pod_handle, "print('test')")

        assert result.exit_code == 1
        assert "Execution error" in result.stderr


class TestPodPoolProperties:
    """Tests for pool properties."""

    def test_available_count(self, pod_pool, pooled_pod):
        """Test available_count property."""
        pooled_pod.acquired = False
        pod_pool._pods[pooled_pod.handle.uid] = pooled_pod

        assert pod_pool.available_count == 1

    def test_total_count(self, pod_pool, pooled_pod):
        """Test total_count property."""
        pod_pool._pods[pooled_pod.handle.uid] = pooled_pod

        assert pod_pool.total_count == 1


# PodPoolManager Tests


@pytest.fixture
def pool_manager(pool_config):
    """Create a pool manager instance."""
    with patch("src.services.kubernetes.pool.get_current_namespace", return_value="test-namespace"):
        manager = PodPoolManager(namespace="test-namespace", configs=[pool_config])
        return manager


class TestPodPoolManagerInit:
    """Tests for PodPoolManager initialization."""

    def test_init_with_configs(self, pool_config):
        """Test initialization with configs."""
        with patch("src.services.kubernetes.pool.get_current_namespace", return_value="test-namespace"):
            manager = PodPoolManager(configs=[pool_config])

            assert "python" in manager._pools
            assert "python" in manager._configs

    def test_init_no_configs(self):
        """Test initialization without configs."""
        with patch("src.services.kubernetes.pool.get_current_namespace", return_value="test-namespace"):
            manager = PodPoolManager()

            assert len(manager._pools) == 0


class TestPodPoolManagerStartStop:
    """Tests for start and stop methods."""

    @pytest.mark.asyncio
    async def test_start(self, pool_manager):
        """Test starting all pools."""
        for pool in pool_manager._pools.values():
            pool.start = AsyncMock()

        await pool_manager.start()

        for pool in pool_manager._pools.values():
            pool.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop(self, pool_manager):
        """Test stopping all pools."""
        for pool in pool_manager._pools.values():
            pool.stop = AsyncMock()

        await pool_manager.stop()

        for pool in pool_manager._pools.values():
            pool.stop.assert_called_once()


class TestPodPoolManagerGetPool:
    """Tests for get_pool method."""

    def test_get_pool_exists(self, pool_manager):
        """Test getting an existing pool."""
        pool = pool_manager.get_pool("python")

        assert pool is not None

    def test_get_pool_not_exists(self, pool_manager):
        """Test getting a non-existing pool."""
        pool = pool_manager.get_pool("rust")

        assert pool is None


class TestPodPoolManagerGetConfig:
    """Tests for get_config method."""

    def test_get_config_exists(self, pool_manager):
        """Test getting an existing config."""
        config = pool_manager.get_config("python")

        assert config is not None
        assert config.language == "python"

    def test_get_config_not_exists(self, pool_manager):
        """Test getting a non-existing config."""
        config = pool_manager.get_config("rust")

        assert config is None


class TestPodPoolManagerUsesPool:
    """Tests for uses_pool method."""

    def test_uses_pool_true(self, pool_manager):
        """Test uses_pool for language with pool."""
        assert pool_manager.uses_pool("python") is True

    def test_uses_pool_false(self, pool_manager):
        """Test uses_pool for language without pool."""
        assert pool_manager.uses_pool("rust") is False


class TestPodPoolManagerAcquire:
    """Tests for acquire method."""

    @pytest.mark.asyncio
    async def test_acquire_success(self, pool_manager, pod_handle):
        """Test successful acquisition."""
        pool = pool_manager._pools["python"]
        pool.acquire = AsyncMock(return_value=pod_handle)

        result = await pool_manager.acquire("python", "session-123")

        assert result is pod_handle

    @pytest.mark.asyncio
    async def test_acquire_no_pool(self, pool_manager):
        """Test acquisition when pool doesn't exist."""
        result = await pool_manager.acquire("rust", "session-123")

        assert result is None


class TestPodPoolManagerRelease:
    """Tests for release method."""

    @pytest.mark.asyncio
    async def test_release(self, pool_manager, pod_handle):
        """Test releasing a pod."""
        pool = pool_manager._pools["python"]
        pool.release = AsyncMock()

        await pool_manager.release(pod_handle)

        pool.release.assert_called_once_with(pod_handle, True)


class TestPodPoolManagerExecute:
    """Tests for execute method."""

    @pytest.mark.asyncio
    async def test_execute_success(self, pool_manager, pod_handle):
        """Test successful execution."""
        mock_result = ExecutionResult(
            exit_code=0,
            stdout="Hello",
            stderr="",
            execution_time_ms=100,
        )
        pool = pool_manager._pools["python"]
        pool.execute = AsyncMock(return_value=mock_result)

        result = await pool_manager.execute(pod_handle, "print('Hello')")

        assert result.exit_code == 0
        assert result.stdout == "Hello"

    @pytest.mark.asyncio
    async def test_execute_no_pool(self, pool_manager, pod_handle):
        """Test execution when pool doesn't exist."""
        pod_handle.language = "rust"

        result = await pool_manager.execute(pod_handle, "println!('test')")

        assert result.exit_code == 1
        assert "No pool" in result.stderr


class TestPodPoolManagerGetPoolStats:
    """Tests for get_pool_stats method."""

    def test_get_pool_stats(self, pool_manager):
        """Test getting pool statistics."""
        stats = pool_manager.get_pool_stats()

        assert "python" in stats
        assert "available" in stats["python"]
        assert "total" in stats["python"]
        assert "target" in stats["python"]


class TestPodPoolStopWithHttpClient:
    """Tests for stop method with HTTP client cleanup."""

    @pytest.mark.asyncio
    async def test_stop_closes_http_client(self, pod_pool):
        """Test that stop closes HTTP client."""
        # Create an http client
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        pod_pool._http_client = mock_client

        await pod_pool.stop()

        mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_handles_cancelled_error_in_health_check_task(self, pod_pool):
        """Test that stop handles CancelledError for health_check_task."""
        pod_pool._running = True

        # Create tasks that will be cancelled
        async def dummy_loop():
            await asyncio.sleep(100)

        pod_pool._replenish_task = asyncio.create_task(dummy_loop())
        pod_pool._health_check_task = asyncio.create_task(dummy_loop())

        await pod_pool.stop()

        assert pod_pool._running is False
        assert pod_pool._replenish_task.cancelled()
        assert pod_pool._health_check_task.cancelled()


class TestPodPoolWaitForPodReadyExtended:
    """Extended tests for _wait_for_pod_ready method."""

    @pytest.mark.asyncio
    async def test_wait_for_pod_ready_api_exception(self, pod_pool, pod_handle):
        """Test waiting when API throws exception."""
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_pod.side_effect = ApiException(status=500)

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            result = await pod_pool._wait_for_pod_ready(pod_handle, timeout=1)

        # Should timeout and return False after handling exceptions
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_pod_ready_timeout(self, pod_pool, pod_handle):
        """Test waiting times out when pod never becomes ready."""
        mock_core_api = MagicMock()
        mock_pod = MagicMock()
        mock_pod.status.pod_ip = "10.0.0.1"
        mock_pod.status.phase = "Pending"  # Never becomes Running
        mock_pod.status.container_statuses = None
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            result = await pod_pool._wait_for_pod_ready(pod_handle, timeout=1)

        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_pod_ready_main_not_ready(self, pod_pool, pod_handle):
        """Test waiting when main container not ready."""
        mock_core_api = MagicMock()
        mock_pod = MagicMock()
        mock_pod.status.pod_ip = "10.0.0.1"
        mock_pod.status.phase = "Running"
        mock_container_status = MagicMock()
        mock_container_status.name = "main"
        mock_container_status.ready = False
        mock_pod.status.container_statuses = [mock_container_status]
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            result = await pod_pool._wait_for_pod_ready(pod_handle, timeout=1)

        # Should timeout since main container is not ready
        assert result is False


class TestPodPoolDeletePodExtended:
    """Extended tests for _delete_pod method."""

    @pytest.mark.asyncio
    async def test_delete_pod_other_api_exception(self, pod_pool, pod_handle):
        """Test deletion with non-404 API exception logs warning."""
        mock_core_api = MagicMock()
        mock_core_api.delete_namespaced_pod.side_effect = ApiException(status=500)

        with patch("src.services.kubernetes.pool.get_core_api", return_value=mock_core_api):
            # Should not raise, just log warning
            await pod_pool._delete_pod(pod_handle)


class TestPodPoolReplenishLoop:
    """Tests for _replenish_loop method."""

    @pytest.mark.asyncio
    async def test_replenish_loop_creates_pods_when_below_target(self, pod_pool):
        """Test replenish loop creates pods when below target."""
        pod_pool._running = True
        call_count = 0

        async def mock_create():
            nonlocal call_count
            call_count += 1
            # Stop after first batch
            if call_count >= 2:
                pod_pool._running = False
            return None

        with patch.object(pod_pool, "_create_warm_pod", side_effect=mock_create):
            # Signal the event to wake the loop immediately
            pod_pool._signal_replenish()
            try:
                await asyncio.wait_for(pod_pool._replenish_loop(), timeout=2)
            except TimeoutError:
                pod_pool._running = False

        assert call_count > 0

    @pytest.mark.asyncio
    async def test_replenish_loop_handles_exception(self, pod_pool):
        """Test replenish loop handles exception gracefully."""
        pod_pool._running = True
        call_count = 0

        async def mock_create():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pod_pool._running = False
            raise Exception("Create failed")

        with patch.object(pod_pool, "_create_warm_pod", side_effect=mock_create):
            pod_pool._signal_replenish()
            try:
                await asyncio.wait_for(pod_pool._replenish_loop(), timeout=2)
            except TimeoutError:
                pod_pool._running = False

    @pytest.mark.asyncio
    async def test_replenish_loop_cancelled_error(self, pod_pool):
        """Test replenish loop handles CancelledError."""
        pod_pool._running = True

        # Start the loop and cancel it
        task = asyncio.create_task(pod_pool._replenish_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestPodPoolHealthCheckLoop:
    """Tests for _health_check_loop method."""

    @pytest.mark.asyncio
    async def test_health_check_loop_healthy_pod(self, pod_pool, pooled_pod):
        """Test health check loop for healthy pod."""
        pod_pool._running = True
        pod_pool._pods[pooled_pod.handle.uid] = pooled_pod
        iteration = 0

        async def mock_sleep(_):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                pod_pool._running = False

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            with patch("asyncio.sleep", side_effect=mock_sleep):
                await pod_pool._health_check_loop()

        assert pooled_pod.health_check_failures == 0

    @pytest.mark.asyncio
    async def test_health_check_loop_unhealthy_pod(self, pod_pool, pooled_pod):
        """Test health check loop removes unhealthy pod."""
        pod_pool._running = True
        pooled_pod.health_check_failures = 2  # One more failure will trigger removal
        pod_pool._pods[pooled_pod.handle.uid] = pooled_pod
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
        assert pooled_pod.handle.uid not in pod_pool._pods

    @pytest.mark.asyncio
    async def test_health_check_loop_exception(self, pod_pool, pooled_pod):
        """Test health check loop handles exception on health check."""
        pod_pool._running = True
        pod_pool._pods[pooled_pod.handle.uid] = pooled_pod
        iteration = 0

        async def mock_sleep(_):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                pod_pool._running = False

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            with patch("asyncio.sleep", side_effect=mock_sleep):
                await pod_pool._health_check_loop()

        # Should increment failure count
        assert pooled_pod.health_check_failures >= 1

    @pytest.mark.asyncio
    async def test_health_check_loop_cancelled_error(self, pod_pool):
        """Test health check loop handles CancelledError."""
        pod_pool._running = True

        async def mock_sleep(_):
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep):
            # Should break out of loop on CancelledError
            await pod_pool._health_check_loop()

    @pytest.mark.asyncio
    async def test_health_check_loop_outer_exception(self, pod_pool, pooled_pod):
        """Test health check loop handles outer exception gracefully."""
        pod_pool._running = True
        pod_pool._pods[pooled_pod.handle.uid] = pooled_pod
        iteration = 0

        async def mock_sleep(_):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                pod_pool._running = False

        with patch.object(pod_pool, "_get_http_client", side_effect=Exception("Client error")):
            with patch("asyncio.sleep", side_effect=mock_sleep):
                # Should not raise
                await pod_pool._health_check_loop()


class TestPodPoolExecuteExtended:
    """Extended tests for execute method."""

    @pytest.mark.asyncio
    async def test_execute_with_file_upload_failure(self, pod_pool, pod_handle):
        """Test execution with file upload failure."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "exit_code": 0,
            "stdout": "OK",
            "stderr": "",
            "execution_time_ms": 50,
        }

        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/files" in url:
                raise Exception("Upload failed")
            return mock_response

        mock_client.post = mock_post

        files = [FileData(filename="test.py", content=b"print('test')")]

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            result = await pod_pool.execute(pod_handle, "print('test')", files=files)

        # Should still try to execute even if file upload fails
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_with_initial_state(self, pod_pool, pod_handle):
        """Test execution with initial state."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "exit_code": 0,
            "stdout": "restored",
            "stderr": "",
            "execution_time_ms": 50,
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            result = await pod_pool.execute(
                pod_handle,
                "print('test')",
                initial_state="base64encodedstate",
            )

        assert result.exit_code == 0
        # Verify initial_state was included in request
        call_args = mock_client.post.call_args
        assert "initial_state" in call_args.kwargs["json"]

    @pytest.mark.asyncio
    async def test_execute_with_capture_state(self, pod_pool, pod_handle):
        """Test execution with capture state."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "exit_code": 0,
            "stdout": "OK",
            "stderr": "",
            "execution_time_ms": 50,
            "state": "newstate",
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            result = await pod_pool.execute(
                pod_handle,
                "x = 1",
                capture_state=True,
            )

        assert result.exit_code == 0
        assert result.state == "newstate"
        # Verify capture_state was included in request
        call_args = mock_client.post.call_args
        assert call_args.kwargs["json"]["capture_state"] is True

    @pytest.mark.asyncio
    async def test_execute_with_state_and_state_errors(self, pod_pool, pod_handle):
        """Test execution returns state_errors from response."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "execution_time_ms": 50,
            "state": "partialstate",
            "state_errors": ["Warning: large object skipped"],
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(pod_pool, "_get_http_client", return_value=mock_client):
            result = await pod_pool.execute(pod_handle, "x = 1", capture_state=True)

        assert result.state_errors == ["Warning: large object skipped"]


class TestPoolConfigResources:
    """Tests for PoolConfig per-language resource configuration."""

    def test_pool_config_default_resources(self):
        """Test PoolConfig has default resource values."""
        config = PoolConfig(
            language="python",
            image="python:3.12",
            pool_size=5,
        )
        assert config.cpu_limit is None
        assert config.memory_limit is None

    def test_pool_config_custom_resources(self):
        """Test PoolConfig accepts custom resource values."""
        config = PoolConfig(
            language="go",
            image="golang:1.22",
            pool_size=2,
            cpu_limit="2",
            memory_limit="1Gi",
        )
        assert config.cpu_limit == "2"
        assert config.memory_limit == "1Gi"


class TestSettingsPerLanguageResources:
    """Tests for Settings.get_pool_configs with per-language resources."""

    def test_get_pool_configs_uses_env_var_resources(self):
        """Test get_pool_configs reads per-language resources from env vars."""
        import os

        from src.config import Settings

        env_vars = {
            "LANG_CPU_LIMIT_GO": "2",
            "LANG_MEMORY_LIMIT_GO": "1Gi",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            settings = Settings()
            configs = settings.get_pool_configs()

        go_config = next(c for c in configs if c.language == "go")
        assert go_config.cpu_limit == "2"
        assert go_config.memory_limit == "1Gi"

    def test_get_pool_configs_falls_back_to_global_defaults(self):
        """Test get_pool_configs falls back to global defaults when no env vars."""
        import os

        from src.config import Settings

        # Clear any per-language env vars
        env_vars_to_clear = [
            "LANG_CPU_LIMIT_PY",
            "LANG_MEMORY_LIMIT_PY",
        ]

        with patch.dict(os.environ, {}, clear=False):
            for key in env_vars_to_clear:
                os.environ.pop(key, None)

            settings = Settings(
                k8s_cpu_limit="750m",
                k8s_memory_limit="768Mi",
            )
            configs = settings.get_pool_configs()

        py_config = next(c for c in configs if c.language == "py")
        assert py_config.cpu_limit == "750m"
        assert py_config.memory_limit == "768Mi"

    def test_get_pool_configs_different_resources_per_language(self):
        """Test get_pool_configs supports different resources for each language."""
        import os

        from src.config import Settings

        env_vars = {
            "LANG_CPU_LIMIT_PY": "500m",
            "LANG_MEMORY_LIMIT_PY": "512Mi",
            "LANG_CPU_LIMIT_GO": "2",
            "LANG_MEMORY_LIMIT_GO": "2Gi",
            "LANG_CPU_LIMIT_RS": "4",
            "LANG_MEMORY_LIMIT_RS": "4Gi",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            settings = Settings()
            configs = settings.get_pool_configs()

        py_config = next(c for c in configs if c.language == "py")
        go_config = next(c for c in configs if c.language == "go")
        rs_config = next(c for c in configs if c.language == "rs")

        # Python - smaller resources
        assert py_config.cpu_limit == "500m"
        assert py_config.memory_limit == "512Mi"

        # Go - medium resources
        assert go_config.cpu_limit == "2"
        assert go_config.memory_limit == "2Gi"

        # Rust - larger resources
        assert rs_config.cpu_limit == "4"
        assert rs_config.memory_limit == "4Gi"
