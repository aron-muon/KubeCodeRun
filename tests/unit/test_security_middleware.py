"""Unit tests for Security Middleware."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.middleware.security import RequestLoggingMiddleware, SecurityMiddleware


@pytest.fixture
def mock_app():
    """Create a mock ASGI app."""
    return AsyncMock()


@pytest.fixture
def security_middleware(mock_app):
    """Create a security middleware instance."""
    with patch("src.middleware.security.settings") as mock_settings:
        mock_settings.max_file_size_mb = 10
        middleware = SecurityMiddleware(mock_app)
        return middleware


@pytest.fixture
def http_scope():
    """Create a basic HTTP scope."""
    return {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/test",
        "query_string": b"",
        "headers": [],
        "state": {},
    }


@pytest.fixture
def mock_receive():
    """Create a mock receive function."""
    return AsyncMock()


@pytest.fixture
def mock_send():
    """Create a mock send function."""
    return AsyncMock()


class TestSecurityMiddlewareInit:
    """Tests for SecurityMiddleware initialization."""

    def test_init(self, mock_app):
        """Test middleware initialization."""
        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.max_file_size_mb = 10
            middleware = SecurityMiddleware(mock_app)

        assert middleware.app is mock_app
        assert middleware.max_request_size == 10 * 1024 * 1024
        assert "/health" in middleware.excluded_paths

    def test_excluded_paths(self, security_middleware):
        """Test excluded paths are set correctly."""
        assert "/health" in security_middleware.excluded_paths
        assert "/ready" in security_middleware.excluded_paths
        assert "/docs" in security_middleware.excluded_paths
        assert "/openapi.json" in security_middleware.excluded_paths


class TestSecurityMiddlewareCall:
    """Tests for SecurityMiddleware __call__ method."""

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self, security_middleware, mock_app, mock_receive, mock_send):
        """Test that non-HTTP requests pass through."""
        scope = {"type": "websocket"}

        await security_middleware(scope, mock_receive, mock_send)

        mock_app.assert_called_once_with(scope, mock_receive, mock_send)

    @pytest.mark.asyncio
    async def test_excluded_path_skips_auth(self, security_middleware, mock_app, mock_receive, mock_send):
        """Test that excluded paths skip authentication."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "query_string": b"",
            "headers": [],
        }

        await security_middleware(scope, mock_receive, mock_send)

        mock_app.assert_called_once()

    @pytest.mark.asyncio
    async def test_options_method_skips_auth(self, security_middleware, mock_app, mock_receive, mock_send):
        """Test that OPTIONS requests skip authentication."""
        scope = {
            "type": "http",
            "method": "OPTIONS",
            "path": "/api/v1/exec",
            "query_string": b"",
            "headers": [],
        }

        await security_middleware(scope, mock_receive, mock_send)

        mock_app.assert_called_once()


class TestShouldSkipAuth:
    """Tests for _should_skip_auth method."""

    def test_skip_health_path(self, security_middleware):
        """Test skip auth for /health path."""
        request = MagicMock()
        request.url.path = "/health"
        request.method = "GET"

        result = security_middleware._should_skip_auth(request, {})

        assert result is True

    def test_skip_ready_path(self, security_middleware):
        """Test skip auth for /ready path."""
        request = MagicMock()
        request.url.path = "/ready"
        request.method = "GET"

        result = security_middleware._should_skip_auth(request, {})

        assert result is True

    def test_skip_docs_path(self, security_middleware):
        """Test skip auth for /docs path."""
        request = MagicMock()
        request.url.path = "/docs"
        request.method = "GET"

        result = security_middleware._should_skip_auth(request, {})

        assert result is True

    def test_skip_admin_path(self, security_middleware):
        """Test skip auth for admin paths."""
        request = MagicMock()
        request.url.path = "/api/v1/admin/keys"
        request.method = "GET"

        result = security_middleware._should_skip_auth(request, {})

        assert result is True

    def test_skip_admin_dashboard_path(self, security_middleware):
        """Test skip auth for admin dashboard paths."""
        request = MagicMock()
        request.url.path = "/admin-dashboard/metrics"
        request.method = "GET"

        result = security_middleware._should_skip_auth(request, {})

        assert result is True

    def test_skip_options_method(self, security_middleware):
        """Test skip auth for OPTIONS method."""
        request = MagicMock()
        request.url.path = "/api/v1/exec"
        request.method = "OPTIONS"

        result = security_middleware._should_skip_auth(request, {})

        assert result is True

    def test_no_skip_regular_path(self, security_middleware):
        """Test no skip for regular paths."""
        request = MagicMock()
        request.url.path = "/api/v1/exec"
        request.method = "POST"

        result = security_middleware._should_skip_auth(request, {})

        assert result is False


