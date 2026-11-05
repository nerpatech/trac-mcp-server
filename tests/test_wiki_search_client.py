"""wiki_search through a real TracClient and the real handler, against a
fake wiki (Trac /trac_mcp_server #154).

``FakeWiki`` stands in for the XML-RPC endpoint: it is installed as the
client's ``_rpc_request``, so everything above the transport is the real
code -- the page cache, the multicall batching and its fallback, the
BM25 index, the handler's pagination. It records what was asked, which
is how the cache tests tell a page that was served from memory from one
that was fetched again.
"""

import asyncio
import re
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
import requests

from trac_mcp_server.core import client as client_module
from trac_mcp_server.core.client import TracClient
from trac_mcp_server.mcp.tools.wiki_read import _handle_search

_EPOCH = datetime(2026, 1, 1)


class FakeWiki:
    """An in-memory Trac wiki behind the XML-RPC methods the search uses.

    ``lastModified`` is Trac's own clock: one tick per write, as the
    ``YYYYMMDDTHH:MM:SS`` string XML-RPC carries.
    """

    def __init__(self, multicall: bool = True, multicall_fault=None):
        self.pages: dict[str, dict] = {}
        self.order: list[str] = []
        self.clock = 0
        self.multicall_enabled = multicall
        self.multicall_fault = multicall_fault
        self.unreadable: set[str] = set()
        self.recent_enabled = True
        self.no_version = False  # leave `version` out of every info
        self.calls: list[str] = []  # top-level methods, in order
        self.fetched: list[str] = []  # names read with getPage
        self.infos: list[str] = []  # names read with getPageInfo

    def stamp(self) -> str:
        self.clock += 1
        t = _EPOCH + timedelta(seconds=self.clock)
        return t.strftime("%Y%m%dT%H:%M:%S")

    def put(self, name: str, text: str) -> None:
        held = self.pages.get(name)
        if held is None:
            self.order.append(name)
        self.pages[name] = {
            "version": held["version"] + 1 if held else 1,
            "text": text,
            "lastModified": self.stamp(),
        }

    def delete(self, name: str) -> None:
        del self.pages[name]
        self.order.remove(name)

    def forget_calls(self) -> None:
        self.calls.clear()
        self.fetched.clear()
        self.infos.clear()

    # the transport ---------------------------------------------------
    def rpc(self, service: str, method: str, *params):
        full = f"{service}.{method}"
        self.calls.append(full)
        if full == "system.multicall":
            if not self.multicall_enabled:
                raise (
                    self.multicall_fault
                    or xmlrpc.client.Fault(
                        -32601,
                        'RPC method "system.multicall" not found',
                    )
                )
            out: list = []
            for call in params[0]:
                try:
                    value = self._dispatch(
                        call["methodName"], *call["params"]
                    )
                    out.append([value])
                except xmlrpc.client.Fault as err:
                    out.append(
                        {
                            "faultCode": err.faultCode,
                            "faultString": err.faultString,
                        }
                    )
            return out
        return self._dispatch(full, *params)

    def _info(self, name: str) -> dict:
        page = self.pages[name]
        info = {
            "name": name,
            "author": "tester",
            "lastModified": page["lastModified"],
        }
        if not self.no_version:
            info["version"] = page["version"]
        return info

    def _dispatch(self, full: str, *params):
        if full == "wiki.getAllPages":
            return list(self.order)
        if full == "wiki.getPageInfo":
            name = params[0]
            self.infos.append(name)
            if name in self.unreadable or name not in self.pages:
                raise xmlrpc.client.Fault(
                    1, f"Page {name} not readable"
                )
            return self._info(name)
        if full == "wiki.getPage":
            name = params[0]
            self.fetched.append(name)
            if name in self.unreadable or name not in self.pages:
                raise xmlrpc.client.Fault(
                    1, f"Page {name} not readable"
                )
            return self.pages[name]["text"]
        if full == "wiki.getRecentChanges":
            if not self.recent_enabled:
                raise xmlrpc.client.Fault(
                    -32601,
                    'RPC method "wiki.getRecentChanges" not found',
                )
            since = params[0].value
            return [
                self._info(n)
                for n, p in self.pages.items()
                if p["lastModified"] >= since
            ]
        raise xmlrpc.client.Fault(
            -32601, f'RPC method "{full}" not found'
        )


def make_client(mock_config, wiki: FakeWiki) -> TracClient:
    client = TracClient(mock_config)
    client._rpc_request = wiki.rpc  # type: ignore[method-assign]
    return client


