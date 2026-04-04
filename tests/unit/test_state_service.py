"""Unit tests for the StateService."""

import base64
import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.state import StateService


@pytest.fixture
def mock_redis_client():
    """Mock Redis client."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.setex = AsyncMock()
    client.delete = AsyncMock()
    client.strlen = AsyncMock(return_value=0)
    client.ttl = AsyncMock(return_value=-1)
    client.expire = AsyncMock()
    client.pipeline = MagicMock()
    return client


@pytest.fixture
def state_service(mock_redis_client):
    """Create StateService with mocked Redis."""
    with patch("src.services.state.redis_pool") as mock_pool:
        mock_pool.get_client.return_value = mock_redis_client
        mock_pool.key_prefix = ""
        service = StateService(redis_client=mock_redis_client)
        return service


class TestComputeHash:
    """Tests for hash computation."""

    def test_compute_hash_returns_sha256(self):
        """Test that compute_hash returns SHA256 hex digest."""
        raw_bytes = b"test data for hashing"
        expected = hashlib.sha256(raw_bytes).hexdigest()

        result = StateService.compute_hash(raw_bytes)

        assert result == expected

    def test_compute_hash_is_deterministic(self):
        """Test that same input produces same hash."""
        raw_bytes = b"reproducible test data"

        hash1 = StateService.compute_hash(raw_bytes)
        hash2 = StateService.compute_hash(raw_bytes)

        assert hash1 == hash2

    def test_compute_hash_different_for_different_input(self):
        """Test that different input produces different hash."""
        bytes1 = b"data version 1"
        bytes2 = b"data version 2"

        hash1 = StateService.compute_hash(bytes1)
        hash2 = StateService.compute_hash(bytes2)

        assert hash1 != hash2


class TestSaveState:
    """Tests for save_state method."""

    @pytest.mark.asyncio
    async def test_save_state_stores_hash_and_metadata(self, state_service, mock_redis_client):
        """Test that save_state stores state, hash, and metadata."""
        session_id = "test-session-123"
        raw_bytes = b"\x02test state data"  # Version 2 prefix
        state_b64 = base64.b64encode(raw_bytes).decode("utf-8")

        # Setup mock pipeline
        mock_pipe = AsyncMock()
        mock_pipe.setex = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True, True, True])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.save_state(session_id, state_b64)

        assert result is True
        # Verify pipeline was used with 3 setex calls (state, hash, meta)
        assert mock_pipe.setex.call_count == 3

    @pytest.mark.asyncio
    async def test_save_state_with_upload_marker(self, state_service, mock_redis_client):
        """Test that from_upload=True sets upload marker."""
        session_id = "test-session-upload"
        raw_bytes = b"\x02uploaded state"
        state_b64 = base64.b64encode(raw_bytes).decode("utf-8")

        mock_pipe = AsyncMock()
        mock_pipe.setex = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True, True, True, True])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.save_state(session_id, state_b64, from_upload=True)

        assert result is True
        # Verify 4 setex calls (state, hash, meta, marker)
        assert mock_pipe.setex.call_count == 4

    @pytest.mark.asyncio
    async def test_save_state_empty_returns_true(self, state_service):
        """Test that empty state returns True without saving."""
        result = await state_service.save_state("session", "")

        assert result is True


class TestGetStateRaw:
    """Tests for get_state_raw method."""

    @pytest.mark.asyncio
    async def test_get_state_raw_decodes_base64(self, state_service, mock_redis_client):
        """Test that get_state_raw returns decoded bytes."""
        session_id = "test-session"
        raw_bytes = b"\x02raw binary state data"
        state_b64 = base64.b64encode(raw_bytes).decode("utf-8")

        mock_redis_client.get.return_value = state_b64

        result = await state_service.get_state_raw(session_id)

        assert result == raw_bytes

    @pytest.mark.asyncio
    async def test_get_state_raw_returns_none_when_no_state(self, state_service, mock_redis_client):
        """Test that get_state_raw returns None when no state exists."""
        mock_redis_client.get.return_value = None

        result = await state_service.get_state_raw("nonexistent")

        assert result is None


class TestSaveStateRaw:
    """Tests for save_state_raw method."""

    @pytest.mark.asyncio
    async def test_save_state_raw_encodes_to_base64(self, state_service, mock_redis_client):
        """Test that save_state_raw encodes bytes to base64."""
        session_id = "test-session"
        raw_bytes = b"\x02raw data to save"

        mock_pipe = AsyncMock()
        mock_pipe.setex = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True, True, True])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.save_state_raw(session_id, raw_bytes)

        assert result is True


class TestGetStateHash:
    """Tests for get_state_hash method."""

    @pytest.mark.asyncio
    async def test_get_state_hash_returns_string(self, state_service, mock_redis_client):
        """Test that get_state_hash returns hash string."""
        expected_hash = "abc123def456"
        mock_redis_client.get.return_value = expected_hash.encode("utf-8")

        result = await state_service.get_state_hash("session-id")

        assert result == expected_hash

    @pytest.mark.asyncio
    async def test_get_state_hash_returns_none_when_missing(self, state_service, mock_redis_client):
        """Test that get_state_hash returns None when no hash."""
        mock_redis_client.get.return_value = None

        result = await state_service.get_state_hash("session-id")

        assert result is None


class TestUploadMarker:
    """Tests for upload marker methods."""

    @pytest.mark.asyncio
    async def test_has_recent_upload_true_when_marker_exists(self, state_service, mock_redis_client):
        """Test that has_recent_upload returns True when marker exists."""
        mock_redis_client.get.return_value = "1"

        result = await state_service.has_recent_upload("session-id")

        assert result is True

    @pytest.mark.asyncio
    async def test_has_recent_upload_false_when_no_marker(self, state_service, mock_redis_client):
        """Test that has_recent_upload returns False when no marker."""
        mock_redis_client.get.return_value = None

        result = await state_service.has_recent_upload("session-id")

        assert result is False

    @pytest.mark.asyncio
    async def test_clear_upload_marker_deletes_key(self, state_service, mock_redis_client):
        """Test that clear_upload_marker deletes the marker key."""
        await state_service.clear_upload_marker("session-id")

        mock_redis_client.delete.assert_called_once()


class TestDeleteState:
    """Tests for delete_state method."""

    @pytest.mark.asyncio
    async def test_delete_state_removes_all_keys(self, state_service, mock_redis_client):
        """Test that delete_state removes state, hash, meta, and marker keys."""
        session_id = "session-to-delete"

        result = await state_service.delete_state(session_id)

        assert result is True
        # Verify delete was called with all 4 keys
        mock_redis_client.delete.assert_called_once()
        call_args = mock_redis_client.delete.call_args[0]
        assert len(call_args) == 4


class TestGetFullStateInfo:
    """Tests for get_full_state_info method."""

    @pytest.mark.asyncio
    async def test_get_full_state_info_returns_metadata(self, state_service, mock_redis_client):
        """Test that get_full_state_info returns complete metadata."""
        session_id = "session-with-state"
        meta = {
            "size_bytes": 1024,
            "hash": "abc123",
            "created_at": "2025-12-21T10:00:00+00:00",
            "from_upload": False,
        }

        mock_pipe = AsyncMock()
        mock_pipe.strlen = MagicMock()
        mock_pipe.ttl = MagicMock()
        mock_pipe.get = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1024, 3600, json.dumps(meta)])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.get_full_state_info(session_id)

        assert result is not None
        assert result["size_bytes"] == 1024
        assert result["hash"] == "abc123"
        assert result["expires_at"] is not None

    @pytest.mark.asyncio
    async def test_get_full_state_info_returns_none_when_no_state(self, state_service, mock_redis_client):
        """Test that get_full_state_info returns None when no state."""
        mock_pipe = MagicMock()
        # Pipeline methods are sync (they just queue commands)
        mock_pipe.strlen = MagicMock()
        mock_pipe.ttl = MagicMock()
        mock_pipe.get = MagicMock()
        # Only execute is async
        mock_pipe.execute = AsyncMock(return_value=[0, -1, None])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.get_full_state_info("nonexistent")

        assert result is None


class TestKeyGeneration:
    """Tests for key generation methods."""

    def test_state_key(self, state_service):
        """Test state key generation."""
        key = state_service._state_key("session-123")
        assert key == "session:state:session-123"

    def test_hash_key(self, state_service):
        """Test hash key generation."""
        key = state_service._hash_key("session-123")
        assert key == "session:state:hash:session-123"

    def test_meta_key(self, state_service):
        """Test metadata key generation."""
        key = state_service._meta_key("session-123")
        assert key == "session:state:meta:session-123"

    def test_upload_marker_key(self, state_service):
        """Test upload marker key generation."""
        key = state_service._upload_marker_key("session-123")
        assert key == "session:state:uploaded:session-123"


class TestGetState:
    """Tests for get_state method."""

    @pytest.mark.asyncio
    async def test_get_state_returns_value(self, state_service, mock_redis_client):
        """Test get_state returns stored value."""
        mock_redis_client.get.return_value = "base64encodedstate"

        result = await state_service.get_state("session-123")

        assert result == "base64encodedstate"

    @pytest.mark.asyncio
    async def test_get_state_returns_none_when_missing(self, state_service, mock_redis_client):
        """Test get_state returns None when state doesn't exist."""
        mock_redis_client.get.return_value = None

        result = await state_service.get_state("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_state_handles_error(self, state_service, mock_redis_client):
        """Test get_state handles Redis errors."""
        mock_redis_client.get.side_effect = Exception("Redis error")

        result = await state_service.get_state("session-123")

        assert result is None


class TestGetStateInfo:
    """Tests for get_state_info method."""

    @pytest.mark.asyncio
    async def test_get_state_info_returns_info(self, state_service, mock_redis_client):
        """Test get_state_info returns size and TTL."""
        mock_pipe = MagicMock()
        mock_pipe.strlen = MagicMock()
        mock_pipe.ttl = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1024, 3600])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.get_state_info("session-123")

        assert result is not None
        assert result["size_bytes"] == 1024
        assert result["ttl_seconds"] == 3600
        assert "estimated_size_mb" in result

    @pytest.mark.asyncio
    async def test_get_state_info_returns_none_when_missing(self, state_service, mock_redis_client):
        """Test get_state_info returns None when no state."""
        mock_pipe = MagicMock()
        mock_pipe.strlen = MagicMock()
        mock_pipe.ttl = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[0, -2])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.get_state_info("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_state_info_handles_no_ttl(self, state_service, mock_redis_client):
        """Test get_state_info handles state with no TTL."""
        mock_pipe = MagicMock()
        mock_pipe.strlen = MagicMock()
        mock_pipe.ttl = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1024, -1])
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.get_state_info("session-123")

        assert result is not None
        assert result["ttl_seconds"] is None

    @pytest.mark.asyncio
    async def test_get_state_info_handles_error(self, state_service, mock_redis_client):
        """Test get_state_info handles Redis errors."""
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=Exception("Redis error"))
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.get_state_info("session-123")

        assert result is None


