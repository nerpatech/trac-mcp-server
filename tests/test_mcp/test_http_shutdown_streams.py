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

import httpx
import pytest
from sse_starlette.sse import AppStatus

from tests.test_mcp.test_http_identity import _free_port
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


async def _drive(script, raises=None, scope_type="http") -> list[dict]:
    recorded: list[dict] = []

    async def send(message):
        recorded.append(message)

    async def receive():  # pragma: no cover - never awaited
        return {"type": "http.disconnect"}

    app = _SessionManagerASGIApp(_FakeSessionManager(script, raises))
    await app({"type": scope_type}, receive, send)
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
            await app({"type": "http"}, None, send)
        assert recorded == [_START, _chunk(True)]

    async def test_non_http_scope_is_passed_through(self, should_exit):
        should_exit(True)
        sent = await _drive([_START], scope_type="websocket")
        assert sent == [_START]


# ---------------------------------------------------------------------------
# Real substrate: a daemon subprocess, open SSE streams, SIGTERM
# ---------------------------------------------------------------------------

_SERVER_SCRIPT = """
import asyncio, logging, sys
from mcp.server.lowlevel import Server
from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.mcp.http_app import run_http

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
config = ServerConfig(
    transport="http", host="127.0.0.1", port=int(sys.argv[1]), path="/mcp"
)
asyncio.run(run_http(Server("t"), config))
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
_ACCEPT = {"Accept": "application/json, text/event-stream"}
_N_STREAMS = 2


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


async def _open_stream(client: httpx.AsyncClient, url: str, stack):
    """Initialize a session and open its standalone GET stream (kept
    open); returns the streaming response."""
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
    response = await stack.enter_async_context(
        client.stream(
            "GET",
            url,
            headers={"Accept": "text/event-stream", **session},
        )
    )
    assert response.status_code == 200
    return response


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(signal, "SIGTERM"),
    reason="needs POSIX SIGTERM",
)
async def test_sigterm_completes_every_open_stream(tmp_path):
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
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await _wait_healthy(client, base, proc)
            async with contextlib.AsyncExitStack() as stack:
                streams = [
                    await _open_stream(client, f"{base}/mcp", stack)
                    for _ in range(_N_STREAMS)
                ]
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

                client_errors: list[BaseException] = []
                for response in streams:
                    try:
                        async for _ in response.aiter_raw():
                            pass
                    except httpx.HTTPError as exc:
                        client_errors.append(exc)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)

    output = log_path.read_text(errors="replace")
    assert "StreamableHTTP session manager shutting down" in output, (
        output
    )
    assert "returned without completing response" not in output, output
    assert client_errors == [], client_errors
