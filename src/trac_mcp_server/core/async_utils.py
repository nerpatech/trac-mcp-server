"""Async utilities for bridging sync XML-RPC calls to async MCP handlers."""

import asyncio
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


def init_semaphore(max_parallel: int = 2) -> None:
    """Initialize the concurrency semaphore. Call once at server startup."""
    global _semaphore
    _semaphore = asyncio.Semaphore(max_parallel)
    logger.info(
        "Trac request semaphore initialized: max_parallel=%d",
        max_parallel,
    )


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
    sends anything. The slot is held until ``func`` returns, so ``func``
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
    async with _semaphore:
        return await asyncio.to_thread(func, *args, **kwargs)


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
    checks, and the link probes and page fetches that go through
    ``client.session.get``, which are deliberately outside
    TRAC_MAX_PARALLEL_REQUESTS (the write gate's probes have their own
    per-write cap). Such work must not queue behind, or hold a slot from,
    real RPCs.

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
    """Run coroutines concurrently.

    Bounds nothing itself: the coroutines do, through run_sync_limited
    (which takes a slot of the asyncio semaphore, like run_sync).
    Returns results in order. Exceptions propagate from the first failure.

    Args:
        coros: Sequence of coroutines to run concurrently.

    Returns:
        List of results in the same order as input coroutines.
    """
    return list(await asyncio.gather(*coros))