class TestExtendTtl:
    """Tests for extend_ttl method."""

    @pytest.mark.asyncio
    async def test_extend_ttl_success(self, state_service, mock_redis_client):
        """Test successful TTL extension."""
        mock_redis_client.expire.return_value = True

        with patch("src.services.state.settings") as mock_settings:
            mock_settings.state_ttl_seconds = 7200
            result = await state_service.extend_ttl("session-123")

        assert result is True

    @pytest.mark.asyncio
    async def test_extend_ttl_with_custom_value(self, state_service, mock_redis_client):
        """Test TTL extension with custom value."""
        mock_redis_client.expire.return_value = True

        result = await state_service.extend_ttl("session-123", ttl_seconds=3600)

        assert result is True
        mock_redis_client.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ttl_returns_false_when_key_missing(self, state_service, mock_redis_client):
        """Test extend_ttl returns False when key doesn't exist."""
        mock_redis_client.expire.return_value = False

        with patch("src.services.state.settings") as mock_settings:
            mock_settings.state_ttl_seconds = 7200
            result = await state_service.extend_ttl("nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_extend_ttl_handles_error(self, state_service, mock_redis_client):
        """Test extend_ttl handles Redis errors."""
        mock_redis_client.expire.side_effect = Exception("Redis error")

        with patch("src.services.state.settings") as mock_settings:
            mock_settings.state_ttl_seconds = 7200
            result = await state_service.extend_ttl("session-123")

        assert result is False


class TestGetStateWithTtl:
    """Tests for get_state_with_ttl method."""

    @pytest.mark.asyncio
    async def test_get_state_with_ttl_returns_both(self, state_service, mock_redis_client):
        """Test get_state_with_ttl returns state and TTL."""
        mock_pipe = MagicMock()
        mock_pipe.get = MagicMock()
        mock_pipe.ttl = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=["base64state", 3600])
        mock_redis_client.pipeline.return_value = mock_pipe

        state, ttl = await state_service.get_state_with_ttl("session-123")

        assert state == "base64state"
        assert ttl == 3600

    @pytest.mark.asyncio
    async def test_get_state_with_ttl_returns_none_when_missing(self, state_service, mock_redis_client):
        """Test get_state_with_ttl returns None when no state."""
        mock_pipe = MagicMock()
        mock_pipe.get = MagicMock()
        mock_pipe.ttl = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, -2])
        mock_redis_client.pipeline.return_value = mock_pipe

        state, ttl = await state_service.get_state_with_ttl("nonexistent")

        assert state is None
        assert ttl == 0

    @pytest.mark.asyncio
    async def test_get_state_with_ttl_handles_error(self, state_service, mock_redis_client):
        """Test get_state_with_ttl handles Redis errors."""
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=Exception("Redis error"))
        mock_redis_client.pipeline.return_value = mock_pipe

        state, ttl = await state_service.get_state_with_ttl("session-123")

        assert state is None
        assert ttl == 0


