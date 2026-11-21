"""Time windows go on the wire in UTC (Trac /trac_mcp_server #266).

``xmlrpc.client.DateTime(int)`` formats with ``time.localtime()`` and
``DateTime(0)`` means "now", while Trac reads an XML-RPC DateTime as UTC.
On a non-UTC host a window was therefore shifted by the UTC offset, and
``timeline_search``'s ``start=0`` asked from "now". Every test here runs
under a non-UTC ``TZ`` and checks the value the server would receive.
"""

import calendar
import time
import xmlrpc.client
from unittest.mock import Mock, patch

import pytest

from trac_mcp_server.core.client import TracClient

# 2026-11-21T20:44:11 UTC.
TS = 1795293851
TS_WIRE = time.strftime("%Y%m%dT%H:%M:%S", time.gmtime(TS))
EPOCH_WIRE = "19700101T00:00:00"

TIMEZONES = ["America/Denver", "Asia/Kolkata", "Pacific/Auckland"]


@pytest.fixture(params=TIMEZONES)
def non_utc_tz(request, monkeypatch):
    """Run the test with the process clock in a non-UTC zone."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is not available on this platform")
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    # The zone must really differ from UTC at TS, or the test proves
    # nothing.
    assert (
        time.strftime("%Y%m%dT%H:%M:%S", time.localtime(TS)) != TS_WIRE
    )
    yield request.param
    monkeypatch.undo()
    time.tzset()


def _rpc_result_response(value) -> Mock:
    response = Mock()
    response.status_code = 200
    response.content = xmlrpc.client.dumps(
        (value,), methodresponse=True
    ).encode()
    return response


def _sent(mock_post):
    """(method, params) of the request the client posted."""
    params, method = xmlrpc.client.loads(
        mock_post.call_args.kwargs["data"]
    )
    return method, params


class TestTimelineWindowIsUtc:
    @patch("trac_mcp_server.core.client.requests.Session.post")
    def test_start_and_stop_are_utc(
        self, mock_post, mock_config, non_utc_tz
    ):
        mock_post.return_value = _rpc_result_response([])
        client = TracClient(mock_config)
        stop = TS + 3600
        client.get_timeline_events(TS, stop, ["ticket"], 10)
        method, params = _sent(mock_post)
        assert method == "timeline.getEvents"
        assert params[0].value == TS_WIRE
        assert params[1].value == time.strftime(
            "%Y%m%dT%H:%M:%S", time.gmtime(stop)
        )
        assert params[2:] == (["ticket"], 10)

    @patch("trac_mcp_server.core.client.requests.Session.post")
    def test_start_zero_is_the_epoch_not_now(
        self, mock_post, mock_config, non_utc_tz
    ):
        mock_post.return_value = _rpc_result_response([])
        client = TracClient(mock_config)
        client.get_timeline_events(0, TS, ["ticket"], 10)
        _, params = _sent(mock_post)
        assert params[0].value == EPOCH_WIRE
        assert params[1].value == TS_WIRE


class TestRecentWikiChangesWindowIsUtc:
    @patch("trac_mcp_server.core.client.requests.Session.post")
    def test_since_timestamp_is_utc(
        self, mock_post, mock_config, non_utc_tz
    ):
        mock_post.return_value = _rpc_result_response([])
        client = TracClient(mock_config)
        client.get_recent_wiki_changes(TS)
        method, (since,) = _sent(mock_post)
        assert method == "wiki.getRecentChanges"
        assert since.value == TS_WIRE

    @patch("trac_mcp_server.core.client.requests.Session.post")
    def test_zero_is_the_epoch(
        self, mock_post, mock_config, non_utc_tz
    ):
        mock_post.return_value = _rpc_result_response([])
        client = TracClient(mock_config)
        client.get_recent_wiki_changes(0)
        _, (since,) = _sent(mock_post)
        assert since.value == EPOCH_WIRE


class TestRecentWikiChangesFallbackIsUtc:
    """The getRecentChanges-missing fallback compares the UTC
    ``lastModified`` of each page with the UTC ``since_timestamp``."""

    @staticmethod
    def _page(name: str, ts: int) -> dict:
        return {
            "name": name,
            "lastModified": xmlrpc.client.DateTime(
                time.strftime("%Y%m%dT%H:%M:%S", time.gmtime(ts))
            ),
        }

    def test_filters_in_utc(self, mock_config, non_utc_tz):
        client = TracClient(mock_config)
        pages = {
            "Before": self._page("Before", TS - 1),
            "At": self._page("At", TS),
            "After": self._page("After", TS + 1),
        }
        missing = xmlrpc.client.Fault(
            -32601, "Method not found: wiki.getRecentChanges"
        )
        with (
            patch.object(client, "_rpc_request", side_effect=missing),
            patch.object(
                client, "list_wiki_pages", return_value=list(pages)
            ),
            patch.object(
                client,
                "get_wiki_page_info",
                side_effect=lambda name: pages[name],
            ),
        ):
            changes = client.get_recent_wiki_changes(TS)
        assert [c["name"] for c in changes] == ["After", "At"]

    def test_timegm_not_mktime(self, mock_config, non_utc_tz):
        """The cutoff is the exact UTC second, whatever the host zone."""
        client = TracClient(mock_config)
        page = self._page("P", TS)
        assert calendar.timegm(page["lastModified"].timetuple()) == TS
        missing = xmlrpc.client.Fault(
            -32601, "Method not found: wiki.getRecentChanges"
        )
        with (
            patch.object(client, "_rpc_request", side_effect=missing),
            patch.object(client, "list_wiki_pages", return_value=["P"]),
            patch.object(
                client, "get_wiki_page_info", return_value=page
            ),
        ):
            assert client.get_recent_wiki_changes(TS) == [page]
            assert client.get_recent_wiki_changes(TS + 1) == []
