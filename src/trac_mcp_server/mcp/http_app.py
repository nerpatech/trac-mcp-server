"""Streamable HTTP transport for the MCP server.

Wires ``mcp.server.streamable_http_manager.StreamableHTTPSessionManager``
into a Starlette app served by uvicorn. Kept separate from ``server.py`` so
the stdio path -- the default, most heavily used transport -- stays free of
HTTP-specific imports and complexity.
"""

import base64
import binascii
import contextlib
import json
import logging
import re
import secrets
import time
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
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .. import version as _version
from ..config_schema import ServerConfig
from ..instances import Identity
from .oidc import extract_bearer_token

# sse-starlette's shutdown flag. If a future release moves or drops
# ``AppStatus`` the shutdown check below is treated as False, so the
# stream completion (Trac /trac_mcp_server #248) goes inert instead of
# failing daemon startup. The real-substrate test in
# tests/test_mcp/test_http_shutdown_streams.py is what catches it going
# inert: it expects no ``returned without completing response`` for GET
# streams on SIGTERM.
try:
    from sse_starlette.sse import AppStatus
except ImportError:  # pragma: no cover
    AppStatus = None

logger = logging.getLogger(__name__)

# Upper bound on the closing send of a stream cut on shutdown; see
# ``_SessionManagerASGIApp``.
_SHUTDOWN_CLOSE_TIMEOUT = 1.0

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
            record.levelname = logging.getLevelName(logging.DEBUG)
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


# A token this close to its expiry is treated as expired: the request it
# authorizes may still be running when it lapses.
_OIDC_EXPIRY_LEEWAY_SECONDS = 30

_BASE64URL_SEGMENT = re.compile(r"[A-Za-z0-9_-]*=*")


def _bearer_token_exp(token: str) -> float | None:
    """The numeric ``exp`` claim of ``token`` if it is a JWT, else None.

    The signature is not checked: Trac's web server validates the token.
    A token that is not a JWT, or has no numeric ``exp``, yields None.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    segment = parts[1]
    # urlsafe_b64decode silently drops characters outside the alphabet, so
    # a segment that is not base64url is rejected before decoding.
    if not _BASE64URL_SEGMENT.fullmatch(segment):
        return None
    try:
        payload = segment.rstrip("=")
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError, RecursionError):
        # RecursionError: json.loads on a deeply nested payload. This runs
        # before any validation, so it must not turn into a 500.
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    return exp


def _exp_has_passed(exp: float, now: float | None = None) -> bool:
    current = time.time() if now is None else now
    return exp <= current + _OIDC_EXPIRY_LEEWAY_SECONDS


def _bearer_token_expired(token: str, now: float | None = None) -> bool:
    """True if ``token`` is a JWT whose ``exp`` claim has passed.

    A token that is not a JWT, or has no numeric ``exp``, counts as not
    expired.
    """
    exp = _bearer_token_exp(token)
    return exp is not None and _exp_has_passed(exp, now)


class OidcTokenRequiredMiddleware:
    """Requires ``Authorization: Bearer <token>`` when OIDC per-user auth is
    configured.

    No-op when ``oidc_rpc_url`` is falsy -- most deployments don't use this
    mode. When it *is* configured, this is the enforcement point for "no
    fallback to a shared identity, ever": every request to the MCP endpoint
    (not ``/healthz``) must carry a bearer token, checked here at the
    transport boundary rather than deep in tool-dispatch logic, so no
    future code path can accidentally skip it. The token's signature is not
    validated here -- that happens downstream when Trac's own web server
    (mod_auth_openidc) receives it forwarded as its own ``Authorization:
    Bearer`` header.

    A JWT whose ``exp`` has passed is answered with 401 and
    ``error="invalid_token"`` here, instead of being forwarded. Trac would
    reject it too, but that 401 only reaches the client as text inside a
    tool result, and an MCP client refreshes its token only on a 401 from
    the MCP endpoint itself.

    A token Trac has rejected is answered the same way, with one
    difference in timing. Trac's 401 (a revoked JWT, an opaque token that
    expired, a token that lapsed while a tool ran) arrives while a tool
    call is already streaming its response, so that request cannot become
    a 401: the call reports the error as tool-result text with HTTP 200.
    ``TracClient`` records the rejection in the ``OidcClientCache``, and
    the NEXT request that carries the token is answered 401 here. See
    ``server.oidc_token_rejected``.

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

        exp = _bearer_token_exp(token)
        if exp is not None and _exp_has_passed(exp):
            # Only the seconds past exp: never the token or another claim.
            logger.info(
                "OIDC access token expired %d s ago; answering 401",
                max(0, int(time.time() - exp)),
            )
            response = Response(
                "Unauthorized: the access token has expired.",
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Bearer error="invalid_token", '
                    'error_description="The access token expired"'
                },
            )
            await response(scope, receive, send)
            return

        # Imported here: server.py imports this module at load time.
        from .server import oidc_token_rejected

        if oidc_token_rejected(token):
            logger.info(
                "OIDC access token was rejected by Trac; answering 401"
            )
            response = Response(
                "Unauthorized: Trac rejected the access token.",
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Bearer error="invalid_token", '
                    'error_description="Trac rejected the access token"'
                },
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


