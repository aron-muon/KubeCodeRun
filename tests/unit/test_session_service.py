"""Unit tests for the session service."""

import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.session import Session, SessionCreate, SessionStatus
from src.services.session import SessionService


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis_mock = AsyncMock()

    # Mock pipeline
    pipeline_mock = AsyncMock()
    pipeline_mock.hset = MagicMock()
    pipeline_mock.expire = MagicMock()
    pipeline_mock.sadd = MagicMock()
    pipeline_mock.delete = MagicMock()
    pipeline_mock.srem = MagicMock()
    pipeline_mock.execute = AsyncMock(return_value=[True, True, True])
    pipeline_mock.reset = AsyncMock()

    # Make pipeline() return the pipeline mock (synchronous, like redis.asyncio)
    redis_mock.pipeline = MagicMock(return_value=pipeline_mock)
    return redis_mock


@pytest.fixture
def session_service(mock_redis):
    """Create a session service with mocked Redis."""
    return SessionService(redis_client=mock_redis)


@pytest.mark.asyncio
async def test_create_session(session_service, mock_redis):
    """Test session creation."""
    request = SessionCreate(metadata={"test": "value"})

    session = await session_service.create_session(request)

    assert session.session_id is not None
    assert session.status == SessionStatus.ACTIVE
    assert session.metadata == {"test": "value"}
    assert isinstance(session.created_at, datetime)
    assert isinstance(session.expires_at, datetime)
    assert session.expires_at > session.created_at

    # Verify Redis operations were called
    mock_redis.pipeline.assert_called_once()


@pytest.mark.asyncio
async def test_get_session_exists(session_service, mock_redis):
    """Test retrieving an existing session."""
    session_id = "test-session-id"
    session_data = {
        "session_id": session_id,
        "status": "active",
        "created_at": "2023-01-01T00:00:00",
        "last_activity": "2023-01-01T00:00:00",
        "expires_at": "2023-01-02T00:00:00",
        "files": "{}",
        "metadata": '{"test": "value"}',
        "working_directory": "/workspace",
    }

    mock_redis.hgetall.return_value = session_data
    mock_redis.hset = AsyncMock()

    session = await session_service.get_session(session_id)

    assert session is not None
    assert session.session_id == session_id
    assert session.status == SessionStatus.ACTIVE
    assert session.metadata == {"test": "value"}
    assert session.files == {}

    # Verify last activity was updated
    mock_redis.hset.assert_called_once()


@pytest.mark.asyncio
async def test_get_session_not_exists(session_service, mock_redis):
    """Test retrieving a non-existent session."""
    mock_redis.hgetall.return_value = {}

    session = await session_service.get_session("non-existent")

    assert session is None


@pytest.mark.asyncio
async def test_update_session(session_service, mock_redis):
    """Test updating a session."""
    session_id = "test-session-id"

    # Mock session exists
    mock_redis.exists.return_value = True
    mock_redis.hset = AsyncMock()

    # Mock get_session to return updated session
    updated_session_data = {
        "session_id": session_id,
        "status": "idle",
        "created_at": "2023-01-01T00:00:00",
        "last_activity": datetime.now(UTC).isoformat(),
        "expires_at": "2023-01-02T00:00:00",
        "files": "{}",
        "metadata": "{}",
        "working_directory": "/workspace",
    }
    mock_redis.hgetall.return_value = updated_session_data

    session = await session_service.update_session(session_id, status=SessionStatus.IDLE)

    assert session is not None
    assert session.session_id == session_id

    # Verify Redis update was called
    mock_redis.hset.assert_called()


@pytest.mark.asyncio
async def test_update_session_not_exists(session_service, mock_redis):
    """Test updating a non-existent session."""
    mock_redis.exists.return_value = False

    session = await session_service.update_session("non-existent", status=SessionStatus.IDLE)

    assert session is None


