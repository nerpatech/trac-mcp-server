"""Tests for the streamable HTTP transport (mcp/http_app.py).

Drives the ASGI app with Starlette's TestClient as a context manager so
the lifespan runs and the StreamableHTTPSessionManager starts.
"""

from mcp.server import Server
from starlette.testclient import TestClient

from trac_mcp_server import __version__
from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.instances import Identity
from trac_mcp_server.mcp.http_app import (
    BearerAuthMiddleware,
    build_http_app,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mcp_server() -> Server:
    """Minimal low-level MCP Server with one handler, for exercising the
    HTTP transport without the full trac-mcp-server tool registry."""
    server = Server("test-trac-mcp-server", version=__version__)

    @server.list_tools()
    async def _list_tools():
        return []

    return server


def _make_server_config(**overrides) -> ServerConfig:
    defaults = {
        "transport": "http",
        "host": "127.0.0.1",
        "port": 8080,
        "path": "/mcp",
    }
    defaults.update(overrides)
    return ServerConfig(**defaults)


_BASE_URL = "http://127.0.0.1:8080"
_INITIALIZE_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0"},
    },
}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


class TestHealthz:
    """The health endpoint is always reachable, with or without a token."""

    def test_healthz_reachable_without_token_configured(self):
        app = build_http_app(_make_mcp_server(), _make_server_config())
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_healthz_reachable_without_auth_header_when_token_configured(
        self,
    ):
        app = build_http_app(
            _make_mcp_server(),
            _make_server_config(auth_token="secret"),
        )
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.get("/healthz")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Bearer auth on the MCP endpoint
# ---------------------------------------------------------------------------


class TestBearerAuth:
    """BearerAuthMiddleware gating of the configured MCP path."""

    def test_no_token_configured_endpoint_is_open(self):
        app = build_http_app(_make_mcp_server(), _make_server_config())
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.post(
                "/mcp", json=_INITIALIZE_PAYLOAD, headers=_MCP_HEADERS
            )
        assert response.status_code == 200

    def test_missing_auth_header_rejected(self):
        app = build_http_app(
            _make_mcp_server(),
            _make_server_config(auth_token="secret"),
        )
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.post(
                "/mcp", json=_INITIALIZE_PAYLOAD, headers=_MCP_HEADERS
            )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    def test_wrong_token_rejected(self):
        app = build_http_app(
            _make_mcp_server(),
            _make_server_config(auth_token="secret"),
        )
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.post(
                "/mcp",
                json=_INITIALIZE_PAYLOAD,
                headers={
                    **_MCP_HEADERS,
                    "Authorization": "Bearer wrong",
                },
            )
        assert response.status_code == 401

    def test_correct_token_accepted_and_returns_session_id(self):
        app = build_http_app(
            _make_mcp_server(),
            _make_server_config(auth_token="secret"),
        )
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.post(
                "/mcp",
                json=_INITIALIZE_PAYLOAD,
                headers={
                    **_MCP_HEADERS,
                    "Authorization": "Bearer secret",
                },
            )
        assert response.status_code == 200
        assert "mcp-session-id" in response.headers

    def test_initialize_reports_project_version_not_sdk_version(self):
        """Regression guard: create_initialization_options() must report
        the project version, not the mcp SDK's, which requires
        Server(..., version=__version__) in server.py."""
        app = build_http_app(_make_mcp_server(), _make_server_config())
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.post(
                "/mcp", json=_INITIALIZE_PAYLOAD, headers=_MCP_HEADERS
            )
        assert f'"version":"{__version__}"' in response.text


# ---------------------------------------------------------------------------
# BearerAuthMiddleware identities (ticket #102) -- exercised at the raw
# ASGI level, below the full MCP protocol machinery TestBearerAuth above
# drives, so a downstream ``scope["trac_identity"]`` is directly visible.
# ---------------------------------------------------------------------------


async def _noop_receive():
    return {"type": "http.disconnect"}


class _RecordingApp:
    """A minimal downstream ASGI app that records the scope it receives
    and always answers 200, so the middleware's forwarding decision and
    its scope mutation can both be asserted independently of any real
    session/routing logic."""

    def __init__(self):
        self.received_scope: dict | None = None

    async def __call__(self, scope, receive, send):
        self.received_scope = scope
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b""})


