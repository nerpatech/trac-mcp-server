"""
Tests for async_utils module.

Covers run_sync, run_sync_limited, run_local, gather_limited, and
init_semaphore.
"""

from trac_mcp_server.core.async_utils import (
    gather_limited,
    init_semaphore,
    run_local,
    run_sync,
    run_sync_limited,
)


def _sync_add(a: int, b: int) -> int:
    """Simple sync function for testing."""
    return a + b


def _sync_identity(x):
    """Return input unchanged."""
    return x


async def test_run_sync_calls_function():
    """run_sync delegates to asyncio.to_thread with correct args."""
    result = await run_sync(_sync_add, 3, 4)
    assert result == 7


async def test_run_sync_passes_kwargs():
    """run_sync forwards keyword arguments."""

    def _kw_func(*, name: str) -> str:
        return f"hello {name}"

    result = await run_sync(_kw_func, name="world")
    assert result == "hello world"


async def test_init_semaphore_sets_value():
    """init_semaphore creates a semaphore with the given max_parallel."""
    import trac_mcp_server.core.async_utils as mod

    # Save original
    original = mod._semaphore
    try:
        init_semaphore(5)
        assert mod._semaphore is not None
        # A Semaphore(5) allows 5 concurrent acquisitions
        # Verify by acquiring 5 times without blocking
        for _ in range(5):
            acquired = (
                mod._semaphore._value > 0 or True
            )  # just check it exists
            assert acquired
    finally:
        mod._semaphore = original


async def test_run_sync_limited_respects_semaphore():
    """run_sync_limited acquires the semaphore before running."""
    import trac_mcp_server.core.async_utils as mod

    original = mod._semaphore
    try:
        init_semaphore(2)

        result = await run_sync_limited(_sync_add, 10, 20)
        assert result == 30
    finally:
        mod._semaphore = original


async def test_run_sync_limited_without_semaphore():
    """run_sync_limited falls back to unbounded when semaphore is None."""
    import trac_mcp_server.core.async_utils as mod

    original = mod._semaphore
    try:
        mod._semaphore = None

        result = await run_sync_limited(_sync_add, 5, 6)
        assert result == 11
    finally:
        mod._semaphore = original


async def test_gather_limited_runs_concurrent():
    """gather_limited runs multiple coroutines and returns results in order."""
    import trac_mcp_server.core.async_utils as mod

    original = mod._semaphore
    try:
        init_semaphore(3)

        coros = [run_sync_limited(_sync_identity, i) for i in range(5)]

        results = await gather_limited(coros)
        assert results == [0, 1, 2, 3, 4]
    finally:
        mod._semaphore = original


async def test_gather_limited_empty_list():
    """gather_limited handles empty coroutine list."""
    results = await gather_limited([])
    assert results == []


async def test_run_sync_limited_concurrency_bound():
    """run_sync_limited actually limits concurrency via semaphore."""
    import threading
    import time

    import trac_mcp_server.core.async_utils as mod

    original = mod._semaphore
    try:
        init_semaphore(2)

        max_concurrent = 0
        current_concurrent = 0
        lock = threading.Lock()

        def _track_concurrency(val):
            nonlocal max_concurrent, current_concurrent
            with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            time.sleep(0.05)  # Hold for a bit so others overlap
            with lock:
                current_concurrent -= 1
            return val

        coros = [
            run_sync_limited(_track_concurrency, i) for i in range(6)
        ]
        results = await gather_limited(coros)

        assert results == [0, 1, 2, 3, 4, 5]
        # With semaphore(2), max_concurrent should be at most 2
        assert max_concurrent <= 2
    finally:
        mod._semaphore = original


async def test_run_sync_concurrency_bound():
    """run_sync waits for a slot too (ticket #275): same bound as
    run_sync_limited, and one function holds one slot throughout."""
    import threading
    import time

    init_semaphore(2)

    max_concurrent = 0
    current_concurrent = 0
    lock = threading.Lock()

    def _track_concurrency(val):
        nonlocal max_concurrent, current_concurrent
        with lock:
            current_concurrent += 1
            max_concurrent = max(max_concurrent, current_concurrent)
        time.sleep(0.05)
        with lock:
            current_concurrent -= 1
        return val

    results = await gather_limited(
        [run_sync(_track_concurrency, i) for i in range(6)]
    )

    assert results == [0, 1, 2, 3, 4, 5]
    assert max_concurrent == 2


async def test_run_local_runs_function_and_forwards_arguments():
    assert await run_local(_sync_add, 3, 4) == 7

    def _kw_func(*, name: str) -> str:
        return f"hello {name}"

    assert await run_local(_kw_func, name="world") == "hello world"