class TestGetStatesForArchival:
    """Tests for get_states_for_archival method."""

    @pytest.mark.asyncio
    async def test_get_states_for_archival_finds_states(self, state_service, mock_redis_client):
        """Test finding states ready for archival."""

        async def mock_scan_iter(**kwargs):
            for key in [b"session:state:session-1", b"session:state:session-2"]:
                yield key

        mock_redis_client.scan_iter = mock_scan_iter
        mock_redis_client.ttl.side_effect = [100, 200]
        mock_redis_client.strlen.side_effect = [1024, 2048]

        with patch("src.services.state.settings") as mock_settings:
            mock_settings.state_ttl_seconds = 7200
            mock_settings.state_archive_after_seconds = 3600
            result = await state_service.get_states_for_archival(ttl_threshold=500)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_states_for_archival_empty(self, state_service, mock_redis_client):
        """Test when no states are ready for archival."""

        async def mock_scan_iter(**kwargs):
            for _ in []:  # empty async generator
                yield

        mock_redis_client.scan_iter = mock_scan_iter

        with patch("src.services.state.settings") as mock_settings:
            mock_settings.state_ttl_seconds = 7200
            mock_settings.state_archive_after_seconds = 3600
            result = await state_service.get_states_for_archival()

        assert result == []

    @pytest.mark.asyncio
    async def test_get_states_for_archival_handles_error(self, state_service, mock_redis_client):
        """Test get_states_for_archival handles Redis errors."""

        async def mock_scan_iter(**kwargs):
            raise Exception("Redis error")
            yield  # noqa: F841 - makes this an async generator

        mock_redis_client.scan_iter = mock_scan_iter

        with patch("src.services.state.settings") as mock_settings:
            mock_settings.state_ttl_seconds = 7200
            mock_settings.state_archive_after_seconds = 3600
            result = await state_service.get_states_for_archival()

        assert result == []


