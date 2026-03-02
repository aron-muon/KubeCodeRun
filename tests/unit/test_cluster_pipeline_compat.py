"""Unit tests verifying that all Redis pipelines use transaction=False.

Redis Cluster does not support MULTI/EXEC transactions across keys in
different hash slots.  Every pipeline that touches keys with different
prefixes (e.g. session data + session index) MUST use transaction=False
so redis-py's ClusterPipeline can split commands by node.

These tests act as a safety net: if someone accidentally changes a
pipeline back to transaction=True, the test will catch it.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.session import SessionCreate
from src.services.api_key_manager import ApiKeyManagerService
from src.services.session import SessionService
from src.services.state import StateService

# ── Session Service ─────────────────────────────────────────────────────


@pytest.fixture
def mock_redis_session():
    """Mock Redis client for session tests."""
    redis_mock = AsyncMock()

    pipeline_mock = AsyncMock()
    pipeline_mock.hset = MagicMock()
    pipeline_mock.expire = MagicMock()
    pipeline_mock.sadd = MagicMock()
    pipeline_mock.delete = MagicMock()
    pipeline_mock.srem = MagicMock()
    pipeline_mock.execute = AsyncMock(return_value=[True, True, True])
    pipeline_mock.reset = AsyncMock()

    redis_mock.pipeline = MagicMock(return_value=pipeline_mock)
    redis_mock.hgetall = AsyncMock(return_value={})
    return redis_mock


@pytest.fixture
def session_service(mock_redis_session):
    return SessionService(redis_client=mock_redis_session)


@pytest.mark.asyncio
async def test_session_create_uses_non_transactional_pipeline(session_service, mock_redis_session):
    """create_session() must use transaction=False for cluster compat."""
    request = SessionCreate(metadata={"test": "value"})
    await session_service.create_session(request)

    mock_redis_session.pipeline.assert_called_once_with(transaction=False)


@pytest.mark.asyncio
async def test_session_delete_uses_non_transactional_pipeline(session_service, mock_redis_session):
    """delete_session() must use transaction=False for cluster compat."""
    session_id = "session-to-delete"
    # Provide minimal session data so delete_session finds the session
    mock_redis_session.hgetall.return_value = {
        "session_id": session_id,
        "status": "active",
        "created_at": "2025-01-01T00:00:00",
        "last_activity": "2025-01-01T00:00:00",
        "expires_at": "2026-01-01T00:00:00",
        "files": "{}",
        "metadata": "{}",
        "working_directory": "/workspace",
    }

    pipeline_mock = mock_redis_session.pipeline.return_value
    pipeline_mock.execute = AsyncMock(return_value=[1, 1])

    await session_service.delete_session(session_id)

    mock_redis_session.pipeline.assert_called_with(transaction=False)


# ── API Key Manager ─────────────────────────────────────────────────────


@pytest.fixture
def mock_redis_apikey():
    """Mock Redis client for API key manager tests."""
    redis_mock = AsyncMock()
    redis_mock.hgetall = AsyncMock(return_value={})
    redis_mock.hset = AsyncMock(return_value=1)
    redis_mock.exists = AsyncMock(return_value=True)
    redis_mock.delete = AsyncMock(return_value=1)
    redis_mock.sadd = AsyncMock(return_value=1)
    redis_mock.srem = AsyncMock(return_value=1)
    redis_mock.smembers = AsyncMock(return_value=set())
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.setex = AsyncMock(return_value=True)
    redis_mock.incr = AsyncMock(return_value=1)
    redis_mock.expire = AsyncMock(return_value=True)
    redis_mock.hincrby = AsyncMock(return_value=1)

    pipeline_mock = AsyncMock()
    pipeline_mock.hset = MagicMock()
    pipeline_mock.sadd = MagicMock()
    pipeline_mock.delete = MagicMock()
    pipeline_mock.srem = MagicMock()
    pipeline_mock.incr = MagicMock()
    pipeline_mock.expire = MagicMock()
    pipeline_mock.hincrby = MagicMock()
    pipeline_mock.execute = AsyncMock(return_value=[True, True, True])
    redis_mock.pipeline = MagicMock(return_value=pipeline_mock)

    return redis_mock


@pytest.fixture
def api_key_manager(mock_redis_apikey):
    return ApiKeyManagerService(redis_client=mock_redis_apikey)


@pytest.mark.asyncio
async def test_create_key_uses_non_transactional_pipeline(api_key_manager, mock_redis_apikey):
    """create_key() must use transaction=False for cluster compat."""
    result = await api_key_manager.create_key(
        name="test-key",
    )

    # create_key calls pipeline at least once
    mock_redis_apikey.pipeline.assert_called()
    for call in mock_redis_apikey.pipeline.call_args_list:
        assert call == ((), {"transaction": False}), f"Expected pipeline(transaction=False), got {call}"


@pytest.mark.asyncio
async def test_ensure_single_env_key_uses_non_transactional_pipeline(api_key_manager, mock_redis_apikey):
    """_ensure_single_env_key_record() must use transaction=False."""
    # Call the internal method directly
    await api_key_manager._ensure_single_env_key_record("test-hash", "test-env")

    mock_redis_apikey.pipeline.assert_called()
    for call in mock_redis_apikey.pipeline.call_args_list:
        assert call == ((), {"transaction": False}), f"Expected pipeline(transaction=False), got {call}"


@pytest.mark.asyncio
async def test_revoke_key_uses_non_transactional_pipeline(api_key_manager, mock_redis_apikey):
    """revoke_key() must use transaction=False for cluster compat."""
    # Setup: make the key "exist" so revoke proceeds
    mock_redis_apikey.hgetall.return_value = {
        "name": "test-key",
        "key_hash": "abc123",
        "environment": "test",
        "status": "active",
        "created_at": "2025-01-01T00:00:00+00:00",
    }
    mock_redis_apikey.exists.return_value = True

    await api_key_manager.revoke_key("abc123")

    mock_redis_apikey.pipeline.assert_called()
    for call in mock_redis_apikey.pipeline.call_args_list:
        assert call == ((), {"transaction": False}), f"Expected pipeline(transaction=False), got {call}"


# ── State Service ───────────────────────────────────────────────────────


@pytest.fixture
def mock_redis_state():
    """Mock Redis client for state service tests."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.setex = AsyncMock()
    client.delete = AsyncMock()
    client.strlen = AsyncMock(return_value=0)
    client.ttl = AsyncMock(return_value=-1)
    client.expire = AsyncMock()

    pipeline_mock = AsyncMock()
    pipeline_mock.set = MagicMock()
    pipeline_mock.setex = MagicMock()
    pipeline_mock.expire = MagicMock()
    pipeline_mock.execute = AsyncMock(return_value=[True, True, True, True, True])
    client.pipeline = MagicMock(return_value=pipeline_mock)

    return client


