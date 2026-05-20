"""Unit tests for the CodeAPI JWT verifier (``src/services/codeapi_jwt.py``).

These tests mint signed tokens with the same shape LibreChat 0.8.5 emits
(``packages/api/src/auth/codeapi.ts``) and run them through our verifier.
Both EdDSA (the default) and RS256 paths are covered.

We mock ``src.services.codeapi_jwt.settings`` rather than the global
``src.config.settings`` so changing one test's config doesn't bleed into
the next.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from src.services import codeapi_jwt
from src.services.codeapi_jwt import (
    CodeApiJwtConfigurationError,
    CodeApiJwtError,
    JwtClaims,
    _looks_like_jwt,
    reset_public_key_cache,
    verify,
)

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _ed25519_keypair() -> tuple[str, str]:
    """Generate an Ed25519 keypair, return (private PEM, public PEM)."""
    private = ed25519.Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def _rsa_keypair() -> tuple[str, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def _mint(
    private_pem: str,
    *,
    algorithm: str = "EdDSA",
    issuer: str = "librechat",
    audience: str = "codeapi",
    sub: str = "user_123",
    tenant_id: str | None = "tenant_abc",
    extra: dict[str, Any] | None = None,
    ttl: int = 300,
    iat_offset: int = 0,
) -> str:
    """Mint a token mirroring LibreChat's signer shape."""
    now = int(time.time()) + iat_offset
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": "test-jti",
        "role": "USER",
        "principal_source": "librechat_jwt",
        "auth_context_hash": "deadbeef",
    }
    if tenant_id:
        claims["tenant_id"] = tenant_id
    if extra:
        claims.update(extra)
    return pyjwt.encode(claims, private_pem, algorithm=algorithm, headers={"kid": "test-kid"})


# ---------------------------------------------------------------------------
# Fixtures: configure settings + load public key
# ---------------------------------------------------------------------------


@pytest.fixture
def configured(request):
    """Configure ``codeapi_jwt`` for one test and reset the key cache after.

    Parametrize via marker if needed; default values match LC's defaults.
    """
    algorithm = getattr(request, "param", {}).get("algorithm", "EdDSA")
    if algorithm == "EdDSA":
        private_pem, public_pem = _ed25519_keypair()
    elif algorithm == "RS256":
        private_pem, public_pem = _rsa_keypair()
    else:
        raise ValueError(f"unsupported test algorithm: {algorithm}")

    with patch.object(codeapi_jwt, "settings") as mock_settings:
        mock_settings.codeapi_jwt_enabled = True
        mock_settings.codeapi_jwt_public_key = public_pem
        mock_settings.codeapi_jwt_algorithm = algorithm
        mock_settings.codeapi_jwt_issuer = "librechat"
        mock_settings.codeapi_jwt_audience = "codeapi"
        mock_settings.codeapi_jwt_leeway_seconds = 10
        reset_public_key_cache()
        try:
            yield {
                "private_pem": private_pem,
                "public_pem": public_pem,
                "algorithm": algorithm,
                "settings": mock_settings,
            }
        finally:
            reset_public_key_cache()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestVerifyEdDSA:
    def test_valid_token_returns_claims(self, configured):
        token = _mint(configured["private_pem"])
        claims = verify(token)
        assert isinstance(claims, JwtClaims)
        assert claims.sub == "user_123"
        assert claims.tenant_id == "tenant_abc"
        assert claims.role == "USER"
        assert claims.principal_source == "librechat_jwt"
        assert claims.jti == "test-jti"


@pytest.mark.parametrize("configured", [{"algorithm": "RS256"}], indirect=True)
class TestVerifyRS256:
    def test_valid_rs256_token_returns_claims(self, configured):
        token = _mint(configured["private_pem"], algorithm="RS256")
        claims = verify(token)
        assert claims.sub == "user_123"


# ---------------------------------------------------------------------------
# Token-side failures (should raise CodeApiJwtError → 401)
# ---------------------------------------------------------------------------


class TestTokenRejections:
    def test_expired(self, configured):
        token = _mint(configured["private_pem"], ttl=1, iat_offset=-3600)
        with pytest.raises(CodeApiJwtError):
            verify(token)

    def test_wrong_issuer(self, configured):
        token = _mint(configured["private_pem"], issuer="evil-issuer")
        with pytest.raises(CodeApiJwtError):
            verify(token)

    def test_wrong_audience(self, configured):
        token = _mint(configured["private_pem"], audience="wrong-audience")
        with pytest.raises(CodeApiJwtError):
            verify(token)

    def test_invalid_signature_via_different_key(self, configured):
        # Mint with a *different* private key — signature won't match.
        other_priv, _ = _ed25519_keypair()
        token = _mint(other_priv)
        with pytest.raises(CodeApiJwtError):
            verify(token)

    def test_malformed_token_not_three_segments(self, configured):
        with pytest.raises(CodeApiJwtError):
            verify("not.a.jwt.because.too.many.dots")

    def test_empty_sub(self, configured):
        token = _mint(configured["private_pem"], sub="")
        with pytest.raises(CodeApiJwtError):
            verify(token)

    def test_alg_confusion_attack_rejected(self, configured):
        """Attacker hand-crafts an HS256 token using the public key as the
        HMAC secret. PyJWT's high-level encode now blocks this, but a hand-
        rolled signer can still produce the malicious payload. Our verifier
        pins alg=EdDSA so it must reject regardless of how the bytes got there.
        """
        import base64
        import hashlib
        import hmac
        import json

        def _b64(payload: bytes) -> str:
            return base64.urlsafe_b64encode(payload).rstrip(b"=").decode()

        header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        claims = _b64(
            json.dumps(
                {
                    "iss": "librechat",
                    "aud": "codeapi",
                    "sub": "user_123",
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 60,
                }
            ).encode()
        )
        signing_input = f"{header}.{claims}".encode()
        signature = _b64(
            hmac.new(
                configured["public_pem"].encode(),
                signing_input,
                hashlib.sha256,
            ).digest()
        )
        attacker_token = f"{header}.{claims}.{signature}"
        with pytest.raises(CodeApiJwtError):
            verify(attacker_token)

    def test_missing_required_claim(self, configured):
        """exp is required; mint without it."""
        private_pem = configured["private_pem"]
        # Build claims manually, omitting exp.
        token = pyjwt.encode(
            {
                "iss": "librechat",
                "aud": "codeapi",
                "sub": "user_123",
                "iat": int(time.time()),
            },
            private_pem,
            algorithm="EdDSA",
        )
        with pytest.raises(CodeApiJwtError):
            verify(token)


