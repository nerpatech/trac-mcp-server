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
from types import SimpleNamespace

import mcp.types as types
import pytest
import requests

from trac_mcp_server.config import Config
from trac_mcp_server.core import async_utils
from trac_mcp_server.core.async_utils import (
    gather_limited,
    init_semaphore,
    run_sync,
    run_sync_limited,
)
from trac_mcp_server.core.client import TracClient
from trac_mcp_server.mcp.tools import ticket_write

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
        # Each request's raw XML-RPC body, so a test can tell WHICH
        # method (``ticket.update``, say) reached the server.
        self.bodies: list[bytes] = []
        self.delay = DELAY
        # When set, a request waits here (up to 5 s) before it sleeps:
        # the barrier only opens once ``parties`` requests are in flight
        # together, so "the limit is used" does not depend on timing.
        self.rendezvous: threading.Barrier | None = None
        self._lock = threading.Lock()
        self._in_flight = 0
        self.peak = 0
        srv = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body_in = self.rfile.read(length)
                with srv._lock:
                    srv.paths.append(self.path)
                    srv.bodies.append(body_in)
                    srv._in_flight += 1
                    srv.peak = max(srv.peak, srv._in_flight)
                    delay = srv.delay
                    rendezvous = srv.rendezvous
                try:
                    if rendezvous is not None:
                        rendezvous.wait(timeout=5)
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


async def _wait_until(predicate, limit: float = 5.0) -> None:
    """Poll ``predicate`` until true; fail (not hang) after ``limit``."""
    deadline = time.monotonic() + limit
    while not predicate():
        assert time.monotonic() < deadline, (
            "condition never became true"
        )
        await asyncio.sleep(0.01)


def _hold_slot(seconds: float):
    """A call that holds a slot for ``seconds``. Returns the call's
    future and an Event set once its thread is running."""
    started = threading.Event()

    def hold():
        started.set()
        time.sleep(seconds)

    return asyncio.ensure_future(run_sync(hold)), started


async def test_run_sync_rpc_calls_are_bounded_by_the_limit(server):
    """8 concurrent ``run_sync`` calls under a limit of 2 never have
    more than 2 requests in flight (unbounded: 8)."""
    init_semaphore(2)
    client = _client(server)
    # Bounded, not serialised: each pair of requests waits for its
    # partner, so the limit is used (a lone request breaks the barrier
    # after 5 s and the call fails).
    server.rendezvous = threading.Barrier(2)

    await asyncio.wait_for(
        asyncio.gather(
            *(run_sync(client.get_ticket, n) for n in range(1, 9))
        ),
        TIMEOUT,
    )

    assert len(server.paths) == 8
    assert server.peak <= 2


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
    # Let every queued call run up to where it waits for the slot.
    for _ in range(50):
        await asyncio.sleep(0)

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
    # A thread abandoned by the timeout would be queued on the (one
    # thread) RPC pool; this marker runs after it, so by now it has sent.
    await asyncio.wait_for(
        asyncio.get_running_loop().run_in_executor(
            async_utils._rpc_executor, lambda: None
        ),
        TIMEOUT,
    )

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
    # All 8 must be in flight together for any of them to answer.
    server.rendezvous = threading.Barrier(8)

    results = await asyncio.wait_for(
        asyncio.gather(
            *(run_sync(client.get_ticket, n) for n in range(1, 9))
        ),
        TIMEOUT,
    )

    assert results == [1] * 8
    assert server.peak > 2


async def test_timed_out_call_keeps_its_slot_until_the_server_answers(
    server,
):
    """Review item 1: a call whose ``wait_for`` fires while its request
    is in flight still has that request on the server, so its slot stays
    taken. With a limit of 1 the next call waits for the first request
    to finish: the server never sees two at once."""
    init_semaphore(1)
    client = _client(server)

    server.delay = 0.6
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(run_sync(client.get_ticket, 1), 0.2)

    assert (
        await asyncio.wait_for(run_sync(client.get_ticket, 2), TIMEOUT)
        == 1
    )

    assert len(server.paths) == 2
    assert server.peak == 1


