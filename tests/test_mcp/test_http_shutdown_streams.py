"""Open SSE streams are completed when the daemon shuts down (Trac
/trac_mcp_server #248).

``sse-starlette`` 3.2.0 (the MCP SDK serves SSE through it) patches
``uvicorn.Server.handle_exit``, so on SIGTERM
``sse_starlette.sse.AppStatus.should_exit`` becomes True. Every open
``EventSourceResponse`` then cancels its task group before it sends the
final ``more_body: False`` message, and uvicorn logs ``ASGI callable
returned without completing response.`` at ERROR, once per open stream, on
every deploy. ``_SessionManagerASGIApp`` sends the closing message itself,
but only when a response started, did not complete, and
``AppStatus.should_exit`` is True.

Sections: unit tests of ``_SessionManagerASGIApp`` with a fake session
manager and a recording ``send``, and one real-substrate test (a real
daemon in a subprocess, real MCP sessions with open standalone GET
streams, a real SIGTERM) that guards the sse-starlette and uvicorn
behaviour end to end.
"""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time

import anyio
import httpx
import pytest
from sse_starlette.sse import AppStatus

from tests.test_mcp.test_http_identity import _free_port
from trac_mcp_server.mcp import http_app
from trac_mcp_server.mcp.http_app import _SessionManagerASGIApp

_START = {"type": "http.response.start", "status": 200, "headers": []}
_CLOSE = {"type": "http.response.body", "body": b"", "more_body": False}


def _chunk(more_body: bool | None = True) -> dict:
    message: dict = {"type": "http.response.body", "body": b"x"}
    if more_body is not None:
        message["more_body"] = more_body
    return message


class _FakeSessionManager:
    """``handle_request`` sends the scripted messages, then raises
    ``raises`` if given."""

    def __init__(self, script, raises: BaseException | None = None):
        self._script = script
        self._raises = raises

    async def handle_request(self, scope, receive, send) -> None:
        for message in self._script:
            await send(message)
        if self._raises is not None:
            raise self._raises


@pytest.fixture
def should_exit(monkeypatch):
    """Set ``AppStatus.should_exit``; restored after the test."""

    def _set(value: bool) -> None:
        monkeypatch.setattr(AppStatus, "should_exit", value)

    _set(False)
    return _set


async def _drive(
    script, raises=None, scope_type="http", method="GET", send=None
) -> list[dict]:
    recorded: list[dict] = []

    async def record(message):
        recorded.append(message)

    async def receive():  # pragma: no cover - never awaited
        return {"type": "http.disconnect"}

    app = _SessionManagerASGIApp(_FakeSessionManager(script, raises))
    await app(
        {"type": scope_type, "method": method}, receive, send or record
    )
    return recorded


