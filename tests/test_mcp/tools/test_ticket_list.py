"""Tests for ticket_check_list."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from trac_mcp_server.core.client import TracClient
from trac_mcp_server.mcp.tools.ticket_list import (
    _handle_check_list,
    parse_terms,
    word_match,
)


async def _direct(func, *args, **kwargs):
    return func(*args, **kwargs)


def _client(tickets: dict[int, dict], search=None) -> MagicMock:
    """A TracClient mock: search_tickets answers an id=1|2 query with the
    ids that exist (or calls ``search``), get_ticket returns the attrs."""
    client = MagicMock(spec=TracClient)

    def search_tickets(query):
        if search is not None:
            return search(query)
        ids = query.split("&")[0].removeprefix("id=").split("|")
        return [int(i) for i in ids if int(i) in tickets]

    client.config = MagicMock(trac_url="https://trac.example/proj/")
    client.search_tickets.side_effect = search_tickets
    client.get_ticket.side_effect = lambda tid: [
        tid,
        None,
        None,
        tickets[tid],
    ]
    return client


def _run(client, args):
    with (
        patch(
            "trac_mcp_server.mcp.tools.ticket_list.run_sync", _direct
        ),
        patch(
            "trac_mcp_server.mcp.tools.ticket_list.run_sync_limited",
            _direct,
        ),
    ):
        return asyncio.run(_handle_check_list(client, args))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1, 2;3\n4", ["1", "2", "3", "4"]),
        ('["a, b", "c"]', ["a, b", "c"]),
        (["x", "X", " y "], ["x", "y"]),
        ("[not json", ["[not json"]),
        ("[]", []),
        ("[ ]", []),
        (None, []),
    ],
)
def test_parse_terms(raw, expected):
    assert parse_terms(raw) == expected


def test_word_match_is_whole_word():
    assert word_match("ABC-12", "see ABC-12 for details")
    assert not word_match("ABC-12", "see ABC-123 for details")


def test_ids_found_missing_and_invalid():
    client = _client(
        {
            1: {"summary": "First", "status": "new"},
            3: {"summary": "Third", "status": "closed"},
        }
    )

    result = _run(client, {"values": "#1, 2, 3, abc"}).structuredContent

    assert result["values_checked"] == 4
    assert result["found_count"] == 2
    assert result["missing"] == ["2"]
    assert result["not_a_ticket_number"] == ["abc"]
    assert result["found"]["#1"] == {
        "id": 1,
        "summary": "First",
        "status": "new",
        "url": "https://trac.example/proj/ticket/1",
    }
    assert result["failed_ids"] == []
    client.search_tickets.assert_called_once_with("id=1|2|3&max=0")


def test_ids_only_missing_skips_details_and_applies_scope():
    client = _client({1: {"summary": "First"}})

    result = _run(
        client,
        {
            "values": "1,2",
            "only_missing": True,
            "query": "status!=closed",
        },
    ).structuredContent

    assert result["missing"] == ["2"]
    assert "found" not in result
    client.get_ticket.assert_not_called()
    client.search_tickets.assert_called_once_with(
        "id=1|2&max=0&status!=closed"
    )


def test_text_matches_whole_words_only():
    tickets = {
        5: {"summary": "Login fails", "status": "new"},
        6: {"summary": "Logins are slow", "status": "new"},
    }
    queries = []

    def search(query):
        queries.append(query)
        return [5, 6]

    client = _client(tickets, search)

    result = _run(
        client, {"values": "login, export", "match_field": "summary"}
    ).structuredContent

    assert result["missing"] == ["export"]
    assert [hit["id"] for hit in result["found"]["login"]] == [5]
    assert queries == ["summary~=login|export&max=0"]


def _text_queries(values, match_field="keywords", tickets=None):
    """Run a text-mode call; return the queries sent and the result."""
    queries = []

    def search(query):
        queries.append(query)
        return []

    result = _run(
        _client(tickets or {}, search),
        {"values": values, "match_field": match_field},
    )
    return queries, result.structuredContent


def test_text_query_escapes_separators():
    queries, _ = _text_queries("a|b&c")

    assert queries == [r"keywords~=a\|b\&c&max=0"]


def test_text_query_searches_the_longest_word_of_a_phrase():
    queries, _ = _text_queries("foo bar-baz")

    assert queries == ["keywords~=bar-baz&max=0"]


def test_text_query_terms_do_not_merge_across_a_backslash():
    queries, _ = _text_queries("x\\y, z")

    assert queries == ["keywords~=x|z&max=0"]


def test_text_query_strips_leading_minus_from_the_needle():
    queries, _ = _text_queries("-foo")

    assert queries == ["keywords~=foo&max=0"]


def test_text_query_needle_splits_on_quotes():
    queries, _ = _text_queries('"ab" c')

    assert queries == ["keywords~=ab&max=0"]


@pytest.mark.parametrize("term", ["\\", "-", '""', "- -"])
def test_unsearchable_terms_are_reported_not_queried(term):
    queries, result = _text_queries(f"{term}")

    assert queries == []
    assert result["unsearchable"] == [term]
    assert result["missing"] == []
    assert result["missing_count"] == 0


def test_unsearchable_key_is_omitted_when_empty():
    _, result = _text_queries("abc")

    assert "unsearchable" not in result


def test_unsearchable_terms_leave_the_others_queried():
    queries, result = _text_queries("abc; \\")

    assert queries == ["keywords~=abc&max=0"]
    assert result["unsearchable"] == ["\\"]
    assert result["missing"] == ["abc"]


def test_term_with_a_pipe_is_found_end_to_end():
    tickets = {7: {"summary": "uses foo|bar here"}}
    queries = []

    def search(query):
        queries.append(query)
        return [7]

    result = _run(
        _client(tickets, search),
        {"values": "foo|bar", "match_field": "summary"},
    ).structuredContent

    assert queries == [r"summary~=foo\|bar&max=0"]
    assert [hit["id"] for hit in result["found"]["foo|bar"]] == [7]
    assert result["missing"] == []


@pytest.mark.parametrize(
    "match_field",
    ["summary~=x&owner", "a b", "1x", "x-y", "summary=", "x|y"],
)
def test_match_field_must_be_a_field_name(match_field):
    client = _client({})

    result = _run(client, {"values": "x", "match_field": match_field})

    assert result.isError
    assert "validation_error" in result.content[0].text
    assert "match_field" in result.content[0].text
    client.search_tickets.assert_not_called()


@pytest.mark.parametrize(
    ("query", "key"),
    [
        ("max=20", "max"),
        ("status=new&MAX=5", "max"),
        ("page=2", "page"),
        ("order=priority", "order"),
        ("desc=1", "desc"),
        ("group=status", "group"),
        ("groupdesc=1", "groupdesc"),
        ("col=summary", "col"),
        ("format=csv", "format"),
        ("report=3", "report"),
        ("verbose=1", "verbose"),
        ("rows=description", "rows"),
        ("row=description", "row"),
        ("max~=3", "max"),
        ("max!=3", "max"),
        ("status=new&or&owner=x", "or"),
        ("status=new&OR&owner=x", "or"),
        ("status", "status"),
        ("status=new&&resolved", "resolved"),
        ("id=5", "id"),
        ("ID!=5", "id"),
    ],
)
def test_query_may_not_override_the_chunk_in_id_mode(query, key):
    client = _client({1: {}})

    result = _run(client, {"values": "1", "query": query})

    assert result.isError
    text = result.content[0].text
    assert "validation_error" in text
    assert f"'{key}'" in text
    assert "narrow" in text
    client.search_tickets.assert_not_called()


def test_query_may_not_repeat_the_match_field_in_text_mode():
    client = _client({})

    result = _run(
        client,
        {
            "values": "x",
            "match_field": "summary",
            "query": "status=new&summary~=y",
        },
    )

    assert result.isError
    assert "'summary'" in result.content[0].text
    client.search_tickets.assert_not_called()


def test_query_may_use_id_in_text_mode_and_escaped_ampersand():
    client = _client({}, lambda query: [])

    result = _run(
        client,
        {
            "values": "x",
            "match_field": "summary",
            "query": r"id=5&status=a\&max=3&&",
        },
    )

    assert not result.isError
    client.search_tickets.assert_called_once_with(
        r"summary~=x&max=0&id=5&status=a\&max=3&&"
    )


def test_a_failed_get_ticket_in_id_mode_is_reported_not_raised():
    client = _client(
        {
            1: {"summary": "One", "status": "new"},
            2: {"summary": "Two", "status": "new"},
            3: {"summary": "Three", "status": "new"},
        }
    )
    good = client.get_ticket.side_effect

    def get_ticket(tid):
        if tid == 2:
            raise RuntimeError("boom")
        return good(tid)

    client.get_ticket.side_effect = get_ticket

    result = _run(client, {"values": "3, 2, 1, 9"}).structuredContent

    assert result["failed_ids"] == [2]
    assert sorted(result["found"]) == ["1", "3"]
    assert result["missing"] == ["9"]
    assert result["found_count"] == 2
    assert result["missing_count"] == 1


def test_a_failed_get_ticket_in_text_mode_is_not_post_filtered():
    tickets = {
        5: {"summary": "Login fails"},
        6: {"summary": "Login slow"},
        7: {"summary": "Login odd"},
    }
    client = _client(tickets, lambda query: [7, 6, 5])
    good = client.get_ticket.side_effect

    def get_ticket(tid):
        if tid in (6, 7):
            raise RuntimeError("boom")
        return good(tid)

    client.get_ticket.side_effect = get_ticket

    result = _run(
        client, {"values": "login", "match_field": "summary"}
    ).structuredContent

    assert result["failed_ids"] == [6, 7]
    assert [hit["id"] for hit in result["found"]["login"]] == [5]


def test_unset_fields_are_empty_strings_not_null():
    client = _client({1: {"summary": "First", "status": None}})

    result = _run(client, {"values": "1"}).structuredContent

    assert result["found"]["1"]["status"] == ""


def test_text_mode_unset_match_field_is_empty_not_the_word_none():
    client = _client(
        {5: {"summary": None, "status": "new"}}, lambda q: [5]
    )

    result = _run(
        client, {"values": "none", "match_field": "summary"}
    ).structuredContent

    assert result["missing"] == ["none"]


def test_a_requested_id_field_does_not_blank_the_id():
    client = _client({4: {"summary": "Four", "status": "new"}})

    result = _run(
        client, {"values": "4", "fields": "id,summary"}
    ).structuredContent

    assert result["found"]["4"] == {
        "id": 4,
        "summary": "Four",
        "url": "https://trac.example/proj/ticket/4",
    }


def test_numbers_are_ascii_decimal_with_at_most_one_hash():
    client = _client({1: {"summary": "One"}, 2: {"summary": "Two"}})

    result = _run(client, {"values": "1, 2, ², ##5, #, ١"})

    assert not result.isError
    out = result.structuredContent
    assert out["not_a_ticket_number"] == ["²", "##5", "#", "١"]
    assert out["found_count"] == 2
    client.search_tickets.assert_called_once_with("id=1|2&max=0")


def test_empty_json_list_is_an_empty_values_error():
    result = _run(_client({}), {"values": "[]"})

    assert result.isError
    assert "values is empty" in result.content[0].text


def test_each_hit_carries_the_ticket_url_in_text_mode():
    client = _client({5: {"summary": "Login fails"}}, lambda q: [5])

    result = _run(
        client, {"values": "login", "match_field": "summary"}
    ).structuredContent

    assert (
        result["found"]["login"][0]["url"]
        == "https://trac.example/proj/ticket/5"
    )


def test_empty_values_is_a_validation_error():
    result = _run(_client({}), {"values": " , "})

    assert result.isError
    assert "values is empty" in result.content[0].text
