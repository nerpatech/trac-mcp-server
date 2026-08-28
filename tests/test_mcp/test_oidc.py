"""Tests for mcp/oidc.py -- the per-user OIDC bearer-token client cache.

Covers extract_bearer_token() and OidcClientCache in isolation: header
parsing, token -> Config/TracClient wiring, missing-token rejection, and
caching/eviction. The transport-level "is a bearer token even present"
enforcement is tested separately in
test_http_app.py::TestOidcTokenRequired.
"""

import pytest

from trac_mcp_server.config import Config
from trac_mcp_server.mcp.oidc import (
    MissingOidcTokenError,
    OidcClientCache,
    extract_bearer_token,
)


class TestExtractBearerToken:
    def test_none_header(self):
        assert extract_bearer_token(None) is None

    def test_empty_header(self):
        assert extract_bearer_token("") is None

    def test_missing_scheme(self):
        assert extract_bearer_token("just-a-token") is None

    def test_wrong_scheme(self):
        assert extract_bearer_token("Basic dXNlcjpwYXNz") is None

    def test_scheme_is_case_insensitive(self):
        assert extract_bearer_token("bearer the-token") == "the-token"
        assert extract_bearer_token("BEARER the-token") == "the-token"

    def test_extracts_token(self):
        assert extract_bearer_token("Bearer the-token") == "the-token"

    def test_blank_token_after_scheme(self):
        assert extract_bearer_token("Bearer ") is None
        assert extract_bearer_token("Bearer    ") is None

    def test_strips_whitespace(self):
        assert (
            extract_bearer_token("Bearer   the-token  ") == "the-token"
        )


@pytest.fixture
def base_config() -> Config:
    return Config(
        trac_url="https://trac.example.com/trac",
        username="service-account",
        password="service-secret",
    )


class TestOidcClientCache:
    def test_none_token_raises(self, base_config):
        cache = OidcClientCache(
            base_config,
            "https://trac.example.com/trac-api/login/xmlrpc",
        )
        with pytest.raises(MissingOidcTokenError):
            cache.get_client(None)

    def test_empty_token_raises(self, base_config):
        cache = OidcClientCache(
            base_config,
            "https://trac.example.com/trac-api/login/xmlrpc",
        )
        with pytest.raises(MissingOidcTokenError):
            cache.get_client("")

    def test_blank_token_raises(self, base_config):
        cache = OidcClientCache(
            base_config,
            "https://trac.example.com/trac-api/login/xmlrpc",
        )
        with pytest.raises(MissingOidcTokenError):
            cache.get_client("   ")

    def test_client_uses_bearer_auth_and_oidc_rpc_url(
        self, base_config
    ):
        oidc_rpc_url = "https://trac.example.com/trac-api/login/xmlrpc"
        cache = OidcClientCache(base_config, oidc_rpc_url)

        client = cache.get_client("the-users-token")

        assert client.rpc_url == oidc_rpc_url
        assert client.session.auth is None
        assert (
            client.session.headers["Authorization"]
            == "Bearer the-users-token"
        )

    def test_client_never_carries_the_shared_service_account(
        self, base_config
    ):
        """The whole point of this mode: the base Config's real
        username/password must never reach a per-user client."""
        cache = OidcClientCache(
            base_config,
            "https://trac.example.com/trac-api/login/xmlrpc",
        )

        client = cache.get_client("the-users-token")

        assert client.config.username == ""
        assert client.config.password == ""
        assert client.config.oidc_only is True

    def test_same_token_returns_cached_client(self, base_config):
        cache = OidcClientCache(
            base_config,
            "https://trac.example.com/trac-api/login/xmlrpc",
        )

        first = cache.get_client("token-a")
        second = cache.get_client("token-a")

        assert first is second

    def test_different_tokens_get_different_clients(self, base_config):
        cache = OidcClientCache(
            base_config,
            "https://trac.example.com/trac-api/login/xmlrpc",
        )

        client_a = cache.get_client("token-a")
        client_b = cache.get_client("token-b")

        assert client_a is not client_b
        assert (
            client_a.session.headers["Authorization"]
            == "Bearer token-a"
        )
        assert (
            client_b.session.headers["Authorization"]
            == "Bearer token-b"
        )

    def test_token_is_stripped(self, base_config):
        cache = OidcClientCache(
            base_config,
            "https://trac.example.com/trac-api/login/xmlrpc",
        )

        cached_via_raw = cache.get_client("  token-a  ")
        cached_via_stripped = cache.get_client("token-a")

        assert cached_via_raw is cached_via_stripped

    def test_cache_bounded_by_fifo_eviction(self, base_config):
        """A long-running process seeing many distinct short-lived
        Keycloak tokens must not grow this cache unboundedly."""
        from trac_mcp_server.mcp.oidc import _MAX_CACHED_CLIENTS

        cache = OidcClientCache(
            base_config,
            "https://trac.example.com/trac-api/login/xmlrpc",
        )

        for i in range(_MAX_CACHED_CLIENTS + 10):
            cache.get_client(f"token-{i}")

        assert len(cache._clients) == _MAX_CACHED_CLIENTS
        # The earliest tokens were evicted first.
        assert "token-0" not in cache._clients
        assert f"token-{_MAX_CACHED_CLIENTS + 9}" in cache._clients