async def test_deadline_counts_the_running_phase_not_the_queue(server):
    """Review item 2: ``run_sync_deadline(0.3, ...)`` queued behind a
    0.8 s call succeeds, because its 0.3 s starts when it gets the slot
    (``wait_for(run_sync(...), 0.3)`` would time out while queued)."""
    init_semaphore(1)
    client = _client(server)

    server.delay = 0.8
    slow = asyncio.ensure_future(run_sync(client.get_ticket, 0))
    for _ in range(100):
        if server.paths:
            break
        await asyncio.sleep(0.02)
    assert server.paths, "the slow call never reached the server"
    server.delay = 0.05

    value = await asyncio.wait_for(
        async_utils.run_sync_deadline(0.3, client.get_ticket, 1),
        TIMEOUT,
    )

    assert value == 1
    assert slow.done(), "the deadline call ran before the slot freed"
    await asyncio.wait_for(slow, TIMEOUT)
    assert server.peak == 1


async def test_deadline_times_out_a_running_call_and_keeps_its_slot(
    server,
):
    """``run_sync_deadline(0.2, slow)`` with a free slot raises
    ``TimeoutError`` (so ``except TimeoutError`` sites still match) and,
    as in ``run_sync``, the slot stays taken until the request ends."""
    init_semaphore(1)
    client = _client(server)

    server.delay = 0.6
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            async_utils.run_sync_deadline(0.2, client.get_ticket, 1),
            TIMEOUT,
        )

    server.delay = 0.05
    assert (
        await asyncio.wait_for(run_sync(client.get_ticket, 2), TIMEOUT)
        == 1
    )
    assert len(server.paths) == 2
    assert server.peak == 1


async def test_deadline_uninitialised_runs_and_times_out(server):
    assert async_utils._semaphore is None
    client = _client(server)

    server.delay = 0.6
    with pytest.raises(asyncio.TimeoutError):
        await async_utils.run_sync_deadline(0.2, client.get_ticket, 1)
    server.delay = 0.05
    assert (
        await async_utils.run_sync_deadline(
            TIMEOUT, client.get_ticket, 2
        )
        == 1
    )


async def test_a_batch_does_not_starve_a_single_call(server):
    """Review item 3: a 20-item batch through ``gather_limited`` (limit
    2) puts at most 2 items in the semaphore's queue at a time, so a
    single ``run_sync`` call that arrives while it runs is served before
    the batch's last item even starts (queued behind the whole batch it
    would be served after it)."""
    init_semaphore(2)
    client = _client(server)
    events: list[tuple[str, int]] = []

    def batch_item(n):
        events.append(("start", n))
        value = client.get_ticket(n)
        events.append(("end", n))
        return value

    def single():
        events.append(("start", 99))
        value = client.get_ticket(99)
        events.append(("end", 99))
        return value

    batch = asyncio.ensure_future(
        gather_limited(
            [run_sync_limited(batch_item, n) for n in range(20)]
        )
    )
    await _wait_until(lambda: ("start", 0) in events)

    assert await asyncio.wait_for(run_sync(single), TIMEOUT) == 1
    await asyncio.wait_for(batch, TIMEOUT)

    assert len(server.paths) == 21
    assert server.peak <= 2
    assert events.index(("end", 99)) < events.index(("start", 19)), (
        "the single call waited for the whole batch"
    )


def _lookup_client(plugin_seconds: float, scan_seconds: float):
    """A client for ``_find_posted_comment``: no plugin (it answers
    ``None`` after ``plugin_seconds``), a changelog holding the comment
    (after ``scan_seconds``). Real semaphore and threads, no HTTP."""
    epoch = 1_793_631_099
    row = [
        time.strftime("%Y%m%dT%H:%M:%S", time.gmtime(epoch)),
        "agent_rpc",
        "comment",
        "7",
        "hello",
        1,
    ]

    def change_at(ticket_id, new_ts):
        time.sleep(plugin_seconds)

    def changelog(ticket_id):
        time.sleep(scan_seconds)
        return [row]

    client = SimpleNamespace(
        config=SimpleNamespace(username="agent_rpc"),
        get_ticket_change_at=change_at,
        get_ticket_changelog=changelog,
    )
    return client, str(epoch * 1_000_000 + 123456)


