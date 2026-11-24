"""Async utilities for bridging sync XML-RPC calls to async MCP handlers."""

import asyncio
import concurrent.futures
import contextvars
import functools
import logging
import time
from typing import Any, Callable, Coroutine, Sequence, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)

# Module-level semaphore, initialized at server startup. Ticket #275: it
# is the bound on concurrent XML-RPC calls to Trac, one per server
# process, shared by every instance and every caller. ``run_sync`` waits
# on it on the event loop; None until init_semaphore (CLI, scripts):
# unbounded.
_semaphore: asyncio.Semaphore | None = None
# The size the semaphore was created with: ``gather_limited`` runs its
# batch through this many workers, so a batch holds at most this many
# places in the semaphore's queue.
_max_parallel: int = 0
# The threads of slotted calls. A slot is held until its thread ends, so
# at most N threads are ever in use, and a call that holds a slot gets a
# thread at once: it never waits behind ``run_local`` work in the loop's
# default executor, so a ``run_sync_deadline`` timer measures the call and
# not a thread queue. None until init_semaphore.
_rpc_executor: concurrent.futures.ThreadPoolExecutor | None = None


def init_semaphore(max_parallel: int = 2) -> None:
    """Initialize the concurrency semaphore and the executor its calls
    run on. Call once per process, at server startup.

    The semaphore holds ``max_parallel`` slots and a call waits for one
    without any time limit (first come, first served on Python 3.12; on
    3.10 an old waiter can occasionally be passed by a newer call). The
    executor is a dedicated pool of ``max_parallel`` threads named
    ``trac-rpc``, so a call that holds a slot always gets a thread at
    once.

    Production calls this once, at startup (the server's lifespan).
    Re-initialising is for tests, and calls in flight across it get
    mixed pools: a call already running finishes on the old executor,
    and a call queued on the old semaphore runs on the NEW pool
    (``_start_in_slot`` reads ``_rpc_executor`` after its acquire)
    while it holds, and later releases, a slot of the OLD semaphore,
    so up to 2N requests can be in flight for a while.
    """
    global _semaphore, _max_parallel, _rpc_executor
    previous = _rpc_executor
    _semaphore = asyncio.Semaphore(max_parallel)
    _max_parallel = max_parallel
    _rpc_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=max_parallel, thread_name_prefix="trac-rpc"
    )
    if previous is not None:
        # In-flight calls finish on the old executor; it takes no more.
        previous.shutdown(wait=False)
    logger.info(
        "Trac request semaphore initialized: max_parallel=%d",
        max_parallel,
    )


def _slot_released(
    semaphore: asyncio.Semaphore, fut: "asyncio.Future[Any]"
) -> None:
    """Done callback of a call's thread future: give the slot back, and
    mark the outcome retrieved so a call nobody awaits any more (it was
    cancelled or timed out) logs no "exception was never retrieved"."""
    semaphore.release()
    if not fut.cancelled():
        fut.exception()


async def _start_in_slot(
    func: Callable[..., T], args: tuple, kwargs: dict
) -> "asyncio.Future[T]":
    """Wait for a slot (cancellable while queued, without a time limit),
    then start ``func`` in a thread and return the future of its result.

    The slot is released when the THREAD finishes, whatever happened to
    the coroutine that awaits the future: a call cancelled or timed out
    while it runs still has its request in flight, so its slot must not
    be handed to another caller yet or the server sees more than N.
    ``Semaphore.acquire`` either takes a permit or raises (a cancelled
    waiter takes none), and there is no ``await`` between the acquire
    and the callback being attached, so cancellation cannot land in
    between and leak the slot.
    """
    semaphore = _semaphore
    assert semaphore is not None
    await semaphore.acquire()
    # Read AFTER the acquire: the pool a call that waited uses is the one
    # current when it got its slot, not the one from before it queued.
    executor = _rpc_executor
    try:
        # What asyncio.to_thread does: the caller's context variables
        # travel into the thread.
        ctx = contextvars.copy_context()
        fut: asyncio.Future[T] = (
            asyncio.get_running_loop().run_in_executor(
                executor,
                functools.partial(ctx.run, func, *args, **kwargs),
            )
        )
    except BaseException:
        semaphore.release()
        raise
    fut.add_done_callback(functools.partial(_slot_released, semaphore))
    return fut


