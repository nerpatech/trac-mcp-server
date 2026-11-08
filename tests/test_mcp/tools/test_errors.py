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
    classify_fault,
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
        """Version conflict — fault code 409 returns version_conflict."""
        fault = xmlrpc.client.Fault(
            409, "[wiki-version-conflict current_version=7] page moved"
        )
        result = translate_xmlrpc_error(fault, "wiki")

        text = _get_error_text(result)
        assert "version_conflict" in text

    def test_version_text_alone_is_not_a_conflict(self):
        """Trac #186: text without the 409 code is no version conflict."""
        fault = xmlrpc.client.Fault(1, "Page version conflict detected")
        result = translate_xmlrpc_error(fault, "wiki")

        text = _get_error_text(result)
        assert "server_error" in text
        assert "version_conflict" not in text

    def test_page_name_containing_version_is_server_error(self):
        """Trac #186: a page name containing "version" is no conflict."""
        fault = xmlrpc.client.Fault(
            1,
            "'Invalid Wiki page name 'VersionNotes/../x'' while executing"
            " 'wiki.putPage()'",
        )
        result = translate_xmlrpc_error(fault, "wiki")

        text = _get_error_text(result)
        assert "server_error" in text
        assert "version_conflict" not in text

    def test_milestone_named_version_is_server_error(self):
        """Trac #186: a milestone named "Version 2.0" is no conflict."""
        fault = xmlrpc.client.Fault(
            1,
            "'Version 2.0 has tickets' while executing"
            " 'ticket.milestone.delete()'",
        )
        result = translate_xmlrpc_error(fault, "milestone")

        text = _get_error_text(result)
        assert "server_error" in text
        assert "version_conflict" not in text

    def test_not_found_by_code_404(self):
        """Trac #186: ResourceNotFound is code 404, whatever its text."""
        fault = xmlrpc.client.Fault(
            404, "No version 3 for comment 2 on Ticket #5"
        )
        result = translate_xmlrpc_error(fault, "ticket")

        text = _get_error_text(result)
        assert "not_found" in text
        assert "version_conflict" not in text

    def test_permission_denied_by_code_403(self):
        """Trac #186: PermissionError is code 403, whatever its text."""
        fault = xmlrpc.client.Fault(403, "WIKI_ADMIN required")
        result = translate_xmlrpc_error(fault, "wiki")

        text = _get_error_text(result)
        assert "permission_denied" in text

    def test_version_conflict_by_code_409(self):
        """Trac #186: code 409 is a version conflict, whatever its text."""
        fault = xmlrpc.client.Fault(409, "x")
        result = translate_xmlrpc_error(fault, "ticket")

        text = _get_error_text(result)
        assert "version_conflict" in text

    def test_409_missing_page_conflict_is_version_conflict(self):
        """Trac #186: the plugin's missing-page conflict says "does not
        exist" but is code 409, so it is a conflict, not not_found."""
        fault = xmlrpc.client.Fault(
            409,
            "[wiki-version-conflict current_version=none] Version"
            ' conflict on wiki page "X": you tried to update version 3'
            " but the page does not exist.",
        )
        result = translate_xmlrpc_error(fault, "wiki", "X")

        text = _get_error_text(result)
        assert "version_conflict" in text
        assert "not_found" not in text

    def test_409_conflict_on_page_named_permission_is_version_conflict(
        self,
    ):
        """Trac #186: a 409 on a page named PermissionPolicy is a
        conflict, not permission_denied."""
        fault = xmlrpc.client.Fault(
            409,
            "[wiki-version-conflict current_version=4] Version conflict"
            ' on wiki page "PermissionPolicy": you tried to update'
            " version 3 but its current version is 4.",
        )
        result = translate_xmlrpc_error(
            fault, "wiki", "PermissionPolicy"
        )

        text = _get_error_text(result)
        assert "version_conflict" in text
        assert "permission_denied" not in text

    def test_403_on_milestone_named_not_found_is_permission_denied(
        self,
    ):
        """Trac #186: a 403 whose text names "Not found bugs" is a
        permission fault, not not_found."""
        fault = xmlrpc.client.Fault(
            403,
            "WIKI_VIEW privileges are required to perform this operation"
            " on Milestone Not found bugs. You don't have the required"
            " permissions.",
        )
        result = translate_xmlrpc_error(fault, "milestone")

        text = _get_error_text(result)
        assert "permission_denied" in text
        assert "TICKET_ADMIN" in text
        assert "not_found" not in text

    def test_404_on_page_named_permission_is_not_found(self):
        """Trac #186: a 404 on a page named PermissionNotes is not_found,
        not permission_denied."""
        fault = xmlrpc.client.Fault(
            404, 'Wiki page "PermissionNotes" does not exist'
        )
        result = translate_xmlrpc_error(
            fault, "wiki", "PermissionNotes"
        )

        text = _get_error_text(result)
        assert "not_found" in text
        assert "permission_denied" not in text

    def test_not_modified_is_not_a_version_conflict(self):
        """Ticket #174: "Page not modified" is no version conflict."""
        fault = xmlrpc.client.Fault(1, "Page not modified")
        result = translate_xmlrpc_error(fault, "wiki", "X")

        text = _get_error_text(result)
        assert "server_error" in text
        assert "version_conflict" not in text

    def test_version_conflict_with_entity(self):
        """Version conflict with entity name — substitutes name in action."""
        fault = xmlrpc.client.Fault(
            409, "[wiki-version-conflict current_version=7] page moved"
        )
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


