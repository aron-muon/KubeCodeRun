"""Tests for Kubernetes client auth resilience (issue #72).

Two layers of protection are covered here:

1. A regression test against the installed ``kubernetes`` client library:
   ``load_incluster_config``'s loader must actually wire the ServiceAccount
   token into ``Configuration.auth_settings()`` and serve a *fresh* token
   after kubelet rotation. kubernetes-client 36.0.0 broke this (the
   regenerated ``auth_settings()`` reads ``api_key['BearerToken']`` while the
   in-cluster loader writes ``api_key['authorization']``, so requests went
   out with no Authorization header at all). This test fails on any future
   dependency bump that reintroduces that class of regression.

2. Unit tests for the throttled 401 recovery in ``client.py``:
   ``handle_unauthorized`` resets the cached API clients so the next call
   reloads config (and the rotated on-disk token) instead of failing forever.
"""

import datetime

import pytest

from src.services.kubernetes import client as k8s_client


class TestInClusterAuthRegression:
    """Guard against kubernetes-client in-cluster auth regressions."""

    @pytest.fixture
    def incluster_config(self, tmp_path):
        from kubernetes.client import Configuration
        from kubernetes.config import incluster_config

        token_file = tmp_path / "token"
        cert_file = tmp_path / "ca.crt"
        token_file.write_text("TOKEN_V1")
        cert_file.write_text("dummy-ca")

        loader = incluster_config.InClusterConfigLoader(
            token_filename=str(token_file),
            cert_filename=str(cert_file),
            try_refresh_token=True,
            environ={
                "KUBERNETES_SERVICE_HOST": "1.2.3.4",
                "KUBERNETES_SERVICE_PORT": "443",
            },
        )
        configuration = Configuration()
        loader.load_and_set(configuration)
        return loader, configuration, token_file

    def test_incluster_loader_populates_auth_settings(self, incluster_config):
        """auth_settings() must expose the SA token as an Authorization header.

        On kubernetes-client 36.0.0 this returns {} — every in-cluster request
        is sent unauthenticated and the API server answers 401.
        """
        _, configuration, _ = incluster_config
        auth = configuration.auth_settings()

        assert "BearerToken" in auth, (
            "In-cluster config produced no BearerToken auth setting — the "
            "kubernetes client would send unauthenticated requests (issue #72). "
            "Check the pinned kubernetes-client version."
        )
        assert auth["BearerToken"]["in"] == "header"
        assert auth["BearerToken"]["key"] == "authorization"
        assert auth["BearerToken"]["value"] == "bearer TOKEN_V1"

    def test_incluster_token_refreshes_after_rotation(self, incluster_config):
        """A kubelet-rotated token must be picked up once the cached one expires."""
        loader, configuration, token_file = incluster_config

        token_file.write_text("TOKEN_V2")
        loader.token_expires_at = datetime.datetime.now() - datetime.timedelta(seconds=1)

        auth = configuration.auth_settings()
        assert auth["BearerToken"]["value"] == "bearer TOKEN_V2"


class TestHandleUnauthorized:
    """Throttled client reset on 401 responses."""

    @pytest.fixture(autouse=True)
    def _reset_module_state(self, monkeypatch):
        monkeypatch.setattr(k8s_client, "_core_api", object())
        monkeypatch.setattr(k8s_client, "_batch_api", object())
        monkeypatch.setattr(k8s_client, "_initialized", True)
        monkeypatch.setattr(k8s_client, "_last_unauthorized_reset", 0.0)

    def test_401_resets_cached_clients(self):
        assert k8s_client.handle_unauthorized(401) is True
        assert k8s_client._core_api is None
        assert k8s_client._batch_api is None
        assert k8s_client._initialized is False

    def test_non_401_does_not_reset(self):
        for status in (None, 403, 404, 500):
            assert k8s_client.handle_unauthorized(status) is False
        assert k8s_client._initialized is True
        assert k8s_client._core_api is not None

    def test_reset_is_throttled(self, monkeypatch):
        assert k8s_client.handle_unauthorized(401) is True

        # A second 401 right after the reset must not thrash config loading.
        monkeypatch.setattr(k8s_client, "_core_api", object())
        monkeypatch.setattr(k8s_client, "_initialized", True)
        assert k8s_client.handle_unauthorized(401) is False
        assert k8s_client._initialized is True

    def test_reset_allowed_after_throttle_window(self, monkeypatch):
        assert k8s_client.handle_unauthorized(401) is True

        monkeypatch.setattr(k8s_client, "_core_api", object())
        monkeypatch.setattr(k8s_client, "_initialized", True)
        monkeypatch.setattr(
            k8s_client,
            "_last_unauthorized_reset",
            k8s_client._last_unauthorized_reset - k8s_client._UNAUTHORIZED_RESET_INTERVAL_SECONDS - 1,
        )
        assert k8s_client.handle_unauthorized(401) is True
        assert k8s_client._initialized is False
