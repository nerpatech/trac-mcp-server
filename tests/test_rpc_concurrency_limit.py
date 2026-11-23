"""Ticket #275: TRAC_MAX_PARALLEL_REQUESTS bounds every XML-RPC call.

Only ``run_sync_limited`` took the asyncio semaphore, so the ~94
``run_sync`` sites and the write gate's render (``wiki.wikiToHtml``)
sent as many simultaneous requests as they had callers. The bound now
sits at ``TracClient._rpc_call``, around ``session.post`` only, on a
process-wide ``threading.BoundedSemaphore`` that ``init_semaphore``
sets.

Real substrate, not mocks (auto_pm:wiki:Rules/testing/
RealSubstrateNotMocks): the defect is how many sockets are open at once
and whether a slot is released on every path, which a mocked
``session.post`` cannot show. A ``ThreadingHTTPServer`` on 127.0.0.1
answers XML-RPC POSTs, sleeps per request, and records the peak number
of requests in flight at once.
"""

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from trac_mcp_server.config import Config
from trac_mcp_server.core import async_utils
from trac_mcp_server.core.async_utils import init_semaphore, run_sync
from trac_mcp_server.core.client import TracClient

OK_BODY = (
    b"<?xml version='1.0'?><methodResponse><params><param>"
    b"<value><int>1</int></value></param></params></methodResponse>"
)

# Long enough that 8 concurrent calls overlap on any machine, short
# enough that the file runs in a couple of seconds.
DELAY = 0.2
# A regression must fail, not hang the suite.
TIMEOUT = 15


class _Server:
    """XML-RPC stand-in: ``routes`` maps a path to ``(status, headers)``
    and any other path answers OK after ``DELAY`` seconds."""

    def __init__(self):
        self.routes: dict[str, tuple[int, dict[str, str]]] = {}
        self.paths: list[str] = []
        self._lock = threading.Lock()
        self._in_flight = 0
        self.peak = 0
        srv = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                with srv._lock:
                    srv.paths.append(self.path)
                    srv._in_flight += 1
                    srv.peak = max(srv.peak, srv._in_flight)
                try:
                    time.sleep(DELAY)
                    status, headers = srv.routes.get(
                        self.path, (200, {})
                    )
                    body = OK_BODY if status == 200 else b""
                    self.send_response(status)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                finally:
                    with srv._lock:
                        srv._in_flight -= 1

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self._thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def server():
    srv = _Server()
    yield srv
    srv.close()


@pytest.fixture(autouse=True)
def _restore_async_utils_state():
    """Each test starts uninitialised and leaves no semaphore behind."""
    saved = {
        name: getattr(async_utils, name)
        for name in ("_semaphore", "_rpc_semaphore")
        if hasattr(async_utils, name)
    }
    for name in saved:
        setattr(async_utils, name, None)
    yield
    for name, value in saved.items():
        setattr(async_utils, name, value)


def _client(server: _Server, **kwargs) -> TracClient:
    config = Config(
        trac_url=f"{server.base}/trac_test",
        username="agent_rpc",
        password="pw",
        rpc_timeout=10,
        **kwargs,
    )
    return TracClient(config)


async def test_run_sync_rpc_calls_are_bounded_by_the_limit(server):
    """8 concurrent ``run_sync`` calls under a limit of 2 never have
    more than 2 requests in flight (unbounded: 8)."""
    init_semaphore(2)
    client = _client(server)

    await asyncio.wait_for(
        asyncio.gather(
            *(run_sync(client.get_ticket, n) for n in range(1, 9))
        ),
        TIMEOUT,
    )

    assert len(server.paths) == 8
    assert server.peak <= 2
    # Bounded, not serialised: the limit is used.
    assert server.peak == 2


async def test_limit_is_shared_by_every_client(server):
    """One semaphore per process: two clients together stay under 2."""
    init_semaphore(2)
    first, second = _client(server), _client(server)

    await asyncio.wait_for(
        asyncio.gather(
            *(
                run_sync((first, second)[n % 2].get_ticket, n)
                for n in range(8)
            )
        ),
        TIMEOUT,
    )

    assert len(server.paths) == 8
    assert server.peak <= 2


async def test_redirect_inside_one_post_does_not_deadlock(server):
    """A redirect hop happens inside the one ``session.post`` and does
    not take a second slot: with a limit of 1 the call completes."""
    init_semaphore(1)
    server.routes["/trac_test/login/rpc"] = (
        307,
        {"Location": "/elsewhere/rpc"},
    )
    client = _client(server)

    result = await asyncio.wait_for(
        run_sync(client.get_ticket, 1), TIMEOUT
    )

    assert result == 1
    assert server.paths == ["/trac_test/login/rpc", "/elsewhere/rpc"]


async def test_401_releases_the_slot_before_the_callback_and_raise(
    server,
):
    """A refused bearer token calls ``on_unauthorized`` and raises
    ``HTTPError``; the slot is back, so a second call (limit 1) fails
    promptly too instead of waiting for a slot that never returns."""
    init_semaphore(1)
    server.routes["/rpc"] = (401, {})
    seen_free: list[bool] = []

    def on_unauthorized():
        # The callback runs with the slot released: it can take it.
        sem = async_utils._rpc_semaphore
        got = sem.acquire(blocking=False)
        seen_free.append(got)
        if got:
            sem.release()

    client = TracClient(
        Config(
            trac_url=f"{server.base}/trac_test",
            username="",
            password="",
            bearer_token="tok",
            rpc_url_override=f"{server.base}/rpc",
            rpc_timeout=10,
        ),
        on_unauthorized=on_unauthorized,
    )

    for _ in range(2):
        with pytest.raises(requests.HTTPError):
            await asyncio.wait_for(
                run_sync(client.get_ticket, 1), TIMEOUT
            )

    assert seen_free == [True, True]
    assert server.paths == ["/rpc", "/rpc"]


async def test_uninitialised_semaphore_leaves_calls_unbounded(server):
    """The CLI and scripts never call ``init_semaphore``: no bound and
    no error."""
    assert async_utils._semaphore is None
    client = _client(server)

    results = await asyncio.wait_for(
        asyncio.gather(
            *(run_sync(client.get_ticket, n) for n in range(1, 9))
        ),
        TIMEOUT,
    )

    assert results == [1] * 8
    assert server.peak > 2


def test_rpc_slot_releases_the_semaphore_it_acquired():
    """A re-init while a call holds a slot must not make that call
    release the new semaphore it never acquired."""
    init_semaphore(1)
    with async_utils.rpc_slot():
        init_semaphore(1)
    # BoundedSemaphore raises ValueError on a release past its initial
    # value; the new semaphore must still be whole.
    sem = async_utils._rpc_semaphore
    assert sem.acquire(blocking=False)
    sem.release()


def test_rpc_slot_uninitialised_is_a_no_op():
    with async_utils.rpc_slot():
        pass
