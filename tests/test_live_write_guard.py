"""Offline tests for the live rows' runtime write guard (Trac #173).

`tests/live_helpers.py` holds the guard and the stale-leftover sweep's
selection; `tests/conftest.py` installs them under --run-live. These
run on every offline run, with the transport mocked and no network.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from tests.live_helpers import (
    LiveWriteRefused,
    is_trac_test_url,
    is_write_method,
    live_write_guard,
    select_stale,
    sweep_stale,
    write_methods_in,
    write_violation,
)
from trac_mcp_server.config import Config
from trac_mcp_server.core.client import TracClient

PROJECT = "https://trac.example.com/trac_mcp_server"
TEST = "https://trac.example.com/trac_test"


def _client(url, **extra):
    client = TracClient(
        Config(trac_url=url, username="u", password="p", **extra)
    )
    session = MagicMock()
    session.post.return_value.content = (
        b"<methodResponse><params><param><value><int>1</int>"
        b"</value></param></params></methodResponse>"
    )
    client._thread_local.session = session
    return client, session


class TestDecision:
    def test_write_to_the_project_instance_is_refused(self):
        client, _ = _client(PROJECT)
        reason = write_violation(client, "wiki", "putPage", ("P", "x"))
        assert reason is not None
        assert "wiki.putPage" in reason
        assert PROJECT in reason

    def test_write_to_trac_test_is_allowed(self):
        client, _ = _client(TEST)
        assert write_violation(client, "wiki", "putPage", ()) is None

    def test_a_trailing_slash_on_trac_test_is_still_trac_test(self):
        assert is_trac_test_url(TEST + "/")

    def test_a_host_or_lookalike_path_is_not_trac_test(self):
        assert not is_trac_test_url("https://trac_test.example.com/x")
        assert not is_trac_test_url(PROJECT + "/trac_test_old")
        assert not is_trac_test_url(PROJECT + "?x=/trac_test")

    @pytest.mark.parametrize(
        ("service", "method"),
        [
            ("wiki", "getPage"),
            ("wiki", "getAllPages"),
            ("ticket", "get"),
            ("ticket", "query"),
            ("ticket", "getChangeAt"),
            ("ticket.milestone", "getAll"),
            ("system", "getAPIVersion"),
            ("system", "listMethods"),
        ],
    )
    def test_reads_are_allowed_anywhere(self, service, method):
        client, _ = _client(PROJECT)
        assert write_violation(client, service, method, ()) is None

    @pytest.mark.parametrize(
        ("service", "method"),
        [
            ("wiki", "putPage"),
            ("wiki", "putPageIfVersion"),
            ("wiki", "deletePage"),
            ("wiki", "putAttachmentEx"),
            ("wiki", "deleteAttachment"),
            ("ticket", "create"),
            ("ticket", "update"),
            ("ticket", "delete"),
            ("ticket", "putAttachment"),
            ("ticket", "deleteAttachment"),
            ("ticket", "editComment"),
            ("ticket", "deleteComment"),
            ("ticket", "replyToComment"),
            ("ticket.milestone", "create"),
            ("ticket.milestone", "update"),
            ("ticket.milestone", "delete"),
            ("ticket.component", "create"),
            ("ticket.priority", "delete"),
        ],
    )
    def test_every_write_method_is_refused_on_the_project(
        self, service, method
    ):
        client, _ = _client(PROJECT)
        assert write_violation(client, service, method, ()) is not None

    def test_a_multicall_carrying_a_write_is_refused(self):
        client, _ = _client(PROJECT)
        calls = [
            {"methodName": "wiki.getPageInfo", "params": ["A"]},
            {"methodName": "wiki.deletePage", "params": ["A"]},
        ]
        reason = write_violation(
            client, "system", "multicall", (calls,)
        )
        assert reason is not None
        assert "wiki.deletePage" in reason

    def test_a_read_only_multicall_is_allowed(self):
        client, _ = _client(PROJECT)
        calls = [
            {"methodName": "wiki.getPageInfo", "params": ["A"]},
            {"methodName": "ticket.get", "params": [1]},
        ]
        assert (
            write_violation(client, "system", "multicall", (calls,))
            is None
        )

    def test_an_unreadable_multicall_entry_is_not_waved_through(self):
        assert write_methods_in("system", "multicall", (["junk"],))

    def test_a_multicall_with_a_write_is_allowed_on_trac_test(self):
        client, _ = _client(TEST)
        calls = [{"methodName": "wiki.deletePage", "params": ["A"]}]
        assert (
            write_violation(client, "system", "multicall", (calls,))
            is None
        )

    def test_an_rpc_override_outside_trac_test_is_refused(self):
        client, _ = _client(
            TEST, rpc_url_override="https://trac.example.com/x/rpc"
        )
        assert (
            write_violation(client, "wiki", "putPage", ()) is not None
        )

    def test_write_prefixes_do_not_match_reads_by_substring(self):
        # "getUpdated..." contains "update"; only the start counts.
        assert not is_write_method("ticket.getUpdated")
        assert is_write_method("ticket.update")


class TestGuardOnARealClient:
    """The guard forced on, a TracClient on a non-/trac_test URL, the
    transport mocked: the write raises before anything is sent."""

    def test_a_write_raises_before_anything_is_sent(self):
        client, session = _client(PROJECT)
        with live_write_guard() as violations:
            with pytest.raises(LiveWriteRefused):
                client.put_wiki_page("Scratch/X", "body", "c")
        session.post.assert_not_called()
        assert len(violations) == 1
        assert "wiki.putPage" in violations[0]

    def test_a_swallowed_refusal_is_still_recorded(self):
        client, session = _client(PROJECT)
        with live_write_guard() as violations:
            try:
                client.delete_wiki_page("Scratch/X")
            except Exception:
                pass
        session.post.assert_not_called()
        assert len(violations) == 1

    def test_a_read_goes_through(self):
        client, session = _client(PROJECT)
        with live_write_guard() as violations:
            client._rpc_call("wiki", "getPage", "WikiStart")
        session.post.assert_called_once()
        assert violations == []

    def test_a_write_on_trac_test_goes_through(self):
        client, session = _client(TEST)
        with live_write_guard() as violations:
            client._rpc_call("wiki", "deletePage", "Scratch/X")
        session.post.assert_called_once()
        assert violations == []

    def test_the_wrapper_is_removed_afterwards(self):
        original = TracClient._rpc_call
        with live_write_guard():
            assert TracClient._rpc_call is not original
        assert TracClient._rpc_call is original


class TestStaleSelection:
    NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
    PREFIXES = ("Scratch/Ticket173", "PageTemplates/Probe")

    def test_old_prefixed_names_are_selected_and_young_are_not(self):
        got = select_stale(
            {
                "Scratch/Ticket173Old": "20261107T09:00:00",
                "Scratch/Ticket173Young": "20261107T11:30:00",
                "Scratch/Ticket173Edge": "20261107T11:00:00",
            },
            self.PREFIXES,
            self.NOW,
        )
        assert got == ["Scratch/Ticket173Old"]

    def test_other_names_are_never_selected(self):
        got = select_stale(
            {
                "WikiStart": "20200101T00:00:00",
                "Scratch/Other": "20200101T00:00:00",
            },
            self.PREFIXES,
            self.NOW,
        )
        assert got == []

    def test_legacy_fixed_names_are_left_alone(self):
        got = select_stale(
            {
                "PageTemplates/Probe123": "20200101T00:00:00",
                "PageTemplates/Probe1a2b3c4d": "20200101T00:00:00",
            },
            self.PREFIXES,
            self.NOW,
        )
        assert got == ["PageTemplates/Probe1a2b3c4d"]

    def test_an_unreadable_timestamp_is_left_alone(self):
        got = select_stale(
            {
                "Scratch/Ticket173A": None,
                "Scratch/Ticket173B": 0,
                "Scratch/Ticket173C": "garbage",
            },
            self.PREFIXES,
            self.NOW,
        )
        assert got == []

    def test_datetime_values_are_read_too(self):
        old = self.NOW - timedelta(hours=2)
        got = select_stale(
            {"Scratch/Ticket173A": old}, self.PREFIXES, self.NOW
        )
        assert got == ["Scratch/Ticket173A"]


class _FakeSweepClient:
    def __init__(self, fail_list=False):
        self.fail_list = fail_list
        self.deleted = []
        self.pages = {
            "Scratch/Ticket173Old": "20261107T08:00:00",
            "Scratch/Ticket173Young": "20261107T11:59:00",
            "PageTemplates/Probe123": "20200101T00:00:00",
            "WikiStart": "20200101T00:00:00",
        }
        self.tickets = {
            1: ("Ticket173AttachmentLive", "20261107T08:00:00"),
            2: ("Ticket173AttachmentLive", "20261107T11:59:00"),
            3: ("Ticket173AttachmentLiveXL", "20200101T00:00:00"),
        }

    def list_wiki_pages(self):
        if self.fail_list:
            raise RuntimeError("boom")
        return list(self.pages)

    def get_wiki_page_info(self, name):
        return {"lastModified": self.pages[name]}

    def delete_wiki_page(self, name):
        self.deleted.append(name)

    def search_tickets(self, query):
        return list(self.tickets)

    def get_ticket(self, tid):
        summary, changed = self.tickets[tid]
        return [tid, 0, 0, {"summary": summary, "changetime": changed}]

    def delete_ticket(self, tid):
        self.deleted.append(tid)


class TestSweep:
    NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)

    def test_only_old_per_run_objects_are_deleted(self):
        client = _FakeSweepClient()
        done = sweep_stale(client, self.NOW)
        assert client.deleted == ["Scratch/Ticket173Old", 1]
        assert done == ["page Scratch/Ticket173Old", "ticket 1"]

    def test_a_failing_pass_does_not_raise_or_stop_the_next(self):
        client = _FakeSweepClient(fail_list=True)
        assert sweep_stale(client, self.NOW) == ["ticket 1"]