class TestCompletionOnShutdown:
    async def test_cut_stream_is_closed_on_shutdown(self, should_exit):
        should_exit(True)
        sent = await _drive([_START, _chunk(True)])
        assert sent == [_START, _chunk(True), _CLOSE]

    async def test_cut_stream_is_left_alone_outside_shutdown(
        self, should_exit
    ):
        should_exit(False)
        sent = await _drive([_START, _chunk(True)])
        assert sent == [_START, _chunk(True)]

    async def test_started_with_no_body_is_closed_on_shutdown(
        self, should_exit
    ):
        should_exit(True)
        sent = await _drive([_START])
        assert sent == [_START, _CLOSE]

    @pytest.mark.parametrize("more_body", [False, None])
    async def test_completed_response_gets_nothing_appended(
        self, should_exit, more_body
    ):
        # None: ``more_body`` absent, which ASGI defaults to False.
        should_exit(True)
        script = [_START, _chunk(True), _chunk(more_body)]
        sent = await _drive(script)
        assert sent == script

    async def test_unstarted_response_gets_nothing_appended(
        self, should_exit
    ):
        should_exit(True)
        sent = await _drive([])
        assert sent == []

    async def test_exception_propagates_and_nothing_is_appended(
        self, should_exit
    ):
        should_exit(True)
        recorded: list[dict] = []

        async def send(message):
            recorded.append(message)

        app = _SessionManagerASGIApp(
            _FakeSessionManager(
                [_START, _chunk(True)], RuntimeError("x")
            )
        )
        with pytest.raises(RuntimeError, match="x"):
            await app({"type": "http", "method": "GET"}, None, send)
        assert recorded == [_START, _chunk(True)]

    async def test_non_http_scope_is_passed_through(self, should_exit):
        should_exit(True)
        sent = await _drive([_START], scope_type="websocket")
        assert sent == [_START]

    @pytest.mark.parametrize("method", ["POST", "DELETE"])
    async def test_cut_non_get_response_gets_nothing_appended(
        self, should_exit, method
    ):
        # A POST's SSE stream carries a tool call's result: completing
        # it would turn an aborted request into a clean 200.
        should_exit(True)
        sent = await _drive([_START, _chunk(True)], method=method)
        assert sent == [_START, _chunk(True)]

    async def test_get_is_closed_on_shutdown(self, should_exit):
        should_exit(True)
        sent = await _drive([_START, _chunk(True)], method="GET")
        assert sent == [_START, _chunk(True), _CLOSE]

    async def test_missing_app_status_makes_completion_inert(
        self, should_exit, monkeypatch
    ):
        # sse-starlette moved or dropped AppStatus: no failure, no
        # completion.
        should_exit(True)
        monkeypatch.setattr(http_app, "AppStatus", None)
        sent = await _drive([_START, _chunk(True)])
        assert sent == [_START, _chunk(True)]

    async def test_stalled_close_is_bounded(
        self, should_exit, monkeypatch
    ):
        # A client that stopped reading blocks uvicorn's send in its
        # drain; the closing send runs after sse-starlette's task group
        # is gone, so only our timeout ends it.
        should_exit(True)
        monkeypatch.setattr(http_app, "_SHUTDOWN_CLOSE_TIMEOUT", 0.05)
        never = asyncio.Event()
        calls: list[dict] = []

        async def stalled_send(message):
            calls.append(message)
            if message == _CLOSE:
                await never.wait()

        # fail_after turns a regression (no timeout) into a failure
        # instead of hanging the suite.
        with anyio.fail_after(5):
            await _drive([_START, _chunk(True)], send=stalled_send)
        assert calls == [_START, _chunk(True), _CLOSE]


# ---------------------------------------------------------------------------
# Real substrate: a daemon subprocess, open SSE streams, SIGTERM
# ---------------------------------------------------------------------------

_SERVER_SCRIPT = """
import anyio, asyncio, logging, sys
import mcp.types as types
from mcp.server.lowlevel import Server
from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.mcp.http_app import run_http

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
config = ServerConfig(
    transport="http", host="127.0.0.1", port=int(sys.argv[1]), path="/mcp"
)
server = Server("t")

@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="block",
            description="never returns",
            inputSchema={"type": "object"},
        )
    ]

@server.call_tool()
async def call_tool(name, arguments):
    await anyio.sleep_forever()

asyncio.run(run_http(server, config))
"""

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
_CALL = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {"name": "block", "arguments": {}},
}
_ACCEPT = {"Accept": "application/json, text/event-stream"}
_N_STREAMS = 2
_RETURNED = "returned without completing response"
_SHUTTING_DOWN = "StreamableHTTP session manager shutting down"


async def _wait_healthy(
    client: httpx.AsyncClient, base: str, proc
) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        assert proc.poll() is None, "server exited during startup"
        try:
            if (await client.get(f"{base}/healthz")).status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.1)
    raise AssertionError("server never became healthy")


