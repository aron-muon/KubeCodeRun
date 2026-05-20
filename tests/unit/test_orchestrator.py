"""Unit tests for Execution Orchestrator."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import (
    CodeExecution,
    ExecRequest,
    ExecutionStatus,
    FileRef,
    Session,
    SessionStatus,
    ValidationError,
)
from src.services.orchestrator import ExecutionContext, ExecutionOrchestrator


@pytest.fixture
def mock_session_service():
    """Create a mock session service."""
    service = MagicMock()
    service.get_session = AsyncMock()
    service.create_session = AsyncMock()
    service.list_sessions_by_entity = AsyncMock(return_value=[])
    return service


@pytest.fixture
def mock_file_service():
    """Create a mock file service."""
    service = MagicMock()
    service.get_file_info = AsyncMock(return_value=None)
    service.list_files = AsyncMock(return_value=[])
    service.upload_file = AsyncMock()
    service.get_file_content = AsyncMock(return_value=b"test content")
    return service


@pytest.fixture
def mock_execution_service():
    """Create a mock execution service."""
    service = MagicMock()
    service.execute_code = AsyncMock()
    service.get_container_for_session = AsyncMock(return_value=None)
    service.pop_job_file_content = MagicMock(return_value=None)
    return service


@pytest.fixture
def mock_state_service():
    """Create a mock state service."""
    service = MagicMock()
    service.get_state = AsyncMock(return_value=None)
    service.set_state = AsyncMock()
    return service


@pytest.fixture
def orchestrator(mock_session_service, mock_file_service, mock_execution_service, mock_state_service):
    """Create an orchestrator with mocked dependencies."""
    return ExecutionOrchestrator(
        session_service=mock_session_service,
        file_service=mock_file_service,
        execution_service=mock_execution_service,
        state_service=mock_state_service,
    )


@pytest.fixture
def sample_request():
    """Create a sample execution request."""
    return ExecRequest(
        code="print('Hello, World!')",
        lang="python",
    )


@pytest.fixture
def sample_session():
    """Create a sample session."""
    from datetime import timedelta

    return Session(
        session_id="session-123",
        status=SessionStatus.ACTIVE,
        language="python",
        created_at=datetime.now(),
        expires_at=datetime.now() + timedelta(hours=1),
    )


class TestExecutionContext:
    """Tests for ExecutionContext dataclass."""

    def test_context_creation(self, sample_request):
        """Test creating an execution context."""
        ctx = ExecutionContext(
            request=sample_request,
            request_id="req-123",
        )

        assert ctx.request is sample_request
        assert ctx.request_id == "req-123"
        assert ctx.session_id is None
        assert ctx.mounted_files is None

    def test_context_defaults(self, sample_request):
        """Test context default values."""
        ctx = ExecutionContext(
            request=sample_request,
            request_id="req-123",
        )

        assert ctx.stdout == ""
        assert ctx.stderr == ""
        assert ctx.container_source == "pool_hit"
        assert ctx.is_env_key is False


class TestOrchestratorInit:
    """Tests for ExecutionOrchestrator initialization."""

    def test_init(self, mock_session_service, mock_file_service, mock_execution_service):
        """Test orchestrator initialization."""
        orchestrator = ExecutionOrchestrator(
            session_service=mock_session_service,
            file_service=mock_file_service,
            execution_service=mock_execution_service,
        )

        assert orchestrator.session_service is mock_session_service
        assert orchestrator.file_service is mock_file_service
        assert orchestrator.execution_service is mock_execution_service

    def test_init_with_state_service(
        self, mock_session_service, mock_file_service, mock_execution_service, mock_state_service
    ):
        """Test orchestrator initialization with state service."""
        orchestrator = ExecutionOrchestrator(
            session_service=mock_session_service,
            file_service=mock_file_service,
            execution_service=mock_execution_service,
            state_service=mock_state_service,
        )

        assert orchestrator.state_service is mock_state_service


class TestValidateRequest:
    """Tests for _validate_request method."""

    def test_validate_unsupported_language(self, orchestrator):
        """Test validation rejects unsupported language."""
        request = ExecRequest(code="print('hello')", lang="unsupported_lang_xyz")
        ctx = ExecutionContext(request=request, request_id="req-123")

        with pytest.raises(ValidationError):
            orchestrator._validate_request(ctx)


class TestGetOrCreateSession:
    """Tests for _get_or_create_session method."""

    @pytest.mark.asyncio
    async def test_create_new_session(self, orchestrator, mock_session_service, sample_request, sample_session):
        """Test creating a new session."""
        mock_session_service.create_session.return_value = sample_session
        ctx = ExecutionContext(request=sample_request, request_id="req-123")

        session_id = await orchestrator._get_or_create_session(ctx)

        assert session_id == "session-123"
        mock_session_service.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_reuse_session_from_request(self, orchestrator, mock_session_service, sample_session):
        """Test reusing session from request."""
        request = ExecRequest(
            code="print('hello')",
            lang="python",
            session_id="session-123",
        )
        mock_session_service.get_session.return_value = sample_session
        ctx = ExecutionContext(request=request, request_id="req-123")

        session_id = await orchestrator._get_or_create_session(ctx)

        assert session_id == "session-123"
        mock_session_service.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_reuse_session_by_entity_id(self, orchestrator, mock_session_service, sample_session):
        """Test reusing session by entity_id (same user only).

        Sessions referenced via entity_id can leak between users of a shared
        agent, so reuse requires matching user_id. Verifies the same-user
        happy path here; the cross-user denial is covered in
        TestSessionIsolation.
        """
        sample_session.metadata = {"user_id": "user-1", "entity_id": "entity-123"}
        request = ExecRequest(
            code="print('hello')",
            lang="python",
            entity_id="entity-123",
            user_id="user-1",
        )
        mock_session_service.get_session.return_value = None
        mock_session_service.list_sessions_by_entity.return_value = [sample_session]
        ctx = ExecutionContext(request=request, request_id="req-123")

        session_id = await orchestrator._get_or_create_session(ctx)

        assert session_id == "session-123"


class TestMountFiles:
    """Tests for _mount_files method."""

    @pytest.mark.asyncio
    async def test_mount_no_files(self, orchestrator, sample_request):
        """Test mounting when no files in request."""
        # Create request with empty files list
        request = ExecRequest(code="print('hello')", lang="python", files=[])
        ctx = ExecutionContext(request=request, request_id="req-123")

        result = await orchestrator._mount_files(ctx)

        assert result == []


class TestCleanup:
    """Tests for _cleanup method."""

    @pytest.mark.asyncio
    async def test_cleanup_no_container(self, orchestrator, sample_request):
        """Test cleanup without container."""
        ctx = ExecutionContext(
            request=sample_request,
            request_id="req-123",
            session_id="session-123",
            container=None,
        )

        # Should not raise
        await orchestrator._cleanup(ctx)


class TestValidateRequestExtended:
    """Extended tests for _validate_request method."""

    def test_validate_empty_code(self, orchestrator):
        """Test validation rejects empty code."""
        request = ExecRequest(code="", lang="python")
        ctx = ExecutionContext(request=request, request_id="req-123")

        with patch("src.services.orchestrator.is_supported_language", return_value=True):
            with pytest.raises(ValidationError):
                orchestrator._validate_request(ctx)

    def test_validate_whitespace_code(self, orchestrator):
        """Test validation rejects whitespace-only code."""
        request = ExecRequest(code="   \n   ", lang="python")
        ctx = ExecutionContext(request=request, request_id="req-123")

        with patch("src.services.orchestrator.is_supported_language", return_value=True):
            with pytest.raises(ValidationError):
                orchestrator._validate_request(ctx)

    def test_validate_valid_request(self, orchestrator, sample_request):
        """Test validation accepts valid request."""
        ctx = ExecutionContext(request=sample_request, request_id="req-123")

        with patch("src.services.orchestrator.is_supported_language", return_value=True):
            # Should not raise
            orchestrator._validate_request(ctx)


class TestGetOrCreateSessionExtended:
    """Extended tests for _get_or_create_session method."""

    @pytest.mark.asyncio
    async def test_session_not_found(self, orchestrator, mock_session_service, sample_session):
        """Test when session_id in request but session not found."""
        request = ExecRequest(
            code="print('hello')",
            lang="python",
            session_id="nonexistent-session",
        )
        mock_session_service.get_session.return_value = None
        mock_session_service.create_session.return_value = sample_session
        ctx = ExecutionContext(request=request, request_id="req-123")

        session_id = await orchestrator._get_or_create_session(ctx)

        # Should create new session since existing wasn't found
        mock_session_service.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_lookup_error(self, orchestrator, mock_session_service, sample_session):
        """Test handling session lookup errors."""
        request = ExecRequest(
            code="print('hello')",
            lang="python",
            session_id="session-123",
        )
        mock_session_service.get_session.side_effect = Exception("Redis error")
        mock_session_service.create_session.return_value = sample_session
        ctx = ExecutionContext(request=request, request_id="req-123")

        session_id = await orchestrator._get_or_create_session(ctx)

        # Should create new session on error
        mock_session_service.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_from_file_ref(self, orchestrator, mock_session_service, sample_session):
        """Test reusing session from file reference (same user only).

        Same-user file_ref reuse is the legitimate session-continuity path
        (upload-then-exec for the same user). Cross-user denial is covered
        in TestSessionIsolation.
        """
        from src.models.exec import RequestFile

        sample_session.metadata = {"user_id": "user-1"}
        request_file = RequestFile(id="file-123", session_id="session-123", name="test.txt")
        request = ExecRequest(
            code="print('hello')",
            lang="python",
            user_id="user-1",
            files=[request_file],
        )
        mock_session_service.get_session.return_value = sample_session
        ctx = ExecutionContext(request=request, request_id="req-123")

        with patch("src.services.orchestrator.is_supported_language", return_value=True):
            session_id = await orchestrator._get_or_create_session(ctx)

        assert session_id == "session-123"

    @pytest.mark.asyncio
    async def test_session_with_metadata(self, orchestrator, mock_session_service, sample_session):
        """Test session creation with entity_id and user_id."""
        request = ExecRequest(
            code="print('hello')",
            lang="python",
            entity_id="entity-123",
            user_id="user-456",
        )
        mock_session_service.list_sessions_by_entity.return_value = []
        mock_session_service.create_session.return_value = sample_session
        ctx = ExecutionContext(request=request, request_id="req-123")

        session_id = await orchestrator._get_or_create_session(ctx)

        # Verify session creation was called
        mock_session_service.create_session.assert_called_once()


class TestExecute:
    """Tests for execute method."""

    @pytest.mark.asyncio
    async def test_execute_validation_error_unsupported_lang(self, orchestrator):
        """Test execute with unsupported language."""
        request = ExecRequest(code="print('hello')", lang="unsupported_xyz")

        with pytest.raises(ValidationError):
            await orchestrator.execute(request, request_id="req-123")

    @pytest.mark.asyncio
    async def test_execute_success(
        self, orchestrator, mock_session_service, mock_execution_service, mock_state_service
    ):
        """Test successful execution."""
        from datetime import timedelta

        from src.models.exec import ExecResponse

        sample_session = Session(
            session_id="session-123",
            status=SessionStatus.ACTIVE,
            language="python",
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=1),
        )

        mock_execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-123",
            code="print('Hello, World!')",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="Hello, World!",
            stderr="",
            execution_time_ms=100,
        )

        mock_session_service.create_session.return_value = sample_session
        mock_execution_service.execute_code.return_value = mock_execution
        mock_state_service.get_state.return_value = None

        request = ExecRequest(code="print('Hello, World!')", lang="python")

        # Mock all the internal methods that can fail
        with patch.object(orchestrator, "_validate_request"):
            with patch.object(orchestrator, "_get_or_create_session", return_value="session-123"):
                with patch.object(orchestrator, "_load_state", return_value=None):
                    with patch.object(orchestrator, "_mount_files", return_value=[]):
                        with patch.object(orchestrator, "_execute_code", return_value=mock_execution):
                            with patch.object(orchestrator, "_handle_generated_files", return_value=[]):
                                with patch.object(orchestrator, "_extract_outputs"):
                                    with patch.object(orchestrator, "_save_state", return_value=None):
                                        with patch.object(
                                            orchestrator,
                                            "_build_response",
                                            return_value=ExecResponse(
                                                session_id="session-123",
                                                stdout="Hello, World!",
                                                stderr="",
                                            ),
                                        ):
                                            with patch.object(orchestrator, "_cleanup", return_value=None):
                                                response = await orchestrator.execute(request, request_id="req-123")

        assert response.session_id == "session-123"
        assert response.stdout == "Hello, World!"

    @pytest.mark.asyncio
    async def test_execute_with_value_error(
        self, orchestrator, mock_session_service, mock_execution_service, mock_state_service
    ):
        """Test that ValueError is converted to ValidationError."""
        from datetime import timedelta

        from src.models import ServiceUnavailableError

        sample_session = Session(
            session_id="session-123",
            status=SessionStatus.ACTIVE,
            language="python",
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=1),
        )

        mock_session_service.create_session.return_value = sample_session
        mock_execution_service.execute_code.side_effect = ValueError("Invalid input")

        request = ExecRequest(code="print('Hello')", lang="python")

        with patch("src.services.orchestrator.is_supported_language", return_value=True):
            with pytest.raises(ValidationError):
                await orchestrator.execute(request, request_id="req-123")

    @pytest.mark.asyncio
    async def test_execute_with_unexpected_error(
        self, orchestrator, mock_session_service, mock_execution_service, mock_state_service
    ):
        """Test that unexpected errors are converted to ServiceUnavailableError."""
        from datetime import timedelta

        from src.models import ServiceUnavailableError

        sample_session = Session(
            session_id="session-123",
            status=SessionStatus.ACTIVE,
            language="python",
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=1),
        )

        mock_session_service.create_session.return_value = sample_session
        mock_execution_service.execute_code.side_effect = RuntimeError("Unexpected error")

        request = ExecRequest(code="print('Hello')", lang="python")

        with patch("src.services.orchestrator.is_supported_language", return_value=True):
            with pytest.raises(ServiceUnavailableError):
                await orchestrator.execute(request, request_id="req-123")


class TestMountFilesExtended:
    """Extended tests for _mount_files method."""

    @pytest.mark.asyncio
    async def test_mount_files_with_valid_files(self, orchestrator, mock_file_service):
        """Test mounting files when files are found."""
        from datetime import datetime

        from src.models.exec import RequestFile
        from src.models.files import FileInfo

        file_info = FileInfo(
            file_id="file-123",
            filename="test.txt",
            size=100,
            content_type="text/plain",
            created_at=datetime.now(),
            path="/mnt/data/test.txt",
        )
        mock_file_service.get_file_info.return_value = file_info

        request_file = RequestFile(id="file-123", session_id="session-123", name="test.txt")
        request = ExecRequest(code="print('hello')", lang="python", files=[request_file])
        ctx = ExecutionContext(request=request, request_id="req-123", session_id="session-123")

        result = await orchestrator._mount_files(ctx)

        assert len(result) == 1
        assert result[0]["file_id"] == "file-123"
        assert result[0]["filename"] == "test.txt"
        assert result[0]["content"] == b"test content"
        mock_file_service.get_file_content.assert_called_once_with("session-123", "file-123")

    @pytest.mark.asyncio
    async def test_mount_files_file_not_found(self, orchestrator, mock_file_service):
        """Test mounting files when file is not found."""
        from src.models.exec import RequestFile

        mock_file_service.get_file_info.return_value = None
        mock_file_service.list_files.return_value = []

        request_file = RequestFile(id="file-123", session_id="session-123", name="test.txt")
        request = ExecRequest(code="print('hello')", lang="python", files=[request_file])
        ctx = ExecutionContext(request=request, request_id="req-123")

        result = await orchestrator._mount_files(ctx)

        assert result == []

    @pytest.mark.asyncio
    async def test_mount_files_lookup_by_name(self, orchestrator, mock_file_service):
        """Test mounting files by name when id lookup fails."""
        from datetime import datetime

        from src.models.exec import RequestFile
        from src.models.files import FileInfo

        file_info = FileInfo(
            file_id="file-456",
            filename="test.txt",
            size=100,
            content_type="text/plain",
            created_at=datetime.now(),
            path="/mnt/data/test.txt",
        )
        mock_file_service.get_file_info.return_value = None
        mock_file_service.list_files.return_value = [file_info]

        request_file = RequestFile(id="file-123", session_id="session-123", name="test.txt")
        request = ExecRequest(code="print('hello')", lang="python", files=[request_file])
        ctx = ExecutionContext(request=request, request_id="req-123", session_id="session-123")

        result = await orchestrator._mount_files(ctx)

        assert len(result) == 1
        assert result[0]["file_id"] == "file-456"
        assert result[0]["content"] == b"test content"

    @pytest.mark.asyncio
    async def test_mount_files_skip_duplicates(self, orchestrator, mock_file_service):
        """Test that duplicate files are skipped."""
        from datetime import datetime

        from src.models.exec import RequestFile
        from src.models.files import FileInfo

        file_info = FileInfo(
            file_id="file-123",
            filename="test.txt",
            size=100,
            content_type="text/plain",
            created_at=datetime.now(),
            path="/mnt/data/test.txt",
        )
        mock_file_service.get_file_info.return_value = file_info

        request_file = RequestFile(id="file-123", session_id="session-123", name="test.txt")
        request = ExecRequest(
            code="print('hello')",
            lang="python",
            files=[request_file, request_file],  # Duplicate
        )
        ctx = ExecutionContext(request=request, request_id="req-123", session_id="session-123")

        result = await orchestrator._mount_files(ctx)

        # Should only include one file
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_mount_files_auto_mount_session_files(self, orchestrator, mock_file_service):
        """Session-scoped files should be re-hydrated from MinIO even when
        request.files is empty (issue #57: pool pods are destroyed after
        each execution, emptyDir /mnt/data is ephemeral)."""
        from datetime import datetime

        from src.models.files import FileInfo

        session_file = FileInfo(
            file_id="file-upload-1",
            filename="data.csv",
            size=2048,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/data.csv",
        )
        mock_file_service.list_files.return_value = [session_file]
        mock_file_service.get_file_content.return_value = b"a,b\n1,2\n"

        request = ExecRequest(code="print('hi')", lang="python", files=[])
        ctx = ExecutionContext(request=request, request_id="req-1", session_id="session-abc")

        result = await orchestrator._mount_files(ctx)

        assert len(result) == 1
        assert result[0]["filename"] == "data.csv"
        assert result[0]["auto_mounted"] is True
        assert result[0]["content"] == b"a,b\n1,2\n"

    @pytest.mark.asyncio
    async def test_mount_files_auto_mount_skips_outputs(self, orchestrator, mock_file_service):
        """Generated output files (path starts with /outputs/) must not be
        auto-mounted — only uploads re-hydrate into /mnt/data."""
        from datetime import datetime

        from src.models.files import FileInfo

        upload = FileInfo(
            file_id="file-upload-1",
            filename="data.csv",
            size=10,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/data.csv",
        )
        output = FileInfo(
            file_id="file-output-1",
            filename="chart.png",
            size=10,
            content_type="image/png",
            created_at=datetime.now(),
            path="/outputs/chart.png",
        )
        mock_file_service.list_files.return_value = [upload, output]

        request = ExecRequest(code="print('hi')", lang="python", files=[])
        ctx = ExecutionContext(request=request, request_id="req-1", session_id="session-abc")

        result = await orchestrator._mount_files(ctx)

        assert len(result) == 1
        assert result[0]["filename"] == "data.csv"

    @pytest.mark.asyncio
    async def test_mount_files_auto_mount_dedupes_with_explicit(self, orchestrator, mock_file_service):
        """When a file is explicitly in request.files, the auto-mount pass
        must not mount it again (dedup by filename and by (session,file_id))."""
        from datetime import datetime

        from src.models.exec import RequestFile
        from src.models.files import FileInfo

        file_info = FileInfo(
            file_id="file-123",
            filename="data.csv",
            size=10,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/data.csv",
        )
        mock_file_service.get_file_info.return_value = file_info
        mock_file_service.list_files.return_value = [file_info]

        request_file = RequestFile(id="file-123", session_id="session-abc", name="data.csv")
        request = ExecRequest(code="print('hi')", lang="python", files=[request_file])
        ctx = ExecutionContext(request=request, request_id="req-1", session_id="session-abc")

        result = await orchestrator._mount_files(ctx)

        assert len(result) == 1
        assert result[0]["auto_mounted"] is False


class TestLoadState:
    """Tests for _load_state method."""

    @pytest.mark.asyncio
    async def test_load_state_disabled(self, orchestrator, mock_state_service, sample_request):
        """Test state loading when persistence disabled."""
        ctx = ExecutionContext(request=sample_request, request_id="req-123", session_id="session-123")

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.state_persistence_enabled = False

            await orchestrator._load_state(ctx)

        assert ctx.initial_state is None
        mock_state_service.get_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_state_non_python(self, orchestrator, mock_state_service):
        """Test state loading skipped for non-Python languages."""
        request = ExecRequest(code="console.log('hello')", lang="javascript")
        ctx = ExecutionContext(request=request, request_id="req-123", session_id="session-123")

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.state_persistence_enabled = True

            await orchestrator._load_state(ctx)

        assert ctx.initial_state is None

    @pytest.mark.asyncio
    async def test_load_state_from_redis(self, orchestrator, mock_state_service):
        """Test state loading from Redis."""
        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(request=request, request_id="req-123", session_id="session-123")

        mock_state_service.has_recent_upload = AsyncMock(return_value=False)
        mock_state_service.get_state.return_value = "base64statedata"

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.state_persistence_enabled = True

            await orchestrator._load_state(ctx)

        assert ctx.initial_state == "base64statedata"

    @pytest.mark.asyncio
    async def test_load_state_from_client_upload(self, orchestrator, mock_state_service):
        """Test state loading from recent client upload."""
        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(request=request, request_id="req-123", session_id="session-123")

        mock_state_service.has_recent_upload = AsyncMock(return_value=True)
        mock_state_service.get_state.return_value = "clientstate"
        mock_state_service.clear_upload_marker = AsyncMock()

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.state_persistence_enabled = True

            await orchestrator._load_state(ctx)

        assert ctx.initial_state == "clientstate"
        mock_state_service.clear_upload_marker.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_state_exception(self, orchestrator, mock_state_service):
        """Test state loading handles exceptions gracefully."""
        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(request=request, request_id="req-123", session_id="session-123")

        mock_state_service.has_recent_upload = AsyncMock(side_effect=Exception("Redis error"))

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.state_persistence_enabled = True

            # Should not raise
            await orchestrator._load_state(ctx)

        # State should remain None
        assert ctx.initial_state is None


class TestSaveState:
    """Tests for _save_state method."""

    @pytest.mark.asyncio
    async def test_save_state_disabled(self, orchestrator, mock_state_service, sample_request):
        """Test state saving when persistence disabled."""
        ctx = ExecutionContext(
            request=sample_request,
            request_id="req-123",
            session_id="session-123",
            new_state="statedata",
        )

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.state_persistence_enabled = False

            await orchestrator._save_state(ctx)

        mock_state_service.save_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_state_non_python(self, orchestrator, mock_state_service):
        """Test state saving skipped for non-Python languages."""
        request = ExecRequest(code="console.log('hello')", lang="javascript")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            new_state="statedata",
        )

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.state_persistence_enabled = True

            await orchestrator._save_state(ctx)

        mock_state_service.save_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_state_success(self, orchestrator, mock_state_service):
        """Test successful state saving."""
        request = ExecRequest(code="print('hello')", lang="py")
        mock_execution = MagicMock()
        mock_execution.status.value = "completed"

        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            new_state="statedata",
            execution=mock_execution,
        )

        mock_state_service.save_state = AsyncMock()

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.state_persistence_enabled = True
            mock_settings.state_ttl_seconds = 7200
            mock_settings.state_capture_on_error = False

            await orchestrator._save_state(ctx)

        mock_state_service.save_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_state_skipped_on_error(self, orchestrator, mock_state_service):
        """Test state saving skipped on execution error."""
        request = ExecRequest(code="print('hello')", lang="py")
        mock_execution = MagicMock()
        mock_execution.status.value = "failed"

        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            new_state="statedata",
            execution=mock_execution,
        )

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.state_persistence_enabled = True
            mock_settings.state_capture_on_error = False

            await orchestrator._save_state(ctx)

        mock_state_service.save_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_state_exception(self, orchestrator, mock_state_service):
        """Test state saving handles exceptions gracefully."""
        request = ExecRequest(code="print('hello')", lang="py")
        mock_execution = MagicMock()
        mock_execution.status.value = "completed"

        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            new_state="statedata",
            execution=mock_execution,
        )

        mock_state_service.save_state = AsyncMock(side_effect=Exception("Redis error"))

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.state_persistence_enabled = True
            mock_settings.state_ttl_seconds = 7200
            mock_settings.state_capture_on_error = False

            # Should not raise
            await orchestrator._save_state(ctx)


class TestExecuteCode:
    """Tests for _execute_code method."""

    @pytest.mark.asyncio
    async def test_execute_code_success(self, orchestrator, mock_execution_service):
        """Test successful code execution."""
        from src.models.execution import CodeExecution, ExecutionStatus

        mock_execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-123",
            code="print('hello')",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="hello",
            stderr="",
            execution_time_ms=100,
        )

        mock_container = MagicMock()
        mock_container.name = "pod-123"
        mock_execution_service.execute_code.return_value = (
            mock_execution,
            mock_container,
            "newstate",
            [],
            "pool_hit",
        )

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            mounted_files=[],
        )

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.max_execution_time = 30
            mock_settings.state_persistence_enabled = True

            result = await orchestrator._execute_code(ctx)

        assert result.status == ExecutionStatus.COMPLETED
        assert ctx.new_state == "newstate"
        assert ctx.container_source == "pool_hit"

    @pytest.mark.asyncio
    async def test_execute_code_with_initial_state(self, orchestrator, mock_execution_service):
        """Test code execution with initial state."""
        from src.models.execution import CodeExecution, ExecutionStatus

        mock_execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-123",
            code="print('hello')",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="hello",
            stderr="",
            execution_time_ms=100,
        )

        mock_execution_service.execute_code.return_value = (
            mock_execution,
            None,
            None,
            [],
            "pool_hit",
        )

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            mounted_files=[],
            initial_state="previousstate",
        )

        with patch("src.services.orchestrator.settings") as mock_settings:
            mock_settings.max_execution_time = 30
            mock_settings.state_persistence_enabled = True

            result = await orchestrator._execute_code(ctx)

        # Verify execute_code was called with initial_state
        call_args = mock_execution_service.execute_code.call_args
        assert call_args[1]["initial_state"] == "previousstate"


class TestHandleGeneratedFiles:
    """Tests for _handle_generated_files method."""

    @pytest.mark.asyncio
    async def test_handle_no_execution(self, orchestrator):
        """Test handling when no execution exists."""
        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            execution=None,
        )

        result = await orchestrator._handle_generated_files(ctx)

        assert result == []

    @pytest.mark.asyncio
    async def test_handle_no_file_outputs(self, orchestrator):
        """Test handling when outputs have no files."""
        from src.models.execution import CodeExecution, ExecutionOutput, ExecutionStatus, OutputType

        mock_execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-123",
            code="print('hello')",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="hello",
            stderr="",
            execution_time_ms=100,
            outputs=[
                ExecutionOutput(type=OutputType.STDOUT, content="hello"),
            ],
        )

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            execution=mock_execution,
        )

        result = await orchestrator._handle_generated_files(ctx)

        assert result == []

    @pytest.mark.asyncio
    async def test_handle_skip_hidden_files(self, orchestrator):
        """Test handling skips hidden files."""
        from src.models.execution import CodeExecution, ExecutionOutput, ExecutionStatus, OutputType

        mock_execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-123",
            code="print('hello')",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="hello",
            stderr="",
            execution_time_ms=100,
            outputs=[
                ExecutionOutput(type=OutputType.FILE, content="/mnt/data/.hidden"),
            ],
        )

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            execution=mock_execution,
        )

        result = await orchestrator._handle_generated_files(ctx)

        assert result == []


class TestGetFileFromContainer:
    """Tests for _get_file_from_container method."""

    @pytest.mark.asyncio
    async def test_get_file_no_container(self, orchestrator):
        """Test getting file when container is None and no job content."""
        result = await orchestrator._get_file_from_container(None, "/mnt/data/test.txt", session_id="session-123")

        assert b"Pod not found" in result

    @pytest.mark.asyncio
    async def test_get_file_no_container_with_job_content(self, orchestrator, mock_execution_service):
        """Test getting file when container is None but job content exists."""
        mock_execution_service.pop_job_file_content = MagicMock(return_value=b"job file data")

        result = await orchestrator._get_file_from_container(None, "/mnt/data/test.txt", session_id="session-123")

        assert result == b"job file data"
        mock_execution_service.pop_job_file_content.assert_called_once_with("session-123", "/mnt/data/test.txt")

    @pytest.mark.asyncio
    async def test_get_file_success(self, orchestrator, mock_execution_service):
        """Test successful file retrieval."""
        mock_container = MagicMock()
        mock_container.name = "pod-123"

        # Mock kubernetes_manager.copy_file_from_pod
        mock_execution_service.kubernetes_manager.copy_file_from_pod = AsyncMock(return_value=b"file content")

        result = await orchestrator._get_file_from_container(mock_container, "/mnt/data/test.txt")

        assert result == b"file content"

    @pytest.mark.asyncio
    async def test_get_file_returns_none(self, orchestrator, mock_execution_service):
        """Test file retrieval when copy returns None."""
        mock_container = MagicMock()
        mock_container.name = "pod-123"

        mock_execution_service.kubernetes_manager.copy_file_from_pod = AsyncMock(return_value=None)

        result = await orchestrator._get_file_from_container(mock_container, "/mnt/data/test.txt")

        assert b"Failed to retrieve" in result


class TestExtractOutputs:
    """Tests for _extract_outputs method."""

    def test_extract_no_execution(self, orchestrator):
        """Test extraction when no execution exists."""
        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            execution=None,
        )

        orchestrator._extract_outputs(ctx)

        # Should not fail, ctx.stdout/stderr remain at defaults (empty string)
        assert ctx.stdout == ""
        assert ctx.stderr == ""

    def test_extract_stdout_and_stderr(self, orchestrator):
        """Test extraction of stdout and stderr."""
        from src.models.execution import CodeExecution, ExecutionOutput, ExecutionStatus, OutputType

        mock_execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-123",
            code="print('hello')",
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout="",
            stderr="",
            execution_time_ms=100,
            outputs=[
                ExecutionOutput(type=OutputType.STDOUT, content="hello"),
                ExecutionOutput(type=OutputType.STDOUT, content="world"),
                ExecutionOutput(type=OutputType.STDERR, content="warning"),
            ],
        )

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            execution=mock_execution,
        )

        orchestrator._extract_outputs(ctx)

        assert "hello" in ctx.stdout
        assert "world" in ctx.stdout
        assert "warning" in ctx.stderr

    def test_extract_adds_error_to_stderr(self, orchestrator):
        """Test extraction adds error_message to stderr on failure."""
        from src.models.execution import CodeExecution, ExecutionStatus

        mock_execution = CodeExecution(
            execution_id="exec-123",
            session_id="session-123",
            code="raise Exception('error')",
            status=ExecutionStatus.FAILED,
            exit_code=1,
            stdout="",
            stderr="",
            error_message="Exception: error",
            execution_time_ms=100,
            outputs=[],
        )

        request = ExecRequest(code="raise Exception('error')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            execution=mock_execution,
        )

        orchestrator._extract_outputs(ctx)

        assert ctx.stderr == "Exception: error"


class TestBuildResponse:
    """Tests for _build_response method."""

    def test_build_response_no_state(self, orchestrator):
        """Test building response without state."""
        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            stdout="hello\n",
            stderr="",
            new_state=None,
        )

        response = orchestrator._build_response(ctx)

        assert response.session_id == "session-123"
        assert response.stdout == "hello\n"
        assert response.has_state is False
        assert response.state_size is None

    def test_build_response_with_state(self, orchestrator, mock_state_service):
        """Test building response with state."""
        import base64

        state_bytes = b"state data"
        encoded_state = base64.b64encode(state_bytes).decode()

        mock_state_service.compute_hash.return_value = "abc123"

        request = ExecRequest(code="x = 1", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            stdout="",
            stderr="",
            new_state=encoded_state,
        )

        response = orchestrator._build_response(ctx)

        assert response.has_state is True
        assert response.state_size == len(state_bytes)
        assert response.state_hash == "abc123"


class TestCleanupExtended:
    """Tests for _cleanup method - extended."""

    @pytest.mark.asyncio
    async def test_cleanup_with_container(self, orchestrator, mock_execution_service):
        """Test cleanup destroys container in background."""
        mock_container = MagicMock()
        mock_container.name = "pod-123"
        mock_execution_service.kubernetes_manager.destroy_pod = AsyncMock()

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            container=mock_container,
        )

        await orchestrator._cleanup(ctx)

        # Give the background task a chance to run
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_cleanup_with_container_destruction_error(self, orchestrator, mock_execution_service):
        """Test cleanup handles container destruction errors."""
        mock_container = MagicMock()
        mock_container.name = "pod-123"
        mock_execution_service.kubernetes_manager.destroy_pod = AsyncMock(side_effect=Exception("Destruction failed"))

        request = ExecRequest(code="print('hello')", lang="py")
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-123",
            container=mock_container,
        )

        # Should not raise
        await orchestrator._cleanup(ctx)

        # Give the background task a chance to run
        await asyncio.sleep(0.1)


class TestSessionIsolation:
    """Tests for cross-user session isolation in _get_or_create_session and _mount_files.

    Background: file references carry a session_id that points to the
    *storage* session where the file was uploaded. When an agent is shared,
    all users see file references pointing at the same upload session. Blindly
    reusing that session for execution leaks state/files across users.

    These tests pin down the same-user/cross-user matrix for every session
    lookup path.
    """

    @staticmethod
    def _session(session_id: str, *, user_id: str | None = None, entity_id: str | None = None):
        from datetime import timedelta

        metadata: dict = {}
        if user_id is not None:
            metadata["user_id"] = user_id
        if entity_id is not None:
            metadata["entity_id"] = entity_id
        return Session(
            session_id=session_id,
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=1),
            metadata=metadata,
        )

    @pytest.mark.asyncio
    async def test_file_ref_session_NOT_reused_for_different_user(self, orchestrator, mock_session_service):
        """Attacker user-B references file_ref.session_id from user-A's session.
        Must NOT reuse — must create a fresh session for user-B.
        """
        from src.models.exec import RequestFile

        upload_session = self._session("upload-by-A", user_id="user-A")
        new_session = self._session("new-for-B", user_id="user-B")
        mock_session_service.get_session.return_value = upload_session
        mock_session_service.create_session.return_value = new_session

        request = ExecRequest(
            code="print('hi')",
            lang="py",
            user_id="user-B",
            files=[RequestFile(id="fid", session_id="upload-by-A", name="leak.csv")],
        )
        ctx = ExecutionContext(request=request, request_id="req")

        session_id = await orchestrator._get_or_create_session(ctx)

        assert session_id == "new-for-B"
        mock_session_service.create_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_file_ref_session_NOT_reused_when_request_has_no_user_id(self, orchestrator, mock_session_service):
        """Even when the upload session has no user_id, a request without
        user_id must NOT inherit it — we can't prove ownership."""
        from src.models.exec import RequestFile

        upload_session = self._session("upload-anon")
        new_session = self._session("new-anon")
        mock_session_service.get_session.return_value = upload_session
        mock_session_service.create_session.return_value = new_session

        request = ExecRequest(
            code="print('hi')",
            lang="py",
            files=[RequestFile(id="fid", session_id="upload-anon", name="x.txt")],
        )
        ctx = ExecutionContext(request=request, request_id="req")

        session_id = await orchestrator._get_or_create_session(ctx)

        assert session_id == "new-anon"
        mock_session_service.create_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_file_ref_session_reused_for_same_user(self, orchestrator, mock_session_service):
        """Same user uploads a file then references it in /exec — reuse OK."""
        from src.models.exec import RequestFile

        upload_session = self._session("upload-by-A", user_id="user-A")
        mock_session_service.get_session.return_value = upload_session

        request = ExecRequest(
            code="print('hi')",
            lang="py",
            user_id="user-A",
            files=[RequestFile(id="fid", session_id="upload-by-A", name="own.csv")],
        )
        ctx = ExecutionContext(request=request, request_id="req")

        session_id = await orchestrator._get_or_create_session(ctx)

        assert session_id == "upload-by-A"
        mock_session_service.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_entity_id_session_NOT_reused_for_different_user(self, orchestrator, mock_session_service):
        """Shared agent with entity_id: user-B must NOT reuse user-A's session."""
        a_session = self._session("entity-by-A", user_id="user-A", entity_id="agent-X")
        b_session = self._session("new-for-B", user_id="user-B", entity_id="agent-X")
        mock_session_service.get_session.return_value = None
        mock_session_service.list_sessions_by_entity.return_value = [a_session]
        mock_session_service.create_session.return_value = b_session

        request = ExecRequest(
            code="print('hi')",
            lang="py",
            user_id="user-B",
            entity_id="agent-X",
        )
        ctx = ExecutionContext(request=request, request_id="req")

        session_id = await orchestrator._get_or_create_session(ctx)

        assert session_id == "new-for-B"

    @pytest.mark.asyncio
    async def test_entity_id_session_picks_users_own_among_many(self, orchestrator, mock_session_service):
        """Entity has multiple users' sessions; pick the requesting user's,
        not just the first."""
        sessions = [
            self._session("entity-by-A", user_id="user-A", entity_id="agent-X"),
            self._session("entity-by-B", user_id="user-B", entity_id="agent-X"),
            self._session("entity-by-C", user_id="user-C", entity_id="agent-X"),
        ]
        mock_session_service.get_session.return_value = None
        mock_session_service.list_sessions_by_entity.return_value = sessions

        request = ExecRequest(
            code="print('hi')",
            lang="py",
            user_id="user-B",
            entity_id="agent-X",
        )
        ctx = ExecutionContext(request=request, request_id="req")

        session_id = await orchestrator._get_or_create_session(ctx)

        assert session_id == "entity-by-B"

    @pytest.mark.asyncio
    async def test_explicit_session_id_in_request_always_reused(self, orchestrator, mock_session_service):
        """request.session_id is opaque; if the auth layer let the request
        through, we trust the user knows their session. (Don't break stateful
        Python continuation with surprise ownership checks.)"""
        existing = self._session("explicit-sid", user_id="user-Z")
        mock_session_service.get_session.return_value = existing

        request = ExecRequest(
            code="x = 1",
            lang="py",
            session_id="explicit-sid",
            user_id="someone-else",
        )
        ctx = ExecutionContext(request=request, request_id="req")

        session_id = await orchestrator._get_or_create_session(ctx)

        assert session_id == "explicit-sid"

    @pytest.mark.asyncio
    async def test_mount_files_skips_foreign_user_session(self, orchestrator, mock_session_service, mock_file_service):
        """Defense in depth: even if a fresh execution session is created
        (per _get_or_create_session security check), _mount_files must refuse
        to copy file contents out of a session owned by a different user."""
        from src.models.exec import RequestFile
        from src.models.files import FileInfo

        # Foreign session (owned by user-A) referenced by user-B's request.
        foreign_session = self._session("upload-by-A", user_id="user-A")
        mock_session_service.get_session.return_value = foreign_session

        # File service would otherwise happily return content for the file.
        foreign_file = FileInfo(
            file_id="fid",
            filename="secret.csv",
            size=10,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/secret.csv",
        )
        mock_file_service.get_file_info.return_value = foreign_file
        mock_file_service.list_files.return_value = []
        mock_file_service.get_file_content = AsyncMock(return_value=b"SECRET")
        mock_file_service.store_uploaded_file = AsyncMock()

        request = ExecRequest(
            code="print('hi')",
            lang="py",
            user_id="user-B",
            files=[RequestFile(id="fid", session_id="upload-by-A", name="secret.csv")],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="req",
            session_id="fresh-for-B",
        )

        mounted = await orchestrator._mount_files(ctx)

        assert mounted == [], "foreign file content must not leak into user-B's pod"
        mock_file_service.get_file_content.assert_not_called()
        mock_file_service.store_uploaded_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_mount_files_allows_same_user_cross_session(
        self, orchestrator, mock_session_service, mock_file_service
    ):
        """Same user can still reference files from a previous session of
        theirs (legitimate continuity)."""
        from src.models.exec import RequestFile
        from src.models.files import FileInfo

        own_session = self._session("upload-by-A", user_id="user-A")
        mock_session_service.get_session.return_value = own_session

        own_file = FileInfo(
            file_id="fid",
            filename="mine.csv",
            size=10,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/mine.csv",
        )
        mock_file_service.get_file_info.return_value = own_file
        mock_file_service.list_files.return_value = []
        mock_file_service.get_file_content = AsyncMock(return_value=b"data")
        mock_file_service.store_uploaded_file = AsyncMock(return_value="new-id")

        request = ExecRequest(
            code="print('hi')",
            lang="py",
            user_id="user-A",
            files=[RequestFile(id="fid", session_id="upload-by-A", name="mine.csv")],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="req",
            session_id="exec-for-A",
        )

        mounted = await orchestrator._mount_files(ctx)

        assert len(mounted) == 1
        assert mounted[0]["filename"] == "mine.csv"

    @pytest.mark.asyncio
    async def test_mount_files_allows_legacy_anonymous_session(
        self, orchestrator, mock_session_service, mock_file_service
    ):
        """Sessions created before user_id was tracked carry no metadata.
        We allow these for backward compat (the leak window predates the
        ownership concept; no further-leakage is created)."""
        from src.models.exec import RequestFile
        from src.models.files import FileInfo

        legacy_session = self._session("legacy-upload")  # no user_id metadata
        mock_session_service.get_session.return_value = legacy_session

        info = FileInfo(
            file_id="fid",
            filename="old.csv",
            size=10,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/old.csv",
        )
        mock_file_service.get_file_info.return_value = info
        mock_file_service.list_files.return_value = []
        mock_file_service.get_file_content = AsyncMock(return_value=b"old")
        mock_file_service.store_uploaded_file = AsyncMock(return_value="new-id")

        request = ExecRequest(
            code="print('hi')",
            lang="py",
            user_id="user-A",
            files=[RequestFile(id="fid", session_id="legacy-upload", name="old.csv")],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="req",
            session_id="exec-for-A",
        )

        mounted = await orchestrator._mount_files(ctx)

        assert len(mounted) == 1