async def test_comment_lookup_budget_does_not_count_the_queue(
    monkeypatch,
):
    """Review follow-up to item 2: the comment lookup's one budget
    (patched to 0.5 s) is charged for the time its two RPCs RUN, not for
    the 0.8 s they wait behind a slow call. Both are fast once they get
    the slot, so the comment is found (with the budget measured from
    the start, the scan found 0 s left and reported "changelog read
    timed out")."""
    init_semaphore(1)
    monkeypatch.setattr(ticket_write, "_COMMENT_LOOKUP_TIMEOUT", 0.5)
    client, new_ts = _lookup_client(0.05, 0.05)

    holder, started = _hold_slot(0.8)
    await _wait_until(started.is_set)
    posted = await asyncio.wait_for(
        ticket_write._find_posted_comment(
            client, 1, "hello", None, new_ts
        ),
        TIMEOUT,
    )
    await asyncio.wait_for(holder, TIMEOUT)

    assert posted.failure is None
    assert posted.number == 7


async def test_comment_lookup_budget_is_charged_the_running_time(
    monkeypatch,
):
    """The running time still counts: a plugin call that runs 0.4 s of a
    0.5 s budget leaves the scan 0.1 s, and a scan that needs 0.3 s
    times out ("changelog read timed out"), queue or no queue."""
    init_semaphore(1)
    monkeypatch.setattr(ticket_write, "_COMMENT_LOOKUP_TIMEOUT", 0.5)
    client, new_ts = _lookup_client(0.4, 0.3)

    posted = await asyncio.wait_for(
        ticket_write._find_posted_comment(
            client, 1, "hello", None, new_ts
        ),
        TIMEOUT,
    )

    assert posted.number is None
    assert posted.failure == "changelog read timed out"


async def test_deadline_does_not_wait_for_a_thread_in_the_default_pool():
    """Review item 4: with the loop's default executor saturated by
    ``run_local`` work, a slot holder still gets a thread at once, so
    ``run_sync_deadline(0.3, fast)`` succeeds. (On the default executor
    it queued behind the blocked thread and timed out.)"""
    import concurrent.futures

    init_semaphore(2)
    loop = asyncio.get_running_loop()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(pool)
    release = threading.Event()
    blocked = threading.Event()

    def block():
        blocked.set()
        release.wait(10)

    blocker = asyncio.ensure_future(async_utils.run_local(block))
    await _wait_until(blocked.is_set)
    try:
        value = await asyncio.wait_for(
            async_utils.run_sync_deadline(0.3, lambda: 42), TIMEOUT
        )
    finally:
        release.set()
        await asyncio.wait_for(blocker, TIMEOUT)
        pool.shutdown(wait=False)

    assert value == 42


async def test_comment_lookup_wall_cap_ends_a_starved_queue(
    monkeypatch,
):
    """Review item 1: with the slot held, a wall cap of 0.4 s (running
    budget 10 s) ends a lookup that is still waiting for its slot, with
    the "no request slot" failure, while the slot is still held (it did
    not wait out the queue)."""
    init_semaphore(1)
    monkeypatch.setattr(ticket_write, "_COMMENT_LOOKUP_TIMEOUT", 10.0)
    monkeypatch.setattr(
        ticket_write, "_COMMENT_LOOKUP_WALL_TIMEOUT", 0.4
    )
    client, new_ts = _lookup_client(0.05, 0.05)

    holder, started = _hold_slot(3)
    await _wait_until(started.is_set)
    posted = await asyncio.wait_for(
        ticket_write._find_posted_comment(
            client, 1, "hello", None, new_ts
        ),
        TIMEOUT,
    )
    still_held = not holder.done()
    await asyncio.wait_for(holder, TIMEOUT)

    assert posted.number is None
    assert posted.failure == ticket_write._LOOKUP_NO_SLOT
    assert still_held, "the lookup returned only after the slot freed"


