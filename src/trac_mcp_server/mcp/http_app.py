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

import anyio
import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import (
    StreamableHTTPSessionManager,
)
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config_schema import ServerConfig
from ..instances import Identity
from .oidc import extract_bearer_token

logger = logging.getLogger(__name__)

_SDK_TRANSPORT_LOGGER = "mcp.server.streamable_http"


class _SseWriterCloseFilter(logging.Filter):
    """Demote the MCP SDK 1.30.0 session-close "standalone SSE writer" record.

    With ``mcp==1.30.0`` on the server side, every streamable-http session
    close logs ``Error in standalone SSE writer`` at ERROR from the logger
    ``mcp.server.streamable_http`` (``logger.exception``, with an
    ``anyio.ClosedResourceError`` in ``exc_info``). The client's DELETE runs
    ``StreamableHTTPServerTransport.terminate()``, which closes the receive
    stream the standalone GET writer is iterating; 1.30.0 moved the GET
    handler's cleanup into a ``finally``, so the writer now reports that
    close as an error. The session ends normally and 1.29.1 does not log
    it, so the record is noise (Trac /trac_mcp_server #169).

    A record matches only when the logger name, the message AND a
    ``ClosedResourceError`` all match; anything else passes unchanged. A
    match is demoted to DEBUG in place and kept only if the logger is
    enabled for DEBUG: handlers compare ``record.levelno`` with their own
    level only, so a NOTSET handler would otherwise still emit it.

    Remove once the SDK stops logging this on a normal session close.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.name == _SDK_TRANSPORT_LOGGER
            and record.getMessage() == "Error in standalone SSE writer"
            and record.exc_info
            and isinstance(
                record.exc_info[1], anyio.ClosedResourceError
            )
        ):
            record.levelno = logging.DEBUG
            record.levelname = "DEBUG"
            return logging.getLogger(record.name).isEnabledFor(
                logging.DEBUG
            )
        return True


def _install_sse_writer_close_filter() -> None:
    """Install ``_SseWriterCloseFilter`` on the SDK transport logger once."""
    lg = logging.getLogger(_SDK_TRANSPORT_LOGGER)
    if not any(
        isinstance(f, _SseWriterCloseFilter) for f in lg.filters
    ):
        lg.addFilter(_SseWriterCloseFilter())


class BearerAuthMiddleware:
    """Pure-ASGI middleware requiring ``Authorization: Bearer <token>``.

    No-op when there is no static ``token`` and no ``identities`` -- some
    deployments intentionally run unauthenticated (loopback-only bind, see
    ``config.validate_server_config``). Does not touch ``/healthz`` so
    health probes work without a token.

    Ticket #102: ``identities`` maps each per-caller bearer token to the
    ``Identity`` it resolves to; the static ``token`` (if any) resolves to
    no identity at all -- a caller using it gets the default instance's
    own configured credentials, exactly as before this ticket. A matched
    identity is stashed on ``scope["trac_identity"]`` for
    ``mcp/server.py``'s ``_caller_identity()`` to read back out of the
    in-flight request later in the dispatch (see that function's
    docstring for the mechanism and its mcp-SDK-version caveat).
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str | None,
        identities: dict[str, Identity] | None = None,
    ) -> None:
        self._app = app
        self._token = token
        self._identities = identities or {}

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http" or scope["path"] == "/healthz":
            await self._app(scope, receive, send)
            return

        if not self._token and not self._identities:
            await self._app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        auth_header = headers.get(b"authorization", b"").decode(
            "latin-1"
        )
        scheme, _, credential = auth_header.partition(" ")

        # Compare against every configured token -- the static one AND
        # every identity's -- rather than looking one up in a dict and
        # stopping at the first hit, so response timing never reveals
        # which of several valid tokens (or none) was presented.
        matched = False
        identity: Identity | None = None
        if scheme.lower() == "bearer":
            if self._token and secrets.compare_digest(
                credential, self._token
            ):
                matched = True
            for (
                candidate_token,
                candidate_identity,
            ) in self._identities.items():
                if secrets.compare_digest(credential, candidate_token):
                    matched = True
                    identity = candidate_identity

        if not matched:
            response = Response(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        if identity is not None:
            scope["trac_identity"] = identity

        await self._app(scope, receive, send)


class OidcTokenRequiredMiddleware:
    """Requires ``Authorization: Bearer <token>`` when OIDC per-user auth is
    configured.

    No-op when ``oidc_rpc_url`` is falsy -- most deployments don't use this
    mode. When it *is* configured, this is the enforcement point for "no
    fallback to a shared identity, ever": every request to the MCP endpoint
    (not ``/healthz``) must carry a bearer token, checked here at the
    transport boundary rather than deep in tool-dispatch logic, so no
    future code path can accidentally skip it. The token's value is not
    validated here -- that happens downstream when Trac's own web server
    (mod_auth_openidc) receives it forwarded as its own ``Authorization:
    Bearer`` header.

    This reuses the standard ``Authorization`` header rather than a custom
    one: an MCP client's own OAuth flow per server (e.g. LibreChat's
    ``oauth:`` config) can only attach a normal bearer header, not a
    bespoke one. Consequently this is mutually exclusive with
    ``BearerAuthMiddleware``'s static token -- ``config.validate_server_config``
    rejects a ``ServerConfig`` that sets both, so in practice at most one of
    the two middlewares is ever actually checking anything on a given
    deployment.
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

        # Starlette's Headers, as _resolve_client reads them through
        # request.headers: with duplicate headers, the one gated here must
        # be the one that gets forwarded.
        token = extract_bearer_token(
            Headers(scope=scope).get("authorization")
        )
        if not token:
            response = Response(
                "Unauthorized: missing 'Authorization: Bearer <token>' "
                "header. This server requires each request to carry its "
                "own per-user Trac OIDC access token; there is no shared "
                "fallback identity.",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


async def _healthz(_request: Request) -> JSONResponse:
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
    _install_sse_writer_close_filter()
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
                BearerAuthMiddleware,
                token=server_config.auth_token,
                identities=server_config.identities,
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
