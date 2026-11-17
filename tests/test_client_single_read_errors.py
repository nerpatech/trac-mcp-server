"""The one-call-per-item fallback of ``_batched_multicall`` skips an item
only on an XML-RPC ``Fault`` (Trac /trac_mcp_server #238).

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

import pytest
import requests

from tests.test_client_get_milestones import FakeTrac as FakeMilestones
from tests.test_client_get_tickets import FakeTrac as FakeTickets
from tests.test_wiki_search_client import FakeWiki
from trac_mcp_server.core.client import TracClient
from trac_mcp_server.mcp.server import (
    PING_SPEC,
    handle_call_tool,
    set_registry,
)
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import ToolRegistry

MULTICALL = "system.multicall"


def http_401() -> requests.HTTPError:
    response = requests.Response()
    response.status_code = 401
    response.reason = "Unauthorized"
    return requests.HTTPError(
        "401 Client Error: Unauthorized", response=response
    )


class Path:
    """One reader, behind a common test surface.

    ``first_single`` is the single-read method that starts an item's
    read, so counting its calls counts the items attempted singly.
    """

    first_single: str

    def __init__(self, mock_config, n: int = 3):
        self.client = TracClient(mock_config)
        self.items = self.populate(n)
        self.multicall_error: Exception | None = None
        # item -> error raised by its first single read
        self.single_errors: dict = {}
        # error raised by the first single read of any item
        self.every_single_error: Exception | None = None
        self.single_calls: list = []  # items whose single read began
        real = self.fake.rpc

        def rpc(service, method, *params):
            full = f"{service}.{method}"
            if full == MULTICALL and self.multicall_error is not None:
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
    [
        requests.ConnectionError("connection dropped"),
        requests.Timeout("read timed out"),
    ],
    ids=["connection_error", "timeout"],
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


def test_an_unparseable_single_reply_propagates(path):
    from xml.etree.ElementTree import ParseError

    path.multicall_error = requests.Timeout("read timed out")
    path.every_single_error = ParseError("not well-formed")

    with pytest.raises(ParseError):
        path.run()

    assert len(path.single_calls) == 1


def test_a_fault_in_a_single_read_leaves_only_that_item_out(path):
    path.multicall_error = requests.ConnectionError("dropped")
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
    assert single_calls == ["v1"]