async def _healthz(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "release": _version.release_sha() or "unknown",
        }
    )


class _SessionManagerASGIApp:
    """Adapts ``StreamableHTTPSessionManager.handle_request`` for ``Route``.

    Starlette's ``Route`` special-cases a bound method as a
    ``func(request) -> response`` handler (defaulting to GET-only) rather
    than a raw ASGI callable -- ``inspect.ismethod()`` is True for
    ``session_manager.handle_request``. Wrapping it in a plain callable
    class (same pattern as the SDK's own
    ``mcp.server.fastmcp.server.StreamableHTTPASGIApp``) makes ``Route``
    treat it as ASGI and dispatch every HTTP method (GET/POST/DELETE) to it.

    It also completes the standalone GET stream that ``sse-starlette``
    cuts on shutdown (Trac /trac_mcp_server #248). ``sse-starlette``
    3.2.0 patches ``uvicorn.Server.handle_exit``, so on SIGTERM
    ``AppStatus.should_exit`` becomes True and every open
    ``EventSourceResponse`` cancels its task group BEFORE it sends the
    final empty ``more_body: False`` message; the response returns
    started but incomplete and uvicorn logs ``ASGI callable returned
    without completing response.`` at ERROR once per open stream, on
    every deploy. This sends that closing message itself, only when ALL
    of these hold:

    * the request is a GET: the standalone notification stream, where a
      clean end loses nothing. A POST's SSE stream carries a tool call's
      result; the SDK returns normally after the cut, so completing it
      would turn an aborted request into a clean 200 with no JSON-RPC
      result and the client could wait until its own timeout. A POST cut
      on shutdown keeps uvicorn's ERROR, an honest signal that a request
      was aborted;
    * ``handle_request`` returned (an exception from it propagates);
    * a response started and was not completed;
    * ``AppStatus.should_exit`` is True (and ``AppStatus`` imported).

    The SDK catches exceptions from the response on its SSE paths
    (``logger.exception("Error in standalone SSE response")`` for GET,
    ``"SSE response error"`` for POST) and returns normally, so a GET
    stream that died of a send error during shutdown also reaches this
    point started and not completed: the SDK has already logged the
    error, and the closing message is attempted on the same connection,
    which uvicorn drops silently once it has disconnected. Outside
    shutdown an unfinished response is a real bug and is left alone.

    The closing send runs after sse-starlette's task group has finished,
    so nothing cancels it, and a stalled client would block uvicorn's
    ``send`` in its flow-control drain until TCP gives up. It is bounded
    by ``_SHUTDOWN_CLOSE_TIMEOUT``; on timeout the response is left
    incomplete, uvicorn logs its ERROR for that one stuck stream and
    closes the transport, as before the fix.

    Drop the completion once sse-starlette completes the response itself
    on shutdown.
    """

    def __init__(
        self, session_manager: StreamableHTTPSessionManager
    ) -> None:
        self._session_manager = session_manager

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self._session_manager.handle_request(
                scope, receive, send
            )
            return

        started = False
        completed = False

        async def tracking_send(message: Message) -> None:
            nonlocal started, completed
            if message["type"] == "http.response.start":
                started = True
            elif message["type"] == "http.response.body" and not (
                message.get("more_body", False)
            ):
                completed = True
            await send(message)

        await self._session_manager.handle_request(
            scope, receive, tracking_send
        )
        if (
            scope.get("method") == "GET"
            and started
            and not completed
            and AppStatus is not None
            and AppStatus.should_exit
        ):
            logger.debug(
                "Completing a stream sse-starlette cut on shutdown"
            )
            with anyio.move_on_after(
                _SHUTDOWN_CLOSE_TIMEOUT
            ) as cancel_scope:
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    }
                )
            if cancel_scope.cancelled_caught:
                logger.debug(
                    "Closing a cut stream timed out after %s s; "
                    "leaving it incomplete",
                    _SHUTDOWN_CLOSE_TIMEOUT,
                )


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