class TestErrorHandling:
    """Tests for error handling in various methods."""

    @pytest.mark.asyncio
    async def test_save_state_handles_error(self, state_service, mock_redis_client):
        """Test save_state handles Redis errors."""
        mock_pipe = MagicMock()
        mock_pipe.setex = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=Exception("Redis error"))
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.save_state("session-123", base64.b64encode(b"test").decode())

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_state_handles_error(self, state_service, mock_redis_client):
        """Test delete_state handles Redis errors."""
        mock_redis_client.delete.side_effect = Exception("Redis error")

        result = await state_service.delete_state("session-123")

        assert result is False

    @pytest.mark.asyncio
    async def test_get_state_raw_handles_error(self, state_service, mock_redis_client):
        """Test get_state_raw handles errors."""
        mock_redis_client.get.side_effect = Exception("Redis error")

        result = await state_service.get_state_raw("session-123")

        assert result is None

    @pytest.mark.asyncio
    async def test_save_state_raw_handles_error(self, state_service, mock_redis_client):
        """Test save_state_raw handles errors."""
        with patch("src.services.state.base64.b64encode", side_effect=Exception("Encode error")):
            result = await state_service.save_state_raw("session-123", b"test data")

        assert result is False

    @pytest.mark.asyncio
    async def test_get_state_hash_handles_error(self, state_service, mock_redis_client):
        """Test get_state_hash handles Redis errors."""
        mock_redis_client.get.side_effect = Exception("Redis error")

        result = await state_service.get_state_hash("session-123")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_full_state_info_handles_error(self, state_service, mock_redis_client):
        """Test get_full_state_info handles Redis errors."""
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=Exception("Redis error"))
        mock_redis_client.pipeline.return_value = mock_pipe

        result = await state_service.get_full_state_info("session-123")

        assert result is None

    @pytest.mark.asyncio
    async def test_has_recent_upload_handles_error(self, state_service, mock_redis_client):
        """Test has_recent_upload handles Redis errors."""
        mock_redis_client.get.side_effect = Exception("Redis error")

        result = await state_service.has_recent_upload("session-123")

        assert result is False

    @pytest.mark.asyncio
    async def test_clear_upload_marker_handles_error(self, state_service, mock_redis_client):
        """Test clear_upload_marker handles Redis errors silently."""
        mock_redis_client.delete.side_effect = Exception("Redis error")

        # Should not raise
        await state_service.clear_upload_marker("session-123")