def names(hits) -> list[str]:
    return [h["name"] for h in hits]


def search(client, query, **args):
    return asyncio.run(_handle_search(client, {"query": query, **args}))


def shown_names(result) -> list[str]:
    return re.findall(r"^\*\*(.+?)\*\*", result.content[0].text, re.M)


def cursor_of(result) -> str | None:
    m = re.search(r"Use cursor '([^']+)'", result.content[0].text)
    return m.group(1) if m else None


# --------------------------------------------------------------------
# The three replications from the ticket's description
# --------------------------------------------------------------------


@pytest.fixture
def store_wiki():
    """30 pages that all hold ``Source:``. The 20 ``Misc/`` pages come
    first in ``getAllPages`` order, the 10 ``Rules/`` pages after."""
    wiki = FakeWiki()
    for i in range(20):
        wiki.put(f"Misc/Page{i:02d}", f"Notes {i}.\nSource: misc {i}\n")
    for i in range(10):
        wiki.put(f"Rules/Rule{i:02d}", f"Rule {i}.\nSource: rule {i}\n")
    return wiki


def test_prefix_pages_after_the_tenth_match_are_still_found(
    mock_config, store_wiki
):
    """(a) The old code stopped at the first 10 matches in list order and
    only then applied ``prefix``: every ``Rules/`` page came later."""
    client = make_client(mock_config, store_wiki)
    result = search(client, "Source:", prefix="Rules/", limit=50)
    assert not result.isError
    assert "Found 10 wiki pages" in result.content[0].text
    assert all(n.startswith("Rules/") for n in shown_names(result))
    assert len(shown_names(result)) == 10


def test_limit_50_returns_more_than_10_and_the_cursor_continues(
    mock_config, store_wiki
):
    """(b) ``limit=50`` was capped at 10 and no second page existed."""
    client = make_client(mock_config, store_wiki)

    everything = search(client, "Source:", limit=50)
    all_names = shown_names(everything)
    assert len(all_names) == 30
    assert "Found 30 wiki pages" in everything.content[0].text
    assert cursor_of(everything) is None

    first = search(client, "Source:", limit=10)
    second = search(
        client, "Source:", limit=10, cursor=cursor_of(first)
    )
    third = search(
        client, "Source:", limit=10, cursor=cursor_of(second)
    )
    pages = [shown_names(r) for r in (first, second, third)]
    assert [len(p) for p in pages] == [10, 10, 10]
    assert cursor_of(third) is None
    # the second page continues the first: no overlap, same order
    assert set(pages[0]).isdisjoint(pages[1])
    assert pages[0] + pages[1] + pages[2] == all_names


def test_a_multi_word_question_matches_words_that_are_not_adjacent(
    mock_config,
):
    """(c) The old code looked for the whole query as one substring."""
    wiki = FakeWiki()
    wiki.put(
        "Reference/trac/StatusChanges",
        "= Dropped updates =\nA status change made through ticket_update"
        " can be silently ignored when the action is not an"
        " allowed transition.\n",
    )
    wiki.put("Reference/trac/Other", "Nothing about that here.\n")
    client = make_client(mock_config, wiki)
    result = search(client, "why was my status change ignored")
    assert shown_names(result) == ["Reference/trac/StatusChanges"]


def test_no_match_keeps_the_two_not_found_messages(mock_config):
    wiki = FakeWiki()
    wiki.put("A", "alpha\n")
    client = make_client(mock_config, wiki)
    plain = search(client, "zzzzzz")
    assert (
        plain.content[0].text == "No wiki pages found matching query."
    )
    prefixed = search(client, "zzzzzz", prefix="Rules/")
    assert prefixed.content[0].text == (
        "No wiki pages found matching query with prefix 'Rules/'."
    )


def test_each_hit_shows_its_score_and_a_stored_snippet(mock_config):
    wiki = FakeWiki()
    text = "Intro.\nThe needle is in this '''bold''' line.\n"
    wiki.put("Page", text)
    client = make_client(mock_config, wiki)
    [hit] = client.search_wiki_pages("needle")
    assert set(hit) == {"name", "score", "snippet"}
    out = search(client, "needle").content[0].text
    assert f"**Page** (score {hit['score']:.2f})" in out
    assert "The needle is in this '''bold''' line." in out
    assert hit["snippet"] in text