# ---------------------------------------------------------------------------
# Configuration failures (should raise CodeApiJwtConfigurationError → 500)
# ---------------------------------------------------------------------------


class TestConfigurationErrors:
    def test_enabled_but_no_public_key(self):
        with patch.object(codeapi_jwt, "settings") as mock_settings:
            mock_settings.codeapi_jwt_enabled = True
            mock_settings.codeapi_jwt_public_key = None
            mock_settings.codeapi_jwt_algorithm = "EdDSA"
            mock_settings.codeapi_jwt_issuer = "librechat"
            mock_settings.codeapi_jwt_audience = "codeapi"
            mock_settings.codeapi_jwt_leeway_seconds = 10
            reset_public_key_cache()
            with pytest.raises(CodeApiJwtConfigurationError):
                verify("aaaa.bbbb.cccc")

    def test_disabled_raises_codeapi_jwt_error(self):
        with patch.object(codeapi_jwt, "settings") as mock_settings:
            mock_settings.codeapi_jwt_enabled = False
            mock_settings.codeapi_jwt_public_key = None
            reset_public_key_cache()
            with pytest.raises(CodeApiJwtError):
                verify("aaaa.bbbb.cccc")

    def test_garbage_public_key_raises_configuration_error(self):
        with patch.object(codeapi_jwt, "settings") as mock_settings:
            mock_settings.codeapi_jwt_enabled = True
            mock_settings.codeapi_jwt_public_key = "this is not a key"
            mock_settings.codeapi_jwt_algorithm = "EdDSA"
            mock_settings.codeapi_jwt_issuer = "librechat"
            mock_settings.codeapi_jwt_audience = "codeapi"
            mock_settings.codeapi_jwt_leeway_seconds = 10
            reset_public_key_cache()
            with pytest.raises(CodeApiJwtConfigurationError):
                verify("aaaa.bbbb.cccc")


# ---------------------------------------------------------------------------
# Key-format support — PEM, JWK, file path
# ---------------------------------------------------------------------------


class TestKeyFormats:
    def test_pem_format(self, configured):
        """Already covered by configured fixture but pin explicitly."""
        token = _mint(configured["private_pem"])
        assert verify(token).sub == "user_123"

    def test_jwk_json_format(self):
        # Generate keypair, export public as JWK JSON, configure verifier with it.
        private = ed25519.Ed25519PrivateKey.generate()
        private_pem = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        public_jwk = pyjwt.algorithms.OKPAlgorithm().to_jwk(private.public_key())

        with patch.object(codeapi_jwt, "settings") as mock_settings:
            mock_settings.codeapi_jwt_enabled = True
            # to_jwk may already return a string; normalize.
            mock_settings.codeapi_jwt_public_key = public_jwk if isinstance(public_jwk, str) else json.dumps(public_jwk)
            mock_settings.codeapi_jwt_algorithm = "EdDSA"
            mock_settings.codeapi_jwt_issuer = "librechat"
            mock_settings.codeapi_jwt_audience = "codeapi"
            mock_settings.codeapi_jwt_leeway_seconds = 10
            reset_public_key_cache()

            token = _mint(private_pem)
            assert verify(token).sub == "user_123"

    def test_file_path(self, tmp_path):
        private_pem, public_pem = _ed25519_keypair()
        key_file = tmp_path / "codeapi.pem"
        key_file.write_text(public_pem)

        with patch.object(codeapi_jwt, "settings") as mock_settings:
            mock_settings.codeapi_jwt_enabled = True
            mock_settings.codeapi_jwt_public_key = str(key_file)
            mock_settings.codeapi_jwt_algorithm = "EdDSA"
            mock_settings.codeapi_jwt_issuer = "librechat"
            mock_settings.codeapi_jwt_audience = "codeapi"
            mock_settings.codeapi_jwt_leeway_seconds = 10
            reset_public_key_cache()

            token = _mint(private_pem)
            assert verify(token).sub == "user_123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestLooksLikeJwt:
    @pytest.mark.parametrize("token", ["", "no-dots", "one.dot", "a.b.c.d"])
    def test_rejects_non_jwt(self, token):
        assert _looks_like_jwt(token) is False

    def test_accepts_three_segments(self):
        assert _looks_like_jwt("aaaa.bbbb.cccc") is True

    def test_rejects_empty_segment(self):
        assert _looks_like_jwt("a..c") is False