class TestExtractApiKey:
    """Tests for _extract_api_key method."""

    def test_extract_from_x_api_key_header(self, security_middleware):
        """Test extracting API key from x-api-key header."""
        request = MagicMock()
        request.headers.get.side_effect = lambda h: "test-key" if h == "x-api-key" else None

        result = security_middleware._extract_api_key(request)

        assert result == "test-key"

    def test_extract_from_bearer_token(self, security_middleware):
        """Test extracting API key from Bearer token."""
        request = MagicMock()
        request.headers.get.side_effect = lambda h: "Bearer my-token" if h == "authorization" else None

        result = security_middleware._extract_api_key(request)

        assert result == "my-token"

    def test_extract_from_apikey_prefix(self, security_middleware):
        """Test extracting API key from ApiKey prefix."""
        request = MagicMock()
        request.headers.get.side_effect = lambda h: "ApiKey my-key" if h == "authorization" else None

        result = security_middleware._extract_api_key(request)

        assert result == "my-key"

    def test_extract_no_key(self, security_middleware):
        """Test when no API key is present."""
        request = MagicMock()
        request.headers.get.return_value = None

        result = security_middleware._extract_api_key(request)

        assert result is None


class TestGetClientIp:
    """Tests for _get_client_ip method."""

    def test_get_ip_from_x_forwarded_for(self, security_middleware):
        """Test getting IP from x-forwarded-for header."""
        request = MagicMock()
        request.headers.get.side_effect = lambda h: "1.2.3.4, 5.6.7.8" if h == "x-forwarded-for" else None

        result = security_middleware._get_client_ip(request)

        assert result == "1.2.3.4"

    def test_get_ip_from_x_real_ip(self, security_middleware):
        """Test getting IP from x-real-ip header."""
        request = MagicMock()
        request.headers.get.side_effect = lambda h: "10.0.0.1" if h == "x-real-ip" else None

        result = security_middleware._get_client_ip(request)

        assert result == "10.0.0.1"

    def test_get_ip_from_client(self, security_middleware):
        """Test getting IP from client."""
        request = MagicMock()
        request.headers.get.return_value = None
        request.client.host = "192.168.1.1"

        result = security_middleware._get_client_ip(request)

        assert result == "192.168.1.1"

    def test_get_ip_no_client(self, security_middleware):
        """Test getting IP when no client is present."""
        request = MagicMock()
        request.headers.get.return_value = None
        request.client = None

        result = security_middleware._get_client_ip(request)

        assert result == "unknown"


class TestValidateRequest:
    """Tests for _validate_request method."""

    @pytest.mark.asyncio
    async def test_validate_get_request(self, security_middleware):
        """Test GET requests don't require content type validation."""
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/test"

        # Should not raise
        await security_middleware._validate_request(request)

    @pytest.mark.asyncio
    async def test_validate_post_json(self, security_middleware):
        """Test POST with JSON content type is valid."""
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/v1/exec"
        request.headers.get.return_value = "application/json"

        # Should not raise
        await security_middleware._validate_request(request)

    @pytest.mark.asyncio
    async def test_validate_post_multipart(self, security_middleware):
        """Test POST with multipart content type is valid."""
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/v1/files"
        request.headers.get.return_value = "multipart/form-data; boundary=----"

        # Should not raise
        await security_middleware._validate_request(request)

    @pytest.mark.asyncio
    async def test_validate_post_invalid_content_type(self, security_middleware):
        """Test POST with invalid content type raises error."""
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/v1/exec"
        request.headers.get.return_value = "application/xml"

        with pytest.raises(HTTPException) as exc_info:
            await security_middleware._validate_request(request)

        assert exc_info.value.status_code == 415
        assert "Unsupported content type" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_validate_upload_path_skips_content_type(self, security_middleware):
        """Test upload path skips content type validation."""
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/upload/file"
        request.headers.get.return_value = "application/octet-stream"

        # Should not raise
        await security_middleware._validate_request(request)

    @pytest.mark.asyncio
    async def test_validate_state_path_skips_content_type(self, security_middleware):
        """Test state path skips content type validation."""
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/state/session-123"
        request.headers.get.return_value = "application/octet-stream"

        # Should not raise
        await security_middleware._validate_request(request)