class TestQuotedNamesAreNotEvidence:
    """A quoted name in a fault's text is not evidence (Trac #188)."""

    @staticmethod
    def _type(fault, domain):
        text = _get_error_text(translate_xmlrpc_error(fault, domain))
        return text.split("):", 1)[0].removeprefix("Error (")

    def test_page_name_with_permission_is_server_error(self):
        fault = xmlrpc.client.Fault(
            1,
            "'Invalid Wiki page name 'PermissionNotes/../x'' while "
            "executing 'wiki.putPage()'",
        )
        assert self._type(fault, "wiki") == "server_error"

    def test_double_quoted_name_with_not_found_is_server_error(self):
        fault = xmlrpc.client.Fault(
            1,
            "'Cannot frobnicate \"NotFoundHandling\"' while executing "
            "'wiki.putPage()'",
        )
        assert self._type(fault, "wiki") == "server_error"

    def test_milestone_name_with_already_is_server_error(self):
        fault = xmlrpc.client.Fault(
            1,
            "'Milestone \"Already Shipped\" has open tickets' while "
            "executing 'ticket.milestone.delete()'",
        )
        assert self._type(fault, "milestone") == "server_error"

    def test_plugin_comment_not_found_still_not_found(self):
        fault = xmlrpc.client.Fault(
            1,
            "'Comment 3 not found on ticket #5' while executing "
            "'ticket.comment.get()'",
        )
        assert self._type(fault, "ticket") == "not_found"

    def test_milestone_already_exists_still_already_exists(self):
        fault = xmlrpc.client.Fault(
            1,
            "'Milestone \"v1\" already exists.' while executing "
            "'ticket.milestone.create()'",
        )
        assert self._type(fault, "milestone") == "already_exists"

    def test_wiki_page_already_exists_stays_server_error(self):
        # The already_exists arm is milestone-only.
        fault = xmlrpc.client.Fault(
            1,
            "'The page \\'X\\' already exists.' while executing "
            "'wiki.putPage()'",
        )
        assert self._type(fault, "wiki") == "server_error"

    def test_apostrophe_in_a_word_does_not_swallow_text(self):
        fault = xmlrpc.client.Fault(
            1, "You don't have permission to do that"
        )
        assert self._type(fault, "wiki") == "permission_denied"