@pytest.mark.asyncio
async def test_delete_session(session_service, mock_redis):
    """Test deleting a session."""
    session_id = "test-session-id"

    # Mock get_session to return None (session doesn't exist, skip cleanup)
    mock_redis.hgetall.return_value = {}

    # The pipeline mock is already set up in the fixture
    pipeline_mock = mock_redis.pipeline.return_value
    pipeline_mock.execute.return_value = [1, 1]  # Both operations successful

    result = await session_service.delete_session(session_id)

    assert result is True
    pipeline_mock.delete.assert_called_once()
    pipeline_mock.srem.assert_called_once()


@pytest.mark.asyncio
async def test_list_sessions(session_service, mock_redis):
    """Test listing sessions."""
    session_ids = ["session1", "session2", "session3"]
    mock_redis.smembers.return_value = session_ids

    # Mock get_session to return valid sessions
    def mock_hgetall(key):
        session_id = key.split(":")[-1]  # Extract session ID from key
        return {
            "session_id": session_id,
            "status": "active",
            "created_at": "2023-01-01T00:00:00+00:00",
            "last_activity": "2023-01-01T00:00:00+00:00",
            "expires_at": "2023-01-02T00:00:00+00:00",
            "files": "{}",
            "metadata": "{}",
            "working_directory": "/workspace",
        }

    mock_redis.hgetall.side_effect = mock_hgetall
    mock_redis.hset = AsyncMock()

    sessions = await session_service.list_sessions(limit=2)

    assert len(sessions) == 2  # Limited to 2
    assert all(isinstance(s, Session) for s in sessions)


@pytest.mark.asyncio
async def test_cleanup_expired_sessions(session_service, mock_redis):
    """Test cleaning up expired sessions."""
    session_ids = ["expired1", "expired2", "active1"]
    mock_redis.smembers.return_value = session_ids

    # Mock sessions - some expired, some active
    def mock_get_session(session_id):
        if session_id.startswith("expired"):
            return Session(
                session_id=session_id,
                status=SessionStatus.ACTIVE,
                created_at=datetime.now(UTC) - timedelta(days=2),
                last_activity=datetime.now(UTC) - timedelta(days=2),
                expires_at=datetime.now(UTC) - timedelta(hours=1),  # Expired
            )
        else:
            return Session(
                session_id=session_id,
                status=SessionStatus.ACTIVE,
                created_at=datetime.now(UTC),
                last_activity=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),  # Active
            )

    # The pipeline mock is already set up in the fixture
    pipeline_mock = mock_redis.pipeline.return_value
    pipeline_mock.execute.return_value = [1, 1]

    with patch.object(session_service, "get_session", side_effect=mock_get_session):
        cleaned_count = await session_service.cleanup_expired_sessions()

    assert cleaned_count == 2  # Two expired sessions cleaned


@pytest.mark.asyncio
async def test_session_key_generation(session_service):
    """Test session key generation."""
    session_id = "test-session"
    key = session_service._session_key(session_id)
    assert key == "sessions:test-session"


@pytest.mark.asyncio
async def test_generate_session_id(session_service):
    """Test session ID generation."""
    session_id = session_service._generate_session_id()
    assert isinstance(session_id, str)
    assert len(session_id) > 0

    # Generate another to ensure uniqueness
    session_id2 = session_service._generate_session_id()
    assert session_id != session_id2


@pytest.mark.asyncio
async def test_cleanup_task_lifecycle(session_service, mock_redis):
    """Test cleanup task start and stop."""
    # Start cleanup task
    await session_service.start_cleanup_task()
    assert session_service._cleanup_task is not None
    assert not session_service._cleanup_task.done()

    # Stop cleanup task
    await session_service.stop_cleanup_task()
    assert session_service._cleanup_task.done()


@pytest.mark.asyncio
async def test_create_session_with_entity_id(session_service, mock_redis):
    """Test session creation with entity_id."""
    request = SessionCreate(metadata={"entity_id": "test-entity", "test": "value"})

    session = await session_service.create_session(request)

    assert session.session_id is not None
    assert session.status == SessionStatus.ACTIVE
    assert session.metadata == {"entity_id": "test-entity", "test": "value"}

    # Verify Redis operations were called including entity grouping
    mock_redis.pipeline.assert_called_once()


