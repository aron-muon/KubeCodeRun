"""Unit tests for trusted networks auth bypass.

Tests cover the AUTH_TRUSTED_NETWORKS feature that allows requests from
configured CIDRs to bypass API key authentication (e.g. in-cluster callers).
"""

import ipaddress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.middleware.auth import AuthenticationMiddleware
from src.middleware.security import SecurityMiddleware


@pytest.fixture
def mock_app():
    """Create a mock ASGI app."""
    return AsyncMock()


def _make_request(client_ip: str = "10.0.0.1", path: str = "/exec", method: str = "POST") -> Request:
    """Create a mock Request with the given client IP."""
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "root_path": "",
    }
    request = Request(scope)
    request._url = MagicMock()
    request._url.path = path
    # Mock client
    request._client = MagicMock()
    request._client.host = client_ip
    # Patch scope client for Request.client property
    scope["client"] = (client_ip, 12345)
    return Request(scope)


class TestParseNetworks:
    """Tests for _parse_trusted_networks static method."""

    def test_empty_string(self):
        result = SecurityMiddleware._parse_trusted_networks("")
        assert result == []

    def test_single_cidr(self):
        result = SecurityMiddleware._parse_trusted_networks("10.0.0.0/8")
        assert len(result) == 1
        assert result[0] == ipaddress.ip_network("10.0.0.0/8")

    def test_multiple_cidrs(self):
        result = SecurityMiddleware._parse_trusted_networks("10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
        assert len(result) == 3

    def test_whitespace_handling(self):
        result = SecurityMiddleware._parse_trusted_networks(" 10.0.0.0/8 , 172.16.0.0/12 ")
        assert len(result) == 2

    def test_trailing_comma(self):
        result = SecurityMiddleware._parse_trusted_networks("10.0.0.0/8,")
        assert len(result) == 1

    def test_invalid_cidr_skipped(self):
        result = SecurityMiddleware._parse_trusted_networks("10.0.0.0/8,not-a-cidr,172.16.0.0/12")
        assert len(result) == 2

    def test_single_host_cidr(self):
        result = SecurityMiddleware._parse_trusted_networks("240.3.25.75/32")
        assert len(result) == 1
        assert ipaddress.ip_address("240.3.25.75") in result[0]

    def test_ipv6_cidr(self):
        result = SecurityMiddleware._parse_trusted_networks("fd00::/8")
        assert len(result) == 1

    def test_class_e_range(self):
        """The LibreChat pod IP 240.3.25.75 falls in Class E (240.0.0.0/4)."""
        result = SecurityMiddleware._parse_trusted_networks("240.0.0.0/4")
        assert len(result) == 1
        assert ipaddress.ip_address("240.3.25.75") in result[0]


class TestIsTrustedNetwork:
    """Tests for _is_trusted_network method."""

    def test_ip_in_trusted_range(self, mock_app):
        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.max_file_size_mb = 10
            mock_settings.auth_trusted_networks = "10.0.0.0/8"
            mw = SecurityMiddleware(mock_app)

        request = _make_request(client_ip="10.1.2.3")
        assert mw._is_trusted_network(request) is True

    def test_ip_not_in_trusted_range(self, mock_app):
        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.max_file_size_mb = 10
            mock_settings.auth_trusted_networks = "10.0.0.0/8"
            mw = SecurityMiddleware(mock_app)

        request = _make_request(client_ip="192.168.1.1")
        assert mw._is_trusted_network(request) is False

    def test_unknown_ip_not_trusted(self, mock_app):
        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.max_file_size_mb = 10
            mock_settings.auth_trusted_networks = "10.0.0.0/8"
            mw = SecurityMiddleware(mock_app)

        # Simulate unknown client
        scope = {"type": "http", "method": "POST", "path": "/exec", "headers": [], "query_string": b"", "root_path": ""}
        request = Request(scope)
        assert mw._is_trusted_network(request) is False

    def test_librechat_pod_ip_trusted(self, mock_app):
        """Real-world scenario: LibreChat pod at 240.3.25.75 with 240.0.0.0/4 trusted."""
        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.max_file_size_mb = 10
            mock_settings.auth_trusted_networks = "240.0.0.0/4,10.0.0.0/8"
            mw = SecurityMiddleware(mock_app)

        request = _make_request(client_ip="240.3.25.75")
        assert mw._is_trusted_network(request) is True

    def test_no_trusted_networks_configured(self, mock_app):
        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.max_file_size_mb = 10
            mock_settings.auth_trusted_networks = ""
            mw = SecurityMiddleware(mock_app)

        request = _make_request(client_ip="10.0.0.1")
        assert mw._is_trusted_network(request) is False


class TestShouldSkipAuth:
    """Tests for _should_skip_auth with trusted networks."""

    def test_trusted_ip_skips_auth(self, mock_app):
        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.max_file_size_mb = 10
            mock_settings.auth_trusted_networks = "10.0.0.0/8"
            mw = SecurityMiddleware(mock_app)

        request = _make_request(client_ip="10.1.2.3", path="/exec")
        assert mw._should_skip_auth(request) is True

    def test_untrusted_ip_requires_auth(self, mock_app):
        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.max_file_size_mb = 10
            mock_settings.auth_trusted_networks = "10.0.0.0/8"
            mw = SecurityMiddleware(mock_app)

        request = _make_request(client_ip="203.0.113.1", path="/exec")
        assert mw._should_skip_auth(request) is False

    def test_excluded_path_still_skips(self, mock_app):
        with patch("src.middleware.security.settings") as mock_settings:
            mock_settings.max_file_size_mb = 10
            mock_settings.auth_trusted_networks = ""
            mw = SecurityMiddleware(mock_app)

        request = _make_request(client_ip="203.0.113.1", path="/health")
        assert mw._should_skip_auth(request) is True


class TestAuthMiddlewareTrustedNetworks:
    """Tests for AuthenticationMiddleware trusted network handling."""

    def test_parse_networks(self):
        result = AuthenticationMiddleware._parse_trusted_networks("10.0.0.0/8,172.16.0.0/12")
        assert len(result) == 2

    def test_trusted_ip_skips_auth(self, mock_app):
        with patch("src.middleware.auth.settings") as mock_settings:
            mock_settings.auth_trusted_networks = "240.0.0.0/4"
            mw = AuthenticationMiddleware(mock_app)

        request = _make_request(client_ip="240.3.25.75", path="/exec")
        assert mw._should_skip_auth(request) is True

    def test_untrusted_ip_requires_auth(self, mock_app):
        with patch("src.middleware.auth.settings") as mock_settings:
            mock_settings.auth_trusted_networks = "240.0.0.0/4"
            mw = AuthenticationMiddleware(mock_app)

        request = _make_request(client_ip="8.8.8.8", path="/exec")
        assert mw._should_skip_auth(request) is False
