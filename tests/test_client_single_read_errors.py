"""The one-call-per-item fallback of ``_batched_multicall`` lets a
transport or HTTP error out and skips any other per-item failure (Trac
/trac_mcp_server #238).

The three single readers behind ``get_tickets``, ``get_milestones`` and
``_fetch_wiki_pages`` caught ``Exception`` per item and skipped it. After
a multicall failed for a transport reason (a dropped connection, a
timeout, a lost login), a read of N items made N single calls, each
waiting out ``rpc_timeout``, and returned success with every item absent:
``milestone_list`` answered ``0 milestone(s) (open): none``.

Each case here runs against all three readers, through the fakes the
paths' own test files use, wrapped so a chosen error comes out of
``system.multicall`` and of the single read.
"""

import asyncio
import xmlrpc.client
from unittest.mock import patch
from xml.parsers.expat import ExpatError

import pytest
import requests

from tests.test_client_get_milestones import FakeTrac as FakeMilestones
from tests.test_client_get_tickets import FakeTrac as FakeTickets
from tests.test_wiki_search_client import FakeWiki
from trac_mcp_server.core import client as client_module
from trac_mcp_server.core.client import TracClient
from trac_mcp_server.mcp.server import (
    PING_SPEC,
    handle_call_tool,
    set_registry,
)
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import ToolRegistry

MULTICALL = "system.multicall"


def http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.reason = "Error"
    return requests.HTTPError(
        f"{status} Error: Error", response=response
    )


def http_401() -> requests.HTTPError:
    return http_error(401)


class Path:
    """One reader, behind a common test surface.

    ``first_single`` is the single-read method that starts an item's
    read, so counting its calls counts the items attempted singly.
    """

    first_single: str
    batch_const: str  # the module constant holding the batch size

    def __init__(self, mock_config, n: int = 3):
        self.client = TracClient(mock_config)
        self.items = self.populate(n)
        self.multicall_error: Exception | None = None
        # 1-based multicall number -> error raised by that multicall
        self.multicall_errors_by_call: dict[int, Exception] = {}
        self.multicall_calls = 0
        # item -> error raised by its first single read
        self.single_errors: dict = {}
        # error raised by the first single read of any item
        self.every_single_error: Exception | None = None
        self.single_calls: list = []  # items whose single read began
        real = self.fake.rpc

        def rpc(service, method, *params):
            full = f"{service}.{method}"
            if full == MULTICALL:
                self.multicall_calls += 1
                if (
                    self.multicall_calls
                    in self.multicall_errors_by_call
                ):
                    raise self.multicall_errors_by_call[
                        self.multicall_calls
                    ]
                if self.multicall_error is not None:
                    raise self.multicall_error
            if full == self.first_single:
                self.single_calls.append(params[0])
                if self.every_single_error is not None:
                    raise self.every_single_error
                if params[0] in self.single_errors:
                    raise self.single_errors[params[0]]
            return real(service, method, *params)

        self.client._rpc_request = rpc  # type: ignore[method-assign]


class TicketPath(Path):
    id = "tickets"
    first_single = "ticket.get"
    batch_const = "TICKET_FETCH_BATCH"

    def populate(self, n):
        ids = list(range(1, n + 1))
        self.fake = FakeTickets(ids)
        return ids

    def run(self):
        return self.client.get_tickets(self.items)

    def expected(self, items):
        return {i: FakeTickets.ticket(i) for i in items}


class MilestonePath(Path):
    id = "milestones"
    first_single = "ticket.milestone.get"
    batch_const = "MILESTONE_FETCH_BATCH"

    def populate(self, n):
        names = [f"v{i}" for i in range(1, n + 1)]
        self.fake = FakeMilestones(names)
        return names

    def run(self):
        return self.client.get_milestones(self.items)

    def expected(self, items):
        return {n: FakeMilestones.milestone(n) for n in items}


class WikiPath(Path):
    id = "wiki"
    first_single = "wiki.getPageInfo"
    batch_const = "WIKI_FETCH_BATCH_PAGES"

    def populate(self, n):
        self.fake = FakeWiki()
        names = [f"P/{i:03d}" for i in range(1, n + 1)]
        for name in names:
            self.fake.put(name, f"text of {name}\n")
        return names

    def run(self):
        return self.client._fetch_wiki_pages(self.items)

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


@pytest.fixture(
    params=[TicketPath, MilestonePath, WikiPath], ids=lambda p: p.id
)
def path(request, mock_config):
    return request.param(mock_config)


@pytest.mark.parametrize(
    "multicall_error",
    [requests.Timeout("read timed out"), http_error(500)],
    ids=["timeout", "http_500"],
)
@pytest.mark.parametrize(
    "single_error",
    [requests.ConnectionError("still down"), http_401()],
    ids=["connection_error", "http_401"],
)
def test_a_transport_or_http_error_in_a_single_read_propagates_after_one_call(
    path, multicall_error, single_error
):
    path.multicall_error = multicall_error
    path.every_single_error = single_error

    with pytest.raises(type(single_error)):
        path.run()

    assert len(path.single_calls) == 1