@pytest.mark.asyncio
async def test_list_sessions_by_entity(session_service, mock_redis):
    """Test listing sessions by entity ID."""
    entity_id = "test-entity"
    session_ids = ["session1", "session2"]

    mock_redis.smembers.return_value = session_ids

    # Mock get_session to return valid sessions
    def mock_hgetall(key):
        session_id = key.split(":")[-1]
        return {
            "session_id": session_id,
            "status": "active",
            "created_at": "2023-01-01T00:00:00+00:00",
            "last_activity": "2023-01-01T00:00:00+00:00",
            "expires_at": "2023-01-02T00:00:00+00:00",
            "files": "{}",
            "metadata": '{"entity_id": "test-entity"}',
            "working_directory": "/workspace",
        }

    mock_redis.hgetall.side_effect = mock_hgetall
    mock_redis.hset = AsyncMock()

    sessions = await session_service.list_sessions_by_entity(entity_id)

    assert len(sessions) == 2
    assert all(isinstance(s, Session) for s in sessions)
    assert all(s.metadata.get("entity_id") == entity_id for s in sessions)


@pytest.mark.asyncio
async def test_validate_session_access_success(session_service, mock_redis):
    """Test successful session access validation."""
    session_id = "test-session"
    entity_id = "test-entity"

    session_data = {
        "session_id": session_id,
        "status": "active",
        "created_at": "2023-01-01T00:00:00+00:00",
        "last_activity": "2023-01-01T00:00:00+00:00",
        "expires_at": "2023-01-02T00:00:00+00:00",
        "files": "{}",
        "metadata": f'{{"entity_id": "{entity_id}"}}',
        "working_directory": "/workspace",
    }

    mock_redis.hgetall.return_value = session_data
    mock_redis.hset = AsyncMock()

    result = await session_service.validate_session_access(session_id, entity_id)

    assert result is True


@pytest.mark.asyncio
async def test_validate_session_access_wrong_entity(session_service, mock_redis):
    """Test session access validation with wrong entity ID."""
    session_id = "test-session"
    entity_id = "wrong-entity"

    session_data = {
        "session_id": session_id,
        "status": "active",
        "created_at": "2023-01-01T00:00:00+00:00",
        "last_activity": "2023-01-01T00:00:00+00:00",
        "expires_at": "2023-01-02T00:00:00+00:00",
        "files": "{}",
        "metadata": '{"entity_id": "test-entity"}',
        "working_directory": "/workspace",
    }

    mock_redis.hgetall.return_value = session_data
    mock_redis.hset = AsyncMock()

    result = await session_service.validate_session_access(session_id, entity_id)

    assert result is False


@pytest.mark.asyncio
async def test_validate_session_access_no_session(session_service, mock_redis):
    """Test session access validation when session doesn't exist."""
    mock_redis.hgetall.return_value = {}

    result = await session_service.validate_session_access("non-existent", "test-entity")

    assert result is False


@pytest.mark.asyncio
async def test_get_session_files_access_success(session_service, mock_redis):
    """Test successful session files access validation."""
    session_id = "test-session"
    entity_id = "test-entity"

    # Mock validate_session_access to return True
    with patch.object(session_service, "validate_session_access", return_value=True):
        # Mock list_sessions_by_entity to return sessions including the target session
        mock_sessions = [
            Session(
                session_id=session_id,
                status=SessionStatus.ACTIVE,
                created_at=datetime.now(UTC),
                last_activity=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                metadata={"entity_id": entity_id},
            )
        ]
        with patch.object(session_service, "list_sessions_by_entity", return_value=mock_sessions):
            result = await session_service.get_session_files_access(session_id, entity_id)

            assert result is True


@pytest.mark.asyncio
async def test_get_session_files_access_invalid_session(session_service, mock_redis):
    """Test session files access validation with invalid session."""
    # Mock validate_session_access to return False
    with patch.object(session_service, "validate_session_access", return_value=False):
        result = await session_service.get_session_files_access("invalid-session", "test-entity")

        assert result is False