async def test_comment_lookup_wall_cap_on_a_running_read_says_timed_out(
    monkeypatch,
):
    """The cap firing while a read is RUNNING (the slot is free, the
    changelog is slow) is "changelog read timed out", not "no slot"."""
    init_semaphore(1)
    monkeypatch.setattr(ticket_write, "_COMMENT_LOOKUP_TIMEOUT", 10.0)
    monkeypatch.setattr(
        ticket_write, "_COMMENT_LOOKUP_WALL_TIMEOUT", 0.4
    )
    client, new_ts = _lookup_client(0.01, 2.0)

    posted = await asyncio.wait_for(
        ticket_write._find_posted_comment(
            client, 1, "hello", None, new_ts
        ),
        TIMEOUT,
    )

    assert posted.number is None
    assert posted.failure == ticket_write._LOOKUP_TIMED_OUT


async def test_run_sync_deadline_with_nothing_left_raises_at_once(
    server,
):
    """A float timeout <= 0, or a spent budget, raises a timeout at once:
    no thread, nothing sent, the budget untouched."""
    init_semaphore(1)
    client = _client(server)
    spent = async_utils.RunBudget(0.0)
    behind = async_utils.RunBudget(-1.5)

    for timeout in (0, -1.0):
        with pytest.raises(asyncio.TimeoutError):
            await async_utils.run_sync_deadline(
                timeout, client.get_ticket, 1
            )
    with pytest.raises(asyncio.TimeoutError):
        await async_utils.run_sync_deadline(spent, client.get_ticket, 1)
    with pytest.raises(asyncio.TimeoutError):
        await async_utils.run_sync_deadline(
            behind, client.get_ticket, 1
        )

    assert server.paths == []
    assert spent.remaining == 0.0
    assert behind.remaining == -1.5
    # Nor did it take a slot.
    assert (
        await asyncio.wait_for(run_sync(client.get_ticket, 2), TIMEOUT)
        == 1
    )


async def test_comment_lookup_distinguishes_no_slot_from_a_slow_read(
    monkeypatch,
):
    """A lookup the wall cap (0.2 s) ends while a phase still waits for
    its slot says so ("server busy"); a read that ran too long (running
    budget 0.3 s, read takes 1 s, slot free) says "changelog read timed
    out"."""
    init_semaphore(1)
    monkeypatch.setattr(
        ticket_write, "_COMMENT_LOOKUP_WALL_TIMEOUT", 0.2
    )
    monkeypatch.setattr(ticket_write, "_COMMENT_LOOKUP_TIMEOUT", 0.3)
    client, new_ts = _lookup_client(0.01, 1.0)

    holder, started = _hold_slot(1.0)
    await _wait_until(started.is_set)
    no_slot = await asyncio.wait_for(
        ticket_write._find_posted_comment(
            client, 1, "hello", None, new_ts
        ),
        TIMEOUT,
    )
    await asyncio.wait_for(holder, TIMEOUT)
    monkeypatch.setattr(
        ticket_write, "_COMMENT_LOOKUP_WALL_TIMEOUT", 10.0
    )
    slow = await asyncio.wait_for(
        ticket_write._find_posted_comment(
            client, 1, "hello", None, new_ts
        ),
        TIMEOUT,
    )

    assert no_slot.failure == ticket_write._LOOKUP_NO_SLOT
    assert slow.failure == ticket_write._LOOKUP_TIMED_OUT
    assert "busy" in ticket_write._LOOKUP_NO_SLOT
    assert "busy" not in ticket_write._LOOKUP_TIMED_OUT


