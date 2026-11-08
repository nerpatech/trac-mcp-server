"""Tests for mcp/tools/errors.py — error response builders and utilities.

Covers:
- build_error_response() structure and format
- translate_xmlrpc_error() domain-specific error mapping
- format_timestamp() for various input types
"""

import xmlrpc.client
from datetime import datetime, timezone

import mcp.types as types

from trac_mcp_server.mcp.tools.errors import (
    build_error_response,
    format_timestamp,
    translate_xmlrpc_error,
)


def _get_error_text(result: types.CallToolResult) -> str:
    """Extract text from first content item with type narrowing for Pyright."""
    content = result.content[0]
    assert isinstance(content, types.TextContent)
    return content.text


# ---------------------------------------------------------------------------
# build_error_response tests
# ---------------------------------------------------------------------------


class TestBuildErrorResponse:
    """Tests for build_error_response()."""

    def test_returns_call_tool_result(self):
        """Returns a CallToolResult instance."""
        result = build_error_response(
            "not_found", "Not found", "Try again"
        )
        assert isinstance(result, types.CallToolResult)

    def test_is_error_flag(self):
        """isError is set to True."""
        result = build_error_response(
            "not_found", "Not found", "Try again"
        )
        assert result.isError is True

    def test_single_text_content(self):
        """Result contains a single TextContent."""
        result = build_error_response(
            "not_found", "Not found", "Try again"
        )
        assert len(result.content) == 1
        assert isinstance(result.content[0], types.TextContent)
        assert result.content[0].type == "text"

    def test_error_format(self):
        """Text format is 'Error ({type}): {message}\\n\\nAction: {action}'."""
        result = build_error_response(
            "not_found", "Page missing", "Use wiki_search"
        )
        content = result.content[0]
        assert isinstance(content, types.TextContent)
        assert (
            content.text
            == "Error (not_found): Page missing\n\nAction: Use wiki_search"
        )

    def test_permission_denied_type(self):
        """permission_denied error type renders correctly."""
        result = build_error_response(
            "permission_denied", "Access denied", "Contact admin"
        )
        content = result.content[0]
        assert isinstance(content, types.TextContent)
        assert "Error (permission_denied):" in content.text

    def test_server_error_type(self):
        """server_error error type renders correctly."""
        result = build_error_response(
            "server_error", "Internal failure", "Retry later"
        )
        content = result.content[0]
        assert isinstance(content, types.TextContent)
        assert "Error (server_error):" in content.text

    def test_special_characters(self):
        """Handles messages with quotes and newlines."""
        result = build_error_response(
            "not_found",
            'Page "test\'s page" not\nfound',
            "Search for alternatives",
        )
        content = result.content[0]
        assert isinstance(content, types.TextContent)
        assert '"test\'s page"' in content.text
        assert "not\nfound" in content.text


# ---------------------------------------------------------------------------
# translate_xmlrpc_error tests
# ---------------------------------------------------------------------------