@pytest.mark.asyncio
async def test_delete_session_with_entity_cleanup(session_service, mock_redis):
    """Test deleting a session with entity cleanup."""
    session_id = "test-session-id"
    entity_id = "test-entity"

    # Mock get_session to return a session with entity_id
    session_data = {
        "session_id": session_id,
        "status": "active",
        "created_at": "2023-01-01T00:00:00+00:00",
        "last_activity": "2023-01-01T00:00:00+00:00",
        "expires_at": "2023-01-02T00:00:00+00:00",
        "files": "{}",
        "metadata": f'{{"entity_id": "{entity_id}"}}',
        "working_directory": "/workspace",
    }
    mock_redis.hgetall.return_value = session_data
    mock_redis.hset = AsyncMock()

    # The pipeline mock is already set up in the fixture
    pipeline_mock = mock_redis.pipeline.return_value
    pipeline_mock.execute.return_value = [1, 1, 1]  # Three operations successful

    result = await session_service.delete_session(session_id)

    assert result is True
    pipeline_mock.delete.assert_called_once()
    pipeline_mock.srem.assert_called()  # Called twice - once for session index, once for entity


@pytest.mark.asyncio
async def test_close(session_service, mock_redis):
    """Test service cleanup."""
    await session_service.start_cleanup_task()
    await session_service.close()

    # Verify cleanup task was stopped and Redis connection closed
    mock_redis.close.assert_called_once()


class TestKeyGeneration:
    """Tests for key generation methods."""

    def test_session_key(self, session_service):
        """Test session key generation."""
        key = session_service._session_key("session-123")
        assert key == "sessions:session-123"

    def test_session_index_key(self, session_service):
        """Test session index key generation."""
        key = session_service._session_index_key()
        assert key == "sessions:index"

    def test_entity_sessions_key(self, session_service):
        """Test entity sessions key generation."""
        key = session_service._entity_sessions_key("entity-123")
        assert key == "entity_sessions:entity-123"


class TestRedisConnectivity:
    """Tests for Redis connectivity check."""

    @pytest.mark.asyncio
    async def test_check_redis_connectivity_success(self, session_service, mock_redis):
        """Test successful Redis connectivity check."""
        mock_redis.ping.return_value = True

        result = await session_service._check_redis_connectivity()

        assert result is True
        assert session_service._redis_available is True

    @pytest.mark.asyncio
    async def test_check_redis_connectivity_failure(self, session_service, mock_redis):
        """Test failed Redis connectivity check."""
        mock_redis.ping.side_effect = Exception("Connection failed")

        result = await session_service._check_redis_connectivity()

        assert result is False
        assert session_service._redis_available is False


class TestCleanupTask:
    """Tests for cleanup task management."""

    @pytest.mark.asyncio
    async def test_start_cleanup_task_redis_unavailable(self, session_service, mock_redis):
        """Test cleanup task not started when Redis unavailable."""
        mock_redis.ping.side_effect = Exception("Connection failed")

        await session_service.start_cleanup_task()

        assert session_service._cleanup_task is None

    @pytest.mark.asyncio
    async def test_stop_cleanup_task_not_started(self, session_service):
        """Test stopping cleanup task when not started."""
        session_service._cleanup_task = None

        # Should not raise
        await session_service.stop_cleanup_task()


class TestForceCleanup:
    """Tests for force_cleanup_all_sessions method."""

    @pytest.mark.asyncio
    async def test_force_cleanup_all_sessions(self, session_service, mock_redis):
        """Test force cleanup of all sessions."""
        session_ids = ["session1", "session2", "session3"]
        mock_redis.smembers.return_value = session_ids
        mock_redis.hgetall.return_value = {}  # No session data

        pipeline_mock = mock_redis.pipeline.return_value
        pipeline_mock.execute.return_value = [1, 1]

        count = await session_service.force_cleanup_all_sessions()

        assert count == 3


