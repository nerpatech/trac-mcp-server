"""Ticket #275: TRAC_MAX_PARALLEL_REQUESTS bounds every XML-RPC call.

``run_sync`` waits for a slot of the process-wide asyncio semaphore
(``init_semaphore``) on the event loop, BEFORE it hands the function to
a thread. So a queued call parks no executor thread, a call cancelled or
timed out while it waits never reaches the server, and a function run
under ``run_sync`` holds one slot for all its sequential RPCs.
``run_local`` is the same hand-off with no slot, for work that makes no
Trac XML-RPC call.

Real substrate, not mocks (auto_pm:wiki:Rules/testing/
RealSubstrateNotMocks): the defect is how many sockets are open at once,
which threads exist, and whether an abandoned call still sends, none of
which a mocked ``session.post`` can show. A ``ThreadingHTTPServer`` on
127.0.0.1 answers XML-RPC POSTs, sleeps per request, and records the
peak number of requests in flight and every path it received.
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
    and any other path answers OK after ``delay`` seconds (``DELAY``
    unless a test changes it; a request reads it once, on arrival)."""

    def __init__(self):
        self.routes: dict[str, tuple[int, dict[str, str]]] = {}
        self.paths: list[str] = []
        self.delay = DELAY
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
                    delay = srv.delay
                try:
                    time.sleep(delay)
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
def _uninitialised(monkeypatch):
    """Each test starts uninitialised; tests/conftest.py restores the
    semaphore afterwards."""
    monkeypatch.setattr(async_utils, "_semaphore", None)


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


async def test_one_run_sync_holds_one_slot_for_its_sequential_rpcs(
    server,
):
    """A function run under ``run_sync`` takes one slot for all the RPCs
    it makes in turn, so concurrent RPCs stay <= N and, with a limit of
    1, three such functions complete instead of waiting on each other
    mid-function."""
    init_semaphore(1)
    client = _client(server)

    def three_calls(n):
        return [client.get_ticket(n) for _ in range(3)]

    results = await asyncio.wait_for(
        asyncio.gather(*(run_sync(three_calls, n) for n in range(3))),
        TIMEOUT,
    )

    assert results == [[1, 1, 1]] * 3
    assert len(server.paths) == 9
    assert server.peak == 1


async def test_queued_calls_park_no_thread_and_local_work_runs(server):
    """The slot is awaited on the event loop, before any thread: with
    the one slot held by a slow call, queued ``run_sync`` calls have not
    entered a thread, so ``run_local`` work (no Trac) still runs at once
    instead of queueing behind them for an executor thread."""
    init_semaphore(1)
    client = _client(server)
    entered: list[int] = []

    def queued_rpc(n):
        entered.append(n)
        return client.get_ticket(n)

    server.delay = 1.0
    slow = asyncio.ensure_future(run_sync(client.get_ticket, 0))
    for _ in range(100):
        if server.paths:
            break
        await asyncio.sleep(0.02)
    assert server.paths, "the slow call never reached the server"
    server.delay = 0.05

    threads_before = threading.active_count()
    queued = [
        asyncio.ensure_future(run_sync(queued_rpc, n))
        for n in range(1, 6)
    ]
    await asyncio.sleep(0.2)

    assert entered == [], (
        "queued calls entered a thread before the slot"
    )
    assert threading.active_count() <= threads_before

    started = time.monotonic()
    value = await asyncio.wait_for(
        async_utils.run_local(lambda: 42), 0.5
    )
    assert value == 42
    assert time.monotonic() - started < 0.5
    assert not slow.done(), "the slot was released before the check"

    await asyncio.wait_for(asyncio.gather(slow, *queued), TIMEOUT)
    assert sorted(entered) == [1, 2, 3, 4, 5]
    assert server.peak == 1


async def test_call_cancelled_while_queued_sends_nothing(server):
    """A deadline (``asyncio.wait_for``, as ticket_write uses) that fires
    while the call waits for a slot raises ``TimeoutError`` and the RPC
    is never sent: the server sees only the slow call's request."""
    init_semaphore(1)
    client = _client(server)

    server.delay = 0.6
    slow = asyncio.ensure_future(run_sync(client.get_ticket, 0))
    for _ in range(100):
        if server.paths:
            break
        await asyncio.sleep(0.02)
    assert server.paths, "the slow call never reached the server"

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(run_sync(client.get_ticket, 1), 0.1)

    await asyncio.wait_for(slow, TIMEOUT)
    # A thread abandoned by the timeout would send its request about now.
    await asyncio.sleep(0.4)

    assert len(server.paths) == 1
    assert server.peak == 1
    # The abandoned wait left the slot whole.
    assert (
        await asyncio.wait_for(run_sync(client.get_ticket, 2), TIMEOUT)
        == 1
    )


async def test_run_local_never_takes_a_slot(server):
    """With every slot held by RPCs, ``run_local`` work completes at
    once, and its own runs leave the slots as they were."""
    init_semaphore(1)
    client = _client(server)

    server.delay = 0.8
    slow = asyncio.ensure_future(run_sync(client.get_ticket, 0))
    for _ in range(100):
        if server.paths:
            break
        await asyncio.sleep(0.02)
    assert server.paths, "the slow call never reached the server"

    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                async_utils.run_local(lambda n=n: n * 2)
                for n in range(5)
            )
        ),
        0.5,
    )

    assert results == [0, 2, 4, 6, 8]
    assert not slow.done(), "the slot was released before the check"
    await asyncio.wait_for(slow, TIMEOUT)
    assert server.peak == 1


async def test_redirect_inside_one_post_does_not_deadlock(server):
    """A redirect hop happens inside the one ``session.post``, under the
    one slot ``run_sync`` took: with a limit of 1 the call completes."""
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


async def test_401_runs_the_callback_raises_and_leaves_the_slot_free(
    server,
):
    """A refused bearer token calls ``on_unauthorized`` and raises
    ``HTTPError``; the slot comes back, so a second call (limit 1)
    fails promptly too instead of waiting for a slot that never returns."""
    init_semaphore(1)
    server.routes["/rpc"] = (401, {})
    callbacks: list[int] = []

    client = TracClient(
        Config(
            trac_url=f"{server.base}/trac_test",
            username="",
            password="",
            bearer_token="tok",
            rpc_url_override=f"{server.base}/rpc",
            rpc_timeout=10,
        ),
        on_unauthorized=lambda: callbacks.append(1),
    )

    for _ in range(2):
        started = time.monotonic()
        with pytest.raises(requests.HTTPError):
            await asyncio.wait_for(
                run_sync(client.get_ticket, 1), TIMEOUT
            )
        assert time.monotonic() - started < 5 * DELAY + 1

    assert callbacks == [1, 1]
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
