"""The XML-RPC reply decoder and the date helpers behind it (Trac
/trac_mcp_server #270).

``_rpc_request`` used to walk the reply with ElementTree: a nested struct
leaked its members into the parent, ``<string/>`` came back as ``None``, an
untyped ``<value>text</value>`` raised ``IndexError``, and ``dateTime.iso8601``
came back as a compact string. It now decodes with the stdlib's
``xmlrpc.client.loads(..., use_builtin_types=True)``, so a ``dateTime`` is a
naive ``datetime`` holding UTC, and every date helper has to take one.
"""

import calendar
import time
import xmlrpc.client
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from trac_mcp_server.core.client import TracClient, _wiki_time_key

# 2026-11-21T20:44:11 UTC.
TS = 1795293851
TS_WIRE = "20261121T20:44:11"
TS_DT = datetime(2026, 11, 21, 20, 44, 11)


def _reply(value_xml: str) -> bytes:
    return (
        '<?xml version="1.0"?><methodResponse><params><param>'
        f"<value>{value_xml}</value></param></params></methodResponse>"
    ).encode()


def _decode(mock_post, mock_config, body: bytes):
    response = Mock()
    response.status_code = 200
    response.content = body
    mock_post.return_value = response
    return TracClient(mock_config)._rpc_request("ticket", "get", 1)


def _struct(*members: tuple[str, str]) -> str:
    return "<struct>{}</struct>".format(
        "".join(
            f"<member><name>{n}</name><value>{v}</value></member>"
            for n, v in members
        )
    )


@pytest.fixture
def post():
    with patch(
        "trac_mcp_server.core.client.requests.Session.post"
    ) as mock_post:
        yield mock_post


class TestDecoder:
    def test_nested_struct_members_do_not_leak_into_the_parent(
        self, post, mock_config
    ):
        inner = _struct(
            ("name", "<string>inner</string>"), ("x", "<i4>1</i4>")
        )
        body = _reply(
            _struct(
                ("name", "<string>outer</string>"),
                ("child", inner),
            )
        )
        result = _decode(post, mock_config, body)
        assert result == {
            "name": "outer",
            "child": {"name": "inner", "x": 1},
        }

    def test_empty_string_is_empty_not_none(self, post, mock_config):
        assert _decode(post, mock_config, _reply("<string/>")) == ""
        assert (
            _decode(post, mock_config, _reply("<string></string>"))
            == ""
        )

    def test_untyped_value_is_a_string(self, post, mock_config):
        assert _decode(post, mock_config, _reply("abc")) == "abc"

    def test_untyped_member_value_is_a_string(self, post, mock_config):
        body = _reply(_struct(("k", "abc")))
        assert _decode(post, mock_config, body) == {"k": "abc"}

    def test_i8_is_an_int(self, post, mock_config):
        result = _decode(
            post, mock_config, _reply("<i8>9007199254740993</i8>")
        )
        assert result == 9007199254740993
        assert isinstance(result, int)

    def test_nil_is_none(self, post, mock_config):
        assert _decode(post, mock_config, _reply("<nil/>")) is None

    def test_date_time_is_a_naive_datetime(self, post, mock_config):
        result = _decode(
            post,
            mock_config,
            _reply(f"<dateTime.iso8601>{TS_WIRE}</dateTime.iso8601>"),
        )
        assert result == TS_DT
        assert isinstance(result, datetime)
        assert result.tzinfo is None

    def test_fault_raises_with_code_and_string(self, post, mock_config):
        body = (
            '<?xml version="1.0"?><methodResponse><fault><value><struct>'
            "<member><name>faultCode</name><value><int>404</int></value>"
            "</member><member><name>faultString</name><value><string>"
            "Wiki page not found</string></value></member></struct>"
            "</value></fault></methodResponse>"
        ).encode()
        with pytest.raises(xmlrpc.client.Fault) as exc:
            _decode(post, mock_config, body)
        assert exc.value.faultCode == 404
        assert exc.value.faultString == "Wiki page not found"

    def test_base64_is_bytes(self, post, mock_config):
        result = _decode(
            post, mock_config, _reply("<base64>SGVsbG8=</base64>")
        )
        assert result == b"Hello"
        assert isinstance(result, bytes)

    def test_array_and_scalars(self, post, mock_config):
        body = _reply(
            "<array><data><value><int>1</int></value>"
            "<value><boolean>1</boolean></value>"
            "<value><double>2.5</double></value>"
            "<value><string>s</string></value></data></array>"
        )
        assert _decode(post, mock_config, body) == [1, True, 2.5, "s"]

    def test_a_reply_that_is_not_xml_raises_expat_error(
        self, post, mock_config
    ):
        from xml.parsers.expat import ExpatError

        with pytest.raises(ExpatError):
            _decode(post, mock_config, b"<html>502 Bad Gateway")