class TestDeleteSessionWithServices:
    """Tests for delete_session with execution and file services."""

    @pytest.mark.asyncio
    async def test_delete_session_cleans_execution_resources(self, mock_redis):
        """Test delete_session cleans up execution resources."""
        mock_execution_service = MagicMock()
        mock_execution_service.cleanup_session = AsyncMock()

        session_service = SessionService(
            redis_client=mock_redis,
            execution_service=mock_execution_service,
        )

        mock_redis.hgetall.return_value = {}
        pipeline_mock = mock_redis.pipeline.return_value
        pipeline_mock.execute.return_value = [1, 1]

        await session_service.delete_session("session-123")

        mock_execution_service.cleanup_session.assert_called_once_with("session-123")

    @pytest.mark.asyncio
    async def test_delete_session_cleans_file_resources(self, mock_redis):
        """Test delete_session cleans up file resources."""
        mock_file_service = MagicMock()
        mock_file_service.cleanup_session_files = AsyncMock(return_value=5)

        session_service = SessionService(
            redis_client=mock_redis,
            file_service=mock_file_service,
        )

        mock_redis.hgetall.return_value = {}
        pipeline_mock = mock_redis.pipeline.return_value
        pipeline_mock.execute.return_value = [1, 1]

        await session_service.delete_session("session-123")

        mock_file_service.cleanup_session_files.assert_called_once_with("session-123")

    @pytest.mark.asyncio
    async def test_delete_session_handles_execution_service_error(self, mock_redis):
        """Test delete_session handles execution service errors gracefully."""
        mock_execution_service = MagicMock()
        mock_execution_service.cleanup_session = AsyncMock(side_effect=Exception("Cleanup failed"))

        session_service = SessionService(
            redis_client=mock_redis,
            execution_service=mock_execution_service,
        )

        mock_redis.hgetall.return_value = {}
        pipeline_mock = mock_redis.pipeline.return_value
        pipeline_mock.execute.return_value = [1, 1]

        # Should not raise
        result = await session_service.delete_session("session-123")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_session_handles_file_service_error(self, mock_redis):
        """Test delete_session handles file service errors gracefully."""
        mock_file_service = MagicMock()
        mock_file_service.cleanup_session_files = AsyncMock(side_effect=Exception("Cleanup failed"))

        session_service = SessionService(
            redis_client=mock_redis,
            file_service=mock_file_service,
        )

        mock_redis.hgetall.return_value = {}
        pipeline_mock = mock_redis.pipeline.return_value
        pipeline_mock.execute.return_value = [1, 1]

        # Should not raise
        result = await session_service.delete_session("session-123")
        assert result is True


class TestCleanupExpiredSessions:
    """Tests for cleanup_expired_sessions method."""

    @pytest.mark.asyncio
    async def test_cleanup_orphaned_session(self, mock_redis):
        """Test cleaning up orphaned session with missing data."""
        mock_file_service = MagicMock()
        mock_file_service.cleanup_session_files = AsyncMock(return_value=2)

        session_service = SessionService(
            redis_client=mock_redis,
            file_service=mock_file_service,
        )

        session_ids = ["orphaned-session"]
        mock_redis.smembers.return_value = session_ids
        mock_redis.hgetall.return_value = {}  # Empty = orphaned
        mock_redis.srem = AsyncMock()

        count = await session_service.cleanup_expired_sessions()

        assert count == 1
        mock_file_service.cleanup_session_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_orphaned_session_file_error(self, mock_redis):
        """Test cleaning up orphaned session when file cleanup fails."""
        mock_file_service = MagicMock()
        mock_file_service.cleanup_session_files = AsyncMock(side_effect=Exception("File error"))

        session_service = SessionService(
            redis_client=mock_redis,
            file_service=mock_file_service,
        )

        session_ids = ["orphaned-session"]
        mock_redis.smembers.return_value = session_ids
        mock_redis.hgetall.return_value = {}
        mock_redis.srem = AsyncMock()

        # Should not raise
        count = await session_service.cleanup_expired_sessions()
        assert count == 1


