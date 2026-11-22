"""Tools given the ``datetime`` a decoded ``dateTime.iso8601`` now is
(Trac /trac_mcp_server #270).

The client's decoder used to return a compact UTC string such as
``20261121T20:44:11``; it now returns a naive ``datetime`` holding UTC.
Every place that read the string must take the ``datetime`` and show the
same text for the same instant, and ``milestone._format_date`` (L30)
formats in UTC rather than the host's zone.
"""

import asyncio
import json
import time
import xmlrpc.client
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from trac_mcp_server.core.client import TicketUpdateConflict
from trac_mcp_server.mcp.tools import (
    milestone,
    ticket_attachment,
    ticket_comment,
    ticket_list,
    timeline_read,
    wiki_read,
)
from trac_mcp_server.mcp.tools.errors import (
    compact_time,
    format_timestamp,
)
from trac_mcp_server.mcp.tools.milestone import (
    _format_date,
    _parse_date,
)

# 2026-11-21T20:44:11 UTC.
TS = 1795293851
WIRE = "20261121T20:44:11"
DT = datetime(2026, 11, 21, 20, 44, 11)


@pytest.fixture
def non_utc_tz(monkeypatch):
    """The process clock in a zone whose offset from UTC is not zero at
    TS (and not a whole number of hours, so a shift cannot hide)."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is not available on this platform")
    try:
        monkeypatch.setenv("TZ", "Asia/Kolkata")
        time.tzset()
        if time.strftime("%Y%m%dT%H:%M:%S", time.localtime(TS)) == WIRE:
            pytest.skip("zone Asia/Kolkata is not available")
        yield
    finally:
        monkeypatch.undo()
        time.tzset()


def run(coro):
    return asyncio.run(coro)


async def _direct(func, *args, **kwargs):
    return func(*args, **kwargs)


class TestFormatTimestamp:
    def test_datetime(self):
        assert format_timestamp(DT) == "2026-11-21 20:44"

    def test_datetime_is_not_shifted_by_the_host_zone(self, non_utc_tz):
        assert format_timestamp(DT) == "2026-11-21 20:44"

    def test_xmlrpc_datetime_is_read_as_utc(self, non_utc_tz):
        assert (
            format_timestamp(xmlrpc.client.DateTime(WIRE))
            == "2026-11-21 20:44"
        )

    def test_int_is_utc(self, non_utc_tz):
        assert format_timestamp(TS) == "2026-11-21 20:44"

    def test_strings_still_work(self):
        assert format_timestamp(WIRE) == "2026-11-21 20:44"
        assert (
            format_timestamp("2026-11-21 20:44:11")
            == "2026-11-21 20:44"
        )
        assert format_timestamp("whenever") == "whenever"


class TestCompactTime:
    def test_datetime_is_the_wire_form(self):
        assert compact_time(DT) == WIRE

    def test_year_below_1000_is_padded(self):
        assert (
            compact_time(datetime(999, 1, 2, 3, 4, 5))
            == "09990102T03:04:05"
        )

    def test_everything_else_is_str(self):
        assert compact_time(WIRE) == WIRE
        assert compact_time(None) == "None"
        assert compact_time(xmlrpc.client.DateTime(WIRE)) == WIRE


class TestMilestoneFormatDate:
    ISO = "2026-11-21T20:44:11"

    def test_unset(self):
        assert _format_date(0) == "(Not set)"
        assert _format_date(None) == "(Not set)"

    def test_datetime(self):
        assert _format_date(DT) == self.ISO

    def test_datetime_is_not_shifted_by_the_host_zone(self, non_utc_tz):
        assert _format_date(DT) == self.ISO

    def test_int_timestamp_is_formatted_in_utc(self, non_utc_tz):
        """L30: it used the host's local zone."""
        assert _format_date(TS) == self.ISO

    def test_float_timestamp_is_formatted_in_utc(self, non_utc_tz):
        assert _format_date(float(TS)) == self.ISO

    def test_xmlrpc_datetime_prints_the_iso_form(self):
        assert _format_date(xmlrpc.client.DateTime(WIRE)) == self.ISO

    def test_compact_wire_string_prints_the_iso_form(self):
        assert _format_date(WIRE) == self.ISO

    def test_other_strings_pass_through(self):
        assert _format_date("2026-11-21T20:44:11") == self.ISO
        assert _format_date("soon") == "soon"

    def test_a_read_value_round_trips_into_the_write_parser(self):
        parsed = _parse_date(_format_date(DT))
        assert parsed.value == WIRE

    def test_milestone_get_shows_a_decoded_due_date(self):
        client = MagicMock()
        client.get_milestone.return_value = {
            "name": "M",
            "due": DT,
            "completed": 0,
            "description": "d",
        }
        with patch.object(milestone, "run_sync", _direct):
            result = run(milestone._handle_get(client, {"name": "M"}))
        assert "Due: 2026-11-21T20:44:11" in result.content[0].text
        assert result.structuredContent["due"] == self.ISO


