"""LibreChat CodeAPI JWT verifier.

LibreChat 0.8.5 (``danny-avila/LibreChat#13028`` — *🔐 feat: Mint Code API
Auth Tokens*) introduced signed JWT auth for its code-interpreter calls.
The signer side lives in ``packages/api/src/auth/codeapi.ts``; this module
is the verifier on the codeapi side.

Wire shape
----------

Header:

  ``Authorization: Bearer <jwt>``

Where ``<jwt>`` is signed with **EdDSA** (Ed25519, the default) or
**RS256**, and carries these claims::

    {
      "iss": "librechat",       # CODEAPI_JWT_ISSUER
      "aud": "codeapi",         # CODEAPI_JWT_AUDIENCE
      "sub": "<userId>",        # authenticated LC user id
      "iat": <unix>,
      "nbf": <unix>,
      "exp": <unix>,            # iat + CODEAPI_JWT_TTL_SECONDS (<=300)
      "jti": "<uuid>",
      "tenant_id": "<tenant>",
      "role": "USER" | "ADMIN" | ...,
      "principal_source": "librechat_jwt" | "openid_reuse",
      "auth_context_hash": "<sha256-hex>",
      # optional: org_id, service_id, chc_user_id, plan_id
    }

JWT header carries ``kid`` for key rotation; we accept any kid the
configured public key validates (single-key deployments). Future work
could add a JWKS endpoint.

Security notes
--------------

- We **pin the algorithm** to the operator-configured one
  (``CODEAPI_JWT_ALGORITHM``) — never trust the header's ``alg`` to pick
  an algorithm. This is the standard "alg confusion" defence.
- We enforce ``iss``/``aud`` exactly and validate ``exp``/``nbf`` with
  a small leeway (``codeapi_jwt_leeway_seconds``).
- The public key is loaded once per process and cached. Operators rotate
  keys by restarting the process.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt as pyjwt
import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes

from ..config import settings

logger = structlog.get_logger(__name__)


# Concrete pyjwt errors we treat as "bad token, log at info, return 401".
# Everything else (configuration / programming) is logged at error.
_TOKEN_ERRORS: tuple[type[Exception], ...] = (
    pyjwt.ExpiredSignatureError,
    pyjwt.ImmatureSignatureError,
    pyjwt.InvalidAudienceError,
    pyjwt.InvalidIssuerError,
    pyjwt.InvalidSignatureError,
    pyjwt.InvalidTokenError,
    pyjwt.DecodeError,
)


@dataclass(frozen=True)
class JwtClaims:
    """The subset of CodeAPI JWT claims downstream code consumes.

    Kept narrow on purpose — the full claim set is for LibreChat's
    auditing/billing model and we don't have a use for most fields.
    """

    sub: str  # LibreChat user id (the value LC otherwise sends as User-Id)
    tenant_id: str | None
    role: str | None
    principal_source: str | None
    jti: str | None  # request id (useful for log correlation)


class CodeApiJwtError(Exception):
    """Base error for the verifier — caller maps to HTTP 401."""


class CodeApiJwtConfigurationError(CodeApiJwtError):
    """Raised when JWT auth is enabled but no public key is configured."""


def _looks_like_jwt(token: str) -> bool:
    """Cheap structural check: 3 base64-ish segments separated by dots.

    Lets callers distinguish "this Bearer is a JWT" from "this Bearer is
    a plain API key" without paying the parse cost.
    """
    if not token or token.count(".") != 2:
        return False
    parts = token.split(".")
    return all(part and len(part) >= 4 for part in parts)


def _load_pem_or_jwk(raw: str) -> PublicKeyTypes:
    """Parse the configured public key from PEM, JWK JSON, or file path."""
    candidate = raw.strip()

    # File path?
    if (candidate.startswith("/") or candidate.startswith("./")) and os.path.isfile(candidate):
        with open(candidate, encoding="utf-8") as fh:
            candidate = fh.read().strip()

    # JWK JSON?
    if candidate.startswith("{"):
        try:
            jwk = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise CodeApiJwtConfigurationError(
                f"codeapi_jwt_public_key looked like JWK JSON but failed to parse: {exc}"
            ) from exc
        algo = pyjwt.get_algorithm_by_name(settings.codeapi_jwt_algorithm)
        return algo.from_jwk(jwk)

    # PEM (or PKCS#1)?
    try:
        return serialization.load_pem_public_key(candidate.encode("utf-8"))
    except ValueError as exc:
        raise CodeApiJwtConfigurationError("codeapi_jwt_public_key is not a recognized PEM, JWK, or file path") from exc


@lru_cache(maxsize=1)
def _get_public_key() -> PublicKeyTypes:
    """Load and cache the configured public key.

    Cached forever within the process — operators rotate by restarting.
    The cache key is implicit (no args); changing the env var requires
    a restart by design.
    """
    raw = settings.codeapi_jwt_public_key
    if not raw:
        raise CodeApiJwtConfigurationError(
            "codeapi_jwt_enabled=true but codeapi_jwt_public_key is unset. "
            "Provide the LibreChat-paired public key as PEM, JWK, or a file path."
        )
    return _load_pem_or_jwk(raw)


def reset_public_key_cache() -> None:
    """Drop the cached key. Tests use this to swap keys between runs."""
    _get_public_key.cache_clear()


def verify(token: str) -> JwtClaims:
    """Verify a CodeAPI JWT and return the narrow claim subset.

    Raises:
        CodeApiJwtConfigurationError: server-side misconfiguration.
            Caller should respond 500, not 401 — the client did nothing
            wrong; we just can't tell who they are.
        CodeApiJwtError: any token-side problem (expired, wrong issuer,
            invalid signature, malformed). Caller maps to 401.
    """
    if not settings.codeapi_jwt_enabled:
        raise CodeApiJwtError("CodeAPI JWT auth is disabled")

    if not _looks_like_jwt(token):
        # Caller should not have invoked us; treat as caller bug.
        raise CodeApiJwtError("Bearer value is not a JWT (expected three base64 segments)")

    public_key = _get_public_key()  # may raise CodeApiJwtConfigurationError

    try:
        claims = pyjwt.decode(
            token,
            key=public_key,
            algorithms=[settings.codeapi_jwt_algorithm],  # alg pinning
            issuer=settings.codeapi_jwt_issuer,
            audience=settings.codeapi_jwt_audience,
            leeway=settings.codeapi_jwt_leeway_seconds,
            options={
                "require": ["iss", "aud", "sub", "exp", "iat"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
    except _TOKEN_ERRORS as exc:
        logger.info("CodeAPI JWT rejected", reason=type(exc).__name__, error=str(exc))
        raise CodeApiJwtError(f"Invalid CodeAPI JWT: {exc}") from exc

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise CodeApiJwtError("CodeAPI JWT has empty sub claim")

    return JwtClaims(
        sub=sub,
        tenant_id=_str_or_none(claims.get("tenant_id")),
        role=_str_or_none(claims.get("role")),
        principal_source=_str_or_none(claims.get("principal_source")),
        jti=_str_or_none(claims.get("jti")),
    )


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