class TestGetSession:
    """Additional tests for get_session method."""

    @pytest.mark.asyncio
    async def test_get_session_parses_numeric_fields(self, session_service, mock_redis):
        """Test get_session correctly parses numeric fields."""
        session_data = {
            "session_id": "test-session",
            "status": "active",
            "created_at": "2023-01-01T00:00:00",
            "last_activity": "2023-01-01T00:00:00",
            "expires_at": "2023-01-02T00:00:00",
            "files": "{}",
            "metadata": "{}",
            "memory_usage_mb": "512.5",
            "cpu_usage_percent": "25.0",
        }

        mock_redis.hgetall.return_value = session_data
        mock_redis.hset = AsyncMock()

        session = await session_service.get_session("test-session")

        assert session is not None
        assert session.memory_usage_mb == 512.5
        assert session.cpu_usage_percent == 25.0

    @pytest.mark.asyncio
    async def test_get_session_handles_empty_numeric_fields(self, session_service, mock_redis):
        """Test get_session handles empty numeric fields."""
        session_data = {
            "session_id": "test-session",
            "status": "active",
            "created_at": "2023-01-01T00:00:00",
            "last_activity": "2023-01-01T00:00:00",
            "expires_at": "2023-01-02T00:00:00",
            "files": "{}",
            "metadata": "{}",
            "memory_usage_mb": "",
            "cpu_usage_percent": "",
        }

        mock_redis.hgetall.return_value = session_data
        mock_redis.hset = AsyncMock()

        session = await session_service.get_session("test-session")

        assert session is not None
        assert session.memory_usage_mb is None
        assert session.cpu_usage_percent is None

    @pytest.mark.asyncio
    async def test_get_session_handles_model_validation_error(self, session_service, mock_redis):
        """Test get_session handles model validation errors gracefully."""
        # Return data that passes datetime parsing but fails model validation
        session_data = {
            "session_id": "test-session",
            "status": "invalid-status",  # Will cause validation error
            "created_at": "2023-01-01T00:00:00",
            "last_activity": "2023-01-01T00:00:00",
            "expires_at": "2023-01-02T00:00:00",
            "files": "{}",
            "metadata": "{}",
        }

        mock_redis.hgetall.return_value = session_data
        mock_redis.hset = AsyncMock()

        session = await session_service.get_session("test-session")

        assert session is None  # Returns None on validation error


class TestUpdateSession:
    """Additional tests for update_session method."""

    @pytest.mark.asyncio
    async def test_update_session_with_datetime(self, session_service, mock_redis):
        """Test updating session with datetime value."""
        session_id = "test-session"
        mock_redis.exists.return_value = True
        mock_redis.hset = AsyncMock()

        session_data = {
            "session_id": session_id,
            "status": "active",
            "created_at": "2023-01-01T00:00:00",
            "last_activity": datetime.now(UTC).isoformat(),
            "expires_at": "2023-01-02T00:00:00",
            "files": "{}",
            "metadata": "{}",
        }
        mock_redis.hgetall.return_value = session_data

        new_expires = datetime.now(UTC) + timedelta(hours=2)
        session = await session_service.update_session(session_id, expires_at=new_expires)

        assert session is not None
        mock_redis.hset.assert_called()

    @pytest.mark.asyncio
    async def test_update_session_with_dict(self, session_service, mock_redis):
        """Test updating session with dict value."""
        session_id = "test-session"
        mock_redis.exists.return_value = True
        mock_redis.hset = AsyncMock()

        session_data = {
            "session_id": session_id,
            "status": "active",
            "created_at": "2023-01-01T00:00:00",
            "last_activity": datetime.now(UTC).isoformat(),
            "expires_at": "2023-01-02T00:00:00",
            "files": "{}",
            "metadata": "{}",
        }
        mock_redis.hgetall.return_value = session_data

        session = await session_service.update_session(session_id, metadata={"key": "value"})

        assert session is not None


class TestGetSessionFilesAccess:
    """Additional tests for get_session_files_access method."""

    @pytest.mark.asyncio
    async def test_get_session_files_access_no_entity(self, session_service, mock_redis):
        """Test session files access without entity ID."""
        session_id = "test-session"

        with patch.object(session_service, "validate_session_access", return_value=True):
            result = await session_service.get_session_files_access(session_id)

        assert result is True


