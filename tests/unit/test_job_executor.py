"""Unit tests for JobExecutor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes.client import ApiException

from src.services.kubernetes.job_executor import JobExecutor
from src.services.kubernetes.models import ExecutionResult, FileData, JobHandle, PodSpec


@pytest.fixture
def job_executor():
    """Create a job executor instance."""
    with patch("src.services.kubernetes.job_executor.get_current_namespace", return_value="test-namespace"):
        executor = JobExecutor(namespace="test-namespace")
        return executor


@pytest.fixture
def pod_spec():
    """Create a pod spec for testing."""
    return PodSpec(
        image="python:3.11",
        language="python",
        session_id="session-123",
        namespace="test-namespace",
        sidecar_image="sidecar:latest",
        cpu_limit="1",
        memory_limit="512Mi",
        cpu_request="100m",
        memory_request="128Mi",
        sidecar_port=8080,
    )


@pytest.fixture
def job_handle():
    """Create a job handle for testing."""
    handle = JobHandle(
        name="test-job",
        namespace="test-namespace",
        uid="test-uid-123",
        language="python",
        session_id="session-123",
    )
    handle.pod_name = "test-job-abc123"
    handle.pod_ip = "10.0.0.1"
    handle.status = "running"
    return handle


class TestJobExecutorInit:
    """Tests for JobExecutor initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default values."""
        with patch("src.services.kubernetes.job_executor.get_current_namespace", return_value="default"):
            executor = JobExecutor()

            assert executor.namespace == "default"
            assert executor.ttl_seconds_after_finished == 60
            assert executor.active_deadline_seconds == 300
            assert executor.sidecar_image == "aronmuon/kubecoderun-sidecar-agent:latest"

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        executor = JobExecutor(
            namespace="custom-ns",
            ttl_seconds_after_finished=120,
            active_deadline_seconds=600,
            sidecar_image="custom/sidecar:v1",
        )

        assert executor.namespace == "custom-ns"
        assert executor.ttl_seconds_after_finished == 120
        assert executor.active_deadline_seconds == 600
        assert executor.sidecar_image == "custom/sidecar:v1"


class TestGenerateJobName:
    """Tests for _generate_job_name method."""

    def test_generate_job_name(self, job_executor):
        """Test generating job name."""
        name = job_executor._generate_job_name("session-123", "python")

        assert name.startswith("exec-python-session-123-")
        assert len(name) <= 63

    def test_generate_job_name_truncates_session_id(self, job_executor):
        """Test that long session IDs are truncated."""
        long_session = "a" * 100
        name = job_executor._generate_job_name(long_session, "python")

        assert len(name) <= 63
        assert "aaaaaaaaaaaa-" in name  # First 12 chars

    def test_generate_job_name_replaces_underscores(self, job_executor):
        """Test that underscores are replaced with hyphens."""
        name = job_executor._generate_job_name("session_with_underscores", "python")

        assert "_" not in name

    def test_generate_job_name_session_lowercase(self, job_executor):
        """Test that session part of name is lowercase."""
        name = job_executor._generate_job_name("SESSION-ABC", "python")

        # The session part is lowercased, language is used as-is
        assert "session-abc" in name


class TestGetHttpClient:
    """Tests for _get_http_client method."""

    @pytest.mark.asyncio
    async def test_creates_http_client(self, job_executor):
        """Test that HTTP client is created."""
        client = await job_executor._get_http_client()

        assert client is not None
        assert not client.is_closed

        await job_executor.close()

    @pytest.mark.asyncio
    async def test_reuses_http_client(self, job_executor):
        """Test that HTTP client is reused."""
        client1 = await job_executor._get_http_client()
        client2 = await job_executor._get_http_client()

        assert client1 is client2

        await job_executor.close()


class TestClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close_with_client(self, job_executor):
        """Test closing with an active client."""
        await job_executor._get_http_client()
        await job_executor.close()

        assert job_executor._http_client.is_closed

    @pytest.mark.asyncio
    async def test_close_without_client(self, job_executor):
        """Test closing without a client."""
        # Should not raise
        await job_executor.close()


class TestCreateJob:
    """Tests for create_job method."""

    @pytest.mark.asyncio
    async def test_create_job_success(self, job_executor, pod_spec):
        """Test successful job creation."""
        mock_batch_api = MagicMock()
        mock_job = MagicMock()
        mock_job.metadata.uid = "job-uid-123"
        mock_batch_api.create_namespaced_job.return_value = mock_job

        with patch("src.services.kubernetes.job_executor.get_batch_api", return_value=mock_batch_api):
            with patch("src.services.kubernetes.job_executor.create_job_manifest", return_value={}):
                job = await job_executor.create_job(pod_spec, "session-123")

        assert job is not None
        assert job.namespace == "test-namespace"
        assert job.language == "python"
        assert job.session_id == "session-123"

    @pytest.mark.asyncio
    async def test_create_job_no_batch_api(self, job_executor, pod_spec):
        """Test job creation when batch API is not available."""
        with patch("src.services.kubernetes.job_executor.get_batch_api", return_value=None):
            with pytest.raises(RuntimeError, match="Kubernetes Batch API not available"):
                await job_executor.create_job(pod_spec, "session-123")

    @pytest.mark.asyncio
    async def test_create_job_api_exception(self, job_executor, pod_spec):
        """Test job creation with API exception."""
        mock_batch_api = MagicMock()
        mock_batch_api.create_namespaced_job.side_effect = ApiException(status=400, reason="Bad Request")

        with patch("src.services.kubernetes.job_executor.get_batch_api", return_value=mock_batch_api):
            with patch("src.services.kubernetes.job_executor.create_job_manifest", return_value={}):
                with pytest.raises(RuntimeError, match="Failed to create job"):
                    await job_executor.create_job(pod_spec, "session-123")


class TestWaitForPodReady:
    """Tests for wait_for_pod_ready method."""

    @pytest.mark.asyncio
    async def test_wait_for_pod_ready_success(self, job_executor, job_handle):
        """Test waiting for pod to be ready."""
        mock_core_api = MagicMock()
        mock_pod = MagicMock()
        mock_pod.metadata.name = "test-pod"
        mock_pod.status.pod_ip = "10.0.0.1"
        mock_pod.status.phase = "Running"
        mock_container_status = MagicMock()
        mock_container_status.name = "sidecar"
        mock_container_status.ready = True
        mock_pod.status.container_statuses = [mock_container_status]

        mock_pod_list = MagicMock()
        mock_pod_list.items = [mock_pod]
        mock_core_api.list_namespaced_pod.return_value = mock_pod_list

        with patch("src.services.kubernetes.job_executor.get_core_api", return_value=mock_core_api):
            result = await job_executor.wait_for_pod_ready(job_handle, timeout=5)

        assert result is True
        assert job_handle.status == "running"

    @pytest.mark.asyncio
    async def test_wait_for_pod_ready_no_core_api(self, job_executor, job_handle):
        """Test waiting when core API is not available."""
        with patch("src.services.kubernetes.job_executor.get_core_api", return_value=None):
            result = await job_executor.wait_for_pod_ready(job_handle, timeout=1)

        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_pod_ready_pod_failed(self, job_executor, job_handle):
        """Test waiting when pod fails."""
        mock_core_api = MagicMock()
        mock_pod = MagicMock()
        mock_pod.metadata.name = "test-pod"
        mock_pod.status.pod_ip = "10.0.0.1"
        mock_pod.status.phase = "Failed"

        mock_pod_list = MagicMock()
        mock_pod_list.items = [mock_pod]
        mock_core_api.list_namespaced_pod.return_value = mock_pod_list

        with patch("src.services.kubernetes.job_executor.get_core_api", return_value=mock_core_api):
            result = await job_executor.wait_for_pod_ready(job_handle, timeout=5)

        assert result is False
        assert job_handle.status == "failed"

    @pytest.mark.asyncio
    async def test_wait_for_pod_ready_api_exception(self, job_executor, job_handle):
        """Test waiting with API exception."""
        mock_core_api = MagicMock()
        mock_core_api.list_namespaced_pod.side_effect = ApiException(status=500)

        with patch("src.services.kubernetes.job_executor.get_core_api", return_value=mock_core_api):
            result = await job_executor.wait_for_pod_ready(job_handle, timeout=1)

        assert result is False


class TestExecute:
    """Tests for execute method."""

    @pytest.mark.asyncio
    async def test_execute_success(self, job_executor, job_handle):
        """Test successful code execution."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "exit_code": 0,
            "stdout": "Hello World",
            "stderr": "",
            "execution_time_ms": 100,
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(job_executor, "_get_http_client", return_value=mock_client):
            result = await job_executor.execute(job_handle, "print('Hello World')")

        assert result.exit_code == 0
        assert result.stdout == "Hello World"
        assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_execute_no_pod_ip(self, job_executor, job_handle):
        """Test execution without pod IP."""
        job_handle.pod_ip = None

        result = await job_executor.execute(job_handle, "print('test')")

        assert result.exit_code == 1
        assert "Job pod not ready" in result.stderr

    @pytest.mark.asyncio
    async def test_execute_no_sidecar_url(self, job_executor, job_handle):
        """Test execution without sidecar URL."""
        # Set pod_ip but mock sidecar_url to return None
        job_handle.pod_ip = "10.0.0.1"

        with patch.object(type(job_handle), "sidecar_url", new_callable=lambda: property(lambda self: None)):
            result = await job_executor.execute(job_handle, "print('test')")

        assert result.exit_code == 1
        assert "sidecar URL" in result.stderr

    @pytest.mark.asyncio
    async def test_execute_with_files(self, job_executor, job_handle):
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

        with patch.object(job_executor, "_get_http_client", return_value=mock_client):
            result = await job_executor.execute(job_handle, "exec(open('test.py').read())", files=files)

        assert result.exit_code == 0
        # Verify file upload was called
        assert mock_client.post.call_count == 2  # File upload + execute

    @pytest.mark.asyncio
    async def test_execute_with_initial_state(self, job_executor, job_handle):
        """Test execution with initial state."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "exit_code": 0,
            "stdout": "42",
            "stderr": "",
            "execution_time_ms": 50,
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(job_executor, "_get_http_client", return_value=mock_client):
            result = await job_executor.execute(job_handle, "print(x)", initial_state="base64state", capture_state=True)

        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_sidecar_error(self, job_executor, job_handle):
        """Test execution with sidecar error."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(job_executor, "_get_http_client", return_value=mock_client):
            result = await job_executor.execute(job_handle, "print('test')")

        assert result.exit_code == 1
        assert "Sidecar error" in result.stderr

    @pytest.mark.asyncio
    async def test_execute_timeout(self, job_executor, job_handle):
        """Test execution timeout."""
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))

        with patch.object(job_executor, "_get_http_client", return_value=mock_client):
            result = await job_executor.execute(job_handle, "import time; time.sleep(100)")

        assert result.exit_code == 124
        assert "timed out" in result.stderr

    @pytest.mark.asyncio
    async def test_execute_generic_exception(self, job_executor, job_handle):
        """Test execution with generic exception."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))

        with patch.object(job_executor, "_get_http_client", return_value=mock_client):
            result = await job_executor.execute(job_handle, "print('test')")

        assert result.exit_code == 1
        assert "Execution error" in result.stderr


class TestUploadFiles:
    """Tests for _upload_files method."""

    @pytest.mark.asyncio
    async def test_upload_files_success(self, job_executor):
        """Test successful file upload."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

        files = [FileData(filename="test.py", content=b"print('hello')")]

        await job_executor._upload_files(mock_client, "http://10.0.0.1:8080", files)

        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_files_failure(self, job_executor):
        """Test file upload failure - should not raise."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Upload failed"))

        files = [FileData(filename="test.py", content=b"print('hello')")]

        # Should not raise, just log warning
        await job_executor._upload_files(mock_client, "http://10.0.0.1:8080", files)


class TestDeleteJob:
    """Tests for delete_job method."""

    @pytest.mark.asyncio
    async def test_delete_job_success(self, job_executor, job_handle):
        """Test successful job deletion."""
        mock_batch_api = MagicMock()

        with patch("src.services.kubernetes.job_executor.get_batch_api", return_value=mock_batch_api):
            await job_executor.delete_job(job_handle)

        mock_batch_api.delete_namespaced_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_job_no_batch_api(self, job_executor, job_handle):
        """Test deletion when batch API is not available."""
        with patch("src.services.kubernetes.job_executor.get_batch_api", return_value=None):
            # Should not raise
            await job_executor.delete_job(job_handle)

    @pytest.mark.asyncio
    async def test_delete_job_not_found(self, job_executor, job_handle):
        """Test deletion when job not found."""
        mock_batch_api = MagicMock()
        mock_batch_api.delete_namespaced_job.side_effect = ApiException(status=404)

        with patch("src.services.kubernetes.job_executor.get_batch_api", return_value=mock_batch_api):
            # Should not raise for 404
            await job_executor.delete_job(job_handle)

    @pytest.mark.asyncio
    async def test_delete_job_api_exception(self, job_executor, job_handle):
        """Test deletion with API exception."""
        mock_batch_api = MagicMock()
        mock_batch_api.delete_namespaced_job.side_effect = ApiException(status=500)

        with patch("src.services.kubernetes.job_executor.get_batch_api", return_value=mock_batch_api):
            # Should not raise, just log warning
            await job_executor.delete_job(job_handle)


class TestExecuteWithJob:
    """Tests for execute_with_job method."""

    @pytest.mark.asyncio
    async def test_execute_with_job_success(self, job_executor, pod_spec, job_handle):
        """Test successful execution with job."""
        mock_result = ExecutionResult(
            exit_code=0,
            stdout="Hello",
            stderr="",
            execution_time_ms=100,
        )

        with patch.object(job_executor, "create_job", return_value=job_handle) as mock_create:
            with patch.object(job_executor, "wait_for_pod_ready", return_value=True):
                with patch.object(job_executor, "execute", return_value=mock_result):
                    with patch.object(job_executor, "delete_job", return_value=None):
                        result = await job_executor.execute_with_job(pod_spec, "session-123", "print('Hello')")

        assert result.exit_code == 0
        assert result.stdout == "Hello"

    @pytest.mark.asyncio
    async def test_execute_with_job_pod_not_ready(self, job_executor, pod_spec, job_handle):
        """Test execution when pod fails to start."""
        with patch.object(job_executor, "create_job", return_value=job_handle):
            with patch.object(job_executor, "wait_for_pod_ready", return_value=False):
                with patch.object(job_executor, "delete_job", return_value=None):
                    result = await job_executor.execute_with_job(pod_spec, "session-123", "print('Hello')")

        assert result.exit_code == 1
        assert "failed to start" in result.stderr

    @pytest.mark.asyncio
    async def test_execute_with_job_cleanup_on_error(self, job_executor, pod_spec, job_handle):
        """Test that job is cleaned up on error."""
        with patch.object(job_executor, "create_job", return_value=job_handle):
            with patch.object(job_executor, "wait_for_pod_ready", side_effect=Exception("Test error")):
                with patch.object(job_executor, "delete_job", return_value=None) as mock_delete:
                    with pytest.raises(Exception, match="Test error"):
                        await job_executor.execute_with_job(pod_spec, "session-123", "print('Hello')")

                    # Give asyncio.create_task time to schedule
                    await asyncio.sleep(0.1)


class TestCreateJobPassesAllPodSpecFields:
    """Tests that create_job passes all PodSpec fields to create_job_manifest."""

    @pytest.mark.asyncio
    async def test_create_job_passes_image_pull_secrets(self, job_executor):
        """Test that image_pull_secrets are forwarded to create_job_manifest."""
        spec = PodSpec(
            image="python:3.11",
            language="python",
            namespace="test-namespace",
            image_pull_secrets=["my-registry-secret", "other-secret"],
        )
        mock_batch_api = MagicMock()
        mock_job = MagicMock()
        mock_job.metadata.uid = "job-uid-123"
        mock_batch_api.create_namespaced_job.return_value = mock_job

        with patch("src.services.kubernetes.job_executor.get_batch_api", return_value=mock_batch_api):
            with patch("src.services.kubernetes.job_executor.create_job_manifest", return_value={}) as mock_manifest:
                await job_executor.create_job(spec, "session-123")

        _, kwargs = mock_manifest.call_args
        assert kwargs["image_pull_secrets"] == ["my-registry-secret", "other-secret"]

    @pytest.mark.asyncio
    async def test_create_job_passes_image_pull_policy(self, job_executor):
        """Test that image_pull_policy is forwarded to create_job_manifest."""
        spec = PodSpec(
            image="python:3.11",
            language="python",
            namespace="test-namespace",
            image_pull_policy="IfNotPresent",
        )
        mock_batch_api = MagicMock()
        mock_job = MagicMock()
        mock_job.metadata.uid = "job-uid-123"
        mock_batch_api.create_namespaced_job.return_value = mock_job

        with patch("src.services.kubernetes.job_executor.get_batch_api", return_value=mock_batch_api):
            with patch("src.services.kubernetes.job_executor.create_job_manifest", return_value={}) as mock_manifest:
                await job_executor.create_job(spec, "session-123")

        _, kwargs = mock_manifest.call_args
        assert kwargs["image_pull_policy"] == "IfNotPresent"

    @pytest.mark.asyncio
    async def test_create_job_passes_execution_mode(self, job_executor):
        """Test that execution_mode is forwarded to create_job_manifest."""
        spec = PodSpec(
            image="python:3.11",
            language="python",
            namespace="test-namespace",
            execution_mode="nsenter",
        )
        mock_batch_api = MagicMock()
        mock_job = MagicMock()
        mock_job.metadata.uid = "job-uid-123"
        mock_batch_api.create_namespaced_job.return_value = mock_job

        with patch("src.services.kubernetes.job_executor.get_batch_api", return_value=mock_batch_api):
            with patch("src.services.kubernetes.job_executor.create_job_manifest", return_value={}) as mock_manifest:
                await job_executor.create_job(spec, "session-123")

        _, kwargs = mock_manifest.call_args
        assert kwargs["execution_mode"] == "nsenter"

    @pytest.mark.asyncio
    async def test_create_job_no_image_pull_secrets_by_default(self, job_executor):
        """Test that image_pull_secrets defaults to None."""
        spec = PodSpec(
            image="python:3.11",
            language="python",
            namespace="test-namespace",
        )
        mock_batch_api = MagicMock()
        mock_job = MagicMock()
        mock_job.metadata.uid = "job-uid-123"
        mock_batch_api.create_namespaced_job.return_value = mock_job

        with patch("src.services.kubernetes.job_executor.get_batch_api", return_value=mock_batch_api):
            with patch("src.services.kubernetes.job_executor.create_job_manifest", return_value={}) as mock_manifest:
                await job_executor.create_job(spec, "session-123")

        _, kwargs = mock_manifest.call_args
        assert kwargs["image_pull_secrets"] is None
