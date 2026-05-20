"""Unit tests for Code Execution API endpoint."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from src.api.exec import execute_code, router
from src.models.exec import ExecRequest, ExecResponse


@pytest.fixture
def mock_request():
    """Create a mock HTTP request.

    ``state.user_id`` is explicitly set to None so the JWT-precedence
    check in exec.py (``getattr(http_request.state, "user_id", None)``)
    correctly falls through to header/body resolution. Without this,
    MagicMock would auto-create a truthy attribute and short-circuit
    the resolution chain.
    """
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.api_key_hash = "abc123hash"
    request.state.is_env_key = False
    request.state.user_id = None
    return request


@pytest.fixture
def mock_session_service():
    """Create a mock session service."""
    return MagicMock()


@pytest.fixture
def mock_file_service():
    """Create a mock file service."""
    return MagicMock()


@pytest.fixture
def mock_execution_service():
    """Create a mock execution service."""
    return MagicMock()


@pytest.fixture
def mock_state_service():
    """Create a mock state service."""
    return MagicMock()


@pytest.fixture
def mock_state_archival_service():
    """Create a mock state archival service."""
    return MagicMock()


class TestExecuteCodeEndpoint:
    """Tests for execute_code endpoint."""

    @pytest.mark.asyncio
    async def test_execute_code_success(
        self,
        mock_request,
        mock_session_service,
        mock_file_service,
        mock_execution_service,
        mock_state_service,
        mock_state_archival_service,
    ):
        """Test successful code execution."""
        exec_request = ExecRequest(code="print('hello')", lang="python")
        expected_response = ExecResponse(
            session_id="session-123",
            stdout="hello",
            stderr="",
        )

        with patch("src.api.exec.ExecutionOrchestrator") as MockOrchestrator:
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=expected_response)
            MockOrchestrator.return_value = mock_orchestrator

            response = await execute_code(
                request=exec_request,
                http_request=mock_request,
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

        assert response.session_id == "session-123"
        assert response.stdout == "hello"
        mock_orchestrator.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_code_passes_api_key_info(
        self,
        mock_request,
        mock_session_service,
        mock_file_service,
        mock_execution_service,
        mock_state_service,
        mock_state_archival_service,
    ):
        """Test that API key info is passed to orchestrator."""
        mock_request.state.api_key_hash = "testhash12345"
        mock_request.state.is_env_key = True

        exec_request = ExecRequest(code="print('hello')", lang="python")
        expected_response = ExecResponse(
            session_id="session-123",
            stdout="hello",
            stderr="",
        )

        with patch("src.api.exec.ExecutionOrchestrator") as MockOrchestrator:
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=expected_response)
            MockOrchestrator.return_value = mock_orchestrator

            await execute_code(
                request=exec_request,
                http_request=mock_request,
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

            # Verify the execute was called with correct api_key_hash and is_env_key
            call_args = mock_orchestrator.execute.call_args
            assert call_args.kwargs["api_key_hash"] == "testhash12345"
            assert call_args.kwargs["is_env_key"] is True

    @pytest.mark.asyncio
    async def test_execute_code_creates_orchestrator_with_services(
        self,
        mock_request,
        mock_session_service,
        mock_file_service,
        mock_execution_service,
        mock_state_service,
        mock_state_archival_service,
    ):
        """Test that orchestrator is created with all services."""
        exec_request = ExecRequest(code="print('hello')", lang="python")
        expected_response = ExecResponse(
            session_id="session-123",
            stdout="hello",
            stderr="",
        )

        with patch("src.api.exec.ExecutionOrchestrator") as MockOrchestrator:
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=expected_response)
            MockOrchestrator.return_value = mock_orchestrator

            await execute_code(
                request=exec_request,
                http_request=mock_request,
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

            # Verify orchestrator was created with all services
            MockOrchestrator.assert_called_once_with(
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

    @pytest.mark.asyncio
    async def test_execute_code_handles_missing_api_key_hash(
        self,
        mock_session_service,
        mock_file_service,
        mock_execution_service,
        mock_state_service,
        mock_state_archival_service,
    ):
        """Test execution when api_key_hash is not set in request state."""
        mock_request = MagicMock(spec=Request)
        mock_request.state = MagicMock(spec=[])  # Empty spec means no attributes

        exec_request = ExecRequest(code="print('hello')", lang="python")
        expected_response = ExecResponse(
            session_id="session-123",
            stdout="hello",
            stderr="",
        )

        with patch("src.api.exec.ExecutionOrchestrator") as MockOrchestrator:
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=expected_response)
            MockOrchestrator.return_value = mock_orchestrator

            response = await execute_code(
                request=exec_request,
                http_request=mock_request,
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

        assert response is not None
        # Verify execute was called with None api_key_hash
        call_args = mock_orchestrator.execute.call_args
        assert call_args.kwargs["api_key_hash"] is None
        assert call_args.kwargs["is_env_key"] is False

    @pytest.mark.asyncio
    async def test_execute_code_returns_exec_response(
        self,
        mock_request,
        mock_session_service,
        mock_file_service,
        mock_execution_service,
        mock_state_service,
        mock_state_archival_service,
    ):
        """Test that execute_code returns ExecResponse."""
        exec_request = ExecRequest(code="1+1", lang="python")
        expected_response = ExecResponse(
            session_id="session-456",
            stdout="2",
            stderr="",
            files=[],
        )

        with patch("src.api.exec.ExecutionOrchestrator") as MockOrchestrator:
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=expected_response)
            MockOrchestrator.return_value = mock_orchestrator

            response = await execute_code(
                request=exec_request,
                http_request=mock_request,
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

        assert isinstance(response, ExecResponse)
        assert response.session_id == "session-456"
        assert response.stdout == "2"
        assert response.stderr == ""

    @pytest.mark.asyncio
    async def test_execute_code_with_entity_id(
        self,
        mock_request,
        mock_session_service,
        mock_file_service,
        mock_execution_service,
        mock_state_service,
        mock_state_archival_service,
    ):
        """Test code execution with entity_id."""
        exec_request = ExecRequest(
            code="print('hello')",
            lang="python",
            entity_id="entity-123",
            user_id="user-456",
        )
        expected_response = ExecResponse(
            session_id="session-123",
            stdout="hello",
            stderr="",
        )

        with patch("src.api.exec.ExecutionOrchestrator") as MockOrchestrator:
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=expected_response)
            MockOrchestrator.return_value = mock_orchestrator

            response = await execute_code(
                request=exec_request,
                http_request=mock_request,
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

        assert response.session_id == "session-123"
        # Verify the request was passed to execute
        call_args = mock_orchestrator.execute.call_args
        assert call_args.args[0].entity_id == "entity-123"
        assert call_args.args[0].user_id == "user-456"


class TestUserIdHeaderFallback:
    """LibreChat 0.8.5 sends user_id in the User-Id HTTP header, not in the
    request body. exec.py must fall back to the header when the body field
    is missing, so the cross-user session-isolation check in
    orchestrator._get_or_create_session has a user_id to match against.
    """

    @pytest.mark.asyncio
    async def test_user_id_falls_back_to_user_id_header(
        self,
        mock_session_service,
        mock_file_service,
        mock_execution_service,
        mock_state_service,
        mock_state_archival_service,
    ):
        """Body user_id is missing → exec.py copies User-Id header onto request."""
        http_request = MagicMock(spec=Request)
        http_request.state = MagicMock()
        http_request.state.api_key_hash = "abc123hash"
        http_request.state.is_env_key = False
        http_request.state.user_id = None
        http_request.headers = {"user-id": "user-from-header", "x-user-id": None}

        exec_request = ExecRequest(code="print('hi')", lang="py")
        expected = ExecResponse(session_id="s", stdout="", stderr="")

        with patch("src.api.exec.ExecutionOrchestrator") as MockOrchestrator:
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=expected)
            MockOrchestrator.return_value = mock_orchestrator

            await execute_code(
                request=exec_request,
                http_request=http_request,
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

        passed = mock_orchestrator.execute.call_args.args[0]
        assert passed.user_id == "user-from-header"

    @pytest.mark.asyncio
    async def test_user_id_falls_back_to_x_user_id_header(
        self,
        mock_session_service,
        mock_file_service,
        mock_execution_service,
        mock_state_service,
        mock_state_archival_service,
    ):
        """X-User-Id is accepted when User-Id is absent (X- naming convention)."""
        http_request = MagicMock(spec=Request)
        http_request.state = MagicMock()
        http_request.state.api_key_hash = "abc123hash"
        http_request.state.is_env_key = False
        http_request.state.user_id = None
        http_request.headers = {"user-id": None, "x-user-id": "user-x"}

        exec_request = ExecRequest(code="print('hi')", lang="py")
        expected = ExecResponse(session_id="s", stdout="", stderr="")

        with patch("src.api.exec.ExecutionOrchestrator") as MockOrchestrator:
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=expected)
            MockOrchestrator.return_value = mock_orchestrator

            await execute_code(
                request=exec_request,
                http_request=http_request,
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

        passed = mock_orchestrator.execute.call_args.args[0]
        assert passed.user_id == "user-x"

    @pytest.mark.asyncio
    async def test_body_user_id_wins_over_header(
        self,
        mock_session_service,
        mock_file_service,
        mock_execution_service,
        mock_state_service,
        mock_state_archival_service,
    ):
        """When the request body already carries user_id, the header is ignored.
        This preserves the existing API contract for direct-API callers."""
        http_request = MagicMock(spec=Request)
        http_request.state = MagicMock()
        http_request.state.api_key_hash = "abc123hash"
        http_request.state.is_env_key = False
        http_request.state.user_id = None
        http_request.headers = {"user-id": "user-from-header", "x-user-id": None}

        exec_request = ExecRequest(code="print('hi')", lang="py", user_id="user-from-body")
        expected = ExecResponse(session_id="s", stdout="", stderr="")

        with patch("src.api.exec.ExecutionOrchestrator") as MockOrchestrator:
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=expected)
            MockOrchestrator.return_value = mock_orchestrator

            await execute_code(
                request=exec_request,
                http_request=http_request,
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

        passed = mock_orchestrator.execute.call_args.args[0]
        assert passed.user_id == "user-from-body"


class TestJwtUserIdPrecedence:
    """request.state.user_id (set by SecurityMiddleware after JWT verification)
    is cryptographically authenticated and MUST override both the request body
    user_id and the User-Id header. Without this, an attacker holding a valid
    JWT for user-A could pass user_id=victim in the request body and operate
    against the victim's sessions."""

    @pytest.mark.asyncio
    async def test_jwt_user_id_overrides_body_user_id(
        self,
        mock_session_service,
        mock_file_service,
        mock_execution_service,
        mock_state_service,
        mock_state_archival_service,
    ):
        """Attacker with JWT for user-A submits body user_id=user-victim.
        Resolved user_id must be user-A (the JWT's sub)."""
        http_request = MagicMock(spec=Request)
        http_request.state = MagicMock()
        http_request.state.api_key_hash = "jwt:abc"
        http_request.state.is_env_key = False
        http_request.state.user_id = "user-A-jwt"  # set by middleware
        http_request.headers = {"user-id": None, "x-user-id": None}

        exec_request = ExecRequest(code="print('x')", lang="py", user_id="user-victim")
        expected = ExecResponse(session_id="s", stdout="", stderr="")

        with patch("src.api.exec.ExecutionOrchestrator") as MockOrchestrator:
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=expected)
            MockOrchestrator.return_value = mock_orchestrator

            await execute_code(
                request=exec_request,
                http_request=http_request,
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

        passed = mock_orchestrator.execute.call_args.args[0]
        assert passed.user_id == "user-A-jwt"

    @pytest.mark.asyncio
    async def test_jwt_user_id_overrides_user_id_header(
        self,
        mock_session_service,
        mock_file_service,
        mock_execution_service,
        mock_state_service,
        mock_state_archival_service,
    ):
        """Attacker sets User-Id: victim but holds valid JWT for user-A.
        JWT wins; header is ignored."""
        http_request = MagicMock(spec=Request)
        http_request.state = MagicMock()
        http_request.state.api_key_hash = "jwt:abc"
        http_request.state.is_env_key = False
        http_request.state.user_id = "user-A-jwt"
        http_request.headers = {"user-id": "user-victim", "x-user-id": None}

        exec_request = ExecRequest(code="print('x')", lang="py")
        expected = ExecResponse(session_id="s", stdout="", stderr="")

        with patch("src.api.exec.ExecutionOrchestrator") as MockOrchestrator:
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=expected)
            MockOrchestrator.return_value = mock_orchestrator

            await execute_code(
                request=exec_request,
                http_request=http_request,
                session_service=mock_session_service,
                file_service=mock_file_service,
                execution_service=mock_execution_service,
                state_service=mock_state_service,
                state_archival_service=mock_state_archival_service,
            )

        passed = mock_orchestrator.execute.call_args.args[0]
        assert passed.user_id == "user-A-jwt"