async def _open_session(
    client: httpx.AsyncClient, url: str
) -> dict[str, str]:
    """Initialize a session; returns its headers."""
    init = await client.post(url, json=_INIT, headers=_ACCEPT)
    assert init.status_code == 200, init.text
    session = {
        "mcp-session-id": init.headers["mcp-session-id"],
        "mcp-protocol-version": "2025-03-26",
    }
    done = await client.post(
        url,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**_ACCEPT, **session},
    )
    assert done.status_code == 202, done.text
    return session


async def _open_stream(client: httpx.AsyncClient, url: str, stack):
    """Initialize a session and open its standalone GET stream (kept
    open); returns the streaming response."""
    session = await _open_session(client, url)
    response = await stack.enter_async_context(
        client.stream(
            "GET",
            url,
            headers={"Accept": "text/event-stream", **session},
        )
    )
    assert response.status_code == 200
    return response


async def _open_blocked_call(
    client: httpx.AsyncClient, url: str, stack
):
    """Initialize a session and stream a ``tools/call`` that never
    returns, until its response headers arrive (SSE 200)."""
    session = await _open_session(client, url)
    response = await stack.enter_async_context(
        client.stream(
            "POST", url, json=_CALL, headers={**_ACCEPT, **session}
        )
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    return response


async def _drain(response) -> BaseException | None:
    """Read a stream to its end; the httpx error it ended with, if any."""
    try:
        async for _ in response.aiter_raw():
            pass
    except httpx.HTTPError as exc:
        return exc
    return None


async def _sigterm_with_open_streams(
    tmp_path, *, blocked_call: bool
) -> tuple[str, list, BaseException | None]:
    """Run the daemon, open ``_N_STREAMS`` GET streams (plus a blocked
    POST ``tools/call`` if asked), SIGTERM it; returns the child's
    output, each GET stream's end (None for clean) and the POST's."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = tmp_path / "server.log"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-c", _SERVER_SCRIPT, str(port)],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
    get_ends: list = []
    post_end: BaseException | None = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await _wait_healthy(client, base, proc)
            async with contextlib.AsyncExitStack() as stack:
                gets = [
                    await _open_stream(client, f"{base}/mcp", stack)
                    for _ in range(_N_STREAMS)
                ]
                post = (
                    await _open_blocked_call(
                        client, f"{base}/mcp", stack
                    )
                    if blocked_call
                    else None
                )
                proc.send_signal(signal.SIGTERM)

                async def _exited() -> None:
                    while proc.poll() is None:
                        await asyncio.sleep(0.05)

                try:
                    await asyncio.wait_for(_exited(), timeout=15)
                except asyncio.TimeoutError:
                    proc.kill()
                    pytest.fail(
                        "server did not exit within 15 s of SIGTERM"
                    )

                get_ends = [await _drain(r) for r in gets]
                if post is not None:
                    post_end = await _drain(post)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
    return log_path.read_text(errors="replace"), get_ends, post_end


_posix_only = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(signal, "SIGTERM"),
    reason="needs POSIX SIGTERM",
)


@_posix_only
async def test_sigterm_completes_every_open_stream(tmp_path):
    output, get_ends, _ = await _sigterm_with_open_streams(
        tmp_path, blocked_call=False
    )
    assert _SHUTTING_DOWN in output, output
    assert _RETURNED not in output, output
    assert get_ends == [None] * _N_STREAMS, get_ends


@_posix_only
async def test_sigterm_leaves_an_aborted_post_as_an_error(tmp_path):
    """A GET stream ends cleanly, but a ``tools/call`` cut mid-flight
    must not look like a completed request: its stream ends with a
    transport error and uvicorn's ERROR stays, exactly once."""
    output, get_ends, post_end = await _sigterm_with_open_streams(
        tmp_path, blocked_call=True
    )
    assert _SHUTTING_DOWN in output, output
    assert get_ends == [None] * _N_STREAMS, get_ends
    assert isinstance(post_end, httpx.HTTPError), (post_end, output)
    assert output.count(_RETURNED) == 1, output