async def run_sync(
    func: Callable[..., T], *args: Any, **kwargs: Any
) -> T:
    """Run a synchronous function that talks to Trac in a thread pool,
    after waiting for a slot of the concurrency semaphore.

    This is used to wrap synchronous XML-RPC calls in async MCP tool handlers.
    The slot (TRAC_MAX_PARALLEL_REQUESTS, per server process, shared by every
    instance and caller) is awaited on the event loop BEFORE the function is
    handed to a thread: a queued call parks no executor thread, and a call
    cancelled or timed out while it waits (``asyncio.wait_for``) never
    sends anything. Once the call has its slot, the slot is held until
    the thread finishes, even if the awaiting coroutine is cancelled or
    times out meanwhile (the request is still in flight), so ``func``
    holds ONE slot for all the RPCs it makes in turn and concurrent RPCs
    stay <= N. ``func`` is sync, so it cannot itself await ``run_sync``:
    there is no nested acquire to deadlock on.

    The wait for the slot has no time limit. A client that gives up
    and retries without cancelling the first call can therefore make
    a queued write run twice.

    Falls back to unbounded if the semaphore is not initialized (CLI,
    scripts). For work that makes no Trac XML-RPC call, use ``run_local``.

    Known limits. On Python 3.10 ``asyncio.Semaphore`` is not strictly
    FIFO, so an old waiter can occasionally be passed by a newer call
    (production runs 3.12). And the semaphore is one per process, shared
    by every Trac instance: a stalled instance can hold every slot, and
    calls to healthy instances then queue behind it.

    Args:
        func: Synchronous function to call
        *args: Positional arguments for func
        **kwargs: Keyword arguments for func

    Returns:
        Result of func(*args, **kwargs)

    Example:
        # In MCP tool handler:
        client = TracClient(config)
        ticket = await run_sync(client.get_ticket, ticket_id)
    """
    if _semaphore is None:
        return await asyncio.to_thread(func, *args, **kwargs)
    fut = await _start_in_slot(func, args, kwargs)
    return await asyncio.shield(fut)


class RunBudget:
    """Seconds of RUNNING time left to a group of ``run_sync_deadline``
    calls that share one deadline.

    Pass it as the ``timeout``: each call gets ``remaining`` seconds and
    spends what its function really ran (measured inside the thread, so
    the time spent queued for a slot is never charged), or all it was
    given when it timed out. A call that never got a slot (cancelled
    while queued) spends nothing.
    """

    def __init__(self, total: float) -> None:
        self.remaining = total
        # True while a call using this budget waits for its slot (set
        # when it starts waiting, cleared when it gets one), so a caller
        # that cancels it can tell "no slot yet" from "slow RPC".
        self.waiting = False

    def spend(self, seconds: float) -> None:
        self.remaining -= seconds