def test_prefix_is_ranked_as_if_the_wiki_held_only_those_pages(
    mock_config,
):
    """idf and the average length are taken over the pages in scope."""
    scoped = FakeWiki()
    scoped.put("Rules/A", "needle one\n")
    scoped.put("Rules/B", "needle two three\n")
    full = FakeWiki()
    full.put("Rules/A", "needle one\n")
    full.put("Rules/B", "needle two three\n")
    for i in range(15):
        full.put(f"Other/{i}", f"filler words number {i} and more\n")
    a = make_client(mock_config, scoped).search_wiki_pages("needle")
    b = make_client(mock_config, full).search_wiki_pages(
        "needle", prefix="Rules/"
    )
    assert [(h["name"], h["score"]) for h in a] == [
        (h["name"], h["score"]) for h in b
    ]


# --------------------------------------------------------------------
# The page cache
# --------------------------------------------------------------------


@pytest.fixture
def three_pages():
    wiki = FakeWiki()
    wiki.put("Alpha", "alpha apple text\n")
    wiki.put("Beta", "beta banana text\n")
    wiki.put("Gamma", "gamma cherry text\n")
    return wiki


def test_first_search_reads_every_page_once_in_multicall_batches(
    mock_config,
):
    wiki = FakeWiki()
    for i in range(120):
        wiki.put(f"P/{i:03d}", f"word{i} common\n")
    client = make_client(mock_config, wiki)
    assert len(client.search_wiki_pages("common")) == 120
    assert (
        wiki.calls
        == ["wiki.getRecentChanges"] + ["system.multicall"] * 3
    )
    assert sorted(wiki.fetched) == sorted(wiki.pages)


def test_an_unchanged_page_is_not_fetched_again(
    mock_config, three_pages
):
    client = make_client(mock_config, three_pages)
    client.search_wiki_pages("text")
    three_pages.forget_calls()
    hits = client.search_wiki_pages("banana")
    assert names(hits) == ["Beta"]
    assert three_pages.fetched == []
    assert three_pages.infos == []
    assert three_pages.calls == ["wiki.getRecentChanges"]


def test_an_edited_page_is_fetched_again_and_the_old_text_is_gone(
    mock_config, three_pages
):
    client = make_client(mock_config, three_pages)
    assert names(client.search_wiki_pages("banana")) == ["Beta"]
    three_pages.put("Beta", "beta mango text\n")
    three_pages.forget_calls()

    assert names(client.search_wiki_pages("mango")) == ["Beta"]
    assert client.search_wiki_pages("banana") == []
    assert three_pages.fetched == ["Beta"]  # once, for both searches


def test_a_deleted_page_is_dropped(mock_config, three_pages):
    client = make_client(mock_config, three_pages)
    assert len(client.search_wiki_pages("text")) == 3
    three_pages.delete("Beta")
    assert names(client.search_wiki_pages("text")) == ["Alpha", "Gamma"]


def test_a_new_page_is_added(mock_config, three_pages):
    client = make_client(mock_config, three_pages)
    assert len(client.search_wiki_pages("text")) == 3
    three_pages.put("Delta", "delta date text\n")
    three_pages.forget_calls()
    assert "Delta" in names(client.search_wiki_pages("date"))
    assert three_pages.fetched == ["Delta"]


def test_the_index_is_rebuilt_only_when_something_changed(
    mock_config, three_pages, monkeypatch
):
    built: list[int] = []
    real = client_module.BM25

    def counting(sections, *a, **kw):
        built.append(len(sections))
        return real(sections, *a, **kw)

    monkeypatch.setattr(client_module, "BM25", counting)
    client = make_client(mock_config, three_pages)
    client.search_wiki_pages("text")
    client.search_wiki_pages("banana")
    client.search_wiki_pages("text", prefix="A")
    client.search_wiki_pages("text", prefix="A")
    # one build for the whole wiki, one for prefix "A"
    assert built == [3, 1]
    three_pages.put("Alpha", "alpha pear text\n")
    client.search_wiki_pages("text", prefix="A")
    assert built == [3, 1, 1]


def test_without_multicall_pages_are_read_one_call_each(
    mock_config, three_pages
):
    three_pages.multicall_enabled = False
    client = make_client(mock_config, three_pages)
    assert names(client.search_wiki_pages("text")) == [
        "Alpha",
        "Beta",
        "Gamma",
    ]
    assert (
        three_pages.calls.count("system.multicall") == 1
    )  # tried once
    assert three_pages.fetched == ["Alpha", "Beta", "Gamma"]
    assert three_pages.infos == ["Alpha", "Beta", "Gamma"]