class TestRecentChangesDisplay:
    @staticmethod
    def _changes(*values):
        return [
            {
                "name": f"P{i}",
                "author": "a",
                "version": 1,
                "lastModified": value,
            }
            for i, value in enumerate(values)
        ]

    def _run(self, changes):
        client = MagicMock()
        client.get_recent_wiki_changes.return_value = changes
        with patch.object(wiki_read, "run_sync", _direct):
            return run(wiki_read._handle_recent_changes(client, {}))

    def test_datetime_shows_what_the_wire_string_showed(self):
        by_string = self._run(self._changes(WIRE))
        by_datetime = self._run(self._changes(DT))
        assert by_datetime.content[0].text == by_string.content[0].text
        assert (
            by_datetime.structuredContent == by_string.structuredContent
        )
        assert (
            by_datetime.structuredContent["pages"][0]["lastModified"]
            == WIRE
        )

    def test_datetimes_sort_newest_first(self):
        later = datetime(2026, 11, 22, 1, 2, 3)
        result = self._run(self._changes(DT, later))
        names = [p["name"] for p in result.structuredContent["pages"]]
        assert names == ["P1", "P0"]

    def test_xmlrpc_datetime_is_read_as_utc(self, non_utc_tz):
        result = self._run(self._changes(xmlrpc.client.DateTime(WIRE)))
        assert (
            result.structuredContent["pages"][0]["lastModified"]
            == "2026-11-21 20:44"
        )


class TestOtherDisplays:
    def test_timeline_event_date_is_the_wire_form(self):
        client = MagicMock()
        client.get_timeline_filters.return_value = [["ticket", "T"]]
        client.get_timeline_events.return_value = [
            {
                "kind": "ticket",
                "date": DT,
                "author": "a",
                "title": "t",
                "description": "",
                "url": "u",
            }
        ]
        with patch.object(timeline_read, "run_sync", _direct):
            result = run(
                timeline_read._handle_search(
                    client, {"start": 0, "stop": TS}
                )
            )
        assert f"[ticket] {WIRE} a: t" in result.content[0].text
        assert result.structuredContent["events"][0]["date"] == WIRE

    def test_attachment_time_is_the_wire_form(self):
        client = MagicMock()
        client.list_ticket_attachments.return_value = [
            ["f.txt", "d", 3, DT, "a"]
        ]
        with patch.object(ticket_attachment, "run_sync", _direct):
            result = run(
                ticket_attachment._handle_list(client, {"ticket_id": 1})
            )
        listed = result.structuredContent["attachments"][0]
        assert listed["time"] == WIRE

    def test_comment_history_when_is_the_wire_form(self):
        client = MagicMock()
        client.get_ticket_comment_history.return_value = [
            [1, DT, "a", "body"]
        ]
        with patch.object(ticket_comment, "run_sync", _direct):
            result = run(
                ticket_comment._handle_history(
                    client, {"ticket_id": 1, "cnum": 1}
                )
            )
        assert f"by a at {WIRE} ---" in result.content[0].text

    def test_version_conflict_names_the_change_time_as_the_wire_form(
        self,
    ):
        from trac_mcp_server.mcp.tools import ticket_write

        conflict = TicketUpdateConflict(
            "changed",
            ticket_id=42,
            base_ts="123",
            changes=[
                {
                    "timestamp": DT,
                    "author": "operator",
                    "field": "comment",
                    "oldvalue": "",
                    "newvalue": "hold off",
                }
            ],
        )
        with patch.object(ticket_write, "run_sync") as mock_run_sync:
            mock_run_sync.side_effect = conflict
            result = run(
                ticket_write._handle_update(
                    MagicMock(),
                    {
                        "ticket_id": 42,
                        "status": "accepted",
                        "base_ts": "1",
                    },
                )
            )
        assert f"operator at {WIRE}" in result.content[0].text


class TestCheckListWithADatetimeField:
    """``ticket_check_list`` dumps a ticket's fields as JSON with no
    ``default=``; a custom time field decodes as a ``datetime`` now."""

    def test_a_datetime_field_does_not_crash_the_dump(self):
        client = MagicMock()
        client.config = MagicMock(trac_url="https://trac.example/proj/")
        client.search_tickets.return_value = [5]
        client.get_tickets.return_value = {
            5: [5, DT, DT, {"summary": "s", "due_close": DT}]
        }
        with (
            patch.object(ticket_list, "run_sync", _direct),
            patch.object(ticket_list, "run_sync_limited", _direct),
        ):
            result = run(
                ticket_list._handle_check_list(
                    client,
                    {"values": "5", "fields": "summary,due_close"},
                )
            )
        assert not result.isError
        found = json.loads(result.content[0].text)["found"]["5"]
        assert found["summary"] == "s"
        assert found["due_close"] == str(DT)
