"""Tests for user_activity: what one account did in a period."""

import asyncio
import base64
import json
import sys
import xmlrpc.client
from types import SimpleNamespace

import pytest

from trac_mcp_server.mcp.tools.user_activity import (
    USER_ACTIVITY_SPECS,
    signed_in_user,
)

HANDLER = USER_ACTIVITY_SPECS[0].handler


# TracClient hands dateTime.iso8601 values on as text; other clients give
# xmlrpc DateTime objects. The fake client builds its data with `stamp`.
STAMPS = {"text": str, "xmlrpc": xmlrpc.client.DateTime}
stamp = STAMPS["text"]


def dt(text):
    return stamp(text)


@pytest.fixture(params=sorted(STAMPS))
def stamps(request, monkeypatch):
    monkeypatch.setattr(
        sys.modules[__name__], "stamp", STAMPS[request.param]
    )


def jwt(claims):
    body = (
        base64.urlsafe_b64encode(json.dumps(claims).encode())
        .decode()
        .rstrip("=")
    )
    return f"e30.{body}.sig"


class FakeClient:
    """Two tickets changed on 2026-11-05 (Europe/Berlin), one later again."""

    def __init__(self, bearer_token=None, username=None):
        self.config = SimpleNamespace(
            trac_url="http://trac.example/trac",
            bearer_token=bearer_token,
            username=username,
        )
        self.queries = []
        self.logs = {
            # Changed by alice on the day, then by bob a week later: a
            # changetime query for the day misses it.
            7: [
                [
                    dt("20261105T08:30:00"),
                    "alice",
                    "status",
                    "new",
                    "accepted",
                    1,
                ],
                [
                    dt("20261105T08:30:00"),
                    "alice",
                    "comment",
                    "1",
                    "Looks good, taking it.",
                    1,
                ],
                # An attachment: the change log has the file name as new value.
                [
                    dt("20261105T09:00:00"),
                    "alice",
                    "attachment",
                    None,
                    "trace.log",
                    0,
                ],
                [
                    dt("20261112T10:00:00"),
                    "bob",
                    "comment",
                    "2",
                    "done",
                    1,
                ],
            ],
            # Before the day (UTC 23:30 on 04.11 is 00:30 on 05.11 in Berlin)
            # and after it.
            9: [
                [
                    dt("20261104T22:59:59"),
                    "alice",
                    "priority",
                    "minor",
                    "major",
                    1,
                ],
                [
                    dt("20261104T23:30:00"),
                    "Alice",
                    "milestone",
                    "",
                    "m1",
                    1,
                ],
                [
                    dt("20261105T23:00:00"),
                    "alice",
                    "comment",
                    "3",
                    "next day",
                    1,
                ],
            ],
        }

    def search_tickets(self, query):
        self.queries.append(query)
        if query.startswith("changetime="):
            return [7, 9]
        if query.startswith("reporter=alice"):
            return [11, 12]
        return []

    def _batched_multicall(
        self, items, batch_size, calls_for, decode, fetch_singly
    ):
        return {
            i: decode(i, [self.logs[i]])
            for i in items
            if i in self.logs
        }

    def _fetch_each(self, items, read_one):
        return {i: read_one(i) for i in items}

    def get_ticket_changelog(self, tid):
        return self.logs[tid]

    def get_tickets(self, ids):
        created = {
            7: dt("20260101T00:00:00"),
            9: dt("20260101T00:00:00"),
            11: dt("20261105T14:00:00"),
            12: dt("20261106T14:00:00"),
        }
        return {
            i: [i, created[i], created[i], {"summary": f"Ticket {i}"}]
            for i in ids
        }

    def get_recent_wiki_changes(self, since):
        return [
            {
                "name": "Guide",
                "author": "bob",
                "version": 3,
                "lastModified": dt("20261106T09:00:00"),
            }
        ]

    def get_wiki_page_info(self, name, version):
        return {
            2: {
                "name": name,
                "author": "alice",
                "version": 2,
                "lastModified": dt("20261105T12:00:00"),
                "comment": "typo",
            },
            1: {
                "name": name,
                "author": "alice",
                "version": 1,
                "lastModified": dt("20261001T12:00:00"),
            },
        }[version]


def call(client, **args):
    result = asyncio.run(HANDLER(client, args))
    return result.structuredContent


def test_activities_of_one_day(stamps):
    client = FakeClient()
    out = call(
        client,
        date_from="2026-11-05",
        username="alice",
        time_zone="Europe/Berlin",
    )
    assert out["username"] == "alice"
    assert out["complete"] is True
    assert out["by_kind"] == {
        "ticket_created": 1,
        "ticket_change": 3,
        "wiki_edit": 1,
    }
    assert out["comments"] == 1
    kinds = [
        (a["kind"], a.get("ticket"), a.get("page"))
        for a in out["activities"]
    ]
    assert kinds == [
        ("ticket_change", 9, None),
        ("ticket_change", 7, None),
        ("ticket_change", 7, None),
        ("wiki_edit", None, "Guide"),
        ("ticket_created", 11, None),
    ]
    first, second = out["activities"][0], out["activities"][1]
    assert first["time"] == "2026-11-05T00:30:00+01:00" and first[
        "fields"
    ] == ["milestone"]
    assert (
        second["fields"] == ["status"]
        and second["comment"] == "Looks good, taking it."
    )
    assert second["url"] == "http://trac.example/trac/ticket/7"
    assert second["summary"] == "Ticket 7"
    attached = out["activities"][2]
    assert attached["time"] == "2026-11-05T10:00:00+01:00"
    assert (
        attached["attachments"] == ["trace.log"]
        and attached["fields"] == []
    )
    assert "attachments" not in second
    assert out["activities"][3]["version"] == 2
    # The query reaches back one day and has no upper bound.
    assert (
        client.queries[0]
        == "changetime=2026-11-03..&max=0&order=changetime&desc=1"
    )


def test_signed_in_user_from_token_claim():
    client = FakeClient(
        bearer_token=jwt({"preferred_username": "alice", "sub": "x"})
    )
    out = call(
        client,
        date_from="2026-11-05",
        time_zone="Europe/Berlin",
        include_wiki=False,
    )
    assert out["username"] == "alice"
    assert "wiki_edit" not in out["by_kind"]
    assert signed_in_user(FakeClient(username="carol")) == "carol"
    assert signed_in_user(FakeClient(bearer_token="opaque")) is None


def test_unknown_user_and_bad_dates():
    client = FakeClient(bearer_token="opaque")
    for args in (
        {"date_from": "2026-11-05"},
        {"date_from": "05.11.2026", "username": "alice"},
        {
            "date_from": "2026-11-06",
            "date_to": "2026-11-05",
            "username": "alice",
        },
        {
            "date_from": "2026-11-05",
            "username": "alice",
            "time_zone": "Mars/Base",
        },
    ):
        result = asyncio.run(HANDLER(client, args))
        assert result.isError, args