class TestCloseWithError:
    """Tests for close method error handling."""

    @pytest.mark.asyncio
    async def test_close_handles_redis_error(self, session_service, mock_redis):
        """Test close handles Redis errors gracefully."""
        mock_redis.close.side_effect = Exception("Close failed")

        # Should not raise
        await session_service.close()


class TestCreateSessionErrors:
    """Tests for create_session error handling."""

    @pytest.mark.asyncio
    async def test_create_session_pipeline_error(self, session_service, mock_redis):
        """Test create_session handles pipeline errors."""
        request = SessionCreate(metadata={})

        pipeline_mock = mock_redis.pipeline.return_value
        pipeline_mock.execute.side_effect = Exception("Pipeline failed")

        with pytest.raises(Exception):
            await session_service.create_session(request)


class TestCreateSessionNoneMetadata:
    """Test creating session with None metadata."""

    @pytest.mark.asyncio
    async def test_create_session_none_metadata(self, session_service, mock_redis):
        """Test session creation with None metadata."""
        request = SessionCreate()
        request.metadata = None

        session = await session_service.create_session(request)

        assert session.session_id is not None
        assert session.metadata == {}


class TestCleanupLoopExtended:
    """Extended tests for _cleanup_loop method."""

    @pytest.mark.asyncio
    async def test_cleanup_loop_redis_not_available(self, mock_redis):
        """Test cleanup loop when Redis is not available."""
        session_service = SessionService(redis_client=mock_redis)
        session_service._redis_available = False

        # Mock ping to fail
        mock_redis.ping = AsyncMock(side_effect=Exception("Connection refused"))

        iteration = 0

        async def mock_sleep(seconds):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep):
            # _cleanup_loop catches CancelledError and returns normally
            await session_service._cleanup_loop()

    @pytest.mark.asyncio
    async def test_cleanup_loop_with_orphan_cleanup(self, mock_redis):
        """Test cleanup loop calls orphan cleanup when enabled."""
        mock_file_service = AsyncMock()
        mock_file_service.cleanup_orphan_objects = AsyncMock(return_value=5)

        session_service = SessionService(redis_client=mock_redis, file_service=mock_file_service)
        session_service._redis_available = True

        # Mock smembers to return empty (no expired sessions)
        mock_redis.smembers = AsyncMock(return_value=[])

        iteration = 0

        async def mock_sleep(seconds):
            nonlocal iteration
            iteration += 1
            if iteration >= 1:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep):
            with patch("src.services.session.settings") as mock_settings:
                mock_settings.enable_orphan_minio_cleanup = True
                mock_settings.session_cleanup_interval_minutes = 1

                # _cleanup_loop catches CancelledError and returns normally
                await session_service._cleanup_loop()

        mock_file_service.cleanup_orphan_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_loop_orphan_cleanup_error(self, mock_redis):
        """Test cleanup loop handles orphan cleanup errors."""
        mock_file_service = AsyncMock()
        mock_file_service.cleanup_orphan_objects = AsyncMock(side_effect=Exception("MinIO error"))

        session_service = SessionService(redis_client=mock_redis, file_service=mock_file_service)
        session_service._redis_available = True

        mock_redis.smembers = AsyncMock(return_value=[])

        iteration = 0

        async def mock_sleep(seconds):
            nonlocal iteration
            iteration += 1
            if iteration >= 1:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep):
            with patch("src.services.session.settings") as mock_settings:
                mock_settings.enable_orphan_minio_cleanup = True
                mock_settings.session_cleanup_interval_minutes = 1

                # Should not raise despite orphan cleanup error
                await session_service._cleanup_loop()

    @pytest.mark.asyncio
    async def test_cleanup_loop_cleanup_error(self, mock_redis):
        """Test cleanup loop handles cleanup errors."""
        session_service = SessionService(redis_client=mock_redis)
        session_service._redis_available = True

        iteration = 0

        async def mock_cleanup():
            raise Exception("Cleanup error")

        async def mock_sleep(seconds):
            nonlocal iteration
            iteration += 1
            if iteration >= 4:
                raise asyncio.CancelledError()

        with patch.object(session_service, "cleanup_expired_sessions", side_effect=mock_cleanup):
            with patch("asyncio.sleep", side_effect=mock_sleep):
                # CancelledError might be raised from the sleep in exception handler
                try:
                    await session_service._cleanup_loop()
                except asyncio.CancelledError:
                    pass

        # Verify error was handled (loop iterated multiple times)
        assert iteration >= 2


