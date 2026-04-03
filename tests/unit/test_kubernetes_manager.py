"""Unit tests for Kubernetes Manager."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.kubernetes.manager import KubernetesManager
from src.services.kubernetes.models import ExecutionResult, FileData, PodHandle, PoolConfig


@pytest.fixture
def mock_pool_manager():
    """Create a mock pool manager."""
    manager = MagicMock()
    manager.start = AsyncMock()
    manager.stop = AsyncMock()
    manager.acquire = AsyncMock()
    manager.release = AsyncMock()
    manager.execute = AsyncMock()
    manager.get_pool_stats = MagicMock(return_value={})
    manager.get_config = MagicMock(return_value=None)
    manager.uses_pool = MagicMock(return_value=False)
    return manager


@pytest.fixture
def mock_job_executor():
    """Create a mock job executor."""
    executor = MagicMock()
    executor.execute_with_job = AsyncMock()
    executor.close = AsyncMock()
    return executor


@pytest.fixture
def kubernetes_manager(mock_pool_manager, mock_job_executor):
    """Create a Kubernetes manager with mocked dependencies."""
    with patch("src.services.kubernetes.manager.get_current_namespace", return_value="test-ns"):
        with patch("src.services.kubernetes.manager.PodPoolManager", return_value=mock_pool_manager):
            with patch("src.services.kubernetes.manager.JobExecutor", return_value=mock_job_executor):
                manager = KubernetesManager()
                manager._pool_manager = mock_pool_manager
                manager._job_executor = mock_job_executor
                return manager


@pytest.fixture
def sample_pod_handle():
    """Create a sample pod handle."""
    return PodHandle(
        name="test-pod",
        namespace="test-ns",
        uid="test-uid-123",
        pod_ip="10.0.0.1",
        session_id="session-123",
        language="python",
        runner_port=8080,
    )


@pytest.fixture
def sample_execution_result():
    """Create a sample execution result."""
    return ExecutionResult(
        stdout="Hello, World!",
        stderr="",
        exit_code=0,
        execution_time_ms=50,
    )


class TestKubernetesManagerInit:
    """Tests for KubernetesManager initialization."""

    def test_init_defaults(self):
        """Test initialization with default values."""
        with patch("src.services.kubernetes.manager.get_current_namespace", return_value="default-ns"):
            with patch("src.services.kubernetes.manager.PodPoolManager"):
                with patch("src.services.kubernetes.manager.JobExecutor"):
                    manager = KubernetesManager()

        assert manager.namespace == "default-ns"
        assert manager._started is False

    def test_init_with_namespace(self):
        """Test initialization with custom namespace."""
        with patch("src.services.kubernetes.manager.PodPoolManager"):
            with patch("src.services.kubernetes.manager.JobExecutor"):
                manager = KubernetesManager(namespace="custom-ns")

        assert manager.namespace == "custom-ns"

    def test_init_with_pool_configs(self):
        """Test initialization with pool configs."""
        configs = [
            PoolConfig(language="python", image="python:3.12", pool_size=5),
        ]
        with patch("src.services.kubernetes.manager.PodPoolManager") as mock_pool_cls:
            with patch("src.services.kubernetes.manager.JobExecutor"):
                manager = KubernetesManager(pool_configs=configs)

        mock_pool_cls.assert_called_once()
        call_kwargs = mock_pool_cls.call_args[1]
        assert call_kwargs["configs"] == configs

    def test_init_with_network_isolated(self):
        """Test initialization with network_isolated parameter."""
        with patch("src.services.kubernetes.manager.PodPoolManager"):
            with patch("src.services.kubernetes.manager.JobExecutor"):
                manager = KubernetesManager(network_isolated=True)

        assert manager.network_isolated is True

    def test_init_network_isolated_default_false(self):
        """Test that network_isolated defaults to False."""
        with patch("src.services.kubernetes.manager.get_current_namespace", return_value="default-ns"):
            with patch("src.services.kubernetes.manager.PodPoolManager"):
                with patch("src.services.kubernetes.manager.JobExecutor"):
                    manager = KubernetesManager()

        assert manager.network_isolated is False


class TestStart:
    """Tests for start method."""

    @pytest.mark.asyncio
    async def test_start_initializes_pool(self, kubernetes_manager, mock_pool_manager):
        """Test that start initializes the pool manager."""
        await kubernetes_manager.start()

        mock_pool_manager.start.assert_called_once()
        assert kubernetes_manager._started is True

    @pytest.mark.asyncio
    async def test_start_only_once(self, kubernetes_manager, mock_pool_manager):
        """Test that start only runs once."""
        kubernetes_manager._started = True

        await kubernetes_manager.start()

        mock_pool_manager.start.assert_not_called()


class TestStop:
    """Tests for stop method."""

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, kubernetes_manager, mock_pool_manager, mock_job_executor):
        """Test that stop cleans up resources."""
        kubernetes_manager._started = True

        await kubernetes_manager.stop()

        mock_pool_manager.stop.assert_called_once()
        mock_job_executor.close.assert_called_once()
        assert kubernetes_manager._started is False

    @pytest.mark.asyncio
    async def test_stop_destroys_active_handles(self, kubernetes_manager, mock_pool_manager, sample_pod_handle):
        """Test that stop destroys active handles."""
        kubernetes_manager._active_handles["session-123"] = sample_pod_handle

        await kubernetes_manager.stop()

        mock_pool_manager.release.assert_called()


class TestIsAvailable:
    """Tests for is_available method."""

    def test_is_available_true(self, kubernetes_manager):
        """Test is_available returns True when K8s is available."""
        with patch("src.services.kubernetes.manager.k8s_available", return_value=True):
            result = kubernetes_manager.is_available()

        assert result is True

    def test_is_available_false(self, kubernetes_manager):
        """Test is_available returns False when K8s is not available."""
        with patch("src.services.kubernetes.manager.k8s_available", return_value=False):
            result = kubernetes_manager.is_available()

        assert result is False


class TestGetInitializationError:
    """Tests for get_initialization_error method."""

    def test_get_error(self, kubernetes_manager):
        """Test getting initialization error."""
        with patch(
            "src.services.kubernetes.manager.get_initialization_error",
            return_value="Test error",
        ):
            result = kubernetes_manager.get_initialization_error()

        assert result == "Test error"


class TestGetImageForLanguage:
    """Tests for get_image_for_language method."""

    def test_get_image_from_pool_config(self, kubernetes_manager, mock_pool_manager):
        """Test getting image from pool config."""
        mock_config = MagicMock()
        mock_config.image = "custom-python:latest"
        mock_pool_manager.get_config.return_value = mock_config

        result = kubernetes_manager.get_image_for_language("python")

        assert result == "custom-python:latest"

    def test_get_image_from_default_mapping(self, kubernetes_manager, mock_pool_manager):
        """Test getting image from default mapping."""
        mock_pool_manager.get_config.return_value = None

        result = kubernetes_manager.get_image_for_language("python")

        assert "python" in result.lower()

    def test_get_image_unknown_language(self, kubernetes_manager, mock_pool_manager):
        """Test getting image for unknown language."""
        mock_pool_manager.get_config.return_value = None

        result = kubernetes_manager.get_image_for_language("unknown")

        assert "unknown" in result


class TestUsesPool:
    """Tests for uses_pool method."""

    def test_uses_pool_true(self, kubernetes_manager, mock_pool_manager):
        """Test uses_pool returns True for pooled language."""
        mock_pool_manager.uses_pool.return_value = True

        result = kubernetes_manager.uses_pool("python")

        assert result is True

    def test_uses_pool_false(self, kubernetes_manager, mock_pool_manager):
        """Test uses_pool returns False for non-pooled language."""
        mock_pool_manager.uses_pool.return_value = False

        result = kubernetes_manager.uses_pool("go")

        assert result is False


class TestAcquirePod:
    """Tests for acquire_pod method."""

    @pytest.mark.asyncio
    async def test_acquire_pod_success(self, kubernetes_manager, mock_pool_manager, sample_pod_handle):
        """Test successful pod acquisition from pool."""
        mock_pool_manager.uses_pool.return_value = True
        mock_pool_manager.acquire.return_value = sample_pod_handle

        handle, source = await kubernetes_manager.acquire_pod("session-123", "python")

        assert handle is sample_pod_handle
        assert source == "pool_hit"
        assert "session-123" in kubernetes_manager._active_handles

    @pytest.mark.asyncio
    async def test_acquire_pod_pool_miss(self, kubernetes_manager, mock_pool_manager):
        """Test pod acquisition when pool is empty."""
        mock_pool_manager.uses_pool.return_value = True
        mock_pool_manager.acquire.return_value = None

        handle, source = await kubernetes_manager.acquire_pod("session-123", "python")

        assert handle is None
        assert source == "pool_miss"

    @pytest.mark.asyncio
    async def test_acquire_pod_no_pool(self, kubernetes_manager, mock_pool_manager):
        """Test pod acquisition for non-pooled language."""
        mock_pool_manager.uses_pool.return_value = False

        handle, source = await kubernetes_manager.acquire_pod("session-123", "go")

        assert handle is None
        assert source == "pool_miss"

    @pytest.mark.asyncio
    async def test_acquire_pod_normalizes_language(self, kubernetes_manager, mock_pool_manager):
        """Test that acquire_pod normalizes language aliases."""
        mock_pool_manager.uses_pool.return_value = True
        mock_pool_manager.acquire.return_value = None

        await kubernetes_manager.acquire_pod("session-123", "Python")

        # Should normalize to 'py'
        mock_pool_manager.uses_pool.assert_called_with("py")


class TestExecuteCode:
    """Tests for execute_code method."""

    @pytest.mark.asyncio
    async def test_execute_code_with_pool(
        self,
        kubernetes_manager,
        mock_pool_manager,
        sample_pod_handle,
        sample_execution_result,
    ):
        """Test code execution using pool."""
        mock_pool_manager.uses_pool.return_value = True
        mock_pool_manager.acquire.return_value = sample_pod_handle
        mock_pool_manager.execute.return_value = sample_execution_result

        result, handle, source = await kubernetes_manager.execute_code(
            session_id="session-123",
            code="print('hello')",
            language="python",
        )

        assert result is sample_execution_result
        assert handle is sample_pod_handle
        assert source == "pool_hit"

    @pytest.mark.asyncio
    async def test_execute_code_with_job(
        self, kubernetes_manager, mock_pool_manager, mock_job_executor, sample_execution_result
    ):
        """Test code execution using Job."""
        mock_pool_manager.uses_pool.return_value = False
        mock_pool_manager.acquire.return_value = None
        mock_job_executor.execute_with_job.return_value = sample_execution_result

        result, handle, source = await kubernetes_manager.execute_code(
            session_id="session-123",
            code='println!("hello")',
            language="rust",
        )

        assert result is sample_execution_result
        assert handle is None
        assert source == "job"
        mock_job_executor.execute_with_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_code_with_files(
        self,
        kubernetes_manager,
        mock_pool_manager,
        sample_pod_handle,
        sample_execution_result,
    ):
        """Test code execution with files."""
        mock_pool_manager.uses_pool.return_value = True
        mock_pool_manager.acquire.return_value = sample_pod_handle
        mock_pool_manager.execute.return_value = sample_execution_result

        files = [{"filename": "data.txt", "content": b"test content"}]

        result, handle, source = await kubernetes_manager.execute_code(
            session_id="session-123",
            code="print('hello')",
            language="python",
            files=files,
        )

        assert result is sample_execution_result
        # Verify files were converted to FileData
        execute_call = mock_pool_manager.execute.call_args
        assert execute_call is not None


class TestDestroyPod:
    """Tests for destroy_pod method."""

    @pytest.mark.asyncio
    async def test_destroy_pod(self, kubernetes_manager, mock_pool_manager, sample_pod_handle):
        """Test destroying a pod."""
        kubernetes_manager._active_handles["session-123"] = sample_pod_handle

        await kubernetes_manager.destroy_pod(sample_pod_handle)

        mock_pool_manager.release.assert_called_once_with(sample_pod_handle, destroy=True)
        assert "session-123" not in kubernetes_manager._active_handles

    @pytest.mark.asyncio
    async def test_destroy_pod_none(self, kubernetes_manager, mock_pool_manager):
        """Test destroying None handle."""
        await kubernetes_manager.destroy_pod(None)

        mock_pool_manager.release.assert_not_called()


class TestCopyFilesToPod:
    """Tests for copy_files_to_pod method."""

    @pytest.mark.asyncio
    async def test_copy_files_success(self, kubernetes_manager, sample_pod_handle):
        """Test successful file copy."""
        files = [FileData(filename="test.txt", content=b"content")]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock()
            mock_client_cls.return_value = mock_client

            result = await kubernetes_manager.copy_files_to_pod(sample_pod_handle, files)

        assert result is True

    @pytest.mark.asyncio
    async def test_copy_files_no_pod_ip(self, kubernetes_manager, sample_pod_handle):
        """Test file copy fails without pod IP."""
        sample_pod_handle.pod_ip = None
        files = [FileData(filename="test.txt", content=b"content")]

        result = await kubernetes_manager.copy_files_to_pod(sample_pod_handle, files)

        assert result is False

    @pytest.mark.asyncio
    async def test_copy_files_http_error(self, kubernetes_manager, sample_pod_handle):
        """Test file copy handles HTTP errors."""
        files = [FileData(filename="test.txt", content=b"content")]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(side_effect=Exception("Connection failed"))
            mock_client_cls.return_value = mock_client

            result = await kubernetes_manager.copy_files_to_pod(sample_pod_handle, files)

        assert result is False


class TestCopyFileFromPod:
    """Tests for copy_file_from_pod method."""

    @pytest.mark.asyncio
    async def test_copy_file_success(self, kubernetes_manager, sample_pod_handle):
        """Test successful file retrieval."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"file content"
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await kubernetes_manager.copy_file_from_pod(sample_pod_handle, "test.txt")

        assert result == b"file content"

    @pytest.mark.asyncio
    async def test_copy_file_not_found(self, kubernetes_manager, sample_pod_handle):
        """Test file retrieval for non-existent file."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await kubernetes_manager.copy_file_from_pod(sample_pod_handle, "missing.txt")

        assert result is None

    @pytest.mark.asyncio
    async def test_copy_file_no_pod_ip(self, kubernetes_manager, sample_pod_handle):
        """Test file retrieval fails without pod IP."""
        sample_pod_handle.pod_ip = None

        result = await kubernetes_manager.copy_file_from_pod(sample_pod_handle, "test.txt")

        assert result is None


class TestGetPoolStats:
    """Tests for get_pool_stats method."""

    def test_get_pool_stats(self, kubernetes_manager, mock_pool_manager):
        """Test getting pool statistics."""
        expected_stats = {"python": {"available": 5, "in_use": 2}}
        mock_pool_manager.get_pool_stats.return_value = expected_stats

        result = kubernetes_manager.get_pool_stats()

        assert result == expected_stats
