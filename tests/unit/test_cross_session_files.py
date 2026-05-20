"""Tests for cross-session file consolidation (GitHub issue #34).

When multiple files are uploaded separately with entity_id=null, each upload
creates a separate session. The first execution mounts files from all sessions
via explicit file refs, but subsequent executions only see the chosen session's
files — losing files from other sessions.

The fix consolidates cross-session files into the chosen session during
_mount_files so they persist for subsequent executions.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import (
    ExecRequest,
    Session,
    SessionStatus,
)
from src.models.exec import RequestFile
from src.models.files import FileInfo
from src.services.orchestrator import ExecutionContext, ExecutionOrchestrator


def _anon_http_request():
    """Build a MagicMock HTTP request with no JWT-resolved user_id.

    upload_file/upload_files_batch now take a `request: Request` parameter
    and read `request.state.user_id` first (set by SecurityMiddleware
    after JWT verification). Tests that don't exercise the JWT path
    pass an anonymous request so resolution falls through to headers.
    """
    request = MagicMock()
    request.state = MagicMock()
    request.state.user_id = None
    return request


@pytest.fixture
def mock_session_service():
    service = MagicMock()
    service.get_session = AsyncMock()
    service.create_session = AsyncMock()
    service.list_sessions_by_entity = AsyncMock(return_value=[])
    return service


@pytest.fixture
def mock_file_service():
    service = MagicMock()
    service.get_file_info = AsyncMock()
    service.list_files = AsyncMock(return_value=[])
    service.get_file_content = AsyncMock(return_value=b"file content")
    service.store_uploaded_file = AsyncMock(return_value="consolidated-file-id")
    return service


@pytest.fixture
def mock_execution_service():
    service = MagicMock()
    service.execute_code = AsyncMock()
    return service


@pytest.fixture
def mock_state_service():
    service = MagicMock()
    service.get_state = AsyncMock(return_value=None)
    return service


@pytest.fixture
def orchestrator(mock_session_service, mock_file_service, mock_execution_service, mock_state_service):
    return ExecutionOrchestrator(
        session_service=mock_session_service,
        file_service=mock_file_service,
        execution_service=mock_execution_service,
        state_service=mock_state_service,
    )


def _make_session(session_id):
    return Session(
        session_id=session_id,
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(),
        expires_at=datetime.now() + timedelta(hours=1),
    )


class TestCrossSessionFileConsolidation:
    """Tests reproducing and verifying the fix for issue #34."""

    @pytest.mark.asyncio
    async def test_files_from_multiple_sessions_are_consolidated(self, orchestrator, mock_file_service):
        """Reproduce issue #34: files uploaded in separate sessions should be
        consolidated into the chosen session during mount.

        Scenario:
        1. File A uploaded → session S1
        2. File B uploaded → session S2
        3. Exec request references both files from S1 and S2
        4. Orchestrator picks S1 as the session
        5. File B (from S2) should be registered into S1
        """
        file_a_info = FileInfo(
            file_id="file-a",
            filename="data_a.csv",
            size=200,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/data_a.csv",
        )
        file_b_info = FileInfo(
            file_id="file-b",
            filename="data_b.csv",
            size=300,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/data_b.csv",
        )

        # get_file_info returns the right file for each session
        async def get_file_info_side_effect(session_id, file_id):
            if session_id == "session-1" and file_id == "file-a":
                return file_a_info
            if session_id == "session-2" and file_id == "file-b":
                return file_b_info
            return None

        mock_file_service.get_file_info.side_effect = get_file_info_side_effect
        mock_file_service.get_file_content.return_value = b"csv data"

        # Legacy pre-user_id sessions: session lookup returns None so the
        # authorization check treats them as legacy/anonymous and allows
        # cross-session mount (backward compatibility). The issue-#34
        # scenario predates the user_id ownership concept.
        orchestrator.session_service.get_session.return_value = None

        # Create request with files from two different sessions
        request = ExecRequest(
            code="import pandas as pd; df = pd.read_csv('data_b.csv')",
            lang="python",
            files=[
                RequestFile(id="file-a", session_id="session-1", name="data_a.csv"),
                RequestFile(id="file-b", session_id="session-2", name="data_b.csv"),
            ],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-1",  # Orchestrator chose session-1
        )

        mounted = await orchestrator._mount_files(ctx)

        # Both files should be mounted
        assert len(mounted) == 2
        filenames = {f["filename"] for f in mounted}
        assert "data_a.csv" in filenames
        assert "data_b.csv" in filenames

        # File B (from session-2) should be consolidated into session-1
        mock_file_service.store_uploaded_file.assert_called_once_with(
            session_id="session-1",
            filename="data_b.csv",
            content=b"csv data",
            content_type="text/csv",
        )

    @pytest.mark.asyncio
    async def test_same_session_files_not_duplicated(self, orchestrator, mock_file_service):
        """Files already in the chosen session should NOT be re-stored."""
        file_info = FileInfo(
            file_id="file-a",
            filename="data.csv",
            size=200,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/data.csv",
        )

        mock_file_service.get_file_info.return_value = file_info
        mock_file_service.get_file_content.return_value = b"csv data"

        request = ExecRequest(
            code="print('hello')",
            lang="python",
            files=[
                RequestFile(id="file-a", session_id="session-1", name="data.csv"),
            ],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-1",
        )

        mounted = await orchestrator._mount_files(ctx)

        assert len(mounted) == 1
        # Should NOT call store_uploaded_file since file is already in session-1
        mock_file_service.store_uploaded_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_consolidation_failure_does_not_break_mount(self, orchestrator, mock_file_service):
        """If consolidation fails (store_uploaded_file raises), the file
        should still be mounted for the current execution."""
        file_a_info = FileInfo(
            file_id="file-a",
            filename="data_a.csv",
            size=200,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/data_a.csv",
        )
        file_b_info = FileInfo(
            file_id="file-b",
            filename="data_b.csv",
            size=300,
            content_type="text/csv",
            created_at=datetime.now(),
            path="/data_b.csv",
        )

        async def get_file_info_side_effect(session_id, file_id):
            if session_id == "session-1" and file_id == "file-a":
                return file_a_info
            if session_id == "session-2" and file_id == "file-b":
                return file_b_info
            return None

        mock_file_service.get_file_info.side_effect = get_file_info_side_effect
        mock_file_service.get_file_content.return_value = b"csv data"
        mock_file_service.store_uploaded_file.side_effect = Exception("MinIO error")

        # Legacy session (no user_id metadata) — see comment in the
        # consolidation test above.
        orchestrator.session_service.get_session.return_value = None

        request = ExecRequest(
            code="print('hello')",
            lang="python",
            files=[
                RequestFile(id="file-a", session_id="session-1", name="data_a.csv"),
                RequestFile(id="file-b", session_id="session-2", name="data_b.csv"),
            ],
        )
        ctx = ExecutionContext(
            request=request,
            request_id="req-123",
            session_id="session-1",
        )

        mounted = await orchestrator._mount_files(ctx)

        # Both files should still be mounted even if consolidation failed
        assert len(mounted) == 2