class TestAuthenticateRequest:
    """Tests for _authenticate_request method."""

    @pytest.mark.asyncio
    async def test_authenticate_success(self, security_middleware):
        """Test successful authentication."""
        request = MagicMock()
        request.headers.get.side_effect = lambda h: "valid-key" if h == "x-api-key" else None
        request.client.host = "127.0.0.1"
        scope = {"state": {}}

        mock_auth_service = MagicMock()
        mock_auth_service.check_rate_limit = AsyncMock(return_value=True)
        mock_result = MagicMock()
        mock_result.is_valid = True
        mock_result.rate_limit_exceeded = False
        mock_result.key_hash = "hash123"
        mock_result.is_env_key = False
        mock_auth_service.validate_api_key_full = AsyncMock(return_value=mock_result)
        mock_auth_service.record_usage = AsyncMock()

        with patch("src.middleware.security.get_auth_service", return_value=mock_auth_service):
            await security_middleware._authenticate_request(request, scope)

        assert scope["state"]["authenticated"] is True
        assert scope["state"]["api_key"] == "valid-key"

    @pytest.mark.asyncio
    async def test_authenticate_rate_limited(self, security_middleware):
        """Test authentication with rate limiting."""
        request = MagicMock()
        request.headers.get.side_effect = lambda h: "valid-key" if h == "x-api-key" else None
        request.client.host = "127.0.0.1"
        scope = {"state": {}}

        mock_auth_service = MagicMock()
        mock_auth_service.check_rate_limit = AsyncMock(return_value=False)

        with patch("src.middleware.security.get_auth_service", return_value=mock_auth_service):
            with pytest.raises(HTTPException) as exc_info:
                await security_middleware._authenticate_request(request, scope)

        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_authenticate_invalid_key(self, security_middleware):
        """Test authentication with invalid key."""
        request = MagicMock()
        request.headers.get.side_effect = lambda h: "invalid-key" if h == "x-api-key" else None
        request.client.host = "127.0.0.1"
        scope = {"state": {}}

        mock_auth_service = MagicMock()
        mock_auth_service.check_rate_limit = AsyncMock(return_value=True)
        mock_result = MagicMock()
        mock_result.is_valid = False
        mock_result.error_message = "Invalid key"
        mock_auth_service.validate_api_key_full = AsyncMock(return_value=mock_result)

        with patch("src.middleware.security.get_auth_service", return_value=mock_auth_service):
            with pytest.raises(HTTPException) as exc_info:
                await security_middleware._authenticate_request(request, scope)

        assert exc_info.value.status_code == 401


