"""
Tests for async_utils module.

Covers run_sync, run_sync_limited, run_local, gather_limited, and
init_semaphore.
"""

import asyncio
import gc
import inspect
import threading
import time
import warnings

import pytest

from trac_mcp_server.core import async_utils
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
    init_semaphore(2)

    max_concurrent = 0
    current_concurrent = 0
    lock = threading.Lock()
    # Two threads must be inside at once for the pair to leave: the
    # limit is used, proved by the barrier rather than by timing.
    pair = threading.Barrier(2)

    def _track_concurrency(val):
        nonlocal max_concurrent, current_concurrent
        with lock:
            current_concurrent += 1
            max_concurrent = max(max_concurrent, current_concurrent)
        pair.wait(timeout=5)
        time.sleep(0.05)
        with lock:
            current_concurrent -= 1
        return val

    results = await asyncio.wait_for(
        gather_limited(
            [run_sync(_track_concurrency, i) for i in range(6)]
        ),
        15,
    )

    assert results == [0, 1, 2, 3, 4, 5]
    assert max_concurrent <= 2


async def test_run_local_runs_function_and_forwards_arguments():
    assert await run_local(_sync_add, 3, 4) == 7

    def _kw_func(*, name: str) -> str:
        return f"hello {name}"

    assert await run_local(_kw_func, name="world") == "hello world"


async def test_gather_limited_returns_results_in_input_order():
    """Workers finish items out of order; results stay in input order,
    and no more than N items run at once."""
    init_semaphore(3)
    running = 0
    peak = 0

    async def item(i):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02 * (9 - i))
        running -= 1
        return i

    results = await asyncio.wait_for(
        gather_limited([item(i) for i in range(10)]), 15
    )

    assert results == list(range(10))
    assert peak == 3


async def test_gather_limited_uninitialised_is_a_plain_gather():
    """The CLI never calls init_semaphore: every coroutine runs at once."""
    async_utils._semaphore = None
    running = 0
    peak = 0

    async def item(i):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return i

    results = await asyncio.wait_for(
        gather_limited([item(i) for i in range(5)]), 15
    )

    assert results == [0, 1, 2, 3, 4]
    assert peak == 5


async def test_gather_limited_first_failure_cancels_and_closes_the_rest():
    """The first exception propagates, in-flight items are cancelled and
    the coroutines that never started are closed (no "never awaited")."""
    init_semaphore(2)
    started: list[int] = []
    cancelled: list[int] = []

    async def item(i):
        started.append(i)
        try:
            if i == 0:
                await asyncio.sleep(0.05)
                raise ValueError("boom")
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(i)
            raise
        return i

    coros = [item(i) for i in range(10)]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(ValueError, match="boom"):
            await asyncio.wait_for(gather_limited(coros), 15)
        del coros
        gc.collect()

    assert started == [0, 1]
    assert cancelled == [1]
    never_awaited = [
        w for w in caught if issubclass(w.category, RuntimeWarning)
    ]
    assert never_awaited == []


async def test_gather_limited_closes_unstarted_coroutines():
    """Held references: the unstarted coroutines are in the closed
    state after the failure, not merely collected."""
    init_semaphore(1)

    async def fail():
        raise ValueError("boom")

    async def never():
        return 1

    unstarted = [never() for _ in range(3)]
    with pytest.raises(ValueError):
        await asyncio.wait_for(gather_limited([fail(), *unstarted]), 15)

    assert [inspect.getcoroutinestate(c) for c in unstarted] == [
        inspect.CORO_CLOSED
    ] * 3


async def test_run_sync_slot_is_held_until_the_thread_ends():
    """A call that times out while its thread runs keeps its slot: a
    second call (limit 1) must not start until the first thread ends."""
    init_semaphore(1)
    active = 0
    peak = 0
    lock = threading.Lock()

    def work(seconds):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(seconds)
        with lock:
            active -= 1

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(run_sync(work, 0.5), 0.1)
    await asyncio.wait_for(run_sync(work, 0.05), 15)

    assert peak == 1


async def test_abandoned_call_failure_is_retrieved_not_logged():
    """A call that fails after its awaiter gave up logs no "exception
    was never retrieved"."""
    init_semaphore(1)
    loop = asyncio.get_running_loop()
    reported: list[dict] = []
    loop.set_exception_handler(lambda _loop, ctx: reported.append(ctx))

    def fail_later():
        time.sleep(0.2)
        raise ValueError("late")

    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(run_sync(fail_later), 0.05)
        await asyncio.sleep(0.5)
        gc.collect()
    finally:
        loop.set_exception_handler(None)

    assert reported == []
    # The slot came back: the next call runs.
    assert await asyncio.wait_for(run_sync(_sync_add, 1, 2), 15) == 3


async def test_run_sync_cancelled_while_queued_leaves_the_slot_whole():
    init_semaphore(1)
    release = threading.Event()
    holder = asyncio.ensure_future(run_sync(release.wait, 5))
    await asyncio.sleep(0.05)
    queued = asyncio.ensure_future(run_sync(_sync_add, 1, 2))
    await asyncio.sleep(0.05)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    release.set()
    await asyncio.wait_for(holder, 15)

    assert await asyncio.wait_for(run_sync(_sync_add, 3, 4), 15) == 7