@pytest.mark.parametrize("hops", range(5))
async def test_a_caller_cancelled_as_its_slot_is_granted_leaks_no_slot(
    hops,
):
    """The slot is granted (the one permit released) and the queued
    caller cancelled ``hops`` loop steps later: while it still waits for
    the permit, as it acquires it, or after, with its function already
    running in a thread. Whichever it was, the caller ends cancelled or
    finished, and the slot comes back (at once, or when the thread
    ends), exactly once: the one slot is free and a following call
    runs."""
    init_semaphore(1)
    semaphore = async_utils._semaphore
    assert semaphore is not None
    await semaphore.acquire()  # the one slot is taken
    ran = threading.Event()
    caller = asyncio.ensure_future(run_sync(ran.set))
    for _ in range(5):
        await asyncio.sleep(0)  # the caller waits for the slot
    assert not caller.done()

    semaphore.release()  # the slot is granted ...
    for _ in range(hops):
        await asyncio.sleep(0)
    took_effect = caller.cancel()  # ... and the caller cancelled

    if took_effect:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(caller, TIMEOUT)
    else:
        await asyncio.wait_for(caller, TIMEOUT)
        assert ran.is_set()
    await _wait_until(lambda: not semaphore.locked())
    # Exactly one slot came back (a double release would leave two).
    await asyncio.wait_for(semaphore.acquire(), 1)
    assert semaphore.locked(), "the slot was released twice"
    semaphore.release()
    assert await asyncio.wait_for(run_sync(lambda: 7), TIMEOUT) == 7


async def test_a_call_cancelled_while_queued_charges_the_budget_nothing(
    server,
):
    """A ``run_sync_deadline`` call cancelled while it waits for its
    slot never ran, so the budget keeps every second for the calls that
    follow, and nothing reaches the server."""
    init_semaphore(1)
    client = _client(server)
    budget = async_utils.RunBudget(5.0)

    holder, started = _hold_slot(0.5)
    await _wait_until(started.is_set)
    queued = asyncio.ensure_future(
        async_utils.run_sync_deadline(budget, client.get_ticket, 2)
    )
    await _wait_until(lambda: budget.waiting)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(queued, TIMEOUT)
    await asyncio.wait_for(holder, TIMEOUT)

    assert budget.remaining == 5.0
    assert server.paths == []


def _probe_registry(handler):
    """A ``ToolRegistry`` serving one tool, ``probe_tool``, whose handler
    is ``handler(client, args)``: the real ``call_tool`` around a
    handler the test controls."""
    from trac_mcp_server.mcp.tools.registry import (
        ToolRegistry,
        ToolSpec,
    )

    spec = ToolSpec(
        tool=types.Tool(
            name="probe_tool",
            description="test probe",
            inputSchema={"type": "object"},
        ),
        permissions=frozenset(),
        handler=handler,
    )
    return ToolRegistry([spec])


def _ok_result() -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text="ok")]
    )


# ---------------------------------------------------------------------------
# Operator decision: no cap on waiting for a slot. A call queues for as long
# as it takes; it is never turned away as "busy".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["run_sync", "run_sync_deadline"])
async def test_a_call_queued_past_the_old_30_s_cap_still_runs(
    server, monkeypatch, kind
):
    """A slotted call that has waited past 30 s (the cap this change
    took out) still runs and reaches the server. The clock inside
    ``async_utils`` jumps 31 s once the tool call is under way; the slot
    is held until after that, so the call has to queue. (With the cap the
    call found its deadline past, was refused and sent nothing.) No real
    30 s wait: order and a patched clock only."""
    init_semaphore(1)
    client = _client(server)
    server.delay = 0.05
    real = time.monotonic
    jump = [0.0]
    monkeypatch.setattr(
        async_utils,
        "time",
        SimpleNamespace(monotonic=lambda: real() + jump[0]),
    )

    async def handler(c, args):
        jump[0] = 31.0  # the tool call is now "31 s old"
        if kind == "run_sync":
            await run_sync(c.get_ticket, 1)
        else:
            await async_utils.run_sync_deadline(
                async_utils.RunBudget(5.0), c.get_ticket, 1
            )
        return _ok_result()

    holder, started = _hold_slot(0.3)
    await _wait_until(started.is_set)
    result = await asyncio.wait_for(
        _probe_registry(handler).call_tool("probe_tool", {}, client),
        TIMEOUT,
    )

    assert holder.done(), "the call did not wait for the slot"
    assert not result.isError, result.content[0].text
    assert len(server.paths) == 1
