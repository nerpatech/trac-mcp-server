"""Tests for mcp/oidc.py -- the per-user OIDC bearer-token client cache.

Covers extract_bearer_token() and OidcClientCache in isolation: header
parsing, token -> Config/TracClient wiring, missing-token rejection, and
caching/eviction. The transport-level "is a bearer token even present"
enforcement is tested separately in
test_http_app.py::TestOidcTokenRequired.
"""

import asyncio
import calendar
import http.server
import threading
from unittest.mock import MagicMock, patch

import pytest
import requests

from trac_mcp_server.config import Config
from trac_mcp_server.mcp.oidc import (
    MissingOidcTokenError,
    OidcClientCache,
    extract_bearer_token,
)
from trac_mcp_server.mcp.tools.ticket_write import _find_posted_comment


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


class _Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


class TestEviction:
    def _session_in_new_thread(self, client) -> requests.Session:
        sessions = []
        worker = threading.Thread(
            target=lambda: sessions.append(client.session)
        )
        worker.start()
        worker.join()
        return sessions[0]

    def test_evicting_a_client_closes_the_sessions_of_all_its_threads(
        self, base_config
    ):
        cache = OidcClientCache(base_config, "https://example.com/rpc")
        with patch("trac_mcp_server.mcp.oidc._MAX_CACHED_CLIENTS", 1):
            evicted = cache.get_client("first")
            sessions = [
                evicted.session,
                self._session_in_new_thread(evicted),
            ]
            assert sessions[0] is not sessions[1]
            with patch.object(requests.Session, "close") as close:
                cache.get_client("second")
        assert close.call_count == 2

    def test_a_client_that_stays_cached_is_not_closed(
        self, base_config
    ):
        cache = OidcClientCache(base_config, "https://example.com/rpc")
        _ = cache.get_client("first").session
        with patch.object(requests.Session, "close") as close:
            cache.get_client("second")
            cache.get_client("first")
        close.assert_not_called()

    def test_a_closed_client_can_still_finish_its_work(
        self, base_config
    ):
        """A call may still be running on the client when it is evicted."""
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _Hello
        )
        thread = threading.Thread(
            target=server.serve_forever, daemon=True
        )
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/rpc"
        try:
            client = OidcClientCache(base_config, url).get_client(
                "token"
            )
            assert client.session.get(url, timeout=5).status_code == 200
            client.close()
            assert client.session.get(url, timeout=5).status_code == 200
        finally:
            server.shutdown()
            server.server_close()


class TestCommentLookup:
    """ticket_update reads a new comment's number off the changelog. Under
    OIDC the client has no username to filter that changelog by."""

    _WHEN = "20261104T07:29:00"
    _EPOCH = str(calendar.timegm((2026, 11, 4, 7, 29, 0)) * 1_000_000)

    def _client(self, changelog) -> MagicMock:
        client = MagicMock()
        client.config = Config(
            trac_url="https://example.com/trac",
            username="",
            password="",
            bearer_token="token",
            oidc_only=True,
        )
        client.get_ticket_changelog.return_value = changelog
        return client

    def _find(self, changelog, comment="mine"):
        return asyncio.run(
            _find_posted_comment(
                self._client(changelog), 1, comment, None, self._EPOCH
            )
        )

    def test_the_number_is_found_without_a_username(self):
        posted = self._find(
            [[self._WHEN, "bob", "comment", "3", "mine", 1]]
        )
        assert posted.failure is None
        assert posted.number == 3

    def test_another_users_comment_in_the_same_second_is_told_apart(
        self,
    ):
        posted = self._find(
            [
                [self._WHEN, "alice", "comment", "3", "theirs", 1],
                [self._WHEN, "bob", "comment", "4", "mine", 1],
            ]
        )
        assert posted.number == 4