@pytest.mark.parametrize(
    "fault",
    [
        xmlrpc.client.Fault(-32601, "method not found"),
        xmlrpc.client.Fault(1, "no such method: system.multicall"),
    ],
)
def test_a_missing_multicall_is_remembered(
    mock_config, three_pages, fault
):
    three_pages.multicall_enabled = False
    three_pages.multicall_fault = fault
    client = make_client(mock_config, three_pages)
    client.search_wiki_pages("text")
    three_pages.put("Delta", "delta date text\n")
    assert "Delta" in names(client.search_wiki_pages("date"))
    assert three_pages.calls.count("system.multicall") == 1


def test_one_failed_page_in_a_multicall_is_skipped(
    mock_config, three_pages
):
    three_pages.unreadable.add("Beta")
    client = make_client(mock_config, three_pages)
    assert names(client.search_wiki_pages("text")) == ["Alpha", "Gamma"]
    assert three_pages.calls.count("system.multicall") == 1


def test_a_failed_page_is_skipped_without_multicall_too(
    mock_config, three_pages
):
    three_pages.multicall_enabled = False
    three_pages.unreadable.add("Beta")
    client = make_client(mock_config, three_pages)
    assert names(client.search_wiki_pages("text")) == ["Alpha", "Gamma"]


def test_a_page_that_could_not_be_read_is_tried_again_later(
    mock_config, three_pages
):
    three_pages.unreadable.add("Beta")
    client = make_client(mock_config, three_pages)
    assert "Beta" not in names(client.search_wiki_pages("text"))
    three_pages.unreadable.clear()
    assert "Beta" in names(client.search_wiki_pages("text"))


def test_an_edit_in_the_same_second_as_the_newest_is_still_seen(
    mock_config, three_pages
):
    """Versions are compared, not times: an edit that carries the very
    same ``lastModified`` as the newest one already seen is not lost."""
    client = make_client(mock_config, three_pages)
    client.search_wiki_pages("text")
    stamp = three_pages.pages["Gamma"]["lastModified"]
    three_pages.pages["Gamma"] = {
        "version": 2,
        "text": "gamma plum text\n",
        "lastModified": stamp,
    }
    assert names(client.search_wiki_pages("plum")) == ["Gamma"]


def test_concurrent_first_searches_fill_the_cache_once(
    mock_config, three_pages
):
    client = make_client(mock_config, three_pages)
    with ThreadPoolExecutor(8) as pool:
        results = list(
            pool.map(
                lambda _: client.search_wiki_pages("text"), range(8)
            )
        )
    assert all(names(r) == ["Alpha", "Beta", "Gamma"] for r in results)
    assert sorted(three_pages.fetched) == ["Alpha", "Beta", "Gamma"]


def test_a_listing_fault_propagates(mock_config):

    def failing(service, method, *params):
        raise xmlrpc.client.Fault(403, "permission denied")

    client = TracClient(mock_config)
    client._rpc_request = failing  # type: ignore[method-assign]
    with pytest.raises(xmlrpc.client.Fault):
        client.search_wiki_pages("x")


# --------------------------------------------------------------------
# The refresh is a full metadata snapshot (review fix)
# --------------------------------------------------------------------


def test_an_edit_made_during_the_fill_is_caught_on_the_next_search(
    mock_config,
):
    """A watermark taken from the pages read later in the same fill hid
    an edit to a page read earlier in it."""
    wiki = FakeWiki()
    for i in range(60):
        wiki.put(f"P{i:02d}", "alpha filler text\n")
    real = wiki.rpc
    multicalls = []

    def rpc(service, method, *params):
        out = real(service, method, *params)
        if method == "multicall":
            multicalls.append(1)
            if len(multicalls) == 1:  # between batch 1 and batch 2
                wiki.put("P00", "zebra now\n")  # read in batch 1
                wiki.put("P55", "alpha again\n")  # read in batch 2
        return out

    wiki.rpc = rpc  # type: ignore[method-assign]
    client = make_client(mock_config, wiki)
    client.search_wiki_pages("alpha")
    assert names(client.search_wiki_pages("zebra")) == ["P00"]
    assert wiki.pages["P00"]["version"] == 2
    assert client._wiki_cache["P00"].version == 2