class TestUploadSessionReuse:
    """Tests for upload endpoint reusing sessions by entity_id."""

    @pytest.mark.asyncio
    async def test_upload_reuses_session_with_entity_id_same_user(self):
        """When entity_id and User-Id are provided AND the existing session
        was created by the same user, upload should reuse it.

        Cross-user sharing via a shared entity_id is now blocked (see
        test_upload_does_NOT_reuse_session_for_different_user below).
        """
        from src.api.files import upload_file

        existing_session = MagicMock()
        existing_session.session_id = "existing-session"
        existing_session.status = MagicMock()
        existing_session.status.value = "active"
        existing_session.metadata = {"user_id": "user-A", "entity_id": "conversation-123"}

        mock_session_service = MagicMock()
        mock_session_service.list_sessions_by_entity = AsyncMock(return_value=[existing_session])
        mock_session_service.create_session = AsyncMock()

        mock_file_service = MagicMock()
        mock_file_service.store_uploaded_file = AsyncMock(return_value="file-123")

        mock_file = MagicMock()
        mock_file.filename = "test.csv"
        mock_file.content_type = "text/csv"
        mock_file.size = 100
        mock_file.read = AsyncMock(return_value=b"csv data")

        result = await upload_file(
            request=_anon_http_request(),
            file=mock_file,
            files=None,
            entity_id="conversation-123",
            user_id_header="user-A",
            x_user_id_header=None,
            file_service=mock_file_service,
            session_service=mock_session_service,
        )

        # Should reuse existing session, not create a new one
        mock_session_service.create_session.assert_not_called()
        assert result["session_id"] == "existing-session"

    @pytest.mark.asyncio
    async def test_upload_does_NOT_reuse_session_for_different_user(self):
        """SECURITY: same entity_id shared across users must NOT collapse
        them into one session. User-B uploading must create a fresh session
        even though user-A's session exists for the same entity_id."""
        from src.api.files import upload_file

        a_session = MagicMock()
        a_session.session_id = "session-by-A"
        a_session.status = MagicMock()
        a_session.status.value = "active"
        a_session.metadata = {"user_id": "user-A", "entity_id": "conversation-123"}

        new_session = MagicMock()
        new_session.session_id = "new-for-B"

        mock_session_service = MagicMock()
        mock_session_service.list_sessions_by_entity = AsyncMock(return_value=[a_session])
        mock_session_service.create_session = AsyncMock(return_value=new_session)

        mock_file_service = MagicMock()
        mock_file_service.store_uploaded_file = AsyncMock(return_value="file-123")

        mock_file = MagicMock()
        mock_file.filename = "test.csv"
        mock_file.content_type = "text/csv"
        mock_file.size = 100
        mock_file.read = AsyncMock(return_value=b"csv data")

        result = await upload_file(
            request=_anon_http_request(),
            file=mock_file,
            files=None,
            entity_id="conversation-123",
            user_id_header="user-B",
            x_user_id_header=None,
            file_service=mock_file_service,
            session_service=mock_session_service,
        )

        # Must create a fresh session for user-B; must NOT reuse user-A's.
        mock_session_service.create_session.assert_called_once()
        assert result["session_id"] == "new-for-B"

    @pytest.mark.asyncio
    async def test_upload_persists_user_id_on_session(self):
        """User-Id header is persisted on session.metadata.user_id so the
        cross-user isolation check at exec-time can recognize ownership."""
        from src.api.files import upload_file
        from src.models.session import SessionCreate

        new_session = MagicMock()
        new_session.session_id = "new-session"

        mock_session_service = MagicMock()
        mock_session_service.list_sessions_by_entity = AsyncMock(return_value=[])
        mock_session_service.create_session = AsyncMock(return_value=new_session)

        mock_file_service = MagicMock()
        mock_file_service.store_uploaded_file = AsyncMock(return_value="file-123")

        mock_file = MagicMock()
        mock_file.filename = "test.csv"
        mock_file.content_type = "text/csv"
        mock_file.size = 100
        mock_file.read = AsyncMock(return_value=b"csv data")

        await upload_file(
            request=_anon_http_request(),
            file=mock_file,
            files=None,
            entity_id="conv-1",
            user_id_header="user-A",
            x_user_id_header=None,
            file_service=mock_file_service,
            session_service=mock_session_service,
        )

        call = mock_session_service.create_session.call_args
        sc: SessionCreate = call.args[0]
        assert sc.metadata.get("user_id") == "user-A"
        assert sc.metadata.get("entity_id") == "conv-1"

    @pytest.mark.asyncio
    async def test_upload_creates_session_without_entity_id(self):
        """When entity_id is null, upload should create a new session."""
        from src.api.files import upload_file

        new_session = MagicMock()
        new_session.session_id = "new-session"

        mock_session_service = MagicMock()
        mock_session_service.create_session = AsyncMock(return_value=new_session)

        mock_file_service = MagicMock()
        mock_file_service.store_uploaded_file = AsyncMock(return_value="file-123")

        mock_file = MagicMock()
        mock_file.filename = "test.csv"
        mock_file.content_type = "text/csv"
        mock_file.size = 100
        mock_file.read = AsyncMock(return_value=b"csv data")

        result = await upload_file(
            request=_anon_http_request(),
            file=mock_file,
            files=None,
            entity_id=None,
            user_id_header=None,
            x_user_id_header=None,
            file_service=mock_file_service,
            session_service=mock_session_service,
        )

        # Should create a new session
        mock_session_service.create_session.assert_called_once()
        assert result["session_id"] == "new-session"

    @pytest.mark.asyncio
    async def test_upload_creates_session_when_entity_lookup_fails(self):
        """When entity_id lookup fails, fall back to creating a new session."""
        from src.api.files import upload_file

        new_session = MagicMock()
        new_session.session_id = "new-session"

        mock_session_service = MagicMock()
        mock_session_service.list_sessions_by_entity = AsyncMock(return_value=[])
        mock_session_service.create_session = AsyncMock(return_value=new_session)

        mock_file_service = MagicMock()
        mock_file_service.store_uploaded_file = AsyncMock(return_value="file-123")

        mock_file = MagicMock()
        mock_file.filename = "test.csv"
        mock_file.content_type = "text/csv"
        mock_file.size = 100
        mock_file.read = AsyncMock(return_value=b"csv data")

        result = await upload_file(
            request=_anon_http_request(),
            file=mock_file,
            files=None,
            entity_id="conversation-123",
            user_id_header="user-A",
            x_user_id_header=None,
            file_service=mock_file_service,
            session_service=mock_session_service,
        )

        # No existing session found, should create new one
        mock_session_service.create_session.assert_called_once()
        assert result["session_id"] == "new-session"
