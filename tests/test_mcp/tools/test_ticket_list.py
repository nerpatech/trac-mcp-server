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
    }
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


def test_text_query_separators_are_neutralised():
    queries = []

    def search(query):
        queries.append(query)
        return []

    client = _client({}, search)

    _run(client, {"values": "a|b&c", "match_field": "keywords"})

    assert queries == ["keywords~=a b c&max=0"]


def test_empty_values_is_a_validation_error():
    result = _run(_client({}), {"values": " , "})

    assert result.isError
    assert "values is empty" in result.content[0].text