class TestGetSessionEdgeCases:
    """Tests for get_session edge cases."""

    @pytest.mark.asyncio
    async def test_get_session_empty_files_string(self, session_service, mock_redis):
        """Test get_session with empty files string."""
        session_data = {
            "session_id": "test-session",
            "status": "active",
            "created_at": "2023-01-01T00:00:00+00:00",
            "last_activity": "2023-01-01T00:00:00+00:00",
            "expires_at": "2023-01-02T00:00:00+00:00",
            "files": "",  # Empty string instead of JSON
            "metadata": "",  # Empty string instead of JSON
            "working_directory": "/workspace",
        }

        mock_redis.hgetall.return_value = session_data
        mock_redis.hset = AsyncMock()

        session = await session_service.get_session("test-session")

        assert session is not None
        assert session.files == {}
        assert session.metadata == {}

    @pytest.mark.asyncio
    async def test_get_session_invalid_numeric_values(self, session_service, mock_redis):
        """Test get_session with invalid numeric values."""
        session_data = {
            "session_id": "test-session",
            "status": "active",
            "created_at": "2023-01-01T00:00:00+00:00",
            "last_activity": "2023-01-01T00:00:00+00:00",
            "expires_at": "2023-01-02T00:00:00+00:00",
            "files": "{}",
            "metadata": "{}",
            "working_directory": "/workspace",
            "memory_usage_mb": "invalid_number",  # Invalid float
            "cpu_usage_percent": "not_a_number",  # Invalid float
        }

        mock_redis.hgetall.return_value = session_data
        mock_redis.hset = AsyncMock()

        session = await session_service.get_session("test-session")

        assert session is not None
        # Invalid numeric values should be converted to None
        assert session.memory_usage_mb is None
        assert session.cpu_usage_percent is None

    @pytest.mark.asyncio
    async def test_get_session_valid_numeric_values(self, session_service, mock_redis):
        """Test get_session with valid numeric values."""
        session_data = {
            "session_id": "test-session",
            "status": "active",
            "created_at": "2023-01-01T00:00:00+00:00",
            "last_activity": "2023-01-01T00:00:00+00:00",
            "expires_at": "2023-01-02T00:00:00+00:00",
            "files": "{}",
            "metadata": "{}",
            "working_directory": "/workspace",
            "memory_usage_mb": "128.5",
            "cpu_usage_percent": "45.3",
        }

        mock_redis.hgetall.return_value = session_data
        mock_redis.hset = AsyncMock()

        session = await session_service.get_session("test-session")

        assert session is not None
        assert session.memory_usage_mb == 128.5
        assert session.cpu_usage_percent == 45.3


class TestCheckRedisConnectivity:
    """Tests for _check_redis_connectivity method."""

    @pytest.mark.asyncio
    async def test_check_redis_connectivity_success(self, session_service, mock_redis):
        """Test Redis connectivity check success."""
        mock_redis.ping = AsyncMock(return_value=True)

        result = await session_service._check_redis_connectivity()

        assert result is True
        assert session_service._redis_available is True

    @pytest.mark.asyncio
    async def test_check_redis_connectivity_failure(self, session_service, mock_redis):
        """Test Redis connectivity check failure."""
        mock_redis.ping = AsyncMock(side_effect=Exception("Connection refused"))

        result = await session_service._check_redis_connectivity()

        assert result is False
        assert session_service._redis_available is False


class TestStartCleanupTask:
    """Tests for start_cleanup_task method."""

    @pytest.mark.asyncio
    async def test_start_cleanup_task_redis_unavailable(self, session_service, mock_redis):
        """Test start_cleanup_task when Redis is unavailable."""
        mock_redis.ping = AsyncMock(side_effect=Exception("Connection refused"))

        await session_service.start_cleanup_task()

        # Task should not be started
        assert session_service._cleanup_task is None