def test_a_page_recreated_at_the_same_version_is_read_again(
    mock_config, three_pages
):
    client = make_client(mock_config, three_pages)
    assert names(client.search_wiki_pages("banana")) == ["Beta"]
    three_pages.delete("Beta")
    three_pages.put("Beta", "beta quince text\n")  # version 1 again
    assert three_pages.pages["Beta"]["version"] == 1
    assert names(client.search_wiki_pages("quince")) == ["Beta"]
    assert client.search_wiki_pages("banana") == []


def test_a_deleted_latest_version_is_read_again(
    mock_config, three_pages
):
    """Deleting the latest version leaves the page at an older, lower
    version with an older time: before any `since` a watermark asks."""
    client = make_client(mock_config, three_pages)
    three_pages.put("Beta", "beta mango text\n")  # version 2
    assert names(client.search_wiki_pages("mango")) == ["Beta"]
    older = "20260101T00:00:00"
    three_pages.pages["Beta"] = {
        "version": 1,
        "text": "beta banana text\n",
        "lastModified": older,
    }
    assert names(client.search_wiki_pages("banana")) == ["Beta"]
    assert client.search_wiki_pages("mango") == []


def test_without_a_version_pages_are_compared_by_time(mock_config):
    wiki = FakeWiki()
    wiki.no_version = True
    wiki.put("Alpha", "alpha apple text\n")
    client = make_client(mock_config, wiki)
    assert names(client.search_wiki_pages("apple")) == ["Alpha"]
    wiki.forget_calls()
    assert names(client.search_wiki_pages("apple")) == ["Alpha"]
    assert wiki.fetched == []  # unchanged: not read again
    wiki.put("Alpha", "alpha pear text\n")
    assert names(client.search_wiki_pages("pear")) == ["Alpha"]
    assert client.search_wiki_pages("apple") == []


def test_the_listing_falls_back_without_getrecentchanges(
    mock_config, three_pages
):
    three_pages.recent_enabled = False
    client = make_client(mock_config, three_pages)
    assert len(client.search_wiki_pages("text")) == 3
    three_pages.put("Beta", "beta mango text\n")
    assert names(client.search_wiki_pages("mango")) == ["Beta"]
    assert client.search_wiki_pages("banana") == []


# --------------------------------------------------------------------
# A failed multicall batch does not fail the search (review fix)
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ReadTimeout("timed out"),
        xmlrpc.client.Fault(1, "internal error"),
        OSError("connection reset"),
    ],
    ids=["timeout", "fault", "oserror"],
)
def test_a_failed_batch_is_read_page_by_page_and_the_search_goes_on(
    mock_config, error
):
    wiki = FakeWiki()
    for i in range(120):
        wiki.put(f"P/{i:03d}", f"word{i} common\n")
    real = wiki.rpc
    multicalls: list[int] = []
    singles: list[str] = []

    def rpc(service, method, *params):
        full = f"{service}.{method}"
        if full == "system.multicall":
            multicalls.append(1)
            if len(multicalls) == 2:
                raise error
        elif full == "wiki.getPage":
            singles.append(params[0])
        return real(service, method, *params)

    wiki.rpc = rpc  # type: ignore[method-assign]
    client = make_client(mock_config, wiki)
    assert len(client.search_wiki_pages("common")) == 120
    assert len(multicalls) == 3  # batch 3 still went by multicall
    assert singles == [f"P/{i:03d}" for i in range(50, 100)]
    # the multicall stays available: the failure was not a missing method
    wiki.put("P/new", "common again\n")
    wiki.forget_calls()
    assert "P/new" in names(client.search_wiki_pages("common"))
    assert wiki.calls[-1] == "system.multicall"


@pytest.mark.parametrize(
    "fault,missing",
    [
        (xmlrpc.client.Fault(-32601, "anything at all"), True),
        (
            xmlrpc.client.Fault(1, "no such method: system.multicall"),
            True,
        ),
        (
            xmlrpc.client.Fault(1, "No Such Method 'system.multicall'"),
            True,
        ),
        (
            xmlrpc.client.Fault(
                1, 'method "system.multicall" not found'
            ),
            True,
        ),
        # the guarantee: the text must name the method
        (xmlrpc.client.Fault(1, "no such method: wiki.getPage"), False),
        (
            xmlrpc.client.Fault(1, "no such page: system.multicall"),
            False,
        ),
        (xmlrpc.client.Fault(1, "internal error"), False),
    ],
)
def test_a_missing_method_fault_must_name_the_method(fault, missing):
    from trac_mcp_server.core.client import _is_missing_method_fault

    assert (
        _is_missing_method_fault(fault, "system.multicall") is missing
    )
