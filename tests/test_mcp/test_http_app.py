"""Tests for the streamable HTTP transport (mcp/http_app.py).

Drives the ASGI app with Starlette's TestClient as a context manager so
the lifespan runs and the StreamableHTTPSessionManager starts.
"""

from mcp.server import Server
from starlette.testclient import TestClient

from trac_mcp_server import __version__
from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.mcp.http_app import build_http_app

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
# OIDC per-user token requirement
# ---------------------------------------------------------------------------


class TestOidcTokenRequired:
    """OidcTokenRequiredMiddleware gating when oidc_rpc_url is configured.

    Mirrors TestBearerAuth's shape -- same no-op-when-unconfigured,
    healthz-exempt, missing-header-rejected pattern -- but there is no
    "correct value" to accept: the standard Authorization header only needs
    to carry *some* bearer token, since validating it is Trac's own Apache
    layer's job (see mcp/oidc.py). It's the same header
    BearerAuthMiddleware's static token would otherwise occupy -- the two
    modes are mutually exclusive (enforced in config.validate_server_config,
    not here), so a ServerConfig with both set never reaches this app in
    practice.
    """

    def test_not_configured_no_bearer_required(self):
        """oidc_rpc_url unset -> completely unaffected, same as before
        this mode existed."""
        app = build_http_app(_make_mcp_server(), _make_server_config())
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.post(
                "/mcp", json=_INITIALIZE_PAYLOAD, headers=_MCP_HEADERS
            )
        assert response.status_code == 200

    def test_healthz_exempt_when_configured(self):
        app = build_http_app(
            _make_mcp_server(),
            _make_server_config(
                oidc_rpc_url="https://trac.example.com/trac-api/login/xmlrpc"
            ),
        )
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.get("/healthz")
        assert response.status_code == 200

    def test_missing_authorization_header_rejected(self):
        app = build_http_app(
            _make_mcp_server(),
            _make_server_config(
                oidc_rpc_url="https://trac.example.com/trac-api/login/xmlrpc"
            ),
        )
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.post(
                "/mcp", json=_INITIALIZE_PAYLOAD, headers=_MCP_HEADERS
            )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    def test_wrong_scheme_rejected(self):
        app = build_http_app(
            _make_mcp_server(),
            _make_server_config(
                oidc_rpc_url="https://trac.example.com/trac-api/login/xmlrpc"
            ),
        )
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.post(
                "/mcp",
                json=_INITIALIZE_PAYLOAD,
                headers={
                    **_MCP_HEADERS,
                    "Authorization": "Basic dXNlcjpwYXNz",
                },
            )
        assert response.status_code == 401

    def test_blank_bearer_token_rejected(self):
        app = build_http_app(
            _make_mcp_server(),
            _make_server_config(
                oidc_rpc_url="https://trac.example.com/trac-api/login/xmlrpc"
            ),
        )
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.post(
                "/mcp",
                json=_INITIALIZE_PAYLOAD,
                headers={**_MCP_HEADERS, "Authorization": "Bearer "},
            )
        assert response.status_code == 401

    def test_present_bearer_token_accepted(self):
        """Any non-empty bearer token is admitted at this layer -- it's
        forwarded to Trac as-is, which is what actually validates it."""
        app = build_http_app(
            _make_mcp_server(),
            _make_server_config(
                oidc_rpc_url="https://trac.example.com/trac-api/login/xmlrpc"
            ),
        )
        with TestClient(app, base_url=_BASE_URL) as client:
            response = client.post(
                "/mcp",
                json=_INITIALIZE_PAYLOAD,
                headers={
                    **_MCP_HEADERS,
                    "Authorization": "Bearer the-users-own-keycloak-token",
                },
            )
        assert response.status_code == 200


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