class TestTranslateXmlrpcError:
    """Tests for translate_xmlrpc_error()."""

    def test_not_found_ticket(self):
        """Ticket not found — returns not_found with ticket corrective action."""
        fault = xmlrpc.client.Fault(1, "Ticket 99 not found")
        result = translate_xmlrpc_error(fault, "ticket")

        text = _get_error_text(result)
        assert "not_found" in text
        assert (
            "ticket_search" in text.lower() or "ticket" in text.lower()
        )

    def test_not_found_wiki(self):
        """Wiki not found without entity name — mentions wiki_search."""
        fault = xmlrpc.client.Fault(1, "Page does not exist")
        result = translate_xmlrpc_error(fault, "wiki")

        text = _get_error_text(result)
        assert "not_found" in text
        assert "wiki_search" in text

    def test_not_found_wiki_with_entity_name(self):
        """Wiki not found with entity name — includes page name in suggestion."""
        fault = xmlrpc.client.Fault(1, "Page 'MyPage' not found")
        result = translate_xmlrpc_error(
            fault, "wiki", entity_name="MyPage"
        )

        text = _get_error_text(result)
        assert "not_found" in text
        assert "MyPage" in text

    def test_permission_denied(self):
        """Permission denied by keyword — returns permission_denied."""
        fault = xmlrpc.client.Fault(
            403, "Permission denied for this resource"
        )
        result = translate_xmlrpc_error(fault, "ticket")

        text = _get_error_text(result)
        assert "permission_denied" in text

    def test_milestone_permission_by_code(self):
        """Milestone fault code 403 — returns permission_denied with TICKET_ADMIN."""
        fault = xmlrpc.client.Fault(403, "Forbidden operation")
        result = translate_xmlrpc_error(fault, "milestone")

        text = _get_error_text(result)
        assert "permission_denied" in text
        assert "TICKET_ADMIN" in text

    def test_milestone_already_exists(self):
        """Milestone already exists — returns already_exists."""
        fault = xmlrpc.client.Fault(
            1, "Milestone 'v1.0' already exists"
        )
        result = translate_xmlrpc_error(fault, "milestone")

        text = _get_error_text(result)
        assert "already_exists" in text
        assert "milestone_update" in text

    def test_version_conflict(self):
        """Version conflict — returns version_conflict."""
        fault = xmlrpc.client.Fault(1, "Page version conflict detected")
        result = translate_xmlrpc_error(fault, "wiki")

        text = _get_error_text(result)
        assert "version_conflict" in text

    def test_not_modified_is_not_a_version_conflict(self):
        """Ticket #174: "Page not modified" is no version conflict."""
        fault = xmlrpc.client.Fault(1, "Page not modified")
        result = translate_xmlrpc_error(fault, "wiki", "X")

        text = _get_error_text(result)
        assert "server_error" in text
        assert "version_conflict" not in text

    def test_version_conflict_with_entity(self):
        """Version conflict with entity name — substitutes name in action."""
        fault = xmlrpc.client.Fault(1, "Page version conflict")
        result = translate_xmlrpc_error(
            fault, "wiki", entity_name="TestPage"
        )

        text = _get_error_text(result)
        assert "version_conflict" in text
        assert "TestPage" in text

    def test_generic_fault_falls_back_to_server_error(self):
        """Unknown fault string — falls back to server_error."""
        fault = xmlrpc.client.Fault(
            500, "Something completely unexpected happened"
        )
        result = translate_xmlrpc_error(fault, "ticket")

        text = _get_error_text(result)
        assert "server_error" in text

    def test_unknown_domain_falls_back_to_ticket(self):
        """Unknown domain — uses ticket messages as fallback."""
        fault = xmlrpc.client.Fault(1, "Resource not found")
        result = translate_xmlrpc_error(fault, "unknown_domain")

        text = _get_error_text(result)
        assert "not_found" in text
        # Ticket's not_found action: "Use ticket_search..."
        assert (
            "ticket_search" in text.lower() or "ticket" in text.lower()
        )


# ---------------------------------------------------------------------------
# format_timestamp tests
# ---------------------------------------------------------------------------


class TestFormatTimestamp:
    """Tests for format_timestamp()."""

    def test_datetime_object(self):
        """datetime -> 'YYYY-MM-DD HH:MM'."""
        dt = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)
        result = format_timestamp(dt)
        assert result == "2026-01-15 10:30"

    def test_unix_timestamp(self):
        """Integer timestamp -> formatted UTC date."""
        # 1700000000 = 2023-11-14 22:13:20 UTC
        result = format_timestamp(1700000000)
        assert result.startswith("2023-11-14")
        assert "22:13" in result

    def test_other_type_string(self):
        """String input -> str() passthrough."""
        result = format_timestamp("some date string")
        assert result == "some date string"

    def test_other_type_none(self):
        """None -> str(None)."""
        result = format_timestamp(None)
        assert result == "None"

    def test_xmlrpc_datetime(self):
        """xmlrpc.client.DateTime -> 'YYYY-MM-DD HH:MM'.

        Trac's XML-RPC responses return timestamps wrapped in
        xmlrpc.client.DateTime. The helper must parse those into a
        displayable date string, not fall through to str().
        """
        xml_dt = xmlrpc.client.DateTime("20260406T00:03:15")
        result = format_timestamp(xml_dt)
        # Exact time depends on local tz; check date component only.
        assert result.startswith("2026-04-0")
        # Must not be the raw iso-like passthrough
        assert "T" not in result

    def test_iso8601_compact_string(self):
        """Compact iso8601 string (no separators) -> 'YYYY-MM-DD HH:MM'.

        trac-mcp-server's custom _parse_xmlrpc_value strips the
        DateTime wrapper and returns the raw text, so handlers receive
        strings like "20260406T00:03:15". format_timestamp must parse
        these for display.
        """
        result = format_timestamp("20260406T00:03:15")
        assert result == "2026-04-06 00:03"

    def test_unknown_string_passthrough(self):
        """Unrecognized strings still pass through unchanged."""
        assert format_timestamp("2026-02-01") == "2026-02-01"
        assert (
            format_timestamp("some date string") == "some date string"
        )
