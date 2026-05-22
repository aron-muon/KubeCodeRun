"""Consolidated security middleware for the Code Interpreter API."""

# Standard library imports
import base64
import binascii
import hashlib
import ipaddress
import json
import time
from typing import Callable, Optional

# Third-party imports
import structlog
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

# Local application imports
from ..config import settings
from ..services.auth import get_auth_service

logger = structlog.get_logger(__name__)

# Max bytes to buffer when inspecting the request body for an API key.
# Keeps memory bounded for unauthenticated requests (issue #59 review).
_MAX_AUTH_BODY_BYTES = 1 * 1024 * 1024  # 1 MB


class SecurityMiddleware:
    """Consolidated middleware for security, authentication, and headers."""

    def __init__(self, app: Callable):
        self.app = app
        self.max_request_size = settings.max_file_size_mb * 1024 * 1024
        self.excluded_paths = {
            "/health",
            "/ready",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/admin",
            "/admin-dashboard",
        }
        self._trusted_networks = self._parse_trusted_networks(settings.auth_trusted_networks)

    async def __call__(self, scope: dict, receive: Callable, send: Callable):
        """Process request through consolidated security middleware."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)

        # Helper to add security headers to a response message
        def add_security_headers(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                path = scope.get("path", "")

                # Base security headers
                security_headers = {
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"x-xss-protection": b"1; mode=block",
                    b"strict-transport-security": b"max-age=31536000; includeSubDomains",
                    b"referrer-policy": b"strict-origin-when-cross-origin",
                    b"permissions-policy": b"geolocation=(), microphone=(), camera=()",
                }

                # Path-specific Content Security Policy
                if path in ["/docs", "/redoc", "/openapi.json"]:
                    security_headers[b"content-security-policy"] = (
                        b"default-src 'self'; "
                        b"script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
                        b"style-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
                        b"img-src 'self' data: fastapi.tiangolo.com; "
                        b"frame-src 'self';"
                    )
                elif path.startswith("/admin-dashboard") or path.startswith("/api/v1/admin"):
                    security_headers[b"content-security-policy"] = (
                        b"default-src 'self'; "
                        b"script-src 'self' 'unsafe-inline' 'unsafe-eval' unpkg.com cdn.jsdelivr.net; "
                        b"style-src 'self' 'unsafe-inline' fonts.googleapis.com unpkg.com cdn.jsdelivr.net; "
                        b"font-src 'self' fonts.gstatic.com; "
                        b"img-src 'self' data:; "
                        b"connect-src 'self';"
                    )
                else:
                    security_headers[b"content-security-policy"] = b"default-src 'self'"

                for key, value in security_headers.items():
                    headers[key] = value

                message["headers"] = list(headers.items())

        # Wrapper to intercept and add headers to any response
        async def send_wrapper(message):
            add_security_headers(message)
            await send(message)

        # Apply security checks and authentication
        try:
            # Check request size and content type
            await self._validate_request(request)

            # Handle authentication (skip for excluded paths and OPTIONS)
            if not self._should_skip_auth(request, scope):
                # CodeAPI JWT path (LibreChat 0.8.5+).
                # If a Bearer token is present AND it structurally looks
                # like a JWT AND JWT verification is enabled, that takes
                # precedence over the API-key path. On JWT validation
                # failure we 401 immediately rather than falling back to
                # API-key extraction — falling back would let an attacker
                # downgrade by submitting a deliberately-bad JWT.
                jwt_token = self._extract_bearer_jwt(request)
                if jwt_token is not None:
                    await self._authenticate_jwt(jwt_token, scope)
                else:
                    # Legacy API-key path.
                    api_key = self._extract_api_key(request)

                    # If no key in headers, try JSON body extraction
                    # for POST/PUT/PATCH (LC ≤ 3.1.74 spread the key
                    # into the body as LIBRECHAT_CODE_API_KEY).
                    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if (
                        api_key is None
                        and request.method in ("POST", "PUT", "PATCH")
                        and content_type == "application/json"
                    ):
                        body_bytes, receive = await self._buffer_body(receive)
                        api_key = self._extract_api_key_from_body(body_bytes)

                    await self._authenticate_request(request, scope, api_key=api_key)

        except HTTPException as e:
            response = JSONResponse(
                status_code=e.status_code,
                content={"error": e.detail, "timestamp": time.time()},
            )
            await response(scope, receive, send_wrapper)
            return
        except Exception as e:
            logger.error("Security middleware error", error=str(e))
            response = JSONResponse(
                status_code=500,
                content={"error": "Internal security error", "timestamp": time.time()},
            )
            await response(scope, receive, send_wrapper)
            return

        # Process the request normally
        await self.app(scope, receive, send_wrapper)

    async def _validate_request(self, request: Request):
        """Validate request content type."""
        # Only validate content type for non-file upload requests
        # File uploads are handled by the files API with specific validation
        # State uploads use raw binary (application/octet-stream)
        if (
            request.method in ["POST", "PUT", "PATCH"]
            and not request.url.path.startswith("/upload")
            and not request.url.path.startswith("/state/")
        ):
            content_type = request.headers.get("content-type", "")
            allowed_types = [
                "application/json",
                "multipart/form-data",
                "application/x-www-form-urlencoded",
                "text/plain",
            ]

            if not any(allowed in content_type for allowed in allowed_types):
                raise HTTPException(status_code=415, detail=f"Unsupported content type: {content_type}")

    def _should_skip_auth(self, request: Request, scope: dict) -> bool:
        """Check if authentication should be skipped.

        Returns True for:
          1. Excluded paths (/health, /docs, /redoc, /openapi.json) or OPTIONS.
          2. Admin paths — middleware skips; the admin endpoints enforce
             MASTER_API_KEY via their own dependency.
          3. Requests from a configured trusted network CIDR
             (AUTH_TRUSTED_NETWORKS — VPC-scoped bypass).
          4. ``settings.auth_enabled == False`` on non-admin paths
             (operator-controlled global bypass for trusted-boundary
             deployments). Admin paths still require MASTER_API_KEY.

        For trusted-network and disabled-auth bypasses we seed scope state
        with anonymous markers so downstream code that reads
        ``request.state.api_key_hash`` / ``is_env_key`` does not raise.
        We *also* attempt best-effort identity extraction (verified JWT
        sub, then User-Id header) so file-ownership checks in the
        orchestrator still work when the request is bypassed but the
        caller did identify itself. Without this, LibreChat bash_tool
        calls from a CIDR-trusted pod can't reach the user's uploaded
        files because we have no user_id to match against.
        """
        path = request.url.path
        is_admin_path = path.startswith("/api/v1/admin") or path.startswith("/admin-dashboard")

        if path in self.excluded_paths or request.method == "OPTIONS":
            return True

        # Admin paths bypass middleware auth; their own dependencies require
        # MASTER_API_KEY, so the bypass is safe and intentional.
        if is_admin_path:
            return True

        # Trusted-network bypass — only applies to user-facing paths.
        if self._trusted_networks and self._is_trusted_network(request):
            self._grant_anonymous_access(scope)
            self._extract_best_effort_identity(request, scope)
            return True

        # Operator-controlled bypass for trusted-boundary deployments
        # (e.g. mTLS sidecar, VPC ingress). Never applies to admin paths.
        if not settings.auth_enabled:
            self._grant_anonymous_access(scope)
            self._extract_best_effort_identity(request, scope)
            return True

        return False

    @staticmethod
    def _grant_anonymous_access(scope: dict) -> None:
        """Seed scope state for requests that bypassed authentication.

        Downstream code (exec endpoint, orchestrator metrics) reads
        ``request.state.api_key_hash`` and ``request.state.is_env_key``.
        Seeding ``"anonymous"`` keeps dashboards / log lines readable and
        avoids ``AttributeError`` when callers do ``getattr(..., None)``
        with type-narrowed code paths.
        """
        scope_state = scope.get("state") or {}
        scope_state.setdefault("authenticated", True)
        scope_state.setdefault("api_key", "")
        scope_state.setdefault("api_key_hash", "anonymous")
        scope_state.setdefault("is_env_key", False)
        scope["state"] = scope_state

    def _extract_best_effort_identity(self, request: Request, scope: dict) -> None:
        """Populate ``scope.state.user_id`` from any identity signal present.

        Even when auth is bypassed (CIDR trust, AUTH_ENABLED=false) we
        still want to know *who* is calling, so the orchestrator's
        cross-user file-isolation checks can find the user's session.
        Without an identity, every bypassed request looks like a brand-
        new anonymous user and prior uploads become unreachable.

        Sources, in order:
          1. ``Authorization: Bearer <jwt>`` — verified if CODEAPI_JWT
             is enabled. Failures here are non-fatal (the bypass already
             allowed the request); we just log and continue without
             user_id.
          2. ``User-Id`` / ``X-User-Id`` header — unsigned, only trusted
             because the bypass already trusted the network boundary
             that delivered the request.
        """
        scope_state = scope.get("state") or {}
        if scope_state.get("user_id"):
            return  # already set by a prior helper

        # JWT path
        if settings.codeapi_jwt_enabled:
            jwt_token = self._extract_bearer_jwt(request)
            if jwt_token:
                from ..services.codeapi_jwt import (
                    CodeApiJwtConfigurationError,
                    CodeApiJwtError,
                    verify,
                )

                try:
                    claims = verify(jwt_token)
                    scope_state["user_id"] = claims.sub
                    scope_state["auth_principal_source"] = "codeapi_jwt_bypassed"
                    if claims.tenant_id and settings.codeapi_jwt_trust_tenant_id:
                        scope_state["tenant_id"] = claims.tenant_id
                    scope["state"] = scope_state
                    return
                except CodeApiJwtConfigurationError as exc:
                    # Operator told us JWT is on but didn't configure a key.
                    # In bypass mode we can't 500 — log loudly and fall back.
                    logger.error(
                        "CodeAPI JWT misconfigured (auth bypassed, identity unknown)",
                        error=str(exc),
                    )
                except CodeApiJwtError as exc:
                    logger.info(
                        "CodeAPI JWT rejected during bypass (continuing anonymous)",
                        error=str(exc),
                    )

        # Header path
        header_user_id = request.headers.get("user-id") or request.headers.get("x-user-id")
        if header_user_id:
            scope_state["user_id"] = header_user_id
            scope_state.setdefault("auth_principal_source", "header_bypassed")
            scope["state"] = scope_state

    @staticmethod
    def _parse_trusted_networks(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        """Parse comma-separated CIDR strings into network objects."""
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        if not raw:
            return networks
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                logger.warning("Invalid trusted network CIDR, skipping", cidr=entry)
        return networks

    def _is_trusted_network(self, request: Request) -> bool:
        """Check if the client IP falls within a trusted CIDR range.

        Uses the actual socket peer address (request.client.host) rather than
        forwarded headers to prevent IP spoofing attacks.
        """
        if not request.client:
            return False
        client_ip_str = request.client.host
        try:
            client_ip = ipaddress.ip_address(client_ip_str)
        except ValueError:
            return False
        return any(client_ip in network for network in self._trusted_networks)

    async def _authenticate_request(self, request: Request, scope: dict, *, api_key: str | None = None):
        """Handle API key authentication with rate limiting."""
        # Use provided api_key or extract from headers as fallback
        if api_key is None:
            api_key = self._extract_api_key(request)

        if not api_key:
            raise HTTPException(status_code=401, detail="Missing API key")

        # Get authentication service
        auth_service = await get_auth_service()

        # Check IP-based rate limiting for auth failures
        client_ip = self._get_client_ip(request)
        if not await auth_service.check_rate_limit(client_ip):
            raise HTTPException(
                status_code=429,
                detail="Too many authentication failures. Please try again later.",
            )

        # Validate API key with full details
        result = await auth_service.validate_api_key_full(api_key)

        if not result.is_valid:
            raise HTTPException(
                status_code=401,
                detail=result.error_message or "Invalid or missing API key",
            )

        # Check for rate limit exceeded
        if result.rate_limit_exceeded:
            exceeded = result.exceeded_limit
            headers = {}
            if exceeded:
                headers = {
                    "X-RateLimit-Limit": str(exceeded.limit or 0),
                    "X-RateLimit-Remaining": str(0),
                    "X-RateLimit-Reset": exceeded.resets_at.isoformat(),
                    "X-RateLimit-Period": exceeded.period,
                    "Retry-After": str(
                        int(
                            (
                                exceeded.resets_at
                                - exceeded.resets_at.replace(
                                    hour=exceeded.resets_at.hour,
                                    minute=0,
                                    second=0,
                                    microsecond=0,
                                )
                            ).total_seconds()
                        )
                        or 60
                    ),
                }
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {exceeded.period if exceeded else 'period'}. "
                f"Limit: {exceeded.limit if exceeded else 0}, "
                f"Used: {exceeded.used if exceeded else 0}",
                headers=headers,
            )

        # Add authenticated state with key info for metrics tracking
        scope["state"] = scope.get("state", {})
        scope["state"]["authenticated"] = True
        scope["state"]["api_key"] = api_key
        scope["state"]["api_key_hash"] = result.key_hash
        scope["state"]["is_env_key"] = result.is_env_key

        # Record usage for all keys (both managed and env keys)
        if result.key_hash:
            await auth_service.record_usage(result.key_hash, is_env_key=result.is_env_key)

    def _extract_bearer_jwt(self, request: Request) -> str | None:
        """Return the Bearer token if it looks like a CodeAPI JWT, else None.

        Returns ``None`` (deferring to API-key auth) when:
          - ``settings.codeapi_jwt_enabled`` is False, OR
          - the Authorization header is missing / not ``Bearer``, OR
          - the Bearer value does not structurally look like a JWT (three
            base64 segments separated by dots). An attacker submitting a
            random 40-char API key as ``Bearer foo`` should keep being
            handled by the API-key path, not get a 401 from the JWT verifier.

        Returns the raw token string otherwise; verification happens in
        ``_authenticate_jwt``.
        """
        if not settings.codeapi_jwt_enabled:
            return None
        auth_header = request.headers.get("authorization") or ""
        if not auth_header.lower().startswith("bearer "):
            return None
        token = auth_header.split(" ", 1)[1].strip()
        # Import locally to keep middleware import-time cheap and to avoid
        # a circular import if codeapi_jwt ever wants to log via middleware.
        from ..services.codeapi_jwt import _looks_like_jwt

        return token if _looks_like_jwt(token) else None

    async def _authenticate_jwt(self, token: str, scope: dict) -> None:
        """Verify a CodeAPI JWT and seed scope state.

        Raises:
            HTTPException(401): the token is invalid (expired, wrong
                signature, wrong iss/aud, malformed).
            HTTPException(500): CodeAPI JWT auth is enabled but no public
                key is configured. This is a server-side bug; the client
                did nothing wrong.
        """
        from ..services.codeapi_jwt import (
            CodeApiJwtConfigurationError,
            CodeApiJwtError,
            verify,
        )

        try:
            claims = verify(token)
        except CodeApiJwtConfigurationError as exc:
            logger.error("CodeAPI JWT misconfigured", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail="CodeAPI JWT auth is enabled but public key is not configured",
            ) from exc
        except CodeApiJwtError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        # Seed request state. The JWT.sub IS the user identity — downstream
        # code (exec endpoint, orchestrator) trusts this over the User-Id
        # HTTP header because it is cryptographically signed.
        # api_key_hash uses a short prefix of sha256(sub) for log aggregation
        # without exposing the user id in metric dashboards.
        sub_hash = hashlib.sha256(claims.sub.encode()).hexdigest()
        scope_state = scope.get("state") or {}
        scope_state["authenticated"] = True
        scope_state["api_key"] = ""  # not an api-key auth path
        scope_state["api_key_hash"] = f"jwt:{sub_hash[:16]}"
        scope_state["is_env_key"] = False
        scope_state["user_id"] = claims.sub
        scope_state["auth_principal_source"] = "codeapi_jwt"
        if claims.tenant_id and settings.codeapi_jwt_trust_tenant_id:
            scope_state["tenant_id"] = claims.tenant_id
        if claims.jti:
            scope_state["jwt_jti"] = claims.jti
        scope["state"] = scope_state

    def _extract_api_key(self, request: Request) -> str | None:
        """Extract API key from request headers.

        Sources checked, in order:
        1. ``x-api-key`` header — preferred when present (reverse-proxy injection,
           older LibreChat versions).
        2. ``Authorization: Bearer <token>`` / ``Authorization: ApiKey <token>``.
        3. ``Authorization: Basic <base64(user:pass)>`` — LibreChat 0.8.5
           (``@librechat/agents`` ≥ 3.1.74) dropped both the ``x-api-key``
           header and the body-spread ``LIBRECHAT_CODE_API_KEY`` field. The
           only way the legacy ``LIBRECHAT_CODE_BASEURL=https://KEY@host/v1``
           pattern still reaches us is via the Basic header that ``axios``
           auto-derives from URL credentials. We return the user half (the
           legacy convention LC uses) and fall back to the password half so
           ``user:KEY`` deployments still work.

        Returns the first key found, or None.
        """
        # 1. x-api-key wins when present.
        api_key = request.headers.get("x-api-key")
        if api_key:
            return api_key

        # 2/3. Authorization header.
        auth_header = request.headers.get("authorization") or ""
        if not auth_header:
            return None

        scheme, _, value = auth_header.partition(" ")
        scheme_lower = scheme.lower()
        if scheme_lower in ("bearer", "apikey") and value:
            return value
        if scheme_lower == "basic" and value:
            try:
                decoded = base64.b64decode(value, validate=True).decode("utf-8", errors="replace")
            except (binascii.Error, ValueError):
                return None
            user, _, password = decoded.partition(":")
            # LibreChat's URL-embedded credential pattern puts the key in the
            # user half (no password); generic ``user:KEY`` deployments use
            # the password half. Prefer user, fall back to password.
            return user or password or None

        return None

    def _extract_api_key_from_body(self, body: bytes) -> str | None:
        """Extract API key from JSON request body fields.

        Supports @librechat/agents >=3.1.74 which spreads params into the
        request body instead of sending the key as a header.
        """
        if not body:
            return None
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                return None
            for field in ("LIBRECHAT_CODE_API_KEY", "api_key", "apiKey"):
                if key := data.get(field):
                    if isinstance(key, str) and key.strip():
                        return key.strip()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        return None

    async def _buffer_body(self, receive: Callable) -> tuple[bytes, Callable]:
        """Read and buffer the request body, returning a replay receive callable.

        This ensures the body remains available for downstream handlers after
        the middleware has inspected it.
        """
        body_parts: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            body = message.get("body", b"")
            if body:
                total += len(body)
                if total > _MAX_AUTH_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="Request body too large")
                body_parts.append(body)
            if not message.get("more_body", False):
                break

        full_body = b"".join(body_parts)

        # Create a replay receive that returns the buffered body
        body_sent = False

        async def replay_receive() -> dict:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": full_body, "more_body": False}
            return {"type": "http.disconnect"}

        return full_body, replay_receive

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP address."""
        # Check forwarded headers
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        return request.client.host if request.client else "unknown"


class RequestLoggingMiddleware:
    """Simplified request logging middleware."""

    def __init__(self, app: Callable):
        self.app = app
        self.health_logged = False

    async def __call__(self, scope: dict, receive: Callable, send: Callable):
        """Log request information."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        start_time = time.time()

        # Skip repeated health check logging
        skip_logging = request.url.path == "/health" and self.health_logged
        if request.url.path == "/health" and not self.health_logged:
            self.health_logged = True

        response_status = None

        async def send_wrapper(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            if not skip_logging:
                logger.error(
                    "Request failed",
                    method=request.method,
                    path=request.url.path,
                    error=str(e),
                )
            raise
        finally:
            if not skip_logging:
                duration = time.time() - start_time
                logger.info(
                    "Request processed",
                    method=request.method,
                    path=request.url.path,
                    status=response_status,
                    duration_ms=round(duration * 1000, 2),
                )
