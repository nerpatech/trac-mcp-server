"""Async utilities for bridging sync XML-RPC calls to async MCP handlers."""

import asyncio
import contextlib
import logging
import threading
from typing import Any, Callable, Coroutine, Sequence, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)

# Module-level semaphore, initialized at server startup
_semaphore: asyncio.Semaphore | None = None

# Ticket #275: the bound on XML-RPC calls themselves. One per process,
# shared by every TracClient and every OIDC client; a thread semaphore
# because the calls run in executor threads (``run_sync``) and in the
# caller's thread. None until init_semaphore (CLI, scripts): unbounded.
_rpc_semaphore: threading.BoundedSemaphore | None = None


def init_semaphore(max_parallel: int = 2) -> None:
    """Initialize the concurrency semaphores. Call once at server startup.

    Two bounds: the asyncio semaphore ``run_sync_limited`` takes (how many
    batch items occupy executor threads) and the thread semaphore
    ``rpc_slot`` hands out (how many XML-RPC calls to Trac are in flight).
    """
    global _semaphore, _rpc_semaphore
    _semaphore = asyncio.Semaphore(max_parallel)
    _rpc_semaphore = threading.BoundedSemaphore(max_parallel)
    logger.info(
        "Trac request semaphore initialized: max_parallel=%d",
        max_parallel,
    )


def rpc_slot() -> contextlib.AbstractContextManager[Any]:
    """A slot for one XML-RPC call: the process-wide bound, or a no-op.

    ``TracClient._rpc_call`` holds it around ``session.post`` only. Before
    ``init_semaphore`` (CLI, scripts) it is ``contextlib.nullcontext()``,
    so those stay unbounded. The semaphore is read once, here, so a
    ``init_semaphore`` call while a slot is held cannot make that slot
    release a semaphore it never acquired.
    """
    semaphore = _rpc_semaphore
    if semaphore is None:
        return contextlib.nullcontext()
    return semaphore


async def run_sync(
    func: Callable[..., T], *args: Any, **kwargs: Any
) -> T:
    """Run a synchronous function in a thread pool without blocking the event loop.

    This is used to wrap synchronous XML-RPC calls in async MCP tool handlers.
    Does NOT acquire the asyncio semaphore, so it does not limit how many
    threads run. Every Trac XML-RPC call inside ``func`` is still bounded
    by TRAC_MAX_PARALLEL_REQUESTS, at ``TracClient._rpc_call`` (``rpc_slot``).

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
    return await asyncio.to_thread(func, *args, **kwargs)


async def run_sync_limited(
    func: Callable[..., T], *args: Any, **kwargs: Any
) -> T:
    """Run a synchronous function in a thread pool, bounded by the concurrency semaphore.

    Falls back to unbounded if semaphore not initialized.

    Args:
        func: Synchronous function to call
        *args: Positional arguments for func
        **kwargs: Keyword arguments for func

    Returns:
        Result of func(*args, **kwargs)
    """
    if _semaphore is None:
        return await asyncio.to_thread(func, *args, **kwargs)
    async with _semaphore:
        return await asyncio.to_thread(func, *args, **kwargs)


async def gather_limited(
    coros: Sequence[Coroutine[Any, Any, T]],
) -> list[T]:
    """Run coroutines concurrently.

    Bounds nothing itself: the coroutines do, through run_sync_limited
    (the asyncio semaphore), and every Trac XML-RPC call is bounded again
    at ``TracClient._rpc_call``.
    Returns results in order. Exceptions propagate from the first failure.

    Args:
        coros: Sequence of coroutines to run concurrently.

    Returns:
        List of results in the same order as input coroutines.
    """
    return list(await asyncio.gather(*coros))
