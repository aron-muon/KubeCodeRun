"""Unit tests for body-based API key extraction (LibreChat agents >=3.1.74 compat).

Tests cover resolve_api_key from all three input locations:
1. X-API-Key header (existing, unchanged)
2. Authorization: Bearer header (existing, unchanged)
3. JSON body fields (new: LIBRECHAT_CODE_API_KEY, api_key, apiKey)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.middleware.auth import AuthenticationMiddleware
from src.middleware.security import SecurityMiddleware


@pytest.fixture
def mock_app():
    """Create a mock ASGI app."""
    return AsyncMock()


@pytest.fixture
def auth_middleware(mock_app):
    """Create auth middleware instance."""
    return AuthenticationMiddleware(mock_app)


@pytest.fixture
def security_middleware(mock_app):
    """Create security middleware instance."""
    with patch("src.middleware.security.settings") as mock_settings:
        mock_settings.max_file_size_mb = 10
        mock_settings.auth_trusted_networks = ""
        return SecurityMiddleware(mock_app)


class TestExtractApiKeyFromBody:
    """Tests for _extract_api_key_from_body method on both middleware classes."""

    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    def test_extract_librechat_code_api_key(self, middleware_fixture, request):
        """Test extraction from LIBRECHAT_CODE_API_KEY body field."""
        middleware = request.getfixturevalue(middleware_fixture)
        body = json.dumps({"lang": "py", "code": "print(1)", "LIBRECHAT_CODE_API_KEY": "my-secret-key"}).encode()

        result = middleware._extract_api_key_from_body(body)

        assert result == "my-secret-key"

    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    def test_extract_api_key_field(self, middleware_fixture, request):
        """Test extraction from api_key body field."""
        middleware = request.getfixturevalue(middleware_fixture)
        body = json.dumps({"lang": "py", "code": "print(1)", "api_key": "another-key"}).encode()

        result = middleware._extract_api_key_from_body(body)

        assert result == "another-key"

    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    def test_extract_apikey_camel_case(self, middleware_fixture, request):
        """Test extraction from apiKey body field (camelCase)."""
        middleware = request.getfixturevalue(middleware_fixture)
        body = json.dumps({"lang": "py", "code": "print(1)", "apiKey": "camel-key"}).encode()

        result = middleware._extract_api_key_from_body(body)

        assert result == "camel-key"

    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    def test_priority_librechat_over_api_key(self, middleware_fixture, request):
        """Test LIBRECHAT_CODE_API_KEY takes priority over api_key."""
        middleware = request.getfixturevalue(middleware_fixture)
        body = json.dumps(
            {
                "LIBRECHAT_CODE_API_KEY": "priority-key",
                "api_key": "fallback-key",
                "apiKey": "last-key",
            }
        ).encode()

        result = middleware._extract_api_key_from_body(body)

        assert result == "priority-key"

    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    def test_empty_body(self, middleware_fixture, request):
        """Test empty body returns None."""
        middleware = request.getfixturevalue(middleware_fixture)

        result = middleware._extract_api_key_from_body(b"")

        assert result is None

    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    def test_invalid_json(self, middleware_fixture, request):
        """Test invalid JSON returns None."""
        middleware = request.getfixturevalue(middleware_fixture)

        result = middleware._extract_api_key_from_body(b"not json at all")

        assert result is None

    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    def test_non_dict_json(self, middleware_fixture, request):
        """Test non-dict JSON (e.g. array) returns None."""
        middleware = request.getfixturevalue(middleware_fixture)
        body = json.dumps(["not", "a", "dict"]).encode()

        result = middleware._extract_api_key_from_body(body)

        assert result is None

    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    def test_no_key_fields_in_body(self, middleware_fixture, request):
        """Test body without any key fields returns None."""
        middleware = request.getfixturevalue(middleware_fixture)
        body = json.dumps({"lang": "py", "code": "print(1)", "user_id": "user123"}).encode()

        result = middleware._extract_api_key_from_body(body)

        assert result is None

    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    def test_whitespace_only_key_ignored(self, middleware_fixture, request):
        """Test whitespace-only key values are ignored."""
        middleware = request.getfixturevalue(middleware_fixture)
        body = json.dumps({"api_key": "   "}).encode()

        result = middleware._extract_api_key_from_body(body)

        assert result is None

    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    def test_non_string_key_ignored(self, middleware_fixture, request):
        """Test non-string key values are ignored."""
        middleware = request.getfixturevalue(middleware_fixture)
        body = json.dumps({"api_key": 12345}).encode()

        result = middleware._extract_api_key_from_body(body)

        assert result is None

    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    def test_key_value_stripped(self, middleware_fixture, request):
        """Test key values are stripped of whitespace."""
        middleware = request.getfixturevalue(middleware_fixture)
        body = json.dumps({"api_key": "  my-key  "}).encode()

        result = middleware._extract_api_key_from_body(body)

        assert result == "my-key"


class TestBufferBody:
    """Tests for _buffer_body method on both middleware classes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    async def test_buffer_single_chunk(self, middleware_fixture, request):
        """Test buffering a single-chunk body."""
        middleware = request.getfixturevalue(middleware_fixture)
        body_content = b'{"lang": "py", "code": "print(1)"}'

        async def mock_receive():
            return {"type": "http.request", "body": body_content, "more_body": False}

        result_body, replay = await middleware._buffer_body(mock_receive)

        assert result_body == body_content

    @pytest.mark.asyncio
    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    async def test_buffer_multi_chunk(self, middleware_fixture, request):
        """Test buffering a multi-chunk body."""
        middleware = request.getfixturevalue(middleware_fixture)
        chunks = [
            {"type": "http.request", "body": b'{"lang": ', "more_body": True},
            {"type": "http.request", "body": b'"py", "code":', "more_body": True},
            {"type": "http.request", "body": b' "print(1)"}', "more_body": False},
        ]
        chunk_iter = iter(chunks)

        async def mock_receive():
            return next(chunk_iter)

        result_body, replay = await middleware._buffer_body(mock_receive)

        assert result_body == b'{"lang": "py", "code": "print(1)"}'

    @pytest.mark.asyncio
    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    async def test_replay_receive_returns_buffered_body(self, middleware_fixture, request):
        """Test replay receive returns the buffered body on first call."""
        middleware = request.getfixturevalue(middleware_fixture)
        body_content = b'{"api_key": "test-key"}'

        async def mock_receive():
            return {"type": "http.request", "body": body_content, "more_body": False}

        _, replay = await middleware._buffer_body(mock_receive)

        # First call returns the body
        msg = await replay()
        assert msg["type"] == "http.request"
        assert msg["body"] == body_content
        assert msg["more_body"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    async def test_replay_receive_disconnects_after_body(self, middleware_fixture, request):
        """Test replay receive returns disconnect after body is consumed."""
        middleware = request.getfixturevalue(middleware_fixture)

        async def mock_receive():
            return {"type": "http.request", "body": b"data", "more_body": False}

        _, replay = await middleware._buffer_body(mock_receive)

        # First call: body
        await replay()
        # Second call: disconnect
        msg = await replay()
        assert msg["type"] == "http.disconnect"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("middleware_fixture", ["auth_middleware", "security_middleware"])
    async def test_buffer_empty_body(self, middleware_fixture, request):
        """Test buffering an empty body."""
        middleware = request.getfixturevalue(middleware_fixture)

        async def mock_receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        result_body, replay = await middleware._buffer_body(mock_receive)

        assert result_body == b""


class TestFullAuthFlowWithBodyKey:
    """Integration-style tests for the full auth flow with body-based keys."""

    @pytest.mark.asyncio
    async def test_auth_middleware_body_key_accepted(self, auth_middleware, mock_app):
        """Test AuthenticationMiddleware accepts API key from body."""
        body = json.dumps({"lang": "py", "code": "print(1)", "api_key": "valid-key"}).encode()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/exec",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        }

        async def mock_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        mock_send = AsyncMock()

        with patch("src.middleware.auth.get_auth_service") as mock_get_auth:
            mock_service = AsyncMock()
            mock_service.check_rate_limit.return_value = True
            mock_service.validate_api_key.return_value = True
            mock_get_auth.return_value = mock_service

            await auth_middleware(scope, mock_receive, mock_send)

        # App should have been called (auth passed)
        mock_app.assert_called_once()
        # Verify the key was extracted from body
        mock_service.validate_api_key.assert_called_once_with("valid-key")

    @pytest.mark.asyncio
    async def test_auth_middleware_header_key_preferred_over_body(self, auth_middleware, mock_app):
        """Test header key takes priority over body key."""
        body = json.dumps({"api_key": "body-key"}).encode()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/exec",
            "query_string": b"",
            "headers": [(b"x-api-key", b"header-key"), (b"content-type", b"application/json")],
        }

        async def mock_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        mock_send = AsyncMock()

        with patch("src.middleware.auth.get_auth_service") as mock_get_auth:
            mock_service = AsyncMock()
            mock_service.check_rate_limit.return_value = True
            mock_service.validate_api_key.return_value = True
            mock_get_auth.return_value = mock_service

            await auth_middleware(scope, mock_receive, mock_send)

        # Header key should be used, not body key
        mock_service.validate_api_key.assert_called_once_with("header-key")

    @pytest.mark.asyncio
    async def test_auth_middleware_no_key_anywhere_returns_401(self, auth_middleware):
        """Test 401 when no API key in headers or body."""
        body = json.dumps({"lang": "py", "code": "print(1)"}).encode()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/exec",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        }

        async def mock_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        mock_send = AsyncMock()

        with patch("src.middleware.auth.get_auth_service") as mock_get_auth:
            mock_service = AsyncMock()
            mock_service.check_rate_limit.return_value = True
            mock_service.validate_api_key.return_value = False
            mock_get_auth.return_value = mock_service

            await auth_middleware(scope, mock_receive, mock_send)

        # Should have sent a 401 response
        mock_send.assert_called()
        # Find the response start message
        for call in mock_send.call_args_list:
            msg = call[0][0]
            if msg.get("type") == "http.response.start":
                assert msg["status"] == 401
                break

    @pytest.mark.asyncio
    async def test_security_middleware_body_key_accepted(self, security_middleware, mock_app):
        """Test SecurityMiddleware accepts API key from body."""
        body = json.dumps({"lang": "py", "code": "print(1)", "LIBRECHAT_CODE_API_KEY": "valid-key"}).encode()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/exec",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        }

        async def mock_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        mock_send = AsyncMock()

        with patch("src.middleware.security.get_auth_service") as mock_get_auth:
            mock_service = AsyncMock()
            mock_service.check_rate_limit.return_value = True
            mock_result = MagicMock()
            mock_result.is_valid = True
            mock_result.rate_limit_exceeded = False
            mock_result.key_hash = "hash123"
            mock_result.is_env_key = False
            mock_service.validate_api_key_full.return_value = mock_result
            mock_service.record_usage = AsyncMock()
            mock_get_auth.return_value = mock_service

            await security_middleware(scope, mock_receive, mock_send)

        # App should have been called (auth passed)
        mock_app.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_request_does_not_buffer_body(self, auth_middleware, mock_app):
        """Test GET requests don't attempt body extraction."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/exec",
            "query_string": b"",
            "headers": [(b"x-api-key", b"valid-key")],
        }

        mock_receive = AsyncMock()
        mock_send = AsyncMock()

        with patch("src.middleware.auth.get_auth_service") as mock_get_auth:
            mock_service = AsyncMock()
            mock_service.check_rate_limit.return_value = True
            mock_service.validate_api_key.return_value = True
            mock_get_auth.return_value = mock_service

            await auth_middleware(scope, mock_receive, mock_send)

        # receive should NOT have been called (no body buffering for GET)
        mock_receive.assert_not_called()
        mock_app.assert_called_once()

    @pytest.mark.asyncio
    async def test_auth_middleware_skips_body_for_non_json_content_type(self, auth_middleware, mock_app):
        """Test body extraction is skipped for non-JSON content types (e.g. multipart)."""
        body = json.dumps({"api_key": "body-key"}).encode()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/exec",
            "query_string": b"",
            "headers": [(b"content-type", b"multipart/form-data; boundary=abc")],
        }

        mock_receive = AsyncMock()
        mock_send = AsyncMock()

        with patch("src.middleware.auth.get_auth_service") as mock_get_auth:
            mock_service = AsyncMock()
            mock_service.check_rate_limit.return_value = True
            mock_service.validate_api_key.return_value = False
            mock_get_auth.return_value = mock_service

            await auth_middleware(scope, mock_receive, mock_send)

        # Body should NOT have been buffered (non-JSON content type)
        mock_receive.assert_not_called()
        # Should have sent a 401 response (no key found in headers)
        mock_send.assert_called()
        for call in mock_send.call_args_list:
            msg = call[0][0]
            if msg.get("type") == "http.response.start":
                assert msg["status"] == 401
                break

    @pytest.mark.asyncio
    async def test_security_middleware_skips_body_for_non_json_content_type(self, security_middleware):
        """Test SecurityMiddleware skips body extraction for non-JSON content types."""
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/exec",
            "query_string": b"",
            "headers": [(b"content-type", b"text/plain")],
        }

        mock_receive = AsyncMock()
        mock_send = AsyncMock()

        with patch("src.middleware.security.get_auth_service") as mock_get_auth:
            mock_service = AsyncMock()
            mock_service.check_rate_limit.return_value = True
            mock_result = MagicMock()
            mock_result.is_valid = False
            mock_result.error_message = "Invalid or missing API key"
            mock_service.validate_api_key_full.return_value = mock_result
            mock_get_auth.return_value = mock_service

            await security_middleware(scope, mock_receive, mock_send)

        # Body should NOT have been buffered
        mock_receive.assert_not_called()


class TestBufferBodySizeLimit:
    """Tests for body size limit enforcement in _buffer_body."""

    @pytest.mark.asyncio
    async def test_auth_buffer_body_rejects_oversized_payload(self, auth_middleware):
        """Test _buffer_body raises 413 for payloads exceeding the size limit."""
        from src.middleware.auth import _MAX_AUTH_BODY_BYTES

        oversized_chunk = b"x" * (_MAX_AUTH_BODY_BYTES + 1)

        async def mock_receive():
            return {"type": "http.request", "body": oversized_chunk, "more_body": False}

        with pytest.raises(HTTPException) as exc_info:
            await auth_middleware._buffer_body(mock_receive)

        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_security_buffer_body_rejects_oversized_payload(self, security_middleware):
        """Test _buffer_body raises 413 for payloads exceeding the size limit."""
        from src.middleware.security import _MAX_AUTH_BODY_BYTES

        oversized_chunk = b"x" * (_MAX_AUTH_BODY_BYTES + 1)

        async def mock_receive():
            return {"type": "http.request", "body": oversized_chunk, "more_body": False}

        with pytest.raises(HTTPException) as exc_info:
            await security_middleware._buffer_body(mock_receive)

        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_auth_buffer_body_rejects_oversized_multi_chunk(self, auth_middleware):
        """Test _buffer_body rejects multi-chunk payloads that exceed the limit."""
        from src.middleware.auth import _MAX_AUTH_BODY_BYTES

        chunk_size = _MAX_AUTH_BODY_BYTES // 2 + 1
        chunks = [
            {"type": "http.request", "body": b"x" * chunk_size, "more_body": True},
            {"type": "http.request", "body": b"x" * chunk_size, "more_body": False},
        ]
        chunk_iter = iter(chunks)

        async def mock_receive():
            return next(chunk_iter)

        with pytest.raises(HTTPException) as exc_info:
            await auth_middleware._buffer_body(mock_receive)

        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_buffer_body_accepts_payload_within_limit(self, auth_middleware):
        """Test _buffer_body accepts payloads within the size limit."""
        body = b'{"api_key": "test-key"}'

        async def mock_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        result_body, _ = await auth_middleware._buffer_body(mock_receive)
        assert result_body == body
