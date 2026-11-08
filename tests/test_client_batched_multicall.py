"""TracClient._batched_multicall: the one batch-then-fallback loop over
``system.multicall`` that ``get_tickets`` and the wiki search's page
fetch share (Trac /trac_mcp_server #172).

The two paths had separate copies of the loop and disagreed on one case:
a multicall reply that is not a list of the expected length. Tickets
re-read that batch singly; the wiki returned ``{}``, so up to 50 pages
silently dropped out of a cache refresh. The tests below run the rules
the paths now share against BOTH, through the fakes the path's own test
file uses (``FakeTrac``, ``FakeWiki``), installed as ``_rpc_request``.
"""

import xmlrpc.client

import pytest

from tests.test_client_get_tickets import MISSING_METHOD, FakeTrac
from tests.test_wiki_search_client import FakeWiki
from trac_mcp_server.core.client import (
    _SKIP,
    TICKET_FETCH_BATCH,
    WIKI_FETCH_BATCH_PAGES,
    TracClient,
)

MULTICALL = "system.multicall"


class Path:
    """One caller of the shared loop, behind a common test surface."""

    batch_size: int
    # the single-read method whose params name the item
    single_method: str

    def __init__(self, mock_config, n: int):
        self.client = TracClient(mock_config)
        self.items = self.populate(n)
        self.calls: list[str] = []  # top-level methods, in order
        self.singles: list = []  # items read by a single call
        self.reply = None  # (calls, real results) -> results to return
        self.raise_on: dict[
            int, Exception
        ] = {}  # nth multicall -> error
        self.multicalls = 0
        real = self.fake.rpc

        def rpc(service, method, *params):
            full = f"{service}.{method}"
            self.calls.append(full)
            if full == MULTICALL:
                self.multicalls += 1
                if self.multicalls in self.raise_on:
                    raise self.raise_on[self.multicalls]
                results = real(service, method, *params)
                if self.reply is not None:
                    return self.reply(params[0], results)
                return results
            if full == self.single_method:
                self.singles.append(params[0])
            return real(service, method, *params)

        self.client._rpc_request = rpc  # type: ignore[method-assign]

    def drop_multicall_support(self):
        self.fake.multicall_enabled = False


class TicketPath(Path):
    id = "tickets"
    single_method = "ticket.get"

    def __init__(self, mock_config, n):
        self.batch_size = TICKET_FETCH_BATCH
        super().__init__(mock_config, n)

    def populate(self, n):
        ids = list(range(1, n + 1))
        self.fake = FakeTrac(ids)
        return ids

    def run(self, items):
        return self.client.get_tickets(items)

    def expected(self, items):
        return {i: FakeTrac.ticket(i) for i in items}

    def break_item(self, item):
        self.fake.failing.add(item)


class WikiPath(Path):
    id = "wiki"
    single_method = "wiki.getPage"

    def __init__(self, mock_config, n):
        self.batch_size = WIKI_FETCH_BATCH_PAGES
        super().__init__(mock_config, n)

    def populate(self, n):
        self.fake = FakeWiki()
        names = [f"P/{i:03d}" for i in range(1, n + 1)]
        for name in names:
            self.fake.put(name, f"text of {name}\n")
        return names

    def run(self, items):
        return self.client._fetch_wiki_pages(items)

    def expected(self, items):
        out = {}
        for name in items:
            page = self.fake.pages[name]
            out[name] = (
                page["version"],
                page["lastModified"],
                page["text"],
            )
        return out

    def break_item(self, item):
        self.fake.unreadable.add(item)


@pytest.fixture(params=[TicketPath, WikiPath], ids=lambda p: p.id)
def make_path(request, mock_config):
    return lambda n: request.param(mock_config, n)


# -- the case the two copies disagreed on ----------------------------


def test_wiki_batch_with_a_reply_one_entry_short_is_read_singly(
    mock_config,
):
    """Red before #172: ``_fetch_wiki_batch`` returned ``{}`` here."""
    path = WikiPath(mock_config, 3)
    path.reply = lambda calls, results: results[:-1]

    assert path.run(path.items) == path.expected(path.items)
    assert path.singles == path.items


# -- the rules both paths share --------------------------------------


@pytest.mark.parametrize(
    "malform",
    [
        lambda calls, results: "nope",
        lambda calls, results: [],
        lambda calls, results: results[:-1],
        lambda calls, results: results + results[:1],
        lambda calls, results: {"faultCode": 1},
    ],
    ids=["string", "empty", "short", "long", "dict"],
)
def test_a_malformed_reply_sends_that_batch_singly(make_path, malform):
    path = make_path(3)
    path.reply = malform

    assert path.run(path.items) == path.expected(path.items)
    assert path.singles == path.items
    assert path.client._multicall.available()


def test_a_missing_multicall_is_remembered_and_goes_singly(make_path):
    path = make_path(3)
    path.drop_multicall_support()

    assert path.run(path.items) == path.expected(path.items)
    assert path.singles == path.items
    assert path.calls.count(MULTICALL) == 1
    assert not path.client._multicall.available()

    path.calls.clear()
    path.singles.clear()
    assert path.run(path.items) == path.expected(path.items)
    assert MULTICALL not in path.calls
    assert path.singles == path.items