async def run_sync_deadline(
    timeout: "float | RunBudget",
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """``run_sync`` with a deadline on the RUNNING phase only.

    Waits for a slot as ``run_sync`` does (no time limit), then gives
    ``func`` ``timeout`` seconds from the moment it starts, raising
    ``TimeoutError`` (what ``asyncio.wait_for`` raises) when that passes.
    ``wait_for(run_sync(...), t)`` would count the time spent queued
    behind other callers and time out calls that never started; this
    does not. A timed-out call keeps its slot until its thread ends, as
    in ``run_sync``.

    ``timeout`` may be a ``RunBudget``: the call then gets its
    ``remaining`` seconds and charges it the time ``func`` ran. A call
    that never got a slot charges it nothing.

    Args:
        timeout: Seconds ``func`` may run once it holds a slot, or a
            ``RunBudget`` shared with other calls
        func: Synchronous function to call
        *args: Positional arguments for func
        **kwargs: Keyword arguments for func

    Returns:
        Result of func(*args, **kwargs)
    """
    budget: RunBudget | None
    seconds: float | None
    if isinstance(timeout, RunBudget):
        budget, seconds = timeout, timeout.remaining
    else:
        budget, seconds = None, timeout
    if seconds is not None and seconds <= 0:
        # Nothing left to run in: no thread, no charge, nothing sent.
        raise asyncio.TimeoutError()
    ran: list[float] = []
    got_slot = False
    run = func
    if budget is not None:

        def run(*a: Any, **kw: Any) -> Any:
            started = time.monotonic()
            try:
                return func(*a, **kw)
            finally:
                ran.append(time.monotonic() - started)

    try:
        if _semaphore is None:
            got_slot = True  # nothing to wait for
            return await asyncio.wait_for(
                asyncio.to_thread(run, *args, **kwargs), seconds
            )
        if budget is not None:
            budget.waiting = True
        fut = await _start_in_slot(run, args, kwargs)
        got_slot = True
        if budget is not None:
            budget.waiting = False
        # ``asyncio.wait`` leaves ``fut`` alone when the caller is
        # cancelled or the timeout passes (the shield's job), and it
        # always lets a cancellation through: ``wait_for`` over a
        # shielded future returns the result and swallows a cancel that
        # lands in the step ``fut`` completes in, on Python 3.10/3.11.
        done, _ = await asyncio.wait({fut}, timeout=seconds)
        if fut not in done:
            raise asyncio.TimeoutError()
        return fut.result()
    finally:
        if budget is not None and got_slot:
            # Its thread has ended: what it ran. Still running (timed
            # out or cancelled): the whole of what it was given. A call
            # that never got a slot (cancelled while queued)
            # ran nothing and is charged nothing.
            budget.spend(ran[0] if ran else seconds)


async def run_sync_limited(
    func: Callable[..., T], *args: Any, **kwargs: Any
) -> T:
    """Same as ``run_sync``; the name the batch tools and ``runner=``
    parameters already use. Takes a slot of the concurrency semaphore
    before running, unbounded if it is not initialized.

    Args:
        func: Synchronous function to call
        *args: Positional arguments for func
        **kwargs: Keyword arguments for func

    Returns:
        Result of func(*args, **kwargs)
    """
    return await run_sync(func, *args, **kwargs)


async def run_local(
    func: Callable[..., T], *args: Any, **kwargs: Any
) -> T:
    """Run a synchronous function in a thread pool, never taking a slot.

    For work that makes no Trac XML-RPC call: local files, converters, CPU
    checks, and the link probes and the live-preview page fetch that go
    through ``client.session.get``, which are deliberately outside
    TRAC_MAX_PARALLEL_REQUESTS (the write gate's probes have their own
    per-write cap). Such work must not queue behind, or hold a slot from,
    real RPCs. ``ping``'s connectivity check is the one RPC that also
    uses it: a diagnostic must not queue behind the limit, or a full
    queue reads as Trac being down.

    Args:
        func: Synchronous function to call
        *args: Positional arguments for func
        **kwargs: Keyword arguments for func

    Returns:
        Result of func(*args, **kwargs)
    """
    return await asyncio.to_thread(func, *args, **kwargs)


async def gather_limited(
    coros: Sequence[Coroutine[Any, Any, T]],
) -> list[T]:
    """Run every coroutine to completion, at most N at a time.

    Bounds nothing itself: the coroutines do, through run_sync_limited
    (which takes a slot of the asyncio semaphore, like run_sync). The
    batch runs through N worker tasks (N = the semaphore's size; with
    the semaphore uninitialized, one per coroutine), each pulling the
    next coroutine when its last one is done, so a batch holds at most
    N places in the semaphore's queue and a single call from another
    caller waits about one item, not the whole batch.

    Every coroutine is attempted: a failure does not cancel the items in
    flight or skip the ones not yet started (batch handlers promise that
    every item is attempted, and a cancelled item's thread may still have
    created its ticket). When all are done, the exception that was
    raised first in time, if any, is raised. Results are in input order.
    Cancelling the caller cancels the workers and closes the coroutines
    that never started. A ``BaseException`` that is not an ``Exception``
    raised inside an item (an inner ``CancelledError``, say) is treated
    the same way, as if the caller had been cancelled: items in flight
    are cancelled and unstarted ones closed. Python 3.10 cannot tell
    the two apart.

    Args:
        coros: Sequence of coroutines to run concurrently.

    Returns:
        List of results in the same order as input coroutines.
    """
    items = list(coros)
    if _semaphore is not None and _max_parallel >= 1:
        n = _max_parallel
    else:
        n = len(items)
    results: list[Any] = [None] * len(items)
    pending = iter(range(len(items)))
    first_error: Exception | None = None

    async def worker() -> None:
        nonlocal first_error
        # ``pending`` is shared; next() and the await it feeds are not
        # separated by a suspension point, so no item is taken twice.
        for i in pending:
            try:
                results[i] = await items[i]
            except Exception as exc:
                if first_error is None:
                    first_error = exc

    workers = [
        asyncio.ensure_future(worker())
        for _ in range(min(n, len(items)))
    ]
    try:
        await asyncio.gather(*workers)
    except BaseException:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for i in pending:
            items[i].close()
        raise
    if first_error is not None:
        raise first_error
    return results