class TestParseTracTime:
    def test_naive_datetime_is_utc(self):
        assert TracClient._parse_trac_time(TS_DT) == TS

    def test_naive_datetime_is_utc_whatever_the_host_zone(
        self, monkeypatch
    ):
        monkeypatch.setenv("TZ", "Asia/Kolkata")
        time.tzset()
        try:
            assert TracClient._parse_trac_time(TS_DT) == TS
        finally:
            monkeypatch.undo()
            time.tzset()

    def test_aware_datetime_is_converted_to_utc(self):
        from datetime import timedelta, timezone

        aware = datetime(
            2026,
            11,
            22,
            2,
            14,
            11,
            tzinfo=timezone(timedelta(hours=5.5)),
        )
        assert TracClient._parse_trac_time(aware) == TS

    def test_strings_and_datetime_wrapper_still_work(self):
        assert TracClient._parse_trac_time(TS_WIRE) == TS
        assert TracClient._parse_trac_time("2026-11-21T20:44:11") == TS
        assert (
            TracClient._parse_trac_time(xmlrpc.client.DateTime(TS_WIRE))
            == TS
        )
        assert TracClient._parse_trac_time("yesterday") is None
        assert TracClient._parse_trac_time(None) is None


class TestWikiTimeKey:
    def test_datetime_is_the_compact_string(self):
        assert _wiki_time_key(TS_DT) == TS_WIRE

    def test_year_below_1000_is_padded(self):
        assert _wiki_time_key(datetime(999, 1, 2, 3, 4, 5)) == (
            "09990102T03:04:05"
        )

    def test_keys_sort_as_times(self):
        later = datetime(2026, 11, 21, 20, 44, 12)
        assert _wiki_time_key(TS_DT) < _wiki_time_key(later)  # type: ignore[operator]

    def test_strings_and_wrapper_unchanged(self):
        assert _wiki_time_key(TS_WIRE) == TS_WIRE
        assert (
            _wiki_time_key(xmlrpc.client.DateTime(TS_WIRE)) == TS_WIRE
        )
        assert _wiki_time_key("") is None
        assert _wiki_time_key(None) is None
        assert _wiki_time_key(5) is None


MISSING_METHOD = xmlrpc.client.Fault(
    -32601, "Method not found: wiki.getRecentChanges"
)


class TestRecentChangesFallbackWithDatetimes:
    """Once dates decode as ``datetime``, the fallback's
    ``_parse_trac_time`` must take one, or every page is dropped
    whenever ``since_timestamp`` is not 0."""

    @staticmethod
    def _fallback(client, pages: dict, since: int):
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
        return {
            "name": name,
            "lastModified": datetime(*time.gmtime(ts)[:6]),
        }

    def test_nonzero_since_filters_and_sorts_datetime_values(
        self, mock_config
    ):
        client = TracClient(mock_config)
        pages = {
            "Before": self._page("Before", TS - 1),
            "At": self._page("At", TS),
            "After": self._page("After", TS + 1),
        }
        changes = self._fallback(client, pages, TS)
        assert [c["name"] for c in changes] == ["After", "At"]
        assert (
            calendar.timegm(changes[0]["lastModified"].timetuple())
            == TS + 1
        )

    def test_since_just_past_every_page_is_empty(self, mock_config):
        client = TracClient(mock_config)
        pages = {"A": self._page("A", TS), "B": self._page("B", TS - 5)}
        assert self._fallback(client, pages, TS + 1) == []