@pytest.mark.parametrize(
    "error",
    [
        xmlrpc.client.Fault(1, "boom"),
        xmlrpc.client.Fault(1, "no such page: system.multicall"),
        RuntimeError("connection dropped"),
        TimeoutError("timed out"),
    ],
    ids=["fault", "fault-not-missing", "runtime", "timeout"],
)
def test_another_failure_sends_that_batch_singly_and_batching_goes_on(
    make_path, error
):
    path = make_path(120)
    path.raise_on = {2: error}

    assert path.run(path.items) == path.expected(path.items)
    assert path.multicalls == 3  # batch 3 still went by multicall
    size = path.batch_size
    assert path.singles == path.items[size : 2 * size]
    assert path.client._multicall.available()


def test_a_missing_multicall_after_a_good_batch_reads_the_rest_singly(
    make_path,
):
    path = make_path(120)
    path.raise_on = {2: MISSING_METHOD}

    assert path.run(path.items) == path.expected(path.items)
    assert path.multicalls == 2  # no third multicall
    assert path.singles == path.items[path.batch_size :]
    assert not path.client._multicall.available()


def test_a_per_entry_fault_leaves_the_item_absent_without_retry(
    make_path,
):
    path = make_path(3)
    path.break_item(path.items[1])

    result = path.run(path.items)

    assert result == path.expected([path.items[0], path.items[2]])
    assert path.calls == [MULTICALL]
    assert path.singles == []


def test_an_empty_input_makes_no_rpc(make_path):
    path = make_path(3)

    assert path.run([]) == {}
    assert path.calls == []


def test_batches_are_batch_size_items_each(make_path):
    path = make_path(120)
    sizes: list[int] = []
    path.reply = lambda calls, results: (
        sizes.append(len(calls)) or results
    )

    assert path.run(path.items) == path.expected(path.items)
    per_item = sizes[0] // path.batch_size
    assert per_item in (1, 2)
    assert [s // per_item for s in sizes] == [
        path.batch_size,
        path.batch_size,
        120 - 2 * path.batch_size,
    ]
    assert path.singles == []


# -- the helper itself: a variable number of calls per item ----------


def test_the_helper_tracks_offsets_for_a_variable_call_count(
    mock_config,
):
    client = TracClient(mock_config)
    wanted = {"a": 1, "b": 3, "c": 0, "d": 2, "e": 1}
    sent: list[list[str]] = []

    def rpc(service, method, *params):
        assert (service, method) == ("system", "multicall")
        sent.append([c["methodName"] for c in params[0]])
        return [[c["methodName"]] for c in params[0]]

    client._rpc_request = rpc  # type: ignore[method-assign]
    seen: dict[str, list] = {}

    def calls_for(item):
        return [
            {"methodName": f"{item}{k}", "params": []}
            for k in range(wanted[item])
        ]

    def decode(item, values):
        seen[item] = values
        return len(values)

    out = client._batched_multicall(
        list(wanted), 2, calls_for, decode, lambda items: {}
    )

    # batches of two items: [a, b], [c, d], [e]
    assert sent == [
        ["a0", "b0", "b1", "b2"],
        ["d0", "d1"],
        ["e0"],
    ]
    assert seen["a"] == ["a0"]
    assert seen["b"] == ["b0", "b1", "b2"]
    assert seen["d"] == ["d0", "d1"]
    assert seen["e"] == ["e0"]
    assert out == {"a": 1, "b": 3, "c": 0, "d": 2, "e": 1}


def test_the_helper_skips_an_item_decode_returns_skip_for(mock_config):
    client = TracClient(mock_config)
    client._rpc_request = lambda s, m, calls: [  # type: ignore[method-assign]
        [c["params"][0]] for c in calls
    ]

    out = client._batched_multicall(
        [1, 2, 3],
        10,
        lambda i: [{"methodName": "x.y", "params": [i]}],
        lambda item, values: _SKIP if item == 2 else values[0],
        lambda items: (
            pytest.fail(f"single read of {items}") if items else {}
        ),
    )

    assert out == {1: 1, 3: 3}


def test_a_none_value_is_kept_when_decode_returns_it(mock_config):
    # only the _SKIP sentinel drops an item; None is a value
    client = TracClient(mock_config)
    client._rpc_request = lambda s, m, calls: [  # type: ignore[method-assign]
        [None] for c in calls
    ]

    out = client._batched_multicall(
        [1, 2],
        10,
        lambda i: [{"methodName": "x.y", "params": [i]}],
        lambda item, values: values[0],
        lambda items: (
            pytest.fail(f"single read of {items}") if items else {}
        ),
    )

    assert out == {1: None, 2: None}


def test_a_ticket_whose_multicall_value_is_none_is_kept(mock_config):
    # as the single path (``_fetch_tickets_singly``) does
    trac = FakeTrac([1])
    trac.multicall_result = [[None]]
    client = TracClient(mock_config)
    client._rpc_request = trac.rpc  # type: ignore[method-assign]

    assert client.get_tickets([1]) == {1: None}
    assert trac.singles == []


def test_a_raise_inside_fetch_singly_does_not_read_singly_twice(
    mock_config,
):
    client = TracClient(mock_config)
    # A reply of the wrong length (malformed).
    client._rpc_request = lambda s, m, calls: []  # type: ignore[method-assign]
    attempts: list[list[int]] = []

    def fetch_singly(items):
        attempts.append(list(items))
        raise RuntimeError("single read blew up")

    with pytest.raises(RuntimeError, match="blew up"):
        client._batched_multicall(
            [1, 2],
            10,
            lambda i: [{"methodName": "x.y", "params": [i]}],
            lambda item, values: values[0],
            fetch_singly,
        )

    assert attempts == [[1, 2]]
