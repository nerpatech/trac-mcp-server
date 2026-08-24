"""Streamable HTTP transport for the MCP server.

Wires ``mcp.server.streamable_http_manager.StreamableHTTPSessionManager``
into a Starlette app served by uvicorn. Kept separate from ``server.py`` so
the stdio path -- the default, most heavily used transport -- stays free of
HTTP-specific imports and complexity.
"""

import contextlib
import logging
import secrets
from collections.abc import AsyncIterator

import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import (
    StreamableHTTPSessionManager,
)
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config_schema import ServerConfig
from .oidc import OIDC_TOKEN_HEADER

logger = logging.getLogger(__name__)


class BearerAuthMiddleware:
    """Pure-ASGI middleware requiring ``Authorization: Bearer <token>``.

    No-op when ``token`` is falsy -- some deployments intentionally run
    unauthenticated (loopback-only bind, see ``config.validate_server_config``).
    Does not touch ``/healthz`` so health probes work without a token.
    """

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self._app = app
        self._token = token

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if (
            scope["type"] != "http"
            or not self._token
            or scope["path"] == "/healthz"
        ):
            await self._app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        auth_header = headers.get(b"authorization", b"").decode(
            "latin-1"
        )
        scheme, _, credential = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            credential, self._token
        ):
            response = Response(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


class OidcTokenRequiredMiddleware:
    """Requires ``X-Trac-OIDC-Token`` when OIDC per-user auth is configured.

    No-op when ``oidc_rpc_url`` is falsy -- most deployments don't use this
    mode. When it *is* configured, this is the enforcement point for "no
    fallback to a shared identity, ever": every request to the MCP endpoint
    (not ``/healthz``) must carry the header, checked here at the transport
    boundary rather than deep in tool-dispatch logic, so no future code path
    can accidentally skip it. The header's value is not validated here --
    that happens downstream when Trac's own web server (mod_auth_openidc)
    receives it as the forwarded ``Authorization: Bearer`` token.
    """

    def __init__(self, app: ASGIApp, oidc_rpc_url: str | None) -> None:
        self._app = app
        self._enabled = bool(oidc_rpc_url)

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if (
            scope["type"] != "http"
            or not self._enabled
            or scope["path"] == "/healthz"
        ):
            await self._app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        token = (
            headers.get(OIDC_TOKEN_HEADER.encode("latin-1"), b"")
            .decode("latin-1")
            .strip()
        )
        if not token:
            response = Response(
                f"Unauthorized: missing {OIDC_TOKEN_HEADER!r} header. This "
                "server requires each request to carry its own per-user "
                "Trac OIDC access token; there is no shared fallback "
                "identity.",
                status_code=401,
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


async def _healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


class _SessionManagerASGIApp:
    """Adapts ``StreamableHTTPSessionManager.handle_request`` for ``Route``.

    Starlette's ``Route`` special-cases a bound method as a
    ``func(request) -> response`` handler (defaulting to GET-only) rather
    than a raw ASGI callable -- ``inspect.ismethod()`` is True for
    ``session_manager.handle_request``. Wrapping it in a plain callable
    class (same pattern as the SDK's own
    ``mcp.server.fastmcp.server.StreamableHTTPASGIApp``) makes ``Route``
    treat it as ASGI and dispatch every HTTP method (GET/POST/DELETE) to it.
    """

    def __init__(
        self, session_manager: StreamableHTTPSessionManager
    ) -> None:
        self._session_manager = session_manager

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        await self._session_manager.handle_request(scope, receive, send)


def build_http_app(
    mcp_server: Server, server_config: ServerConfig
) -> Starlette:
    """Build the Starlette app that serves ``mcp_server`` over streamable HTTP.

    Mounts the MCP endpoint at ``server_config.path`` and an unauthenticated
    ``GET /healthz`` for probes. DNS-rebinding protection is always on for
    the MCP endpoint; ``allowed_hosts``/``allowed_origins`` default to the
    configured bind address plus loopback, extended by config.
    """
    security_settings = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1:*",
            "localhost:*",
            f"{server_config.host}:{server_config.port}",
            *server_config.allowed_hosts,
        ],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            *server_config.allowed_origins,
        ],
    )
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        json_response=False,
        stateless=False,
        security_settings=security_settings,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            logger.info(
                "MCP streamable HTTP session manager started on %s:%d%s",
                server_config.host,
                server_config.port,
                server_config.path,
            )
            yield

    # Route (not Mount): Mount treats its path as a prefix and 307-redirects
    # a bare request at exactly that path to path + "/" -- most MCP HTTP
    # clients won't replay a POST body across a redirect.
    return Starlette(
        routes=[
            Route("/healthz", _healthz, methods=["GET"]),
            Route(
                server_config.path,
                endpoint=_SessionManagerASGIApp(session_manager),
            ),
        ],
        middleware=[
            Middleware(
                BearerAuthMiddleware, token=server_config.auth_token
            ),
            Middleware(
                OidcTokenRequiredMiddleware,
                oidc_rpc_url=server_config.oidc_rpc_url,
            ),
        ],
        lifespan=lifespan,
    )


async def run_http(
    mcp_server: Server, server_config: ServerConfig
) -> None:
    """Serve ``mcp_server`` over streamable HTTP until cancelled.

    ``log_config=None`` stops uvicorn installing its own stdout/stderr log
    handlers -- logging is already configured by ``logger.setup_logging()``.
    """
    app = build_http_app(mcp_server, server_config)
    uvicorn_config = uvicorn.Config(
        app,
        host=server_config.host,
        port=server_config.port,
        log_config=None,
    )
    await uvicorn.Server(uvicorn_config).serve()
