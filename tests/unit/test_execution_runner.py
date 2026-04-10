"""Unit tests for the code execution runner."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import (
    CodeExecution,
    ExecuteCodeRequest,
    ExecutionOutput,
    ExecutionStatus,
    OutputType,
)
from src.services.execution.runner import CodeExecutionRunner
from src.services.kubernetes import ExecutionResult, PodHandle


@pytest.fixture
def mock_kubernetes_manager():
    """Create mock Kubernetes manager."""
    manager = MagicMock()
    # Sync methods
    manager.is_available = MagicMock(return_value=True)
    manager.get_initialization_error = MagicMock(return_value=None)
    # Async methods
    manager.start = AsyncMock()
    manager.stop = AsyncMock()
    manager.acquire_pod = AsyncMock(return_value=(None, "pool_miss"))
    manager.execute_code = AsyncMock()
    manager.destroy_pod = AsyncMock()
    manager.destroy_pods_batch = AsyncMock(return_value=0)
    return manager


@pytest.fixture
def runner(mock_kubernetes_manager):
    """Create a runner with mocked Kubernetes manager."""
    return CodeExecutionRunner(kubernetes_manager=mock_kubernetes_manager)


@pytest.fixture
def sample_request():
    """Create a sample execution request."""
    return ExecuteCodeRequest(
        code="print('Hello, World!')",
        language="python",
    )


@pytest.fixture
def sample_execution_result():
    """Create a sample execution result."""
    return ExecutionResult(
        stdout="Hello, World!\n",
        stderr="",
        exit_code=0,
        execution_time_ms=50,
        state=None,
        state_errors=None,
    )


class TestRunnerInit:
    """Tests for CodeExecutionRunner initialization."""

    def test_init_with_manager(self, mock_kubernetes_manager):
        """Test initialization with provided manager."""
        runner = CodeExecutionRunner(kubernetes_manager=mock_kubernetes_manager)

        assert runner._kubernetes_manager == mock_kubernetes_manager
        assert runner._manager_started is False
        assert runner.active_executions == {}
        assert runner.session_handles == {}
        assert runner._job_file_contents == {}

    def test_init_without_manager(self):
        """Test initialization without manager."""
        runner = CodeExecutionRunner()

        assert runner._kubernetes_manager is None
        assert runner._manager_started is False

    def test_kubernetes_manager_property_lazy_creation(self):
        """Test Kubernetes manager is created lazily."""
        runner = CodeExecutionRunner()

        with patch("src.services.execution.runner.KubernetesManager") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance

            manager = runner.kubernetes_manager

            mock_cls.assert_called_once()
            assert manager == mock_instance


class TestEnsureStarted:
    """Tests for _ensure_started method."""

    @pytest.mark.asyncio
    async def test_ensure_started_starts_manager(self, runner, mock_kubernetes_manager):
        """Test that ensure_started starts the manager."""
        await runner._ensure_started()

        mock_kubernetes_manager.start.assert_called_once()
        assert runner._manager_started is True

    @pytest.mark.asyncio
    async def test_ensure_started_only_once(self, runner, mock_kubernetes_manager):
        """Test that ensure_started only starts once."""
        await runner._ensure_started()
        await runner._ensure_started()

        mock_kubernetes_manager.start.assert_called_once()


class TestGetPod:
    """Tests for _get_pod method."""

    @pytest.mark.asyncio
    async def test_get_pod_from_pool(self, runner, mock_kubernetes_manager):
        """Test getting pod from pool."""
        mock_handle = MagicMock(name="test-pod")
        mock_kubernetes_manager.acquire_pod.return_value = (mock_handle, "pool_hit")

        handle, source = await runner._get_pod("session-123", "python")

        assert handle == mock_handle
        assert source == "pool_hit"
        mock_kubernetes_manager.acquire_pod.assert_called_once_with("session-123", "python")

    @pytest.mark.asyncio
    async def test_get_pod_pool_miss(self, runner, mock_kubernetes_manager):
        """Test when no pod available from pool."""
        mock_kubernetes_manager.acquire_pod.return_value = (None, "pool_miss")

        handle, source = await runner._get_pod("session-123", "go")

        assert handle is None
        assert source == "pool_miss"


class TestExecute:
    """Tests for execute method."""

    @pytest.mark.asyncio
    async def test_execute_successful(self, runner, mock_kubernetes_manager, sample_request, sample_execution_result):
        """Test successful code execution."""
        mock_handle = MagicMock(name="test-pod", pod_ip=None)
        mock_kubernetes_manager.execute_code.return_value = (sample_execution_result, mock_handle, "pool_hit")

        with patch("src.services.execution.runner.metrics_collector"):
            execution, handle, state, state_errors, source = await runner.execute(
                "session-123",
                sample_request,
            )

        assert execution.status == ExecutionStatus.COMPLETED
        assert execution.exit_code == 0
        assert len(execution.outputs) == 1
        assert execution.outputs[0].type == OutputType.STDOUT
        assert "Hello, World!" in execution.outputs[0].content
        assert source == "pool_hit"

    @pytest.mark.asyncio
    async def test_execute_kubernetes_unavailable(self, runner, mock_kubernetes_manager, sample_request):
        """Test execution when Kubernetes is unavailable."""
        # Reset the mock to return False for is_available
        mock_kubernetes_manager.is_available = MagicMock(return_value=False)
        mock_kubernetes_manager.get_initialization_error = MagicMock(return_value="Connection refused")

        with patch("src.services.execution.runner.metrics_collector"):
            execution, handle, state, state_errors, source = await runner.execute(
                "session-123",
                sample_request,
            )

        assert execution.status == ExecutionStatus.FAILED
        assert "Kubernetes unavailable" in execution.error_message
        assert handle is None

    @pytest.mark.asyncio
    async def test_execute_with_stderr(self, runner, mock_kubernetes_manager, sample_request):
        """Test execution with stderr output."""
        result = ExecutionResult(
            stdout="",
            stderr="NameError: name 'undefined' is not defined",
            exit_code=1,
            execution_time_ms=30,
            state=None,
            state_errors=None,
        )
        mock_kubernetes_manager.execute_code.return_value = (result, None, "pool_hit")

        with patch("src.services.execution.runner.metrics_collector"):
            execution, _, _, _, _ = await runner.execute(
                "session-123",
                sample_request,
            )

        assert execution.status == ExecutionStatus.FAILED
        assert execution.exit_code == 1
        assert len(execution.outputs) == 1
        assert execution.outputs[0].type == OutputType.STDERR

    @pytest.mark.asyncio
    async def test_execute_with_state(self, runner, mock_kubernetes_manager, sample_request):
        """Test execution with state capture."""
        result = ExecutionResult(
            stdout="output",
            stderr="",
            exit_code=0,
            execution_time_ms=45,
            state="base64encodedstate==",
            state_errors=["Warning: skipped large object"],
        )
        mock_kubernetes_manager.execute_code.return_value = (result, None, "pool_hit")

        with patch("src.services.execution.runner.metrics_collector"):
            execution, _, new_state, state_errors, _ = await runner.execute(
                "session-123",
                sample_request,
                capture_state=True,
            )

        assert new_state == "base64encodedstate=="
        assert state_errors == ["Warning: skipped large object"]

    @pytest.mark.asyncio
    async def test_execute_timeout(self, runner, mock_kubernetes_manager, sample_request):
        """Test execution timeout."""
        mock_kubernetes_manager.execute_code.side_effect = TimeoutError("Execution timed out")

        with patch("src.services.execution.runner.metrics_collector"):
            execution, _, state, state_errors, _ = await runner.execute(
                "session-123",
                sample_request,
            )

        assert execution.status == ExecutionStatus.TIMEOUT
        assert "timed out" in execution.error_message.lower()
        assert state is None
        assert state_errors == []

    @pytest.mark.asyncio
    async def test_execute_exception(self, runner, mock_kubernetes_manager, sample_request):
        """Test execution with exception."""
        mock_kubernetes_manager.execute_code.side_effect = Exception("Pod crashed")

        with patch("src.services.execution.runner.metrics_collector"):
            execution, _, state, state_errors, _ = await runner.execute(
                "session-123",
                sample_request,
            )

        assert execution.status == ExecutionStatus.FAILED
        assert "Pod crashed" in execution.error_message
        assert state is None

    @pytest.mark.asyncio
    async def test_execute_stores_handle(
        self, runner, mock_kubernetes_manager, sample_request, sample_execution_result
    ):
        """Test that execution stores the pod handle."""
        mock_handle = MagicMock(name="test-pod", pod_ip=None)
        mock_kubernetes_manager.execute_code.return_value = (sample_execution_result, mock_handle, "pool_hit")

        with patch("src.services.execution.runner.metrics_collector"):
            await runner.execute("session-123", sample_request)

        assert runner.session_handles["session-123"] == mock_handle

    @pytest.mark.asyncio
    async def test_execute_with_files(self, runner, mock_kubernetes_manager, sample_request, sample_execution_result):
        """Test execution with mounted files."""
        mock_kubernetes_manager.execute_code.return_value = (sample_execution_result, None, "pool_hit")

        files = [{"filename": "data.csv", "content": "a,b,c"}]

        with patch("src.services.execution.runner.metrics_collector"):
            execution, _, _, _, _ = await runner.execute(
                "session-123",
                sample_request,
                files=files,
            )

        assert execution.status == ExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_with_initial_state(
        self, runner, mock_kubernetes_manager, sample_request, sample_execution_result
    ):
        """Test execution with initial state."""
        mock_kubernetes_manager.execute_code.return_value = (sample_execution_result, None, "pool_hit")

        with patch("src.services.execution.runner.metrics_collector"):
            await runner.execute(
                "session-123",
                sample_request,
                initial_state="base64state==",
            )

        mock_kubernetes_manager.execute_code.assert_called_once()
        call_kwargs = mock_kubernetes_manager.execute_code.call_args[1]
        assert call_kwargs["initial_state"] == "base64state=="


class TestProcessOutputs:
    """Tests for _process_outputs method."""

    def test_process_outputs_stdout_only(self, runner):
        """Test processing stdout only."""
        timestamp = datetime.now(UTC)
        outputs = runner._process_outputs("Hello World", "", timestamp)

        assert len(outputs) == 1
        assert outputs[0].type == OutputType.STDOUT
        assert outputs[0].content == "Hello World"

    def test_process_outputs_stderr_only(self, runner):
        """Test processing stderr only."""
        timestamp = datetime.now(UTC)
        outputs = runner._process_outputs("", "Error occurred", timestamp)

        assert len(outputs) == 1
        assert outputs[0].type == OutputType.STDERR
        assert outputs[0].content == "Error occurred"

    def test_process_outputs_both(self, runner):
        """Test processing both stdout and stderr."""
        timestamp = datetime.now(UTC)
        outputs = runner._process_outputs("Output", "Warning", timestamp)

        assert len(outputs) == 2
        types = [o.type for o in outputs]
        assert OutputType.STDOUT in types
        assert OutputType.STDERR in types

    def test_process_outputs_empty(self, runner):
        """Test processing empty outputs."""
        timestamp = datetime.now(UTC)
        outputs = runner._process_outputs("", "", timestamp)

        assert outputs == []

    def test_process_outputs_whitespace_only(self, runner):
        """Test processing whitespace-only outputs."""
        timestamp = datetime.now(UTC)
        outputs = runner._process_outputs("   ", "\n\t", timestamp)

        assert outputs == []


class TestGetMountedFilenames:
    """Tests for _get_mounted_filenames method."""

    def test_get_mounted_filenames_empty(self, runner):
        """Test with no files."""
        result = runner._get_mounted_filenames(None)
        assert result == set()

    def test_get_mounted_filenames_with_filename_key(self, runner):
        """Test with filename key."""
        files = [{"filename": "test.txt"}]
        result = runner._get_mounted_filenames(files)
        assert "test.txt" in result

    def test_get_mounted_filenames_with_name_key(self, runner):
        """Test with name key."""
        files = [{"name": "data.csv"}]
        result = runner._get_mounted_filenames(files)
        assert "data.csv" in result

    def test_get_mounted_filenames_multiple(self, runner):
        """Test with multiple files."""
        files = [
            {"filename": "test.txt"},
            {"name": "data.csv"},
        ]
        result = runner._get_mounted_filenames(files)
        assert "test.txt" in result
        assert "data.csv" in result


class TestFilterGeneratedFiles:
    """Tests for _filter_generated_files method."""

    def test_filter_removes_mounted(self, runner):
        """Test filtering removes mounted files."""
        generated = [
            {"path": "/mnt/data/output.txt"},
            {"path": "/mnt/data/input.csv"},
        ]
        mounted = {"input.csv"}

        result = runner._filter_generated_files(generated, mounted)

        assert len(result) == 1
        assert result[0]["path"] == "/mnt/data/output.txt"

    def test_filter_empty_generated(self, runner):
        """Test filtering with no generated files."""
        result = runner._filter_generated_files([], {"input.csv"})
        assert result == []

    def test_filter_empty_mounted(self, runner):
        """Test filtering with no mounted files."""
        generated = [{"path": "/mnt/data/output.txt"}]
        result = runner._filter_generated_files(generated, set())
        assert len(result) == 1


class TestDetectGeneratedFiles:
    """Tests for _detect_generated_files method."""

    @pytest.mark.asyncio
    async def test_detect_no_handle(self, runner):
        """Test detection with no handle."""
        result = await runner._detect_generated_files(None)
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_no_pod_ip(self, runner):
        """Test detection with handle but no pod IP."""
        handle = MagicMock(pod_ip=None)
        result = await runner._detect_generated_files(handle)
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_successful(self, runner):
        """Test successful file detection."""
        handle = MagicMock(
            pod_ip="10.0.0.1",
            runner_url="http://10.0.0.1:8080",
            name="test-pod",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "files": [
                {"name": "output.txt", "size": 100},
                {"name": "chart.png", "size": 5000},
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await runner._detect_generated_files(handle)

        assert len(result) == 2
        assert result[0]["path"] == "/mnt/data/output.txt"
        assert result[1]["path"] == "/mnt/data/chart.png"

    @pytest.mark.asyncio
    async def test_detect_skips_code_files(self, runner):
        """Test that all language code files are skipped."""
        handle = MagicMock(
            pod_ip="10.0.0.1",
            runner_url="http://10.0.0.1:8080",
            name="test-pod",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "files": [
                {"name": "code.py", "size": 100},
                {"name": "Code.java", "size": 200},
                {"name": "main.go", "size": 150},
                {"name": "main.rs", "size": 120},
                {"name": "code.js", "size": 90},
                {"name": "code.ts", "size": 80},
                {"name": "code.c", "size": 70},
                {"name": "code.cpp", "size": 60},
                {"name": "code.php", "size": 50},
                {"name": "code.r", "size": 40},
                {"name": "code.f90", "size": 30},
                {"name": "code.d", "size": 20},
                {"name": "output.txt", "size": 50},
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await runner._detect_generated_files(handle)

        assert len(result) == 1
        assert result[0]["path"] == "/mnt/data/output.txt"

    @pytest.mark.asyncio
    async def test_detect_handles_error(self, runner):
        """Test graceful error handling."""
        handle = MagicMock(
            pod_ip="10.0.0.1",
            runner_url="http://10.0.0.1:8080",
            name="test-pod",
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.side_effect = Exception("Connection failed")
            mock_client_cls.return_value = mock_client

            result = await runner._detect_generated_files(handle)

        assert result == []


class TestGetContainerBySession:
    """Tests for get_container_by_session method."""

    def test_get_existing_handle(self, runner):
        """Test getting existing handle."""
        mock_handle = MagicMock()
        runner.session_handles["session-123"] = mock_handle

        result = runner.get_container_by_session("session-123")

        assert result == mock_handle

    def test_get_nonexistent_handle(self, runner):
        """Test getting nonexistent handle."""
        result = runner.get_container_by_session("nonexistent")
        assert result is None


class TestPopJobFileContent:
    """Tests for pop_job_file_content method."""

    def test_pop_existing_content(self, runner):
        """Test popping pre-downloaded file content."""
        runner._job_file_contents[("session-123", "/mnt/data/output.png")] = b"PNG data"

        result = runner.pop_job_file_content("session-123", "/mnt/data/output.png")

        assert result == b"PNG data"
        assert ("session-123", "/mnt/data/output.png") not in runner._job_file_contents

    def test_pop_nonexistent_content(self, runner):
        """Test popping content for non-existent path."""
        result = runner.pop_job_file_content("session-123", "/mnt/data/nonexistent.txt")
        assert result is None

    def test_pop_wrong_session(self, runner):
        """Test that files are scoped by session_id."""
        runner._job_file_contents[("session-A", "/mnt/data/output.png")] = b"PNG data"

        result = runner.pop_job_file_content("session-B", "/mnt/data/output.png")

        assert result is None
        assert ("session-A", "/mnt/data/output.png") in runner._job_file_contents


class TestGetExecution:
    """Tests for get_execution method."""

    @pytest.mark.asyncio
    async def test_get_existing_execution(self, runner):
        """Test getting existing execution."""
        execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-456",
            code="print(1)",
            language="python",
            status=ExecutionStatus.COMPLETED,
        )
        runner.active_executions["exec-123"] = execution

        result = await runner.get_execution("exec-123")

        assert result == execution

    @pytest.mark.asyncio
    async def test_get_nonexistent_execution(self, runner):
        """Test getting nonexistent execution."""
        result = await runner.get_execution("nonexistent")
        assert result is None


class TestCancelExecution:
    """Tests for cancel_execution method."""

    @pytest.mark.asyncio
    async def test_cancel_running_execution(self, runner, mock_kubernetes_manager):
        """Test cancelling a running execution."""
        execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-456",
            code="import time; time.sleep(60)",
            language="python",
            status=ExecutionStatus.RUNNING,
        )
        runner.active_executions["exec-123"] = execution

        mock_handle = MagicMock()
        runner.session_handles["session-456"] = mock_handle

        result = await runner.cancel_execution("exec-123")

        assert result is True
        assert execution.status == ExecutionStatus.CANCELLED
        assert "session-456" not in runner.session_handles
        mock_kubernetes_manager.destroy_pod.assert_called_once_with(mock_handle)

    @pytest.mark.asyncio
    async def test_cancel_pending_execution(self, runner, mock_kubernetes_manager):
        """Test cancelling a pending execution."""
        execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-456",
            code="print(1)",
            language="python",
            status=ExecutionStatus.PENDING,
        )
        runner.active_executions["exec-123"] = execution

        result = await runner.cancel_execution("exec-123")

        assert result is True
        assert execution.status == ExecutionStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_completed_execution(self, runner):
        """Test cancelling a completed execution fails."""
        execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-456",
            code="print(1)",
            language="python",
            status=ExecutionStatus.COMPLETED,
        )
        runner.active_executions["exec-123"] = execution

        result = await runner.cancel_execution("exec-123")

        assert result is False
        assert execution.status == ExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_execution(self, runner):
        """Test cancelling nonexistent execution fails."""
        result = await runner.cancel_execution("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_handles_destroy_error(self, runner, mock_kubernetes_manager):
        """Test cancel handles destroy error gracefully."""
        execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-456",
            code="print(1)",
            language="python",
            status=ExecutionStatus.RUNNING,
        )
        runner.active_executions["exec-123"] = execution
        runner.session_handles["session-456"] = MagicMock()

        mock_kubernetes_manager.destroy_pod.side_effect = Exception("Pod not found")

        result = await runner.cancel_execution("exec-123")

        assert result is False


class TestListExecutions:
    """Tests for list_executions method."""

    @pytest.mark.asyncio
    async def test_list_executions_for_session(self, runner):
        """Test listing executions for a session."""
        exec1 = CodeExecution(
            execution_id="exec-1",
            session_id="session-123",
            code="print(1)",
            language="python",
            status=ExecutionStatus.COMPLETED,
        )
        exec2 = CodeExecution(
            execution_id="exec-2",
            session_id="session-123",
            code="print(2)",
            language="python",
            status=ExecutionStatus.COMPLETED,
        )
        exec3 = CodeExecution(
            execution_id="exec-3",
            session_id="session-other",
            code="print(3)",
            language="python",
            status=ExecutionStatus.COMPLETED,
        )

        runner.active_executions["exec-1"] = exec1
        runner.active_executions["exec-2"] = exec2
        runner.active_executions["exec-3"] = exec3

        result = await runner.list_executions("session-123")

        assert len(result) == 2
        assert all(e.session_id == "session-123" for e in result)

    @pytest.mark.asyncio
    async def test_list_executions_empty(self, runner):
        """Test listing executions for session with none."""
        result = await runner.list_executions("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_executions_with_limit(self, runner):
        """Test listing executions with limit."""
        for i in range(10):
            exec_obj = CodeExecution(
                execution_id=f"exec-{i}",
                session_id="session-123",
                code=f"print({i})",
                language="python",
                status=ExecutionStatus.COMPLETED,
            )
            runner.active_executions[f"exec-{i}"] = exec_obj

        result = await runner.list_executions("session-123", limit=5)

        assert len(result) == 5


class TestCleanupSession:
    """Tests for cleanup_session method."""

    @pytest.mark.asyncio
    async def test_cleanup_session_with_handle(self, runner, mock_kubernetes_manager):
        """Test cleaning up session with pod handle."""
        mock_handle = MagicMock()
        runner.session_handles["session-123"] = mock_handle

        execution = CodeExecution(
            execution_id="exec-1",
            session_id="session-123",
            code="print(1)",
            language="python",
            status=ExecutionStatus.COMPLETED,
        )
        runner.active_executions["exec-1"] = execution

        result = await runner.cleanup_session("session-123")

        assert result is True
        assert "session-123" not in runner.session_handles
        assert "exec-1" not in runner.active_executions
        mock_kubernetes_manager.destroy_pod.assert_called_once_with(mock_handle)

    @pytest.mark.asyncio
    async def test_cleanup_session_without_handle(self, runner, mock_kubernetes_manager):
        """Test cleaning up session without pod handle."""
        execution = CodeExecution(
            execution_id="exec-1",
            session_id="session-123",
            code="print(1)",
            language="python",
            status=ExecutionStatus.COMPLETED,
        )
        runner.active_executions["exec-1"] = execution

        result = await runner.cleanup_session("session-123")

        assert result is True
        assert "exec-1" not in runner.active_executions
        mock_kubernetes_manager.destroy_pod.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_session_handles_error(self, runner, mock_kubernetes_manager):
        """Test cleanup handles destroy error."""
        runner.session_handles["session-123"] = MagicMock()
        mock_kubernetes_manager.destroy_pod.side_effect = Exception("Pod not found")

        result = await runner.cleanup_session("session-123")

        assert result is False


class TestCleanupExpiredExecutions:
    """Tests for cleanup_expired_executions method."""

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, runner):
        """Test cleaning up expired executions."""
        old_exec = CodeExecution(
            execution_id="exec-old",
            session_id="session-123",
            code="print(1)",
            language="python",
            status=ExecutionStatus.COMPLETED,
        )
        # Manually set old creation time
        old_exec.created_at = datetime.now(UTC) - timedelta(hours=48)

        new_exec = CodeExecution(
            execution_id="exec-new",
            session_id="session-123",
            code="print(2)",
            language="python",
            status=ExecutionStatus.COMPLETED,
        )

        runner.active_executions["exec-old"] = old_exec
        runner.active_executions["exec-new"] = new_exec

        result = await runner.cleanup_expired_executions(max_age_hours=24)

        assert result == 1
        assert "exec-old" not in runner.active_executions
        assert "exec-new" in runner.active_executions

    @pytest.mark.asyncio
    async def test_cleanup_does_not_remove_running(self, runner):
        """Test that running executions are not cleaned up."""
        old_exec = CodeExecution(
            execution_id="exec-old",
            session_id="session-123",
            code="print(1)",
            language="python",
            status=ExecutionStatus.RUNNING,
        )
        old_exec.created_at = datetime.now(UTC) - timedelta(hours=48)

        runner.active_executions["exec-old"] = old_exec

        result = await runner.cleanup_expired_executions(max_age_hours=24)

        assert result == 0
        assert "exec-old" in runner.active_executions

    @pytest.mark.asyncio
    async def test_cleanup_empty(self, runner):
        """Test cleanup with no executions."""
        result = await runner.cleanup_expired_executions()
        assert result == 0


class TestCleanupAllContainers:
    """Tests for cleanup_all_containers method."""

    @pytest.mark.asyncio
    async def test_cleanup_all(self, runner, mock_kubernetes_manager):
        """Test cleaning up all containers."""
        mock_handle1 = MagicMock()
        mock_handle2 = MagicMock()
        runner.session_handles["session-1"] = mock_handle1
        runner.session_handles["session-2"] = mock_handle2

        runner.active_executions["exec-1"] = MagicMock()

        mock_kubernetes_manager.destroy_pods_batch.return_value = 2

        await runner.cleanup_all_containers()

        assert runner.session_handles == {}
        assert runner.active_executions == {}
        mock_kubernetes_manager.destroy_pods_batch.assert_called_once()
        mock_kubernetes_manager.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_all_empty(self, runner, mock_kubernetes_manager):
        """Test cleanup with no containers."""
        await runner.cleanup_all_containers()

        mock_kubernetes_manager.destroy_pods_batch.assert_not_called()
        mock_kubernetes_manager.stop.assert_called_once()


class TestRecordMetrics:
    """Tests for _record_metrics method."""

    def test_record_metrics_success(self, runner):
        """Test successful metrics recording."""
        execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-456",
            code="print(1)",
            language="python",
            status=ExecutionStatus.COMPLETED,
            execution_time_ms=100,
        )
        execution.outputs = [
            ExecutionOutput(
                type=OutputType.STDOUT,
                content="output",
                timestamp=datetime.now(UTC),
            )
        ]

        with patch("src.services.execution.runner.metrics_collector") as mock_collector:
            runner._record_metrics(execution, "session-456", "python", None)

            mock_collector.record_execution_metrics.assert_called_once()

    def test_record_metrics_with_files(self, runner):
        """Test metrics recording with files."""
        execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-456",
            code="print(1)",
            language="python",
            status=ExecutionStatus.COMPLETED,
            execution_time_ms=100,
        )
        files = [{"filename": "test.txt"}, {"filename": "data.csv"}]

        with patch("src.services.execution.runner.metrics_collector") as mock_collector:
            runner._record_metrics(execution, "session-456", "python", files)

            call_args = mock_collector.record_execution_metrics.call_args[0][0]
            assert call_args.file_count == 2

    def test_record_metrics_handles_error(self, runner):
        """Test metrics recording handles errors gracefully."""
        execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-456",
            code="print(1)",
            language="python",
            status=ExecutionStatus.COMPLETED,
        )

        with patch("src.services.execution.runner.metrics_collector") as mock_collector:
            mock_collector.record_execution_metrics.side_effect = Exception("Metrics error")

            # Should not raise
            runner._record_metrics(execution, "session-456", "python", None)