async def _call_middleware(
    middleware: BearerAuthMiddleware, token: str | None
) -> list[dict]:
    """Drive ``middleware`` with one bare ASGI request, no header at all
    when ``token`` is ``None``. Returns the messages sent to ``send``."""
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    headers = (
        [(b"authorization", f"Bearer {token}".encode())]
        if token is not None
        else []
    )
    scope = {"type": "http", "path": "/mcp", "headers": headers}
    await middleware(scope, _noop_receive, send)
    return sent


class TestBearerAuthMiddlewareIdentities:
    """BearerAuthMiddleware resolving a bearer token to zero, one, or
    many identities (ticket #102)."""

    async def test_legacy_token_accepted_no_identity_in_scope(self):
        app = _RecordingApp()
        middleware = BearerAuthMiddleware(app, token="legacy-token")

        sent = await _call_middleware(middleware, "legacy-token")

        assert sent[0]["status"] == 200
        assert "trac_identity" not in app.received_scope

    async def test_identity_token_accepted_and_stored(self):
        app = _RecordingApp()
        alice = Identity(name="alice", username="a", password="a-pw")
        middleware = BearerAuthMiddleware(
            app, token="legacy-token", identities={"tok-alice": alice}
        )

        sent = await _call_middleware(middleware, "tok-alice")

        assert sent[0]["status"] == 200
        assert app.received_scope["trac_identity"] is alice

    async def test_unknown_token_rejected(self):
        app = _RecordingApp()
        alice = Identity(name="alice", username="a", password="a-pw")
        middleware = BearerAuthMiddleware(
            app, token="legacy-token", identities={"tok-alice": alice}
        )

        sent = await _call_middleware(middleware, "wrong-token")

        assert sent[0]["status"] == 401
        assert app.received_scope is None

    async def test_identities_only_no_static_token_rejects_missing_token(
        self,
    ):
        """No TRAC_MCP_AUTH_TOKEN configured, only identities -- the
        endpoint is still gated, not the falsy-token "no-op" open case."""
        app = _RecordingApp()
        alice = Identity(name="alice", username="a", password="a-pw")
        middleware = BearerAuthMiddleware(
            app, token=None, identities={"tok-alice": alice}
        )

        sent = await _call_middleware(middleware, None)

        assert sent[0]["status"] == 401
        assert app.received_scope is None

    async def test_identities_only_no_static_token_accepts_identity(
        self,
    ):
        app = _RecordingApp()
        alice = Identity(name="alice", username="a", password="a-pw")
        middleware = BearerAuthMiddleware(
            app, token=None, identities={"tok-alice": alice}
        )

        sent = await _call_middleware(middleware, "tok-alice")

        assert sent[0]["status"] == 200
        assert app.received_scope["trac_identity"] is alice


# ---------------------------------------------------------------------------
# DNS-rebinding / Host header validation
# ---------------------------------------------------------------------------


class TestHostValidation:
    """TransportSecuritySettings.allowed_hosts gates the MCP endpoint."""

    def test_unrecognized_host_header_rejected(self):
        app = build_http_app(_make_mcp_server(), _make_server_config())
        with TestClient(
            app, base_url="http://evil.example.com"
        ) as client:
            response = client.post(
                "/mcp", json=_INITIALIZE_PAYLOAD, headers=_MCP_HEADERS
            )
        assert response.status_code == 421

    def test_extra_allowed_host_from_config_accepted(self):
        app = build_http_app(
            _make_mcp_server(),
            _make_server_config(allowed_hosts=["extra.example.com:*"]),
        )
        with TestClient(
            app, base_url="http://extra.example.com:8080"
        ) as client:
            response = client.post(
                "/mcp", json=_INITIALIZE_PAYLOAD, headers=_MCP_HEADERS
            )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Bare path (no trailing slash) is served directly, no redirect
# ---------------------------------------------------------------------------


class TestNoTrailingSlashRedirect:
    """A bare request at server_config.path must not 307-redirect.

    Regression guard: Mount() treats its path as a prefix and redirects
    "/mcp" -> "/mcp/"; most MCP HTTP clients won't replay a POST body
    across a redirect, so the endpoint must be reachable at the exact
    configured path.
    """

    def test_bare_path_post_is_not_redirected(self):
        app = build_http_app(_make_mcp_server(), _make_server_config())
        with TestClient(
            app, base_url=_BASE_URL, follow_redirects=False
        ) as client:
            response = client.post(
                "/mcp", json=_INITIALIZE_PAYLOAD, headers=_MCP_HEADERS
            )
        assert response.status_code == 200
