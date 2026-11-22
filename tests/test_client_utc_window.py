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
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from trac_mcp_server.core.client import TracClient, _utc_xmlrpc_datetime

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
    try:
        monkeypatch.setenv("TZ", request.param)
        time.tzset()
        # The zone must really differ from UTC at TS (not so if tzdata
        # is missing), or the test proves nothing.
        if (
            time.strftime("%Y%m%dT%H:%M:%S", time.localtime(TS))
            == TS_WIRE
        ):
            pytest.skip(f"zone {request.param} is not available")
        yield request.param
    finally:
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


def _wire(ts: int) -> str:
    """lastModified as Trac sends it: compact UTC, one dateTime.iso8601."""
    return time.strftime("%Y%m%dT%H:%M:%S", time.gmtime(ts))


MISSING_METHOD = xmlrpc.client.Fault(
    -32601, "Method not found: wiki.getRecentChanges"
)


class TestRecentWikiChangesFallbackIsUtc:
    """The getRecentChanges-missing fallback compares the UTC
    ``lastModified`` of each page with the UTC ``since_timestamp``.

    On the wire ``lastModified`` is a ``dateTime.iso8601`` element, which
    the client's decoder returns as a naive ``datetime`` holding UTC
    (Trac #270); the string rows feed the older compact wire form."""

    @staticmethod
    def _fallback(client, pages: dict, since: int):
        """Run the fallback with ``pages`` (name -> info dict)."""
        with (
            patch.object(
                client, "_rpc_request", side_effect=MISSING_METHOD
            ),
            patch.object(
                client, "list_wiki_pages", return_value=list(pages)
            ),
            patch.object(
                client,
                "get_wiki_page_info",
                side_effect=lambda name: pages[name],
            ),
        ):
            return client.get_recent_wiki_changes(since)

    @staticmethod
    def _page(name: str, ts: int) -> dict:
        return {"name": name, "lastModified": _wire(ts)}

    def test_filters_in_utc(self, mock_config, non_utc_tz):
        client = TracClient(mock_config)
        pages = {
            "Before": self._page("Before", TS - 1),
            "At": self._page("At", TS),
            "After": self._page("After", TS + 1),
        }
        changes = self._fallback(client, pages, TS)
        assert [c["name"] for c in changes] == ["After", "At"]

    def test_just_after_in_just_before_out(
        self, mock_config, non_utc_tz
    ):
        client = TracClient(mock_config)
        pages = {
            "Before": self._page("Before", TS - 1),
            "After": self._page("After", TS + 1),
        }
        assert [
            c["name"] for c in self._fallback(client, pages, TS)
        ] == ["After"]
        assert [
            c["name"] for c in self._fallback(client, pages, TS + 1)
        ] == ["After"]
        assert self._fallback(client, pages, TS + 2) == []

    def test_wire_string_is_not_mutated(self, mock_config, non_utc_tz):
        client = TracClient(mock_config)
        page = self._page("P", TS)
        assert self._fallback(client, {"P": page}, TS) == [page]
        assert page["lastModified"] == TS_WIRE

    def test_since_zero_returns_every_page(self, mock_config):
        client = TracClient(mock_config)
        pages = {
            "Old": self._page("Old", 1),
            "NoDate": {"name": "NoDate"},
        }
        names = {c["name"] for c in self._fallback(client, pages, 0)}
        assert names == {"Old", "NoDate"}

    def test_datetime_typed_value_still_works(
        self, mock_config, non_utc_tz
    ):
        """``_parse_trac_time`` takes a DateTime too (older setups)."""
        client = TracClient(mock_config)
        page = {
            "name": "P",
            "lastModified": xmlrpc.client.DateTime(TS_WIRE),
        }
        assert calendar.timegm(page["lastModified"].timetuple()) == TS
        assert self._fallback(client, {"P": page}, TS) == [page]
        assert self._fallback(client, {"P": page}, TS + 1) == []

    def test_page_without_last_modified(self, mock_config, non_utc_tz):
        """A page with no lastModified is excluded for a nonzero since,
        does not crash the filter, and does not break the sort."""
        client = TracClient(mock_config)
        pages = {
            "NoDate": {"name": "NoDate"},
            "Garbage": {"name": "Garbage", "lastModified": "yesterday"},
            "Old": self._page("Old", TS - 10),
            "New": self._page("New", TS + 10),
            "Newer": self._page("Newer", TS + 20),
        }
        changes = self._fallback(client, pages, TS)
        assert [c["name"] for c in changes] == ["Newer", "New"]
        # Included under since=0, the unparseable ones sort last.
        everything = self._fallback(client, pages, 0)
        assert [c["name"] for c in everything[:3]] == [
            "Newer",
            "New",
            "Old",
        ]
        assert {c["name"] for c in everything[3:]} == {
            "NoDate",
            "Garbage",
        }

    @patch("trac_mcp_server.core.client.requests.Session.post")
    def test_through_a_real_xmlrpc_response(
        self, mock_post, mock_config, non_utc_tz
    ):
        """End to end: a ``<dateTime.iso8601>`` struct member."""
        info = {
            "A": "<value><struct><member><name>name</name><value>"
            "<string>A</string></value></member><member>"
            "<name>lastModified</name><value><dateTime.iso8601>"
            f"{_wire(TS + 5)}</dateTime.iso8601></value></member>"
            "</struct></value>",
            "B": "<value><struct><member><name>name</name><value>"
            "<string>B</string></value></member><member>"
            "<name>lastModified</name><value><dateTime.iso8601>"
            f"{_wire(TS - 5)}</dateTime.iso8601></value></member>"
            "</struct></value>",
        }

        def post(url, data=None, **kwargs):
            (params, method) = xmlrpc.client.loads(data)
            response = Mock()
            response.status_code = 200
            if method == "wiki.getRecentChanges":
                response.content = xmlrpc.client.dumps(
                    MISSING_METHOD, methodresponse=True
                ).encode()
            elif method == "wiki.getAllPages":
                response.content = xmlrpc.client.dumps(
                    (["A", "B"],), methodresponse=True
                ).encode()
            else:
                assert method == "wiki.getPageInfo"
                response.content = (
                    "<?xml version='1.0'?><methodResponse><params><param>"
                    + info[params[0]]
                    + "</param></params></methodResponse>"
                ).encode()
            return response

        mock_post.side_effect = post
        client = TracClient(mock_config)
        changes = client.get_recent_wiki_changes(TS)
        assert [c["name"] for c in changes] == ["A"]
        # Decoded as a naive datetime holding UTC (Trac #270).
        assert changes[0]["lastModified"] == datetime(
            *time.gmtime(TS + 5)[:6]
        )


class TestUtcXmlrpcDatetimeFormat:
    def test_zero_is_the_epoch(self):
        assert _utc_xmlrpc_datetime(0).value == EPOCH_WIRE

    @pytest.mark.parametrize(
        "ts", [-50000000000, -62135596800, 253402300799]
    )
    def test_years_are_padded_to_four_digits(self, ts):
        expected = "%04d%02d%02dT%02d:%02d:%02d" % time.gmtime(ts)[:6]
        assert _utc_xmlrpc_datetime(ts).value == expected
        assert len(expected.split("T")[0]) >= 8