class TestRequestLoggingMiddleware:
    """Tests for RequestLoggingMiddleware."""

    @pytest.fixture
    def logging_middleware(self, mock_app):
        """Create a logging middleware instance."""
        return RequestLoggingMiddleware(mock_app)

    @pytest.mark.asyncio
    async def test_non_http_passes_through(self, logging_middleware, mock_app, mock_receive, mock_send):
        """Test non-HTTP requests pass through."""
        scope = {"type": "websocket"}

        await logging_middleware(scope, mock_receive, mock_send)

        mock_app.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_request(self, logging_middleware, mock_app, mock_receive, mock_send):
        """Test that requests are logged."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/test",
            "query_string": b"",
            "headers": [],
        }

        await logging_middleware(scope, mock_receive, mock_send)

        mock_app.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_logged_once(self, logging_middleware, mock_app, mock_receive, mock_send):
        """Test health endpoint is logged only once."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "query_string": b"",
            "headers": [],
        }

        # First request - should log
        await logging_middleware(scope, mock_receive, mock_send)
        assert logging_middleware.health_logged is True

        # Second request - should skip logging
        await logging_middleware(scope, mock_receive, mock_send)

    @pytest.mark.asyncio
    async def test_captures_response_status(self, mock_app, mock_receive):
        """Test that response status is captured."""
        logging_middleware = RequestLoggingMiddleware(mock_app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/test",
            "query_string": b"",
            "headers": [],
        }

        # Track what send receives
        captured_status = None

        async def mock_send_capturing(message):
            nonlocal captured_status
            if message["type"] == "http.response.start":
                captured_status = message.get("status")

        # Configure mock app to send a response
        async def app_that_responds(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        logging_middleware.app = app_that_responds

        await logging_middleware(scope, mock_receive, mock_send_capturing)

    @pytest.mark.asyncio
    async def test_handles_exception(self, mock_receive, mock_send):
        """Test that exceptions are logged and re-raised."""

        async def failing_app(scope, receive, send):
            raise ValueError("Test error")

        logging_middleware = RequestLoggingMiddleware(failing_app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/test",
            "query_string": b"",
            "headers": [],
        }

        with pytest.raises(ValueError):
            await logging_middleware(scope, mock_receive, mock_send)


class TestExtractApiKeyBasicAuth:
    """LibreChat 0.8.5 (@librechat/agents >=3.1.74) dropped x-api-key and the
    body-spread LIBRECHAT_CODE_API_KEY field. The only remaining channel for
    the legacy LIBRECHAT_CODE_BASEURL=https://KEY@host/v1 pattern is the
    Basic auth header that axios derives from URL credentials. These tests
    pin down our handling of that channel.
    """

    @staticmethod
    def _request_with_auth(value: str | None):
        import base64

        request = MagicMock()

        def _get(name, default=None):
            if name == "x-api-key":
                return None
            if name == "authorization":
                return value if value is not None else default
            return default

        request.headers.get.side_effect = _get
        return request

    def _basic(self, raw: str) -> str:
        import base64

        return "Basic " + base64.b64encode(raw.encode("utf-8")).decode("ascii")

    def test_basic_auth_user_only(self, security_middleware):
        """axios URL-creds form: ``https://KEY@host`` -> ``Basic base64('KEY:')``.
        We must return KEY (the user half)."""
        request = self._request_with_auth(self._basic("test-api-key-12345:"))
        assert security_middleware._extract_api_key(request) == "test-api-key-12345"

    def test_basic_auth_password_only(self, security_middleware):
        """``:KEY`` form falls through to password half."""
        request = self._request_with_auth(self._basic(":test-api-key-12345"))
        assert security_middleware._extract_api_key(request) == "test-api-key-12345"

    def test_basic_auth_user_and_password_prefers_user(self, security_middleware):
        """``user:KEY`` is ambiguous; LibreChat uses the user half, so we
        match upstream and return ``user``. Document the convention so
        ``user:KEY`` deployments know to flip the values."""
        request = self._request_with_auth(self._basic("apiuser:apikey"))
        assert security_middleware._extract_api_key(request) == "apiuser"

    def test_basic_auth_invalid_base64(self, security_middleware):
        """Malformed Basic header returns None (no crash, no 500)."""
        request = self._request_with_auth("Basic !!!notbase64!!!")
        assert security_middleware._extract_api_key(request) is None

    def test_basic_auth_empty(self, security_middleware):
        """``Basic `` with no value returns None."""
        request = self._request_with_auth("Basic ")
        assert security_middleware._extract_api_key(request) is None

    def test_x_api_key_wins_over_basic(self, security_middleware):
        """When both x-api-key AND Authorization are present, x-api-key wins
        so reverse-proxy injection has deterministic behaviour."""
        import base64

        request = MagicMock()

        def _get(name, default=None):
            if name == "x-api-key":
                return "from-header"
            if name == "authorization":
                return "Basic " + base64.b64encode(b"from-basic:").decode("ascii")
            return default

        request.headers.get.side_effect = _get
        assert security_middleware._extract_api_key(request) == "from-header"

    def test_bearer_still_works(self, security_middleware):
        """Adding Basic must not regress Bearer."""
        request = self._request_with_auth("Bearer my-jwt")
        assert security_middleware._extract_api_key(request) == "my-jwt"

    def test_apikey_scheme_still_works(self, security_middleware):
        """Adding Basic must not regress ApiKey."""
        request = self._request_with_auth("ApiKey my-key")
        assert security_middleware._extract_api_key(request) == "my-key"

    def test_unknown_scheme_returns_none(self, security_middleware):
        """Unknown schemes (Digest, Negotiate) return None."""
        request = self._request_with_auth("Digest realm=foo")
        assert security_middleware._extract_api_key(request) is None


class TestAuthEnabledBypass:
    """AUTH_ENABLED=false flips middleware into trust-the-boundary mode for
    user paths. Admin paths must still require MASTER_API_KEY."""

    def test_user_path_bypassed_when_auth_disabled(self, security_middleware):
        request = MagicMock()
        request.url.path = "/exec"
        request.method = "POST"
        scope = {}

        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.auth_enabled = False
            mock_settings.auth_trusted_networks = ""
            mock_settings.max_file_size_mb = 10
            assert security_middleware._should_skip_auth(request, scope) is True

        # Anonymous state must be seeded so downstream metrics don't crash.
        assert scope["state"]["authenticated"] is True
        assert scope["state"]["api_key_hash"] == "anonymous"
        assert scope["state"]["is_env_key"] is False

    def test_user_path_NOT_bypassed_when_auth_enabled(self, security_middleware):
        request = MagicMock()
        request.url.path = "/exec"
        request.method = "POST"
        scope: dict = {}

        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.auth_enabled = True
            mock_settings.auth_trusted_networks = ""
            mock_settings.max_file_size_mb = 10
            assert security_middleware._should_skip_auth(request, scope) is False

        assert scope == {}, "no state seed when auth still required"

    def test_admin_path_does_not_get_anonymous_seed(self, security_middleware):
        """Admin paths skip middleware auth (their dependency enforces master
        key) but must NOT be seeded with anonymous state — a bug in an admin
        endpoint dependency must fail closed, not silently impersonate
        anonymous."""
        request = MagicMock()
        request.url.path = "/api/v1/admin/keys"
        request.method = "GET"
        scope: dict = {}

        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.auth_enabled = False  # still skip — admin path
            mock_settings.auth_trusted_networks = ""
            mock_settings.max_file_size_mb = 10
            assert security_middleware._should_skip_auth(request, scope) is True

        assert "state" not in scope, "admin path must not receive anonymous seed"

    def test_trusted_network_seeds_anonymous(self, security_middleware):
        """Trusted-network bypass also gets the anonymous state seed."""
        request = MagicMock()
        request.url.path = "/exec"
        request.method = "POST"
        request.client.host = "10.0.0.5"
        scope: dict = {}

        # Inject a trusted CIDR after construction (constructor read empty).
        import ipaddress

        security_middleware._trusted_networks = [ipaddress.ip_network("10.0.0.0/8")]

        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.auth_enabled = True
            mock_settings.max_file_size_mb = 10
            assert security_middleware._should_skip_auth(request, scope) is True

        assert scope["state"]["api_key_hash"] == "anonymous"


class TestExtractBearerJwt:
    """``_extract_bearer_jwt`` is the gate: it returns a token only when
    ``settings.codeapi_jwt_enabled`` AND the Bearer value looks like a JWT.
    Everything else falls through to the API-key path."""

    def test_returns_none_when_jwt_disabled(self, security_middleware):
        request = MagicMock()
        request.headers.get.return_value = "Bearer aaaa.bbbb.cccc"

        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.codeapi_jwt_enabled = False
            assert security_middleware._extract_bearer_jwt(request) is None

    def test_returns_none_without_bearer_scheme(self, security_middleware):
        request = MagicMock()
        request.headers.get.return_value = "Basic ZG9lOnNlY3JldA=="

        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.codeapi_jwt_enabled = True
            assert security_middleware._extract_bearer_jwt(request) is None

    def test_returns_none_when_bearer_is_not_jwt_shaped(self, security_middleware):
        """A plain API key submitted as Bearer must NOT be classified as JWT
        (otherwise we'd 401 every legacy ApiKey-via-Bearer client)."""
        request = MagicMock()
        request.headers.get.return_value = "Bearer my-flat-api-key-deadbeef"

        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.codeapi_jwt_enabled = True
            assert security_middleware._extract_bearer_jwt(request) is None

    def test_returns_token_when_jwt_enabled_and_shaped(self, security_middleware):
        request = MagicMock()
        request.headers.get.return_value = "Bearer aaaa.bbbb.cccc"

        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.codeapi_jwt_enabled = True
            assert security_middleware._extract_bearer_jwt(request) == "aaaa.bbbb.cccc"


class TestAuthenticateJwt:
    """``_authenticate_jwt`` calls the verifier and seeds scope state."""

    @pytest.mark.asyncio
    async def test_valid_jwt_seeds_user_id_and_auth_state(self, security_middleware):
        from unittest.mock import patch as _patch

        from src.services.codeapi_jwt import JwtClaims

        scope: dict = {}
        claims = JwtClaims(
            sub="user-from-jwt",
            tenant_id="tenant-A",
            role="USER",
            principal_source="librechat_jwt",
            jti="jti-1",
        )

        with _patch("src.services.codeapi_jwt.verify", return_value=claims):
            with _patch("src.middleware.security.settings") as mock_settings:
                mock_settings.codeapi_jwt_trust_tenant_id = True
                await security_middleware._authenticate_jwt("a.b.c", scope)

        state = scope["state"]
        assert state["authenticated"] is True
        assert state["user_id"] == "user-from-jwt"
        assert state["api_key"] == ""  # not an api-key path
        assert state["api_key_hash"].startswith("jwt:")
        assert state["is_env_key"] is False
        assert state["auth_principal_source"] == "codeapi_jwt"
        assert state["tenant_id"] == "tenant-A"
        assert state["jwt_jti"] == "jti-1"

    @pytest.mark.asyncio
    async def test_tenant_id_omitted_when_trust_disabled(self, security_middleware):
        from unittest.mock import patch as _patch

        from src.services.codeapi_jwt import JwtClaims

        scope: dict = {}
        claims = JwtClaims(sub="u", tenant_id="t", role=None, principal_source=None, jti=None)

        with _patch("src.services.codeapi_jwt.verify", return_value=claims):
            with _patch("src.middleware.security.settings") as mock_settings:
                mock_settings.codeapi_jwt_trust_tenant_id = False
                await security_middleware._authenticate_jwt("a.b.c", scope)

        assert "tenant_id" not in scope["state"]

    @pytest.mark.asyncio
    async def test_invalid_jwt_raises_401(self, security_middleware):
        from unittest.mock import patch as _patch

        from src.services.codeapi_jwt import CodeApiJwtError

        with _patch("src.services.codeapi_jwt.verify", side_effect=CodeApiJwtError("expired")):
            with pytest.raises(HTTPException) as exc:
                await security_middleware._authenticate_jwt("a.b.c", {})
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail

    @pytest.mark.asyncio
    async def test_misconfigured_jwt_raises_500(self, security_middleware):
        """Operator enabled JWT auth but didn't configure a public key.
        Client did nothing wrong; 500 is correct."""
        from unittest.mock import patch as _patch

        from src.services.codeapi_jwt import CodeApiJwtConfigurationError

        with _patch(
            "src.services.codeapi_jwt.verify",
            side_effect=CodeApiJwtConfigurationError("no key"),
        ):
            with pytest.raises(HTTPException) as exc:
                await security_middleware._authenticate_jwt("a.b.c", {})
        assert exc.value.status_code == 500


class TestJwtTakesPrecedenceOverApiKey:
    """When a JWT-shaped Bearer is present AND JWT auth is enabled, the
    JWT path runs INSTEAD OF the API-key path. A failed JWT must NOT
    fall back to API-key auth (downgrade-attack defence)."""

    @pytest.mark.asyncio
    async def test_failed_jwt_does_not_fall_back_to_api_key(self, security_middleware, mock_app, mock_send):
        """End-to-end through __call__: bad JWT must 401, NEVER reach the
        API-key auth path with the same Bearer string."""
        import json as _json
        from unittest.mock import patch as _patch

        from src.services.codeapi_jwt import CodeApiJwtError

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/exec",
            "query_string": b"",
            "headers": [
                (b"authorization", b"Bearer aaaa.bbbb.cccc"),
                (b"content-type", b"application/json"),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        sent = []

        async def send(message):
            sent.append(message)

        with _patch("src.middleware.security.settings") as mock_settings:
            mock_settings.codeapi_jwt_enabled = True
            mock_settings.codeapi_jwt_trust_tenant_id = False
            mock_settings.auth_enabled = True
            mock_settings.auth_trusted_networks = ""
            mock_settings.max_file_size_mb = 10
            with _patch(
                "src.services.codeapi_jwt.verify",
                side_effect=CodeApiJwtError("bad signature"),
            ):
                await security_middleware(scope, receive, send)

        # Response is a 401; downstream app was NEVER called (no fallback).
        mock_app.assert_not_called()
        start = sent[0]
        assert start["type"] == "http.response.start"
        assert start["status"] == 401