@pytest.fixture
def state_service(mock_redis_state):
    with patch("src.services.state.redis_pool") as mock_pool:
        mock_pool.get_client.return_value = mock_redis_state
        service = StateService(redis_client=mock_redis_state)
        return service


@pytest.mark.asyncio
async def test_save_state_uses_non_transactional_pipeline(state_service, mock_redis_state):
    """save_state() must use transaction=False for cluster compat."""
    import base64

    session_id = "state-test-session"
    raw_bytes = b"\x02test state data"
    state_b64 = base64.b64encode(raw_bytes).decode("utf-8")

    await state_service.save_state(session_id, state_b64)

    mock_redis_state.pipeline.assert_called()
    for call in mock_redis_state.pipeline.call_args_list:
        assert call == ((), {"transaction": False}), f"Expected pipeline(transaction=False), got {call}"


# ── Version resolution ──────────────────────────────────────────────────


class TestVersionResolution:
    """Tests for SERVICE_VERSION env var override."""

    def test_logging_uses_service_version_when_set(self):
        """add_service_context should prefer settings.service_version."""
        with (
            patch("src.utils.logging.settings") as mock_settings,
            patch("src.utils.logging.__version__", "0.0.0.dev0"),
        ):
            mock_settings.service_version = "2.1.4"
            from src.utils.logging import add_service_context

            event_dict = {}
            add_service_context(None, None, event_dict)

            assert event_dict["version"] == "2.1.4"

    def test_logging_falls_back_to_build_version(self):
        """add_service_context should fall back to __version__ when SERVICE_VERSION unset."""
        with (
            patch("src.utils.logging.settings") as mock_settings,
            patch("src.utils.logging.__version__", "1.2.3"),
        ):
            mock_settings.service_version = None
            from src.utils.logging import add_service_context

            event_dict = {}
            add_service_context(None, None, event_dict)

            assert event_dict["version"] == "1.2.3"