@pytest.mark.parametrize(
    "item_error",
    [
        ExpatError("not well-formed: line 1, column 0"),
        IndexError("list index out of range"),
        ValueError("bad value"),
    ],
    ids=["expat_error", "index_error", "value_error"],
)
def test_a_non_transport_error_on_one_item_skips_only_that_item(
    path, item_error
):
    # e.g. a page holding a control character xmlrpc emits raw and
    # xmlrpc.client.loads rejects: one item's problem, not the read's.
    path.multicall_error = requests.Timeout("read timed out")
    bad = path.items[1]
    path.single_errors = {bad: item_error}

    got = path.run()

    assert got == path.expected([i for i in path.items if i != bad])
    assert path.single_calls == path.items


def test_a_later_batch_failing_after_earlier_ones_succeeded_raises(
    path, monkeypatch
):
    monkeypatch.setattr(client_module, path.batch_const, 2)
    # batches: [items 0, 1] succeeds, [item 2] times out; its single
    # read is refused too.
    path.multicall_errors_by_call = {
        2: requests.Timeout("read timed out")
    }
    path.single_errors = {
        path.items[2]: requests.ConnectionError("down")
    }

    with pytest.raises(requests.ConnectionError):
        path.run()

    assert path.multicall_calls == 2
    assert path.single_calls == [path.items[2]]


@pytest.mark.parametrize(
    "multicall_error",
    [
        requests.ConnectionError("connection dropped"),
        requests.ConnectTimeout("connect timed out"),
        http_error(401),
        http_error(403),
    ],
    ids=["connection_error", "connect_timeout", "http_401", "http_403"],
)
def test_a_global_multicall_error_raises_without_any_single_read(
    path, multicall_error
):
    path.multicall_error = multicall_error

    with pytest.raises(type(multicall_error)):
        path.run()

    assert path.single_calls == []


@pytest.mark.parametrize(
    "multicall_error",
    [
        requests.Timeout("read timed out"),
        http_error(413),
        http_error(500),
        http_error(502),
        ExpatError("not well-formed: line 1, column 0"),
        xmlrpc.client.Fault(1, "some other fault"),
    ],
    ids=[
        "read_timeout",
        "http_413",
        "http_500",
        "http_502",
        "expat_error",
        "fault",
    ],
)
def test_any_other_multicall_error_still_falls_back_to_single_reads(
    path, multicall_error
):
    path.multicall_error = multicall_error

    assert path.run() == path.expected(path.items)
    assert path.single_calls == path.items


def test_a_fault_in_a_single_read_leaves_only_that_item_out(path):
    path.multicall_error = requests.Timeout("dropped")
    broken = path.items[1]
    path.single_errors = {
        broken: xmlrpc.client.Fault(1, "not viewable")
    }

    got = path.run()

    assert got == path.expected([i for i in path.items if i != broken])
    assert path.single_calls == path.items


def test_a_multicall_timeout_with_healthy_single_reads_recovers_every_item(
    path,
):
    path.multicall_error = requests.Timeout("read timed out")

    assert path.run() == path.expected(path.items)
    assert path.single_calls == path.items


def test_a_deleted_page_leaves_the_wiki_index_cleared_when_the_fetch_raises(
    mock_config,
):
    from tests.test_wiki_search_client import make_client, names

    wiki = FakeWiki()
    wiki.put("Alpha", "alpha apple text\n")
    wiki.put("Beta", "beta banana text\n")
    wiki.put("Gamma", "gamma cherry text\n")
    client = make_client(mock_config, wiki)
    assert len(client.search_wiki_pages("text")) == 3  # index built

    wiki.delete("Beta")
    wiki.put("Delta", "delta date text\n")  # new: must be fetched
    wiki.unreadable.add(
        "Delta"
    )  # ...and, once the server is back, a Fault
    down = requests.ConnectionError("connection dropped")

    def rpc(service, method, *params):
        if f"{service}.{method}" == "wiki.getRecentChanges":
            return wiki.rpc(service, method, *params)
        raise down

    client._rpc_request = rpc  # type: ignore[method-assign]
    with pytest.raises(requests.ConnectionError):
        client.search_wiki_pages("text")

    # The server is back. Delta cannot be read (a Fault), so this refresh
    # changes nothing: only a cleared index keeps Beta out.
    client._rpc_request = wiki.rpc  # type: ignore[method-assign]
    assert names(client.search_wiki_pages("text")) == ["Alpha", "Gamma"]


# -- through a tool ----------------------------------------------------


def test_milestone_list_answers_an_error_when_the_connection_is_down(
    mock_config,
):
    names = ["v1", "v2", "v3"]
    client = TracClient(mock_config)
    single_calls: list = []

    def rpc(service, method, *params):
        full = f"{service}.{method}"
        if full == "ticket.milestone.getAll":
            return names
        if full == "ticket.milestone.get":
            single_calls.append(params[0])
        raise requests.ConnectionError("connection dropped")

    client._rpc_request = rpc  # type: ignore[method-assign]
    set_registry(ToolRegistry([PING_SPEC] + ALL_SPECS))
    try:
        with patch(
            "trac_mcp_server.mcp.server.get_instances"
        ) as instances:
            instances.return_value.get_client.return_value = client
            result = asyncio.run(
                handle_call_tool("milestone_list", {"status": "open"})
            )
    finally:
        set_registry(None)

    assert result.isError
    text = result.content[0].text
    assert "server_error" in text
    assert "connection dropped" in text
    assert "0 milestone(s)" not in text
    assert single_calls == []
