"""Async utilities for bridging sync XML-RPC calls to async MCP handlers."""

import asyncio
import contextvars
import functools
import logging
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


def init_semaphore(max_parallel: int = 2) -> None:
    """Initialize the concurrency semaphore. Call once at server startup."""
    global _semaphore, _max_parallel
    _semaphore = asyncio.Semaphore(max_parallel)
    _max_parallel = max_parallel
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
    """Wait for a slot (cancellable while queued), then start ``func`` in
    a thread and return the future of its result.

    The slot is released when the THREAD finishes, whatever happened to
    the coroutine that awaits the future: a call cancelled or timed out
    while it runs still has its request in flight, so its slot must not
    be handed to another caller yet or the server sees more than N.
    There is no ``await`` between the acquire and the callback being
    attached, so cancellation cannot land in between and leak the slot.
    """
    semaphore = _semaphore
    assert semaphore is not None
    await semaphore.acquire()
    try:
        # What asyncio.to_thread does: the caller's context variables
        # travel into the thread.
        ctx = contextvars.copy_context()
        fut = asyncio.get_running_loop().run_in_executor(
            None, functools.partial(ctx.run, func, *args, **kwargs)
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

    Falls back to unbounded if the semaphore is not initialized (CLI,
    scripts). For work that makes no Trac XML-RPC call, use ``run_local``.

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


async def run_sync_deadline(
    timeout: float,
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """``run_sync`` with a deadline on the RUNNING phase only.

    Waits for a slot with no deadline, then gives ``func`` ``timeout``
    seconds from the moment it starts, raising ``TimeoutError`` (what
    ``asyncio.wait_for`` raises) when that passes. ``wait_for(run_sync(
    ...), t)`` would count the time spent queued behind other callers
    and time out calls that never started; this does not. A timed-out
    call keeps its slot until its thread ends, as in ``run_sync``.

    Args:
        timeout: Seconds ``func`` may run once it holds a slot
        func: Synchronous function to call
        *args: Positional arguments for func
        **kwargs: Keyword arguments for func

    Returns:
        Result of func(*args, **kwargs)
    """
    if _semaphore is None:
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args, **kwargs), timeout
        )
    fut = await _start_in_slot(func, args, kwargs)
    return await asyncio.wait_for(asyncio.shield(fut), timeout)


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
    """Run coroutines concurrently, at most N at a time.

    Bounds nothing itself: the coroutines do, through run_sync_limited
    (which takes a slot of the asyncio semaphore, like run_sync). But
    with the semaphore initialized the batch runs through N worker
    tasks (N = the semaphore's size), each pulling the next coroutine
    when its last one is done, so a batch holds at most N places in the
    semaphore's queue and a single call from another caller waits about
    one item, not the whole batch. Uninitialized: a plain gather.
    Returns results in order. Exceptions propagate from the first
    failure; the workers are cancelled and the coroutines not yet
    started are closed.

    Args:
        coros: Sequence of coroutines to run concurrently.

    Returns:
        List of results in the same order as input coroutines.
    """
    if _semaphore is None or _max_parallel < 1:
        return list(await asyncio.gather(*coros))
    items = list(coros)
    results: list[Any] = [None] * len(items)
    pending = iter(range(len(items)))

    async def worker() -> None:
        # ``pending`` is shared; next() and the await it feeds are not
        # separated by a suspension point, so no item is taken twice.
        for i in pending:
            results[i] = await items[i]

    workers = [
        asyncio.ensure_future(worker())
        for _ in range(min(_max_parallel, len(items)))
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
    return results