class TestClassifyFault:
    """Direct rows for classify_fault() (Trac #188)."""

    def test_409_is_version_conflict(self):
        fault = xmlrpc.client.Fault(409, "not found permission")
        assert classify_fault(fault) == "version_conflict"

    def test_404_is_not_found(self):
        assert (
            classify_fault(xmlrpc.client.Fault(404, "x")) == "not_found"
        )

    def test_403_is_permission_denied(self):
        fault = xmlrpc.client.Fault(
            403, "WIKI_VIEW privileges are required"
        )
        assert classify_fault(fault) == "permission_denied"

    def test_code_beats_text(self):
        fault = xmlrpc.client.Fault(
            403, "'Not found bugs' does not exist"
        )
        assert classify_fault(fault) == "permission_denied"

    def test_code_1_text_not_found(self):
        fault = xmlrpc.client.Fault(1, "Page not found")
        assert classify_fault(fault) == "not_found"

    def test_code_1_text_permission(self):
        fault = xmlrpc.client.Fault(1, "Permission denied")
        assert classify_fault(fault) == "permission_denied"

    def test_already_exists_only_in_milestone_domain(self):
        fault = xmlrpc.client.Fault(1, "Milestone already exists.")
        assert classify_fault(fault, "milestone") == "already_exists"
        assert classify_fault(fault, "wiki") is None
        assert classify_fault(fault) is None

    def test_quoted_name_is_not_evidence(self):
        fault = xmlrpc.client.Fault(
            1,
            "'Invalid Wiki page name 'PermissionNotes/../x'' while "
            "executing 'wiki.putPage()'",
        )
        assert classify_fault(fault, "wiki") is None

    def test_unclassified_is_none(self):
        assert classify_fault(xmlrpc.client.Fault(500, "boom")) is None

    def test_single_quoted_name_with_an_apostrophe_is_stripped(self):
        fault = xmlrpc.client.Fault(
            1, "Invalid Wiki page name 'Bob's/PermissionNotes'"
        )
        assert classify_fault(fault, "wiki") is None

    def test_contraction_still_classifies(self):
        fault = xmlrpc.client.Fault(
            1, "You don't have permission to do that"
        )
        assert classify_fault(fault, "wiki") == "permission_denied"

    def test_unquoted_comment_not_found_still_classifies(self):
        fault = xmlrpc.client.Fault(
            1, "Comment 3 not found on ticket #5"
        )
        assert classify_fault(fault, "ticket") == "not_found"

    def test_whole_message_quoted_without_the_wrapper(self):
        fault = xmlrpc.client.Fault(1, "'Wiki page \"Foo\" not found'")
        assert classify_fault(fault, "wiki") == "not_found"

    def test_whole_message_in_double_quotes(self):
        fault = xmlrpc.client.Fault(1, '"Page not found"')
        assert classify_fault(fault, "wiki") == "not_found"

    def test_typographic_single_quoted_name_is_stripped(self):
        fault = xmlrpc.client.Fault(
            1, "Invalid name \u2018PermissionNotes\u2019"
        )
        assert classify_fault(fault, "wiki") is None

    def test_typographic_double_quoted_name_is_stripped(self):
        fault = xmlrpc.client.Fault(
            1, "Invalid name \u201cNotFoundNotes not found\u201d"
        )
        assert classify_fault(fault, "wiki") is None


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


# ---------------------------------------------------------------------------
# The fault readers live in core.faults (Trac #191)
# ---------------------------------------------------------------------------


class TestFaultReadersLiveInCoreFaults:
    """classify_fault and unwrap_tracrpc_fault moved to core/faults.py,
    which imports only the standard library; the old import paths are
    the same objects."""

    def test_errors_re_exports_classify_fault(self):
        from trac_mcp_server.core import faults
        from trac_mcp_server.mcp.tools import errors

        assert errors.classify_fault is faults.classify_fault

    def test_client_re_exports_unwrap_tracrpc_fault(self):
        from trac_mcp_server.core import client, faults

        assert (
            client.unwrap_tracrpc_fault is faults.unwrap_tracrpc_fault
        )
