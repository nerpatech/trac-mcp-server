"""Tests for ticket tool handlers."""

import asyncio
import calendar
import time
import unittest
import xmlrpc.client
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types as types
import pytest

from trac_mcp_server.config import Config
from trac_mcp_server.core.client import TicketCreateTimeout, TracClient
from trac_mcp_server.mcp.tools import TICKET_TOOLS
from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.mcp.tools.ticket_read import (
    TICKET_READ_SPECS,
    _handle_actions,
    _handle_changelog,
    _handle_fields,
    _handle_get,
    _handle_search,
)
from trac_mcp_server.mcp.tools.ticket_write import (
    TICKET_WRITE_SPECS,
    _comment_cnum,
    _comment_mismatch,
    _handle_create,
    _handle_delete,
    _handle_update,
)


def _spec_client(trac_url="https://trac.example.com"):
    """A MagicMock(spec=TracClient) carrying a real Config.

    `config` is assigned in TracClient.__init__, so a spec mock refuses
    the attribute until it is set; ticket_search/ticket_get read it for
    the ticket's web URL.
    """
    client = MagicMock(spec=TracClient)
    client.config = Config(
        trac_url=trac_url, username="u", password="p"
    )
    return client


class TestTicketDeleteSchema(unittest.TestCase):
    """Tests for ticket_delete tool schema."""

    def test_ticket_delete_schema(self):
        """Test ticket_delete tool has correct schema."""
        tool = next(
            t for t in TICKET_TOOLS if t.name == "ticket_delete"
        )
        self.assertEqual(tool.name, "ticket_delete")
        description = tool.description or ""
        self.assertIn("delete", description.lower())
        self.assertIn("TICKET_ADMIN", description)
        schema = tool.inputSchema
        self.assertIn("ticket_id", schema["properties"])
        self.assertEqual(schema["required"], ["ticket_id"])
        self.assertEqual(
            schema["properties"]["ticket_id"]["type"], "integer"
        )
        self.assertEqual(
            schema["properties"]["ticket_id"]["minimum"], 1
        )


class TestHandleDelete(unittest.TestCase):
    """Tests for _handle_delete handler."""

    def setUp(self):
        self.mock_client = MagicMock()

    def test_handle_delete_success(self):
        """Test _handle_delete deletes ticket successfully."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            # First call: get_ticket (existence check), Second call: delete_ticket
            mock_run_sync.return_value = True

            result = asyncio.run(
                _handle_delete(self.mock_client, {"ticket_id": 42})
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertEqual(len(result.content), 1)
            self.assertIn("Deleted ticket #42", result.content[0].text)

            # Verify both get_ticket (existence check) and delete_ticket were called
            self.assertEqual(mock_run_sync.call_count, 2)
            # First call: existence check with get_ticket
            first_call_args = mock_run_sync.call_args_list[0][0]
            self.assertEqual(
                first_call_args[0], self.mock_client.get_ticket
            )
            self.assertEqual(first_call_args[1], 42)
            # Second call: delete_ticket
            second_call_args = mock_run_sync.call_args_list[1][0]
            self.assertEqual(
                second_call_args[0], self.mock_client.delete_ticket
            )
            self.assertEqual(second_call_args[1], 42)

    def test_handle_delete_missing_ticket_id(self):
        """Test _handle_delete returns error when ticket_id is missing."""
        result = asyncio.run(_handle_delete(self.mock_client, {}))

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        self.assertIn(
            "Error (validation_error)", result.content[0].text
        )
        self.assertIn("ticket_id is required", result.content[0].text)

    def test_handle_delete_ticket_not_found(self):
        """Test _handle_delete handles ticket not found via registry error translation."""
        config = Config(
            trac_url="http://test", username="test", password="test"
        )
        mock_client = MagicMock()
        mock_client.config = config

        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = xmlrpc.client.Fault(
                404, "Ticket 99999 not found"
            )

            registry = ToolRegistry(TICKET_WRITE_SPECS)
            result = asyncio.run(
                registry.call_tool(
                    "ticket_delete", {"ticket_id": 99999}, mock_client
                )
            )

            assert isinstance(result, types.CallToolResult)
            self.assertTrue(result.isError)
            self.assertEqual(len(result.content), 1)
            self.assertIn("Error (not_found)", result.content[0].text)

    def test_handle_delete_permission_denied(self):
        """Test _handle_delete handles permission denied with specific error message."""
        config = Config(
            trac_url="http://test", username="test", password="test"
        )
        mock_client = MagicMock()
        mock_client.config = config

        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            # First call (get_ticket) succeeds, second call (delete_ticket) raises permission error
            mock_run_sync.side_effect = [
                True,  # get_ticket succeeds
                xmlrpc.client.Fault(
                    403, "Permission denied: TICKET_ADMIN required"
                ),  # delete_ticket fails
            ]

            registry = ToolRegistry(TICKET_WRITE_SPECS)
            result = asyncio.run(
                registry.call_tool(
                    "ticket_delete", {"ticket_id": 42}, mock_client
                )
            )

            assert isinstance(result, types.CallToolResult)
            self.assertTrue(result.isError)
            self.assertEqual(len(result.content), 1)
            self.assertIn(
                "Error (permission_denied)", result.content[0].text
            )
            self.assertIn(
                "TICKET_ADMIN permission", result.content[0].text
            )
            self.assertIn(
                "tracopt.ticket.deleter", result.content[0].text
            )


# ---------------------------------------------------------------------------
# Ticket Create handler tests
# ---------------------------------------------------------------------------


class TestHandleTicketCreate(unittest.TestCase):
    """Tests for _handle_create handler."""

    def setUp(self):
        self.mock_client = MagicMock()

    def test_create_success(self):
        """Create ticket with a summary and a TracWiki description."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = 42

            result = asyncio.run(
                _handle_create(
                    self.mock_client,
                    {
                        "summary": "Test ticket",
                        "description": "== Description ==\n\nWith '''bold'''",
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertEqual(len(result.content), 1)
            self.assertIn("Created ticket #42", result.content[0].text)
            self.assertIn("Test ticket", result.content[0].text)

            # Verify run_sync called with correct args
            mock_run_sync.assert_called_once()
            call_args = mock_run_sync.call_args[0]
            self.assertEqual(
                call_args[0], self.mock_client.create_ticket
            )
            self.assertEqual(call_args[1], "Test ticket")  # summary
            self.assertEqual(
                call_args[2],
                "== Description ==\n\nWith '''bold'''",
            )  # stored as written
            self.assertEqual(
                call_args[3], "defect"
            )  # default ticket_type

    def test_create_minimal(self):
        """Create ticket with summary and minimal description uses default type."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = 1

            result = asyncio.run(
                _handle_create(
                    self.mock_client,
                    {
                        "summary": "Minimal ticket",
                        "description": "Simple description",
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertEqual(len(result.content), 1)
            self.assertIn("Created ticket #1", result.content[0].text)

            # Verify default ticket_type is "defect"
            call_args = mock_run_sync.call_args[0]
            self.assertEqual(call_args[3], "defect")
            # Verify empty attributes dict (no optional fields)
            self.assertEqual(call_args[4], {})

    def test_create_with_all_fields(self):
        """Create ticket with all optional fields passed through."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = 99

            result = asyncio.run(
                _handle_create(
                    self.mock_client,
                    {
                        "summary": "Full ticket",
                        "description": "Full description",
                        "ticket_type": "enhancement",
                        "priority": "major",
                        "severity": "blocker",
                        "component": "core",
                        "milestone": "v1.0",
                        "owner": "alice",
                        "cc": "bob@test.com",
                        "keywords": "test",
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertEqual(len(result.content), 1)
            self.assertIn("Created ticket #99", result.content[0].text)

            # Verify ticket_type override
            call_args = mock_run_sync.call_args[0]
            self.assertEqual(call_args[3], "enhancement")

            # Verify all optional attributes passed
            attributes = call_args[4]
            self.assertEqual(attributes["priority"], "major")
            self.assertEqual(attributes["severity"], "blocker")
            self.assertEqual(attributes["component"], "core")
            self.assertEqual(attributes["milestone"], "v1.0")
            self.assertEqual(attributes["owner"], "alice")
            self.assertEqual(attributes["cc"], "bob@test.com")
            self.assertEqual(attributes["keywords"], "test")

    def test_create_with_severity(self):
        """Severity attribute is forwarded to create_ticket (not dropped)."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = 123

            result = asyncio.run(
                _handle_create(
                    self.mock_client,
                    {
                        "summary": "Severity ticket",
                        "description": "Body",
                        "severity": "blocker",
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertFalse(getattr(result, "isError", False))
            self.assertIn("Created ticket #123", result.content[0].text)

            # Verify severity made it into the attributes dict
            call_args = mock_run_sync.call_args[0]
            attributes = call_args[4]
            self.assertIn("severity", attributes)
            self.assertEqual(attributes["severity"], "blocker")

    def test_create_missing_summary(self):
        """Missing summary returns validation_error."""
        result = asyncio.run(
            _handle_create(
                self.mock_client,
                {
                    "description": "No summary provided",
                },
            )
        )

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        self.assertIn(
            "Error (validation_error)", result.content[0].text
        )
        self.assertIn("summary is required", result.content[0].text)

    def test_create_empty_summary(self):
        """Empty summary returns validation_error."""
        result = asyncio.run(
            _handle_create(
                self.mock_client,
                {
                    "summary": "",
                    "description": "Has description",
                },
            )
        )

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        self.assertIn(
            "Error (validation_error)", result.content[0].text
        )
        self.assertIn("summary is required", result.content[0].text)

    def test_create_missing_description(self):
        """Missing description returns validation_error."""
        result = asyncio.run(
            _handle_create(
                self.mock_client,
                {
                    "summary": "Has summary",
                },
            )
        )

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        self.assertIn(
            "Error (validation_error)", result.content[0].text
        )
        self.assertIn("description is required", result.content[0].text)

    def test_create_empty_description(self):
        """Empty description returns validation_error."""
        result = asyncio.run(
            _handle_create(
                self.mock_client,
                {
                    "summary": "Has summary",
                    "description": "",
                },
            )
        )

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        self.assertIn(
            "Error (validation_error)", result.content[0].text
        )
        self.assertIn("description is required", result.content[0].text)

    def test_create_xmlrpc_fault(self):
        """XML-RPC fault during create produces structured error via dispatcher."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = xmlrpc.client.Fault(
                500, "Internal server error"
            )

            registry = ToolRegistry(TICKET_WRITE_SPECS)
            result = asyncio.run(
                registry.call_tool(
                    "ticket_create",
                    {
                        "summary": "Test",
                        "description": "Test desc",
                    },
                    self.mock_client,
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertTrue(result.isError)
            self.assertEqual(len(result.content), 1)
            self.assertIn(
                "Error (server_error)", result.content[0].text
            )

    def test_create_timeout_ticket_landed_tells_caller_not_to_retry(
        self,
    ):
        """When the create timed out but landed, the agent must be told the
        id and told NOT to retry -- the generic 'retry later' advice would
        produce a duplicate ticket."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = TicketCreateTimeout(
                "timed out, but the ticket was created anyway as #77.",
                ticket_id=77,
            )

            registry = ToolRegistry(TICKET_WRITE_SPECS)
            result = asyncio.run(
                registry.call_tool(
                    "ticket_create",
                    {"summary": "Test", "description": "Test desc"},
                    self.mock_client,
                )
            )

            text = result.content[0].text
            self.assertTrue(result.isError)
            self.assertIn("timeout_ticket_created", text)
            self.assertIn("#77", text)
            self.assertIn("Do NOT retry", text)
            self.assertNotIn("retry later", text)

    def test_create_timeout_not_created_says_retry_is_safe(self):
        """When the create timed out and did not land, say so."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = TicketCreateTimeout(
                "timed out and no matching ticket was found afterwards.",
                ticket_id=None,
            )

            registry = ToolRegistry(TICKET_WRITE_SPECS)
            result = asyncio.run(
                registry.call_tool(
                    "ticket_create",
                    {"summary": "Test", "description": "Test desc"},
                    self.mock_client,
                )
            )

            text = result.content[0].text
            self.assertTrue(result.isError)
            self.assertIn("timeout_not_created", text)
            self.assertIn("most likely safe", text)

    def test_create_permission_denied(self):
        """Permission denied fault returns permission_denied error."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = xmlrpc.client.Fault(
                403, "TICKET_CREATE permission denied"
            )

            registry = ToolRegistry(TICKET_WRITE_SPECS)
            result = asyncio.run(
                registry.call_tool(
                    "ticket_create",
                    {
                        "summary": "Test",
                        "description": "Test desc",
                    },
                    self.mock_client,
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertTrue(result.isError)
            self.assertEqual(len(result.content), 1)
            self.assertIn(
                "Error (permission_denied)", result.content[0].text
            )


# ---------------------------------------------------------------------------
# Ticket Update handler tests
# ---------------------------------------------------------------------------


class TestHandleTicketUpdate(unittest.TestCase):
    """Tests for _handle_update handler."""

    def setUp(self):
        self.mock_client = MagicMock()

    def test_update_with_comment(self):
        """Update ticket with a TracWiki comment stores it as written."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True

            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 42,
                        "comment": "=== Update ===\n\nWith '''bold'''",
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertEqual(len(result.content), 1)
            self.assertIn("Updated ticket #42", result.content[0].text)
            self.assertIn("added comment", result.content[0].text)

            # Verify run_sync called with the comment as written
            call_args = mock_run_sync.call_args[0]
            self.assertEqual(
                call_args[0], self.mock_client.update_ticket
            )
            self.assertEqual(call_args[1], 42)
            self.assertEqual(
                call_args[2], "=== Update ===\n\nWith '''bold'''"
            )
            self.assertEqual(call_args[3], {})  # no attribute changes

    def test_update_fields(self):
        """Update ticket fields without comment."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True

            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 42,
                        "priority": "major",
                        "keywords": "updated",
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertEqual(len(result.content), 1)
            self.assertIn("Updated ticket #42", result.content[0].text)
            self.assertIn("updated 2 field(s)", result.content[0].text)

            # Verify attributes passed to client
            call_args = mock_run_sync.call_args[0]
            self.assertEqual(call_args[1], 42)
            self.assertEqual(call_args[2], "")  # empty comment
            self.assertEqual(
                call_args[3],
                {"priority": "major", "keywords": "updated"},
            )

    def test_update_forwards_base_ts(self):
        """base_ts is forwarded to the client call so Trac's optimistic
        lock actually gets the caller's token (ticket #50)."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True

            asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 42,
                        "priority": "major",
                        "base_ts": "1788093861682305",
                    },
                )
            )

            call_args = mock_run_sync.call_args[0]
            self.assertEqual(
                call_args[0], self.mock_client.update_ticket
            )
            self.assertEqual(call_args[-1], "1788093861682305")

    def test_update_omitted_base_ts_forwards_none(self):
        """Omitting base_ts forwards None, preserving the legacy
        self-minted-token fallback in TracClient.update_ticket."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True

            asyncio.run(
                _handle_update(
                    self.mock_client,
                    {"ticket_id": 42, "priority": "major"},
                )
            )

            call_args = mock_run_sync.call_args[0]
            self.assertIsNone(call_args[-1])

    def test_update_base_ts_conflict_returns_version_conflict(self):
        """A TicketUpdateConflict from the client becomes a
        version_conflict error naming what changed, not a bare exception
        or a silently-accepted write (ticket #50)."""
        from trac_mcp_server.core.client import TicketUpdateConflict

        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = TicketUpdateConflict(
                "Ticket #42 changed since base_ts=123: comment by operator",
                ticket_id=42,
                base_ts="123",
                changes=[
                    {
                        "timestamp": "20260830T12:16:00",
                        "author": "operator",
                        "field": "comment",
                        "oldvalue": "",
                        "newvalue": "hold off",
                    }
                ],
            )

            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 42,
                        "status": "accepted",
                        "base_ts": "123",
                    },
                )
            )

            self.assertTrue(result.isError)
            text = result.content[0].text
            self.assertIn("version_conflict", text)
            self.assertIn("operator", text)
            self.assertIn("comment", text)
            self.assertIn("ticket_get(ticket_id=42)", text)

    def test_update_comment_and_fields(self):
        """Update ticket with both comment and field changes."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True

            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 10,
                        "comment": "Adding a note",
                        "status": "assigned",
                        "owner": "alice",
                        "milestone": "v2.0",
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertEqual(len(result.content), 1)
            self.assertIn("Updated ticket #10", result.content[0].text)
            self.assertIn("added comment", result.content[0].text)
            self.assertIn("updated 3 field(s)", result.content[0].text)

            # Verify both comment and attributes passed
            call_args = mock_run_sync.call_args[0]
            self.assertEqual(call_args[2], "Adding a note")
            self.assertEqual(
                call_args[3],
                {
                    "status": "assigned",
                    "owner": "alice",
                    "milestone": "v2.0",
                },
            )

    def test_update_missing_ticket_id(self):
        """Missing ticket_id returns validation_error."""
        result = asyncio.run(
            _handle_update(
                self.mock_client,
                {
                    "comment": "orphan comment",
                },
            )
        )

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        self.assertIn(
            "Error (validation_error)", result.content[0].text
        )
        self.assertIn("ticket_id is required", result.content[0].text)

    def test_update_no_changes(self):
        """Update with ticket_id only but no comment or fields returns no-changes."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True

            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 42,
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertEqual(len(result.content), 1)
            self.assertIn("Updated ticket #42", result.content[0].text)
            self.assertIn("no changes", result.content[0].text)

    def test_update_not_found(self):
        """Ticket not found returns not_found error via dispatcher."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = xmlrpc.client.Fault(
                404, "Ticket 99999 not found"
            )

            registry = ToolRegistry(TICKET_WRITE_SPECS)
            result = asyncio.run(
                registry.call_tool(
                    "ticket_update",
                    {
                        "ticket_id": 99999,
                    },
                    self.mock_client,
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertTrue(result.isError)
            self.assertEqual(len(result.content), 1)
            self.assertIn("Error (not_found)", result.content[0].text)

    def test_update_permission_denied(self):
        """Permission denied fault returns permission_denied error."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = xmlrpc.client.Fault(
                403, "TICKET_MODIFY permission denied"
            )

            registry = ToolRegistry(TICKET_WRITE_SPECS)
            result = asyncio.run(
                registry.call_tool(
                    "ticket_update",
                    {
                        "ticket_id": 42,
                    },
                    self.mock_client,
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertTrue(result.isError)
            self.assertEqual(len(result.content), 1)
            self.assertIn(
                "Error (permission_denied)", result.content[0].text
            )

    def test_update_all_attribute_fields(self):
        """All supported update attributes are passed through."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True

            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 7,
                        "status": "closed",
                        "priority": "critical",
                        "component": "auth",
                        "milestone": "v3.0",
                        "owner": "bob",
                        "resolution": "fixed",
                        "cc": "team@test.com",
                        "keywords": "release",
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertEqual(len(result.content), 1)
            self.assertIn("Updated ticket #7", result.content[0].text)
            self.assertIn("updated 8 field(s)", result.content[0].text)

            call_args = mock_run_sync.call_args[0]
            attrs = call_args[3]
            self.assertEqual(attrs["status"], "closed")
            self.assertEqual(attrs["priority"], "critical")
            self.assertEqual(attrs["component"], "auth")
            self.assertEqual(attrs["milestone"], "v3.0")
            self.assertEqual(attrs["owner"], "bob")
            self.assertEqual(attrs["resolution"], "fixed")
            self.assertEqual(attrs["cc"], "team@test.com")
            self.assertEqual(attrs["keywords"], "release")

    def test_update_summary_field(self):
        """summary kwarg is forwarded as a ticket attribute (title rewrite)."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {"ticket_id": 11, "summary": "Renamed ticket"},
                )
            )
            self.assertIsInstance(result, types.CallToolResult)
            attrs = mock_run_sync.call_args[0][3]
            self.assertEqual(attrs["summary"], "Renamed ticket")
            self.assertIn("updated 1 field(s)", result.content[0].text)

    def test_update_type_field(self):
        """type kwarg is forwarded (note: Trac's field is `type`, not `ticket_type`)."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {"ticket_id": 12, "type": "enhancement"},
                )
            )
            self.assertIsInstance(result, types.CallToolResult)
            attrs = mock_run_sync.call_args[0][3]
            self.assertEqual(attrs["type"], "enhancement")

    def test_update_description_field_stored_as_written(self):
        """description kwarg is forwarded byte-for-byte (ticket #69)."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True

            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 13,
                        "description": "'''bold''' body",
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            attrs = mock_run_sync.call_args[0][3]
            self.assertEqual(attrs["description"], "'''bold''' body")

    def test_update_workflow_action(self):
        """action kwarg triggers a Trac workflow transition (e.g. accept)."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 14,
                        "comment": "moving to accepted",
                        "action": "accept",
                    },
                )
            )
            self.assertIsInstance(result, types.CallToolResult)
            attrs = mock_run_sync.call_args[0][3]
            self.assertEqual(attrs["action"], "accept")

    def test_update_workflow_action_input_field(self):
        """action_<name>_<name>_<field> kwargs are forwarded by pattern.

        Trac's workflow input convention is
        ``action_<action>_<action>_<field>`` (e.g.
        ``action_resolve_resolve_resolution``).  These are pattern-forwarded
        rather than statically allowlisted because action names are
        workflow-config-dependent.
        """
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 15,
                        "action": "resolve",
                        "action_resolve_resolve_resolution": "fixed",
                        "action_reassign_reassign_owner": "bob",
                    },
                )
            )
            self.assertIsInstance(result, types.CallToolResult)
            attrs = mock_run_sync.call_args[0][3]
            self.assertEqual(attrs["action"], "resolve")
            self.assertEqual(
                attrs["action_resolve_resolve_resolution"], "fixed"
            )
            self.assertEqual(
                attrs["action_reassign_reassign_owner"], "bob"
            )

    def test_update_resolve_action_with_resolution_maps_to_action_field(
        self,
    ):
        """action='resolve' + resolution='fixed' lands as a single call
        that both closes the ticket AND sets the resolution -- not a
        bare `resolution` attribute the workflow action then silently
        overwrites with an empty string (ticket #32).

        ``get_ticket_actions`` is mocked with the real wire shape: each
        ``input_fields`` entry is a ``[name, default, options]`` list,
        not a bare field-name string. Ticket #32's remap block assumed
        the latter, so ``str(field).endswith("resolution")`` was always
        False and the remap never fired (ticket #49) -- a defect this
        test's earlier, string-shaped mock could not have caught.
        """

        def run_sync_side_effect(func, *args, **kwargs):
            if func is self.mock_client.get_ticket_actions:
                return [
                    ["leave", "leave", [], []],
                    [
                        "resolve",
                        "resolve",
                        ["closed"],
                        [
                            [
                                "action_resolve_resolve_resolution",
                                "fixed",
                                [
                                    "fixed",
                                    "invalid",
                                    "wontfix",
                                    "duplicate",
                                    "worksforme",
                                ],
                            ]
                        ],
                    ],
                ]
            return True

        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = run_sync_side_effect

            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 21,
                        "action": "resolve",
                        "resolution": "fixed",
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            update_call = next(
                c
                for c in mock_run_sync.call_args_list
                if c.args[0] is self.mock_client.update_ticket
            )
            attrs = update_call.args[3]
            self.assertEqual(attrs["action"], "resolve")
            self.assertEqual(
                attrs["action_resolve_resolve_resolution"], "fixed"
            )
            self.assertNotIn("resolution", attrs)

    def test_update_resolution_alone_still_sets_bare_field(self):
        """resolution with no action still sets the plain attribute,
        as it did before ticket #32's fix -- no action means no
        workflow field to look up or conflict with.
        """
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True

            asyncio.run(
                _handle_update(
                    self.mock_client,
                    {"ticket_id": 22, "resolution": "wontfix"},
                )
            )

            attrs = mock_run_sync.call_args[0][3]
            self.assertEqual(attrs, {"resolution": "wontfix"})

    def test_update_resolve_action_no_matching_field_warns(self):
        """If no action input field ends in 'resolution', the bare
        `resolution` attribute is still sent (unchanged behavior) but
        the tool result carries a warning rather than reporting quiet
        success -- ticket #49's "do not fail silently" suggestion.
        """

        def run_sync_side_effect(func, *args, **kwargs):
            if func is self.mock_client.get_ticket_actions:
                return [
                    ["resolve", "resolve", ["closed"], []],
                ]
            return True

        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = run_sync_side_effect

            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 24,
                        "action": "resolve",
                        "resolution": "fixed",
                    },
                )
            )

            update_call = next(
                c
                for c in mock_run_sync.call_args_list
                if c.args[0] is self.mock_client.update_ticket
            )
            attrs = update_call.args[3]
            self.assertEqual(attrs["resolution"], "fixed")
            text = result.content[0].text
            self.assertIn("WARNING", text)

    def test_update_explicit_action_resolution_field_wins(self):
        """An explicit action_resolve_resolve_resolution still wins over
        a plain resolution if both are given -- and the lookup is
        skipped entirely since there's nothing to remap.
        """
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True

            asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 23,
                        "action": "resolve",
                        "resolution": "fixed",
                        "action_resolve_resolve_resolution": "wontfix",
                    },
                )
            )

            attrs = mock_run_sync.call_args[0][3]
            self.assertEqual(
                attrs["action_resolve_resolve_resolution"], "wontfix"
            )
            # The lookup is skipped entirely -- run_sync is only called
            # once, for the final update_ticket call.
            self.assertEqual(mock_run_sync.call_count, 1)
            self.assertIs(
                mock_run_sync.call_args[0][0],
                self.mock_client.update_ticket,
            )

    def test_update_unknown_kwargs_still_dropped(self):
        """Non-allowlisted, non-action_-prefixed kwargs are silently dropped.

        Locks the contract: only the documented allowlist + action_*
        pattern reach the wire.  Random unknown keys (typos, future-proofing
        attempts) do NOT get forwarded.
        """
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 16,
                        "status": "accepted",
                        "not_a_field": "should-be-dropped",
                        "act": "should-also-be-dropped",
                    },
                )
            )
            attrs = mock_run_sync.call_args[0][3]
            self.assertIn("status", attrs)
            self.assertNotIn("not_a_field", attrs)
            self.assertNotIn("act", attrs)

    def test_update_reply_to_prepends_quote_block(self):
        """reply_to=N prepends Trac's standard "Replying to
        [comment:N author]:" quote block, quoting that comment's own
        text, before the new comment.
        """

        def run_sync_side_effect(func, *args, **kwargs):
            if func is self.mock_client.get_ticket_changelog:
                return [
                    (
                        1700000000,
                        "alice",
                        "comment",
                        "3",
                        "First line.\nSecond line.",
                        1,
                    ),
                ]
            return True

        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = run_sync_side_effect

            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 42,
                        "comment": "New reply text",
                        "reply_to": 3,
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            update_call = next(
                c
                for c in mock_run_sync.call_args_list
                if c.args[0] is self.mock_client.update_ticket
            )
            posted_comment = update_call.args[2]
            self.assertIn(
                "Replying to [comment:3 alice]:", posted_comment
            )
            self.assertIn("> First line.", posted_comment)
            self.assertIn("> Second line.", posted_comment)
            self.assertIn("New reply text", posted_comment)

    def test_update_reply_to_missing_comment_number_errors(self):
        """reply_to pointing at a comment number that doesn't exist in
        the changelog returns a not_found error instead of silently
        posting an unquoted comment.
        """

        def run_sync_side_effect(func, *args, **kwargs):
            if func is self.mock_client.get_ticket_changelog:
                return []
            return True

        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = run_sync_side_effect

            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 42,
                        "comment": "New reply text",
                        "reply_to": 99,
                    },
                )
            )

            self.assertTrue(result.isError)
            self.assertIn("Comment #99", result.content[0].text)

    def _reply_to_run(self, changelog, reply_to):
        def run_sync_side_effect(func, *args, **kwargs):
            if func is self.mock_client.get_ticket_changelog:
                return changelog
            return True

        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = run_sync_side_effect
            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "ticket_id": 42,
                        "comment": "New reply text",
                        "reply_to": reply_to,
                    },
                )
            )
            posted = [
                c.args[2]
                for c in mock_run_sync.call_args_list
                if c.args[0] is self.mock_client.update_ticket
            ]
        return result, posted

    def test_update_reply_to_quotes_a_threaded_comment_by_its_number(
        self,
    ):
        """Trac #131. A threaded row's oldvalue is "2.5": comment 5, a
        reply to 2. reply_to=5 must find it, and the link names it by
        its own number, which is what comment:5 means to Trac."""
        changelog = [
            (1700000000, "alice", "comment", "2", "Flat two.", 1),
            (1700000100, "bob", "comment", "2.5", "Threaded five.", 1),
        ]
        for reply_to in (5, "5", "2.5"):
            result, posted = self._reply_to_run(changelog, reply_to)
            self.assertFalse(result.isError, reply_to)
            self.assertEqual(len(posted), 1)
            self.assertIn("Replying to [comment:5 bob]:", posted[0])
            self.assertIn("> Threaded five.", posted[0])
            self.assertNotIn("Flat two.", posted[0])

    def test_update_reply_to_flat_row_still_matches(self):
        changelog = [(1700000000, "alice", "comment", "3", "Three.", 1)]
        result, posted = self._reply_to_run(changelog, "3")
        self.assertFalse(result.isError)
        self.assertIn("Replying to [comment:3 alice]:", posted[0])

    def test_update_reply_to_skips_rows_without_a_comment_number(self):
        changelog = [
            (1700000000, "alice", "attachment", None, "f.txt", 1),
            (1700000010, "alice", "comment", None, "No number.", 1),
            (1700000020, "alice", "comment", "abc", "Junk.", 1),
            (1700000030, "bob", "comment", "4", "Four.", 1),
        ]
        result, posted = self._reply_to_run(changelog, 4)
        self.assertFalse(result.isError)
        self.assertIn("Replying to [comment:4 bob]:", posted[0])
        self.assertIn("> Four.", posted[0])

    def test_update_reply_to_unparseable_is_not_found(self):
        changelog = [(1700000000, "alice", "comment", "3", "Three.", 1)]
        for reply_to in ("abc", "2.", 0, True):
            result, posted = self._reply_to_run(changelog, reply_to)
            self.assertTrue(result.isError, reply_to)
            self.assertIn("not_found", result.content[0].text)
            self.assertEqual(posted, [])

    def test_update_reply_to_unparseable_never_reads_the_changelog(
        self,
    ):
        changelog = [(1700000000, "alice", "comment", "3", "Three.", 1)]
        for reply_to in ("abc", "2.", 0, True, "1_0", "+3"):
            self.mock_client.get_ticket_changelog.reset_mock()
            with patch(
                "trac_mcp_server.mcp.tools.ticket_write.run_sync"
            ) as mock_run_sync:
                mock_run_sync.return_value = changelog
                result = asyncio.run(
                    _handle_update(
                        self.mock_client,
                        {
                            "ticket_id": 42,
                            "comment": "New reply text",
                            "reply_to": reply_to,
                        },
                    )
                )
            self.assertTrue(result.isError, reply_to)
            self.assertIn("not_found", result.content[0].text)
            mock_run_sync.assert_not_called()

    def test_update_reply_to_without_comment_errors(self):
        """reply_to without a comment is rejected -- there's nothing to
        post as the reply.
        """
        result = asyncio.run(
            _handle_update(
                self.mock_client,
                {"ticket_id": 42, "reply_to": 3},
            )
        )

        self.assertTrue(result.isError)
        self.assertIn("comment is required", result.content[0].text)


class TestHandleTicketUpdateChangeToken(unittest.TestCase):
    """ticket_update hands back the new _ts, so a chained write needs no
    ticket_get (ticket #127)."""

    def _run(self, update_reply, changelog=None):
        # The write is followed by a changelog read-back for the comment
        # (#124); a reply with no usable change time never reaches it.
        client = MagicMock()
        mock_run_sync = AsyncMock(
            side_effect=[update_reply, changelog or []]
        )

        async def routed(func, *a, **kw):
            # The plugin is absent (#132): the scan is what this drives.
            if func is client.get_ticket_change_at:
                return None
            return await mock_run_sync(func, *a, **kw)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync", routed
        ):
            return asyncio.run(
                _handle_update(
                    client,
                    {
                        "ticket_id": 42,
                        "comment": "x",
                        "base_ts": "1793631012200654",
                    },
                )
            )

    def test_new_token_in_text_and_structured_content(self):
        # Trac's ticket.update reply: [id, created, changed, attrs].
        result = self._run(
            [
                42,
                "c",
                "m",
                {"status": "new", "_ts": "1793631099000001"},
            ],
            changelog=[
                [
                    _trac_time(1793631099),
                    "agent_rpc",
                    "comment",
                    "1",
                    "x",
                    1,
                ]
            ],
        )

        self.assertFalse(result.isError)
        text = result.content[0].text
        self.assertIn("Updated ticket #42 (added comment 1)", text)
        self.assertIn(
            "New change token (base_ts): 1793631099000001", text
        )
        self.assertNotIn("WARNING", text)
        self.assertEqual(
            result.structuredContent["_ts"], "1793631099000001"
        )
        self.assertEqual(result.structuredContent["ticket_id"], 42)
        self.assertEqual(result.structuredContent["message"], text)

    def test_token_returned_as_string(self):
        # _ts overflows XML-RPC's 32-bit int, so it travels as a string;
        # the handler keeps it one even if the client hands back an int.
        result = self._run([42, "c", "m", {"_ts": 1793631099000001}])

        self.assertEqual(
            result.structuredContent["_ts"], "1793631099000001"
        )

    def test_missing_token_warns_but_write_succeeds(self):
        for reply in (
            True,  # not the reply shape at all
            [42, "c", "m"],  # too short
            [42, "c", "m", "not-a-dict"],
            [42, "c", "m", {"status": "new"}],  # no _ts
            [42, "c", "m", {"_ts": ""}],
        ):
            with self.subTest(reply=reply):
                result = self._run(reply)

                self.assertFalse(result.isError)
                text = result.content[0].text
                self.assertIn("Updated ticket #42", text)
                self.assertIn("WARNING", text)
                self.assertIn(
                    "ticket_get(ticket_id=42, include_comments=false)",
                    text,
                )
                self.assertNotIn("New change token", text)
                self.assertIsNone(result.structuredContent["_ts"])
                self.assertIn(
                    "carried no change token",
                    result.structuredContent["message"],
                )

    def test_structured_content_keeps_the_remap_warning(self):
        # Claude Code shows structuredContent, not the text, so a
        # warning that only lived in the text would never reach it.
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = [
                [],  # get_ticket_actions: no resolve input field
                [42, "c", "m", {"_ts": "1793631099000001"}],
            ]
            result = asyncio.run(
                _handle_update(
                    MagicMock(),
                    {
                        "ticket_id": 42,
                        "action": "resolve",
                        "resolution": "fixed",
                        "base_ts": "1793631012200654",
                    },
                )
            )

        self.assertFalse(result.isError)
        message = result.structuredContent["message"]
        self.assertIn("could not find an input field", message)
        self.assertEqual(message, result.content[0].text)
        self.assertEqual(
            result.structuredContent["_ts"], "1793631099000001"
        )

    def test_description_advertises_the_returned_token(self):
        tool = next(
            t
            for t in TICKET_WRITE_SPECS
            if t.tool.name == "ticket_update"
        ).tool
        self.assertIn("new change token", tool.description)
        self.assertIn(
            "ticket_update",
            tool.inputSchema["properties"]["base_ts"]["description"],
        )


@pytest.mark.live
async def test_live_chained_update_uses_returned_token():
    """Real Trac (/trac_test): the token ticket_update returns is the
    one Trac now holds -- a second write with it lands, and the
    superseded token is refused (ticket #127)."""
    import os
    from urllib.parse import urlparse

    # The destructive-test instance on the same host, whatever project
    # or sub-path TRAC_URL itself names.
    parsed = urlparse(os.environ["TRAC_URL"])
    config = Config(
        trac_url=f"{parsed.scheme}://{parsed.netloc}/trac_test",
        username=os.environ["TRAC_USERNAME"],
        password=os.environ["TRAC_PASSWORD"],
        insecure=os.environ.get("TRAC_INSECURE", "").lower()
        in ("1", "true", "yes"),
    )
    client = TracClient(config)
    ticket_id = client.create_ticket(
        "#127 live test: chained ticket_update",
        "Throwaway; deleted by the test.",
        attributes={"owner": "agent_rpc"},
    )
    try:
        first_ts = client.get_ticket(ticket_id)[3]["_ts"]
        first = await _handle_update(
            client,
            {
                "ticket_id": ticket_id,
                "comment": "one",
                "base_ts": first_ts,
            },
        )
        assert not first.isError, first.content[0].text
        second_ts = first.structuredContent["_ts"]
        assert second_ts and second_ts != first_ts
        expected = f"New change token (base_ts): {second_ts}"
        assert expected in first.structuredContent["message"]
        assert client.get_ticket(ticket_id)[3]["_ts"] == second_ts

        second = await _handle_update(
            client,
            {
                "ticket_id": ticket_id,
                "comment": "two",
                "base_ts": second_ts,
            },
        )
        assert not second.isError, second.content[0].text

        stale = await _handle_update(
            client,
            {
                "ticket_id": ticket_id,
                "comment": "three",
                "base_ts": second_ts,
            },
        )
        assert stale.isError
        assert "version_conflict" in stale.content[0].text
    finally:
        client.delete_ticket(ticket_id)


# ---------------------------------------------------------------------------
# ticket_update names the comment it posted and verifies the stored bytes
# (ticket #124)
# ---------------------------------------------------------------------------

_EPOCH = calendar.timegm((2026, 11, 3, 10, 15, 30))  # 20261103T10:15:30


def _trac_time(epoch):
    """Trac's compact XML-RPC timestamp for a UTC epoch."""
    return time.strftime("%Y%m%dT%H:%M:%S", time.gmtime(epoch))


def _token(epoch, micro=123456):
    """The microsecond ``_ts`` Trac holds for a change made at ``epoch``."""
    return str(epoch * 1_000_000 + micro)


def _update_reply(epoch=_EPOCH, with_token=True):
    attrs = {"status": "new"}
    if with_token:
        attrs["_ts"] = _token(epoch)
    return [42, _trac_time(epoch - 600), _trac_time(epoch), attrs]


_USER = "agent_rpc"


def _comment_entry(
    epoch, cnum, text, field="comment", author=_USER, permanent=1
):
    """A ticket.changeLog row: [time, author, field, old, new, perm]."""
    return [
        _trac_time(epoch),
        author,
        field,
        str(cnum),
        text,
        permanent,
    ]


def _user_client():
    """A client whose configured Trac user is ``_USER``."""
    client = MagicMock()
    client.config.username = _USER
    return client


class TestCommentMismatch(unittest.TestCase):
    """_comment_mismatch: where the stored text first departs from the
    sent text."""

    def test_equal_strings_have_no_mismatch(self):
        self.assertIsNone(_comment_mismatch("same\ntext", "same\ntext"))
        self.assertIsNone(_comment_mismatch("", ""))

    def test_differs_in_the_middle(self):
        self.assertEqual(
            _comment_mismatch("abcdefgh", "abcXefgh"),
            {
                "offset": 3,
                "sent": "abcdefgh",
                "stored": "abcXefgh",
                "sent_length": 8,
                "stored_length": 8,
            },
        )

    def test_differs_at_offset_zero(self):
        result = _comment_mismatch("abc", "xbc")
        self.assertEqual(result["offset"], 0)
        self.assertEqual(result["sent"], "abc")
        self.assertEqual(result["stored"], "xbc")

    def test_prefix_case_offset_is_the_shorter_length(self):
        stored_short = _comment_mismatch("abcdef", "abc")
        self.assertEqual(stored_short["offset"], 3)
        self.assertEqual(stored_short["sent"], "abcdef")
        self.assertEqual(stored_short["stored"], "abc")
        self.assertEqual(stored_short["sent_length"], 6)
        self.assertEqual(stored_short["stored_length"], 3)

        sent_short = _comment_mismatch("abc", "abcdef")
        self.assertEqual(sent_short["offset"], 3)
        self.assertEqual(sent_short["sent_length"], 3)
        self.assertEqual(sent_short["stored_length"], 6)

    def test_trailing_whitespace_stripped(self):
        result = _comment_mismatch("body\n\n  ", "body")
        self.assertEqual(result["offset"], 4)
        self.assertEqual(result["sent"], "body\n\n  ")
        self.assertEqual(result["stored"], "body")
        self.assertEqual(result["sent_length"], 8)
        self.assertEqual(result["stored_length"], 4)

    def test_crlf_versus_lf(self):
        result = _comment_mismatch("a\r\nb", "a\nb")
        self.assertEqual(result["offset"], 1)
        self.assertEqual(result["sent"], "a\r\nb")
        self.assertEqual(result["stored"], "a\nb")

    def test_context_is_forty_characters_either_side(self):
        sent = "a" * 100 + "X" + "b" * 100
        stored = "a" * 100 + "Y" + "b" * 100
        result = _comment_mismatch(sent, stored)
        self.assertEqual(result["offset"], 100)
        self.assertEqual(result["sent"], "a" * 40 + "X" + "b" * 39)
        self.assertEqual(result["stored"], "a" * 40 + "Y" + "b" * 39)
        self.assertEqual(result["sent_length"], 201)
        self.assertEqual(result["stored_length"], 201)


class TestHandleTicketUpdateCommentNumber(unittest.TestCase):
    """A comment write reports its comment number and whether Trac stored
    it byte-for-byte, with one changelog read (ticket #124)."""

    def _run(self, effects, args=None, client=None):
        base = {"ticket_id": 42, "comment": "hello"}
        base.update(args or {})
        client = client or _user_client()
        mock_run_sync = AsyncMock(side_effect=effects)

        async def routed(func, *a, **kw):
            # The plugin lookup is not among the scripted effects:
            # these tests drive the changelog scan, so the plugin is
            # absent (None) and the call is not counted.
            if func is client.get_ticket_change_at:
                return None
            return await mock_run_sync(func, *a, **kw)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync", routed
        ):
            result = asyncio.run(_handle_update(client, base))
        return result, mock_run_sync

    def test_match_reports_number_and_byte_for_byte(self):
        client = _user_client()
        result, mock = self._run(
            [
                _update_reply(),
                [
                    _comment_entry(_EPOCH - 50, 4, "older"),
                    _comment_entry(_EPOCH, 5, "hello"),
                ],
            ],
            client=client,
        )

        self.assertFalse(result.isError)
        text = result.content[0].text
        self.assertIn("Updated ticket #42 (added comment 5)", text)
        self.assertIn(
            "Stored comment 5 matches the text sent, byte-for-byte.",
            text,
        )
        self.assertNotIn("WARNING", text)
        sc = result.structuredContent
        self.assertEqual(sc["comment_number"], 5)
        self.assertIs(sc["comment_stored_matches"], True)
        self.assertIsNone(sc["comment_mismatch"])
        self.assertEqual(sc["_ts"], _token(_EPOCH))
        self.assertEqual(sc["ticket_id"], 42)
        self.assertEqual(sc["message"], text)
        # One write, one changelog read.
        self.assertEqual(mock.call_count, 2)
        self.assertEqual(
            mock.call_args_list[1][0],
            (client.get_ticket_changelog, 42),
        )

    def test_lines_come_after_token_and_before_gate_lines(self):
        result, _ = self._run(
            [_update_reply(), [_comment_entry(_EPOCH, 5, "hello")]]
        )
        lines = result.content[0].text.split("\n")
        self.assertTrue(lines[1].startswith("New change token"))
        self.assertTrue(lines[2].startswith("Stored comment 5"))

    def test_mismatch_reports_first_difference(self):
        sent = "line one  \nline two"  # two trailing spaces mid-text
        stored = "line one\nline two"
        result, _ = self._run(
            [_update_reply(), [_comment_entry(_EPOCH, 5, stored)]],
            {"comment": sent},
        )

        self.assertFalse(result.isError)
        text = result.content[0].text
        self.assertIn("(added comment 5)", text)
        self.assertNotIn("byte-for-byte", text)
        self.assertIn(
            "WARNING: comment 5 was stored differently from the text "
            "sent: first difference at offset 8 (sent 19 chars, "
            "stored 17)",
            text,
        )
        self.assertIn(f"sent ...{sent!r}...", text)
        self.assertIn(f"stored ...{stored!r}...", text)
        self.assertIn("ticket_comment_edit", text)
        sc = result.structuredContent
        self.assertEqual(sc["comment_number"], 5)
        self.assertIs(sc["comment_stored_matches"], False)
        self.assertEqual(
            sc["comment_mismatch"],
            {
                "offset": 8,
                "sent": sent,
                "stored": stored,
                "sent_length": 19,
                "stored_length": 17,
            },
        )
        self.assertEqual(sc["message"], text)

    def test_no_comment_makes_no_changelog_call(self):
        result, mock = self._run(
            [_update_reply()],
            {"comment": "", "priority": "major"},
        )

        self.assertEqual(mock.call_count, 1)
        text = result.content[0].text
        self.assertIn("Updated ticket #42 (updated 1 field(s))", text)
        self.assertNotIn("WARNING", text)
        self.assertNotIn("Stored comment", text)
        sc = result.structuredContent
        for key in (
            "comment_number",
            "comment_stored_matches",
            "comment_mismatch",
        ):
            self.assertIn(key, sc)
            self.assertIsNone(sc[key])

    def test_changelog_error_warns_but_write_succeeds(self):
        result, _ = self._run(
            [_update_reply(), xmlrpc.client.Fault(1, "boom")]
        )

        self.assertFalse(result.isError)
        text = result.content[0].text
        self.assertIn("Updated ticket #42 (added comment)", text)
        self.assertIn(
            "WARNING: the comment was posted, but its number could not "
            "be determined (changelog unreadable). Call "
            "ticket_get(ticket_id=42) on the same instance; its "
            "comments are listed with their numbers.",
            text,
        )
        self.assertIn("New change token", text)
        sc = result.structuredContent
        self.assertIsNone(sc["comment_number"])
        self.assertIsNone(sc["comment_stored_matches"])
        self.assertIsNone(sc["comment_mismatch"])
        self.assertEqual(sc["_ts"], _token(_EPOCH))

    def test_no_entry_at_the_update_time_warns(self):
        result, _ = self._run(
            [
                _update_reply(),
                [
                    _comment_entry(_EPOCH - 5, 4, "hello"),
                    _comment_entry(_EPOCH, 5, "hello", field="status"),
                ],
            ]
        )
        text = result.content[0].text
        self.assertIn(
            f"no comment entry by {_USER} at the update's time", text
        )
        self.assertIn("WARNING: the comment was posted", text)
        self.assertIsNone(result.structuredContent["comment_number"])

    def test_same_second_entries_pick_the_one_matching_the_text(self):
        result, _ = self._run(
            [
                _update_reply(),
                [
                    _comment_entry(_EPOCH, 5, "someone else's"),
                    _comment_entry(_EPOCH, 6, "hello"),
                    _comment_entry(_EPOCH, 7, "another one"),
                ],
            ]
        )
        sc = result.structuredContent
        self.assertEqual(sc["comment_number"], 6)
        self.assertIs(sc["comment_stored_matches"], True)
        self.assertNotIn("WARNING", result.content[0].text)

    def test_same_second_entries_all_matching_take_the_highest(self):
        result, _ = self._run(
            [
                _update_reply(),
                [
                    _comment_entry(_EPOCH, 5, "hello"),
                    _comment_entry(_EPOCH, 7, "hello"),
                    _comment_entry(_EPOCH, 6, "hello"),
                ],
            ]
        )
        self.assertEqual(result.structuredContent["comment_number"], 7)
        self.assertIs(
            result.structuredContent["comment_stored_matches"], True
        )

    def test_same_second_entries_none_matching_is_ambiguous(self):
        result, _ = self._run(
            [
                _update_reply(),
                [
                    _comment_entry(_EPOCH, 5, "not it"),
                    _comment_entry(_EPOCH, 6, "nor this"),
                ],
            ]
        )
        text = result.content[0].text
        self.assertIn(
            "several comment entries at that time, none matching the "
            "text sent",
            text,
        )
        sc = result.structuredContent
        self.assertIsNone(sc["comment_number"])
        self.assertIsNone(sc["comment_stored_matches"])

    def test_no_token_falls_back_to_the_reply_change_time(self):
        result, _ = self._run(
            [
                _update_reply(with_token=False),
                [_comment_entry(_EPOCH, 5, "hello")],
            ]
        )
        text = result.content[0].text
        self.assertIn("(added comment 5)", text)
        self.assertIn("Stored comment 5 matches", text)
        # The missing token still warns, separately.
        self.assertIn("carried no change token", text)
        sc = result.structuredContent
        self.assertIsNone(sc["_ts"])
        self.assertEqual(sc["comment_number"], 5)

    def test_token_second_is_whole_seconds_of_the_microsecond_ts(self):
        # 999999 microseconds past the second still floors to it.
        reply = _update_reply()
        reply[3]["_ts"] = _token(_EPOCH, micro=999999)
        result, _ = self._run(
            [reply, [_comment_entry(_EPOCH, 5, "hello")]]
        )
        self.assertEqual(result.structuredContent["comment_number"], 5)

    def test_no_time_at_all_warns_without_a_changelog_read(self):
        result, _ = self._run(
            [[42, "c", "not-a-time", {"status": "new"}]]
        )
        text = result.content[0].text
        self.assertIn("no change time in Trac's reply", text)
        self.assertIn("WARNING: the comment was posted", text)
        self.assertIsNone(result.structuredContent["comment_number"])

    def test_unparseable_comment_number_is_not_a_candidate(self):
        result, _ = self._run(
            [_update_reply(), [_comment_entry(_EPOCH, "abc", "hello")]]
        )
        self.assertIn(
            f"no comment entry by {_USER} at the update's time",
            result.content[0].text,
        )
        self.assertIsNone(result.structuredContent["comment_number"])

    def test_superscript_digit_oldvalue_is_ignored_without_raising(
        self,
    ):
        # '\u00b2'.isdigit() is True but int('\u00b2') raises.
        result, _ = self._run(
            [
                _update_reply(),
                [
                    _comment_entry(_EPOCH, "\u00b2", "hello"),
                    _comment_entry(_EPOCH, 5, "hello"),
                ],
            ]
        )
        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent["comment_number"], 5)

        result, _ = self._run(
            [
                _update_reply(),
                [_comment_entry(_EPOCH, "\u00b2", "hello")],
            ]
        )
        self.assertFalse(result.isError)
        self.assertIsNone(result.structuredContent["comment_number"])
        self.assertIn("no comment entry by", result.content[0].text)

    def test_other_authors_lone_same_second_row_is_not_ours(self):
        result, _ = self._run(
            [
                _update_reply(),
                [_comment_entry(_EPOCH, 5, "hello", author="someone")],
            ]
        )
        text = result.content[0].text
        self.assertIn(
            f"no comment entry by {_USER} at the update's time", text
        )
        sc = result.structuredContent
        self.assertIsNone(sc["comment_number"])
        self.assertIsNone(sc["comment_stored_matches"])

    def test_author_compare_is_case_insensitive_and_stripped(self):
        client = _user_client()
        client.config.username = "  Agent_RPC "
        result, _ = self._run(
            [_update_reply(), [_comment_entry(_EPOCH, 5, "hello")]],
            client=client,
        )
        self.assertEqual(result.structuredContent["comment_number"], 5)

    def test_non_string_username_skips_the_author_filter(self):
        # A MagicMock config (or any non-str) cannot be compared.
        result, _ = self._run(
            [
                _update_reply(),
                [_comment_entry(_EPOCH, 5, "hello", author="whoever")],
            ],
            client=MagicMock(),
        )
        self.assertEqual(result.structuredContent["comment_number"], 5)

    def test_attachment_pseudo_row_is_ignored(self):
        # Trac's changelog UNIONs attachment rows in with permanent 0.
        # With a real, mismatching row present the mismatch is reported,
        # not an ambiguity between two candidates.
        result, _ = self._run(
            [
                _update_reply(),
                [
                    _comment_entry(_EPOCH, 5, "hell"),
                    _comment_entry(_EPOCH, 6, "x", permanent=0),
                ],
            ]
        )
        sc = result.structuredContent
        self.assertEqual(sc["comment_number"], 5)
        self.assertIs(sc["comment_stored_matches"], False)
        self.assertIn("was stored differently", result.content[0].text)

    def test_dotted_row_is_someone_elses_threaded_reply(self):
        result, _ = self._run(
            [
                _update_reply(),
                [
                    _comment_entry(_EPOCH, "2.6", "hello"),
                    _comment_entry(_EPOCH, 5, "hello"),
                ],
            ]
        )
        self.assertEqual(result.structuredContent["comment_number"], 5)

    def test_lone_dotted_row_is_not_found(self):
        result, _ = self._run(
            [_update_reply(), [_comment_entry(_EPOCH, "2.5", "hello")]]
        )
        self.assertIsNone(result.structuredContent["comment_number"])
        self.assertIn("no comment entry by", result.content[0].text)

    def test_row_without_a_permanent_column_is_skipped(self):
        row = _comment_entry(_EPOCH, 5, "hello")[:5]
        result, _ = self._run([_update_reply(), [row]])
        self.assertIsNone(result.structuredContent["comment_number"])

    # -- nothing stored ----------------------------------------------

    def test_blank_comment_stores_nothing(self):
        # Trac stores nothing for a comment that is empty after
        # stripping when no field changed; tracrpc then returns the
        # UNCHANGED _ts.
        for blank in ("   ", "\n\t ", "\u200b", " \u200b "):
            with self.subTest(blank=blank):
                result, mock = self._run(
                    [
                        _update_reply(),
                        [_comment_entry(_EPOCH, 1, "old")],
                    ],
                    {"comment": blank, "base_ts": _token(_EPOCH)},
                )
                self.assertEqual(mock.call_count, 1)
                self._assert_nothing_stored(
                    result,
                    "Trac stored nothing: a comment that is empty after "
                    "whitespace stripping, with no field changes, is "
                    "not saved.",
                )

    def test_blank_comment_without_base_ts_stores_nothing(self):
        result, mock = self._run(
            [_update_reply(), []], {"comment": "   "}
        )
        self.assertEqual(mock.call_count, 1)
        self._assert_nothing_stored(
            result,
            "Trac stored nothing: a comment that is empty after "
            "whitespace stripping, with no field changes, is not saved.",
        )

    def test_unmoved_token_with_a_real_comment_stores_nothing(self):
        result, mock = self._run(
            [_update_reply(), []],
            {"comment": "hello", "base_ts": _token(_EPOCH)},
        )
        self.assertEqual(mock.call_count, 1)
        self._assert_nothing_stored(
            result,
            "Trac stored nothing: the change token did not move, so no "
            "change was saved.",
        )

    def test_unmoved_token_with_fields_stores_nothing(self):
        result, mock = self._run(
            [_update_reply()],
            {
                "comment": "",
                "priority": "major",
                "base_ts": _token(_EPOCH),
            },
        )
        self.assertEqual(mock.call_count, 1)
        self._assert_nothing_stored(
            result,
            "Trac stored nothing: the change token did not move, so no "
            "change was saved.",
        )

    def test_moved_token_is_a_normal_write(self):
        result, mock = self._run(
            [_update_reply(), [_comment_entry(_EPOCH, 5, "hello")]],
            {"base_ts": _token(_EPOCH - 10)},
        )
        self.assertEqual(mock.call_count, 2)
        self.assertEqual(result.structuredContent["comment_number"], 5)

    def test_blank_comment_with_a_field_change_is_not_flagged(self):
        # A field change is stored, so the nothing-stored branch is not
        # taken just because the comment is blank.
        result, _ = self._run(
            [_update_reply()],
            {"comment": "", "priority": "major"},
        )
        self.assertNotIn("stored nothing", result.content[0].text)

    def _assert_nothing_stored(self, result, line):
        self.assertFalse(result.isError)
        text = result.content[0].text
        self.assertIn("Updated ticket #42 (no change stored)", text)
        self.assertNotIn("added comment", text)
        self.assertIn(line, text)
        self.assertNotIn("WARNING", text)
        self.assertNotIn("ticket_comment_edit", text)
        self.assertNotIn("Stored comment", text)
        sc = result.structuredContent
        self.assertIsNone(sc["comment_number"])
        self.assertIsNone(sc["comment_stored_matches"])
        self.assertIsNone(sc["comment_mismatch"])
        self.assertEqual(sc["message"], text)

    # -- CR line endings ---------------------------------------------

    def test_crlf_folded_to_lf_is_a_match(self):
        for sent in ("a\r\nb", "a\rb", "a\r\nb\rc\n"):
            with self.subTest(sent=sent):
                stored = sent.replace("\r\n", "\n").replace("\r", "\n")
                result, _ = self._run(
                    [
                        _update_reply(),
                        [_comment_entry(_EPOCH, 5, stored)],
                    ],
                    {"comment": sent},
                )
                text = result.content[0].text
                self.assertIn(
                    "Stored comment 5 matches the text sent, except "
                    "that its CR/CRLF line endings were folded to LF by "
                    "the XML-RPC transport (Trac cannot receive CR this "
                    "way; nothing to correct).",
                    text,
                )
                self.assertNotIn("WARNING", text)
                self.assertNotIn("byte-for-byte", text)
                sc = result.structuredContent
                self.assertIs(sc["comment_stored_matches"], True)
                self.assertIs(sc["comment_line_endings_folded"], True)
                self.assertIsNone(sc["comment_mismatch"])

    def test_plain_match_is_not_folded(self):
        result, _ = self._run(
            [_update_reply(), [_comment_entry(_EPOCH, 5, "hello")]]
        )
        self.assertIs(
            result.structuredContent["comment_line_endings_folded"],
            False,
        )

    def test_no_lookup_result_has_no_folded_flag(self):
        result, _ = self._run(
            [_update_reply(), xmlrpc.client.Fault(1, "boom")]
        )
        self.assertIsNone(
            result.structuredContent["comment_line_endings_folded"]
        )
        result, _ = self._run([_update_reply()], {"comment": ""})
        self.assertIn(
            "comment_line_endings_folded", result.structuredContent
        )
        self.assertIsNone(
            result.structuredContent["comment_line_endings_folded"]
        )

    def test_real_mismatch_offsets_ignore_folded_crs(self):
        sent = "a\r\nb  "
        stored = "a\nb"
        result, _ = self._run(
            [_update_reply(), [_comment_entry(_EPOCH, 5, stored)]],
            {"comment": sent},
        )
        sc = result.structuredContent
        self.assertIs(sc["comment_stored_matches"], False)
        self.assertIs(sc["comment_line_endings_folded"], False)
        self.assertEqual(
            sc["comment_mismatch"],
            {
                "offset": 3,
                "sent": "a\nb  ",
                "stored": "a\nb",
                "sent_length": 5,
                "stored_length": 3,
            },
        )
        self.assertIn("ticket_comment_edit", result.content[0].text)

    def test_candidates_are_chosen_by_normalised_text(self):
        result, _ = self._run(
            [
                _update_reply(),
                [
                    _comment_entry(_EPOCH, 5, "other"),
                    _comment_entry(_EPOCH, 6, "a\nb"),
                ],
            ],
            {"comment": "a\r\nb"},
        )
        self.assertEqual(result.structuredContent["comment_number"], 6)
        self.assertIs(
            result.structuredContent["comment_line_endings_folded"],
            True,
        )

    # -- empty stored text -------------------------------------------

    def test_empty_stored_text_is_not_the_string_none(self):
        # _parse_xmlrpc_value turns an empty <string></string> into None.
        row = _comment_entry(_EPOCH, 5, None)
        result, _ = self._run(
            [_update_reply(), [row]], {"comment": "None"}
        )
        sc = result.structuredContent
        self.assertEqual(sc["comment_number"], 5)
        self.assertIs(sc["comment_stored_matches"], False)
        self.assertEqual(sc["comment_mismatch"]["stored_length"], 0)

    def test_empty_stored_text_among_several_candidates(self):
        result, _ = self._run(
            [
                _update_reply(),
                [
                    _comment_entry(_EPOCH, 5, None),
                    _comment_entry(_EPOCH, 6, "None"),
                ],
            ],
            {"comment": "None"},
        )
        self.assertEqual(result.structuredContent["comment_number"], 6)
        self.assertIs(
            result.structuredContent["comment_stored_matches"], True
        )

    # -- deadline ----------------------------------------------------

    def test_slow_changelog_read_times_out(self):
        calls = []

        async def effect(func, *args):
            calls.append(func)
            if len(calls) == 1:
                return _update_reply()
            await asyncio.sleep(5)

        with (
            patch(
                "trac_mcp_server.mcp.tools.ticket_write.run_sync",
                side_effect=effect,
            ),
            patch(
                "trac_mcp_server.mcp.tools.ticket_write."
                "_COMMENT_LOOKUP_TIMEOUT",
                0.05,
            ),
        ):
            result = asyncio.run(
                _handle_update(
                    _user_client(),
                    {"ticket_id": 42, "comment": "hello"},
                )
            )
        self.assertFalse(result.isError)
        text = result.content[0].text
        self.assertIn("(changelog read timed out)", text)
        self.assertIn("New change token", text)
        self.assertIsNone(result.structuredContent["comment_number"])

    def test_reply_to_compares_the_full_string_with_the_quote(self):
        full = (
            "Replying to [comment:3 alice]:\n> quoted text\n\nmy reply"
        )
        result, mock = self._run(
            [
                # Before the write: the changelog read for the quote.
                [
                    [
                        _trac_time(_EPOCH - 60),
                        "alice",
                        "comment",
                        "3",
                        "quoted text",
                        1,
                    ]
                ],
                _update_reply(),
                [_comment_entry(_EPOCH, 4, full)],
            ],
            {"comment": "my reply", "reply_to": 3},
        )

        self.assertEqual(mock.call_count, 3)
        sent = mock.call_args_list[1][0][2]
        self.assertEqual(sent, full)
        text = result.content[0].text
        self.assertIn("(added comment 4)", text)
        self.assertIn("Stored comment 4 matches", text)
        sc = result.structuredContent
        self.assertIs(sc["comment_stored_matches"], True)

    def test_reply_to_mismatch_measures_against_the_quote_prefix(self):
        full = (
            "Replying to [comment:3 alice]:\n> quoted text\n\nmy reply"
        )
        result, _ = self._run(
            [
                [
                    [
                        _trac_time(_EPOCH - 60),
                        "alice",
                        "comment",
                        "3",
                        "quoted text",
                        1,
                    ]
                ],
                _update_reply(),
                [_comment_entry(_EPOCH, 4, "my reply")],
            ],
            {"comment": "my reply", "reply_to": 3},
        )
        mismatch = result.structuredContent["comment_mismatch"]
        self.assertEqual(mismatch["sent_length"], len(full))
        self.assertEqual(mismatch["stored_length"], len("my reply"))
        self.assertEqual(mismatch["offset"], 0)

    def test_description_advertises_the_comment_report(self):
        tool = next(
            t
            for t in TICKET_WRITE_SPECS
            if t.tool.name == "ticket_update"
        ).tool
        self.assertIn("comment_number", tool.description)
        self.assertIn("comment_stored_matches", tool.description)
        self.assertIn("comment_line_endings_folded", tool.description)


class TestCommentLookupViaPlugin(unittest.TestCase):
    """ticket_update finds its comment with the plugin's
    ticket.getChangeAt and falls back to the changelog scan (Trac
    /trac_mcp_server #132).

    The plugin's answer is taken only when its author is the configured
    user (or there is none, OIDC) AND its stored text is the text sent:
    tracrpc reads ``_ts`` back AFTER saving, so a write landing in
    between makes ``_ts`` name the other writer's change."""

    def _go(
        self,
        change_at,
        args=None,
        update_reply=None,
        changelog=None,
        client=None,
    ):
        """Run ticket_update; ``change_at`` is the stub's result, an
        exception to raise, or an async callable to await."""
        client = client or _user_client()
        calls = []
        if changelog is None:
            changelog = [_comment_entry(_EPOCH, 7, "hello")]

        async def fake_run_sync(func, *a, **kw):
            calls.append((func, a))
            if func is client.update_ticket:
                return update_reply or _update_reply()
            if func is client.get_ticket_change_at:
                if callable(change_at):
                    return await change_at()
                if isinstance(change_at, BaseException):
                    raise change_at
                return change_at
            if func is client.get_ticket_changelog:
                return changelog
            return None

        base = {"ticket_id": 42, "comment": "hello"}
        base.update(args or {})
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync",
            side_effect=fake_run_sync,
        ):
            result = asyncio.run(_handle_update(client, base))
        return result, client, [c[0] for c in calls], calls

    def test_plugin_path_gives_number_and_match_without_the_changelog(
        self,
    ):
        result, client, funcs, calls = self._go(["5", _USER, "hello"])

        self.assertFalse(result.isError)
        sc = result.structuredContent
        self.assertEqual(sc["comment_number"], 5)
        self.assertIs(sc["comment_stored_matches"], True)
        self.assertIsNone(sc["comment_mismatch"])
        self.assertIn(
            "Stored comment 5 matches the text sent, byte-for-byte.",
            result.content[0].text,
        )
        self.assertNotIn(client.get_ticket_changelog, funcs)
        # Asked for the microsecond token, not the whole second.
        change_at = [
            c for c in calls if c[0] is client.get_ticket_change_at
        ]
        self.assertEqual(change_at[0][1], (42, _token(_EPOCH)))

    def test_plugin_author_compare_is_case_insensitive_and_stripped(
        self,
    ):
        result, client, funcs, _ = self._go(
            ["5", "  Agent_RPC ", "hello"]
        )
        self.assertEqual(result.structuredContent["comment_number"], 5)
        self.assertNotIn(client.get_ticket_changelog, funcs)

    def test_another_authors_change_falls_back_to_the_scan(self):
        # tracrpc reads _ts back after saving, so a concurrent write by
        # someone else can make _ts name THEIR change. The text may even
        # be the same. Never report it as ours.
        result, client, funcs, _ = self._go(
            ["5", "someone_else", "hello"]
        )
        self._assert_fell_back(result, funcs, client)

    def test_non_string_plugin_author_falls_back_to_the_scan(self):
        for author in (None, 5, ["agent_rpc"]):
            with self.subTest(author=author):
                result, client, funcs, _ = self._go(
                    ["5", author, "hello"]
                )
                self._assert_fell_back(result, funcs, client)

    def test_no_configured_user_accepts_the_plugin_without_author_check(
        self,
    ):
        # OIDC per-user auth: the client's username is empty, so the
        # microsecond and the text are what identify the write.
        for username in ("", "   ", None):
            with self.subTest(username=username):
                client = MagicMock()
                client.config.username = username
                result, client, funcs, _ = self._go(
                    ["5", "whoever", "hello"], client=client
                )
                self.assertEqual(
                    result.structuredContent["comment_number"], 5
                )
                self.assertIs(
                    result.structuredContent["comment_stored_matches"],
                    True,
                )
                self.assertNotIn(client.get_ticket_changelog, funcs)

    def test_no_configured_user_still_falls_back_on_a_text_difference(
        self,
    ):
        client = MagicMock()
        client.config.username = ""
        result, client, funcs, _ = self._go(
            ["5", "whoever", "other writer's text"], client=client
        )
        self._assert_fell_back(result, funcs, client)

    def test_text_difference_falls_back_to_the_scan(self):
        # Stored != sent: either another writer's change or a real
        # mismatch. The scan decides.
        result, client, funcs, _ = self._go(["5", _USER, "hell"])
        self._assert_fell_back(result, funcs, client)

    def test_real_mismatch_is_still_reported_through_the_scan(self):
        result, client, funcs, _ = self._go(
            ["5", _USER, "hell"],
            changelog=[_comment_entry(_EPOCH, 5, "hell")],
        )
        self.assertIn(client.get_ticket_changelog, funcs)
        sc = result.structuredContent
        self.assertEqual(sc["comment_number"], 5)
        self.assertIs(sc["comment_stored_matches"], False)
        self.assertEqual(sc["comment_mismatch"]["offset"], 4)
        self.assertEqual(sc["comment_mismatch"]["sent_length"], 5)
        self.assertEqual(sc["comment_mismatch"]["stored_length"], 4)
        self.assertIn("was stored differently", result.content[0].text)

    def test_plugin_path_folds_line_endings_like_the_scan(self):
        result, client, funcs, _ = self._go(
            ["6", _USER, "a\nb\nc"], {"comment": "a\r\nb\rc"}
        )
        sc = result.structuredContent
        self.assertEqual(sc["comment_number"], 6)
        self.assertIs(sc["comment_stored_matches"], True)
        self.assertIs(sc["comment_line_endings_folded"], True)
        self.assertNotIn(client.get_ticket_changelog, funcs)

    def test_empty_stored_text_is_not_the_string_none(self):
        # An empty <string></string> arrives as None. It is not the text
        # "None", so the plugin answer is refused and the scan reports
        # the mismatch.
        result, client, funcs, _ = self._go(
            ["5", _USER, None],
            {"comment": "None"},
            changelog=[_comment_entry(_EPOCH, 5, None)],
        )
        self.assertIn(client.get_ticket_changelog, funcs)
        sc = result.structuredContent
        self.assertEqual(sc["comment_number"], 5)
        self.assertIs(sc["comment_stored_matches"], False)
        self.assertEqual(sc["comment_mismatch"]["stored_length"], 0)

    def _assert_fell_back(self, result, funcs, client):
        self.assertFalse(result.isError)
        self.assertIn(client.get_ticket_changelog, funcs)
        sc = result.structuredContent
        # The changelog fixture holds comment 7; the plugin stub's
        # answers, where it gave one, said 5 or 2.5.
        self.assertEqual(sc["comment_number"], 7)
        self.assertIs(sc["comment_stored_matches"], True)

    def test_falls_back_to_the_scan_when_the_plugin_is_absent(self):
        result, client, funcs, _ = self._go(None)
        self._assert_fell_back(result, funcs, client)

    def test_falls_back_to_the_scan_on_an_empty_list(self):
        result, client, funcs, _ = self._go([])
        self._assert_fell_back(result, funcs, client)

    def test_falls_back_to_the_scan_on_a_dotted_cnum(self):
        result, client, funcs, _ = self._go(["2.5", _USER, "hello"])
        self._assert_fell_back(result, funcs, client)

    def test_falls_back_to_the_scan_on_a_malformed_result(self):
        for bad in (
            ["5", _USER],
            ["5", _USER, "hello", "extra"],
            ["abc", _USER, "hello"],
            ["", _USER, "hello"],
            ["0", _USER, "hello"],
            [None, _USER, "hello"],
            "5",
            {"cnum": "5"},
            5,
            ("5", _USER, "hello"),
        ):
            with self.subTest(bad=bad):
                result, client, funcs, _ = self._go(bad)
                self._assert_fell_back(result, funcs, client)

    def test_falls_back_to_the_scan_on_an_exception(self):
        for err in (
            xmlrpc.client.Fault(1, "TICKET_VIEW required"),
            RuntimeError("boom"),
            OSError("network"),
        ):
            with self.subTest(err=err):
                result, client, funcs, _ = self._go(err)
                self._assert_fell_back(result, funcs, client)

    def test_a_plugin_timeout_spends_the_budget_the_scan_would_get(
        self,
    ):
        # One deadline covers plugin and scan together: a plugin call
        # that uses the whole budget leaves nothing for the changelog.
        async def slow():
            await asyncio.sleep(5)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_write."
            "_COMMENT_LOOKUP_TIMEOUT",
            0.05,
        ):
            result, client, funcs, _ = self._go(slow)
        self.assertFalse(result.isError)
        self.assertNotIn(client.get_ticket_changelog, funcs)
        self.assertIsNone(result.structuredContent["comment_number"])
        self.assertIn(
            "(changelog read timed out)", result.content[0].text
        )

    def test_the_scan_gets_only_the_budget_the_plugin_left(self):
        real_wait_for = asyncio.wait_for
        timeouts = []

        async def recording_wait_for(aw, timeout=None):
            timeouts.append(timeout)
            return await real_wait_for(aw, timeout=timeout)

        async def plugin_takes_a_while():
            await asyncio.sleep(0.2)
            return None

        with (
            patch(
                "trac_mcp_server.mcp.tools.ticket_write."
                "_COMMENT_LOOKUP_TIMEOUT",
                5.0,
            ),
            patch(
                "trac_mcp_server.mcp.tools.ticket_write.asyncio.wait_for",
                recording_wait_for,
            ),
        ):
            result, client, funcs, _ = self._go(plugin_takes_a_while)

        self._assert_fell_back(result, funcs, client)
        self.assertEqual(len(timeouts), 2)
        plugin_timeout, scan_timeout = timeouts
        self.assertLessEqual(plugin_timeout, 5.0)
        self.assertGreater(plugin_timeout, 4.5)
        # The plugin call used at least 0.2 s of the one budget.
        self.assertLess(scan_timeout, plugin_timeout - 0.15)
        self.assertGreater(scan_timeout, 0)

    def test_no_token_means_no_plugin_call(self):
        result, client, funcs, _ = self._go(
            ["5", _USER, "hello"],
            update_reply=_update_reply(with_token=False),
        )
        self.assertNotIn(client.get_ticket_change_at, funcs)
        self._assert_fell_back(result, funcs, client)

    def test_fallback_failure_is_still_reported_not_raised(self):
        result, *_ = self._go(RuntimeError("boom"), changelog=[])
        self.assertFalse(result.isError)
        self.assertIsNone(result.structuredContent["comment_number"])
        self.assertIn("no comment entry", result.content[0].text)


@pytest.mark.live
async def test_live_update_reports_comment_number_and_stored_bytes():
    """Real Trac (/trac_test): a description-only edit consumes comment
    number 1 with an empty comment, so the comment that follows is number
    2, and the byte-for-byte verdict agrees with the changelog (ticket
    #124)."""
    import os
    from urllib.parse import urlparse

    parsed = urlparse(os.environ["TRAC_URL"])
    config = Config(
        trac_url=f"{parsed.scheme}://{parsed.netloc}/trac_test",
        username=os.environ["TRAC_USERNAME"],
        password=os.environ["TRAC_PASSWORD"],
        insecure=os.environ.get("TRAC_INSECURE", "").lower()
        in ("1", "true", "yes"),
    )
    client = TracClient(config)
    ticket_id = client.create_ticket(
        "#124 live test: comment number",
        "Throwaway; deleted by the test.",
        attributes={"owner": "agent_rpc"},
    )
    try:
        first_ts = client.get_ticket(ticket_id)[3]["_ts"]
        first = await _handle_update(
            client,
            {
                "ticket_id": ticket_id,
                "description": "Throwaway, description rewritten.",
                "base_ts": first_ts,
            },
        )
        assert not first.isError, first.content[0].text
        assert first.structuredContent["comment_number"] is None

        body = (
            "Café — naïve 日本語\n"
            "{{{\n#!sh\necho 'in a block'\n}}}\n"
            "two trailing spaces  \n"
            "and a last line"
        )
        second = await _handle_update(
            client,
            {
                "ticket_id": ticket_id,
                "comment": body,
                "base_ts": first.structuredContent["_ts"],
            },
        )
        assert not second.isError, second.content[0].text
        sc = second.structuredContent
        assert sc["comment_number"] == 2, sc["message"]
        assert sc["comment_stored_matches"] is True, sc["message"]
        assert sc["comment_mismatch"] is None

        stored = [
            entry
            for entry in client.get_ticket_changelog(ticket_id)
            if entry[2] == "comment"
            and str(entry[3]).split(".")[-1] == "2"
        ]
        assert len(stored) == 1
        assert stored[0][4] == body

        # A blank comment stores nothing and leaves _ts where it was:
        # no comment number, and no advice to edit a comment.
        blank = await _handle_update(
            client,
            {
                "ticket_id": ticket_id,
                "comment": "   ",
                "base_ts": sc["_ts"],
            },
        )
        assert not blank.isError, blank.content[0].text
        bsc = blank.structuredContent
        assert bsc["comment_number"] is None, bsc["message"]
        assert "Trac stored nothing" in bsc["message"]
        assert "ticket_comment_edit" not in bsc["message"]

        # Two writes back to back, likely in the same second: the second
        # is told apart by its text, and its CRLF arrives as LF.
        third = await _handle_update(
            client,
            {
                "ticket_id": ticket_id,
                "comment": "x",
                "base_ts": bsc["_ts"],
            },
        )
        assert not third.isError, third.content[0].text
        assert third.structuredContent["comment_number"] == 3
        fourth = await _handle_update(
            client,
            {
                "ticket_id": ticket_id,
                "comment": "a\r\nb",
                "base_ts": third.structuredContent["_ts"],
            },
        )
        assert not fourth.isError, fourth.content[0].text
        fsc = fourth.structuredContent
        assert fsc["comment_number"] == 4, fsc["message"]
        assert fsc["comment_stored_matches"] is True, fsc["message"]
        assert fsc["comment_line_endings_folded"] is True, fsc[
            "message"
        ]
    finally:
        client.delete_ticket(ticket_id)


# ---------------------------------------------------------------------------
# Ticket Write Tool Dispatcher tests
# ---------------------------------------------------------------------------


class TestCommentCnum(unittest.TestCase):
    """_comment_cnum keeps its flat-only contract (Trac #131): a
    ticket.update write is never threaded, so a dotted row is not ours,
    and the shared parser decides what a number is."""

    @staticmethod
    def _row(oldvalue):
        return ["t", "a", "comment", oldvalue, "x", 1]

    def test_flat_row(self):
        self.assertEqual(_comment_cnum(self._row("5")), 5)
        self.assertEqual(_comment_cnum(self._row(5)), 5)

    def test_threaded_row_is_none(self):
        self.assertIsNone(_comment_cnum(self._row("2.5")))

    def test_none_oldvalue_is_none(self):
        # An attachment pseudo-row: str(None) is "None", still not a
        # number, but the parser is what says so.
        self.assertIsNone(_comment_cnum(self._row(None)))

    def test_non_numeric_is_none(self):
        for bad in ("abc", "\u00b2", "", "0", "1.2.3"):
            self.assertIsNone(_comment_cnum(self._row(bad)), bad)


class TestHandleTicketWriteTool(unittest.TestCase):
    """Tests for ToolRegistry dispatch of ticket write tools."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.registry = ToolRegistry(TICKET_WRITE_SPECS)

    def _patch_handler(self, tool_name, mock_handler):
        """Replace a handler in the registry with a mock.

        ToolSpec is frozen so we replace the entire spec entry, preserving
        the original tool and permissions but swapping the handler.
        """
        from trac_mcp_server.mcp.tools.registry import ToolSpec

        orig = self.registry._specs[tool_name]
        self.registry._specs[tool_name] = ToolSpec(
            tool=orig.tool,
            permissions=orig.permissions,
            handler=mock_handler,
        )

    def test_routes_to_create(self):
        """Registry routes ticket_create to _handle_create."""
        mock_handler = AsyncMock()
        mock_handler.return_value = types.CallToolResult(
            content=[
                types.TextContent(
                    type="text", text="Created ticket #1: Test"
                )
            ]
        )
        self._patch_handler("ticket_create", mock_handler)

        result = asyncio.run(
            self.registry.call_tool(
                "ticket_create",
                {
                    "summary": "Test",
                    "description": "Desc",
                },
                self.mock_client,
            )
        )

        mock_handler.assert_awaited_once_with(
            self.mock_client,
            {
                "summary": "Test",
                "description": "Desc",
            },
        )
        self.assertEqual(
            result.content[0].text, "Created ticket #1: Test"
        )

    def test_routes_to_update(self):
        """Registry routes ticket_update to _handle_update."""
        mock_handler = AsyncMock()
        mock_handler.return_value = types.CallToolResult(
            content=[
                types.TextContent(
                    type="text", text="Updated ticket #42"
                )
            ]
        )
        self._patch_handler("ticket_update", mock_handler)

        result = asyncio.run(
            self.registry.call_tool(
                "ticket_update",
                {
                    "ticket_id": 42,
                },
                self.mock_client,
            )
        )

        mock_handler.assert_awaited_once_with(
            self.mock_client, {"ticket_id": 42}
        )
        self.assertEqual(result.content[0].text, "Updated ticket #42")

    def test_routes_to_delete(self):
        """Registry routes ticket_delete to _handle_delete."""
        mock_handler = AsyncMock()
        mock_handler.return_value = types.CallToolResult(
            content=[
                types.TextContent(
                    type="text", text="Deleted ticket #42."
                )
            ]
        )
        self._patch_handler("ticket_delete", mock_handler)

        result = asyncio.run(
            self.registry.call_tool(
                "ticket_delete",
                {
                    "ticket_id": 42,
                },
                self.mock_client,
            )
        )

        mock_handler.assert_awaited_once_with(
            self.mock_client, {"ticket_id": 42}
        )
        self.assertEqual(result.content[0].text, "Deleted ticket #42.")

    def test_unknown_tool(self):
        """Unknown tool name raises ValueError from registry."""
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(
                self.registry.call_tool(
                    "ticket_unknown", {}, self.mock_client
                )
            )

        self.assertIn("Unknown tool", str(ctx.exception))

    def test_none_arguments_defaults_to_empty_dict(self):
        """Passing None arguments is handled gracefully (converted to empty dict)."""
        mock_handler = AsyncMock()
        mock_handler.return_value = types.CallToolResult(
            content=[types.TextContent(type="text", text="error")]
        )
        self._patch_handler("ticket_create", mock_handler)

        asyncio.run(
            self.registry.call_tool(
                "ticket_create", None, self.mock_client
            )
        )

        # The registry converts None to {} before passing
        mock_handler.assert_awaited_once_with(self.mock_client, {})

    def test_generic_exception_returns_server_error(self):
        """Unexpected exception returns server_error."""
        mock_handler = AsyncMock()
        mock_handler.side_effect = RuntimeError("connection reset")
        self._patch_handler("ticket_update", mock_handler)

        result = asyncio.run(
            self.registry.call_tool(
                "ticket_update",
                {
                    "ticket_id": 1,
                },
                self.mock_client,
            )
        )

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        self.assertIn("Error (server_error)", result.content[0].text)
        self.assertIn("connection reset", result.content[0].text)


# ---------------------------------------------------------------------------
# Ticket Read handler tests
# ---------------------------------------------------------------------------


class TestHandleTicketSearch:
    """Tests for _handle_search handler."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_search_default_query(self):
        """Search with no args uses default query and returns ticket summaries."""
        client = MagicMock(spec=TracClient)

        mock_data = [
            {
                "id": 1,
                "summary": "Bug A",
                "status": "new",
                "owner": "alice",
                "url": "https://trac.example.com/ticket/1",
            },
            {
                "id": 2,
                "summary": "Bug B",
                "status": "assigned",
                "owner": "bob",
                "url": "https://trac.example.com/ticket/2",
            },
            {
                "id": 3,
                "summary": "Bug C",
                "status": "new",
                "owner": "charlie",
                "url": "https://trac.example.com/ticket/3",
            },
        ]

        def _close_coros_and_return(coros):
            for coro in coros:
                coro.close()
            return mock_data

        with (
            patch(
                "trac_mcp_server.mcp.tools.ticket_read.run_sync"
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.tools.ticket_read.gather_limited",
                side_effect=_close_coros_and_return,
            ) as _mock_gather,
        ):
            mock_run_sync.return_value = [1, 2, 3]

            result = self._run(_handle_search(client, {}))

            assert isinstance(result, types.CallToolResult)
            text = result.content[0].text
            assert "Found 3 tickets" in text
            assert "#1" in text
            assert "#2" in text
            assert "#3" in text
            # Default query
            mock_run_sync.assert_called_once()
            call_args = mock_run_sync.call_args[0]
            assert call_args[0] == client.search_tickets
            assert call_args[1] == "status!=closed"

    def test_search_custom_query_with_max_results(self):
        """Custom query and max_results are forwarded correctly."""
        client = MagicMock(spec=TracClient)

        mock_data = [
            {
                "id": 10,
                "summary": "T10",
                "status": "closed",
                "owner": "x",
                "url": "https://trac.example.com/ticket/10",
            },
            {
                "id": 20,
                "summary": "T20",
                "status": "closed",
                "owner": "x",
                "url": "https://trac.example.com/ticket/20",
            },
            {
                "id": 30,
                "summary": "T30",
                "status": "closed",
                "owner": "x",
                "url": "https://trac.example.com/ticket/30",
            },
            {
                "id": 40,
                "summary": "T40",
                "status": "closed",
                "owner": "x",
                "url": "https://trac.example.com/ticket/40",
            },
            {
                "id": 50,
                "summary": "T50",
                "status": "closed",
                "owner": "x",
                "url": "https://trac.example.com/ticket/50",
            },
        ]

        def _close_coros_and_return(coros):
            for coro in coros:
                coro.close()
            return mock_data

        with (
            patch(
                "trac_mcp_server.mcp.tools.ticket_read.run_sync"
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.tools.ticket_read.gather_limited",
                side_effect=_close_coros_and_return,
            ) as _mock_gather,
        ):
            mock_run_sync.return_value = [10, 20, 30, 40, 50, 60]

            result = self._run(
                _handle_search(
                    client, {"query": "status=closed", "max_results": 5}
                )
            )

            assert isinstance(result, types.CallToolResult)
            text = result.content[0].text
            assert "Found 6 tickets" in text
            assert "showing 5" in text
            # Verify query passed correctly
            call_args = mock_run_sync.call_args[0]
            assert call_args[1] == "status=closed"
            # Verify structured content
            assert result.structuredContent["total"] == 6
            assert result.structuredContent["showing"] == 5

    def test_search_empty_results(self):
        """Empty search returns no-tickets message."""
        client = MagicMock(spec=TracClient)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = []

            result = self._run(_handle_search(client, {}))

            assert isinstance(result, types.CallToolResult)
            text = result.content[0].text
            assert "No tickets found" in text
            assert result.structuredContent["tickets"] == []
            assert result.structuredContent["total"] == 0

    def test_search_with_ticket_details(self):
        """Search fetches details for each ticket via gather_limited."""
        client = MagicMock(spec=TracClient)

        mock_data = [
            {
                "id": 7,
                "summary": "Feature request",
                "status": "new",
                "owner": "dev1",
                "url": "https://trac.example.com/ticket/7",
            },
            {
                "id": 8,
                "summary": "Enhancement",
                "status": "accepted",
                "owner": "dev2",
                "url": "https://trac.example.com/ticket/8",
            },
        ]

        def _close_coros_and_return(coros):
            for coro in coros:
                coro.close()
            return mock_data

        with (
            patch(
                "trac_mcp_server.mcp.tools.ticket_read.run_sync"
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.tools.ticket_read.gather_limited",
                side_effect=_close_coros_and_return,
            ) as mock_gather,
        ):
            mock_run_sync.return_value = [7, 8]

            result = self._run(_handle_search(client, {}))

            text = result.content[0].text
            assert "Feature request" in text
            assert "Enhancement" in text
            assert "status: new" in text
            assert "owner: dev1" in text
            # Verify gather_limited was called
            mock_gather.assert_called_once()

    def test_search_partial_fetch_failure_reports_failed_ids(self):
        """A ticket that fails to fetch is excluded but explained, not silently dropped.

        Reproduces the ticket_search total/showing mismatch from ticket #22:
        total=2, showing=1 with no indication a fetch failed.
        """
        client = _spec_client()

        def _get_ticket_side_effect(tid):
            if tid == 15:
                raise xmlrpc.client.Fault(500, "temporary failure")
            return [
                tid,
                "20260101T00:00:00",
                "20260101T00:00:00",
                {
                    "summary": f"Ticket {tid}",
                    "status": "new",
                    "owner": "alice",
                },
            ]

        async def _fake_run_sync_limited(func, *args, **kwargs):
            return _get_ticket_side_effect(*args, **kwargs)

        with (
            patch(
                "trac_mcp_server.mcp.tools.ticket_read.run_sync"
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.tools.ticket_read.run_sync_limited",
                side_effect=_fake_run_sync_limited,
            ),
        ):
            mock_run_sync.return_value = [10, 15]

            result = self._run(_handle_search(client, {}))

            assert isinstance(result, types.CallToolResult)
            assert result.structuredContent["total"] == 2
            assert result.structuredContent["showing"] == 1
            assert result.structuredContent["failed_ids"] == [15]
            assert "#10" in result.content[0].text
            assert (
                "#15" not in result.content[0].text.split("Warning")[0]
            )
            assert (
                "failed to load 1 ticket(s)" in result.content[0].text
            )
            assert "#15" in result.content[0].text

    def test_search_url_keeps_instance_path(self):
        """A trac_url with an instance path keeps it in each result's
        url: https://trac.example.com/proj/ticket/10."""
        client = _spec_client("https://trac.example.com/proj")

        async def _fake_run_sync_limited(func, *args, **kwargs):
            return [
                args[0],
                "20260101T00:00:00",
                "20260101T00:00:00",
                {
                    "summary": "Pathed",
                    "status": "new",
                    "owner": "alice",
                },
            ]

        with (
            patch(
                "trac_mcp_server.mcp.tools.ticket_read.run_sync"
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.tools.ticket_read.run_sync_limited",
                side_effect=_fake_run_sync_limited,
            ),
        ):
            mock_run_sync.return_value = [10]

            result = self._run(_handle_search(client, {}))

            url = "https://trac.example.com/proj/ticket/10"
            assert result.structuredContent["tickets"][0]["url"] == url
            assert url in result.content[0].text

    def test_search_xmlrpc_fault(self):
        """XML-RPC fault during search produces structured error."""
        client = MagicMock(spec=TracClient)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = xmlrpc.client.Fault(
                500, "Internal server error"
            )

            # Call through registry so the fault is caught and translated
            registry = ToolRegistry(TICKET_READ_SPECS)
            result = self._run(
                registry.call_tool("ticket_search", {}, client)
            )

            assert isinstance(result, types.CallToolResult)
            assert result.isError is True
            assert "Error" in result.content[0].text
            assert "server_error" in result.content[0].text


class TestHandleTicketGet:
    """Tests for _handle_get handler."""

    def _run(self, coro):
        return asyncio.run(coro)

    @staticmethod
    def _client():
        """A TracClient mock with a Config, which ticket_get reads for the
        ticket's web URL."""
        return _spec_client()

    @staticmethod
    def _dispatch(client, ticket_data, changelog=()):
        """run_sync side effect routing on the function being called.

        ticket_get makes two calls now (ticket_get #60), so a bare
        return_value would hand the ticket payload to the changelog
        fetch as well.
        """

        def side_effect(func, *args, **kwargs):
            if func is client.get_ticket_changelog:
                return list(changelog)
            return ticket_data

        return side_effect

    @staticmethod
    def _comment(number, author, body, minute=0):
        """One changelog comment row, shaped as Trac returns it."""
        return [
            datetime(2026, 1, 20, 10, minute, 0),
            author,
            "comment",
            number,
            body,
            1,
        ]

    def test_get_success(self):
        """Get ticket returns full details with the stored description."""
        client = _spec_client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        modified = datetime(2026, 1, 15, 14, 30, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client,
                [
                    42,
                    created,
                    modified,
                    {
                        "summary": "Fix login bug",
                        "description": "= Problem =\nLogin fails",
                        "status": "new",
                        "owner": "alice",
                        "type": "defect",
                        "priority": "high",
                        "component": "auth",
                        "milestone": "v2.0",
                    },
                ],
            )

            result = self._run(_handle_get(client, {"ticket_id": 42}))

            assert isinstance(result, types.CallToolResult)
            text = result.content[0].text
            assert "Ticket #42" in text
            assert "Fix login bug" in text
            assert "= Problem =" in text  # stored, not converted
            assert "new" in text
            assert "alice" in text
            assert "https://trac.example.com/ticket/42" in text
            # Structured content
            assert result.structuredContent["id"] == 42
            assert (
                result.structuredContent["summary"] == "Fix login bug"
            )
            assert (
                result.structuredContent["url"]
                == "https://trac.example.com/ticket/42"
            )

    def test_get_url_keeps_instance_path(self):
        """A trac_url with an instance path (one daemon serving several
        Trac instances) keeps it: the ticket's URL is under /proj, not
        at the host root."""
        client = _spec_client("https://trac.example.com/proj")
        created = datetime(2026, 1, 10, 9, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client,
                [7, created, created, {"summary": "Pathed"}],
            )

            result = self._run(_handle_get(client, {"ticket_id": 7}))

            url = "https://trac.example.com/proj/ticket/7"
            assert result.structuredContent["url"] == url
            assert f"URL: {url}" in result.content[0].text

    def test_get_none_description(self):
        """A ticket whose description was never set comes back from Trac
        with description None; ticket_get must still answer instead of
        failing to join its response lines."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client,
                [
                    7,
                    created,
                    created,
                    {"summary": "No text", "description": None},
                ],
            )

            result = self._run(_handle_get(client, {"ticket_id": 7}))

            assert not result.isError
            assert "Ticket #7: No text" in result.content[0].text
            assert result.structuredContent["description"] == ""

    def test_get_exposes_change_token(self):
        """ticket_get surfaces _ts as the change token callers pass back
        to ticket_update's base_ts (ticket #50) -- without this, there is
        no way for a caller to obtain a token to lock against."""
        client = _spec_client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        modified = datetime(2026, 1, 15, 14, 30, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client,
                [
                    42,
                    created,
                    modified,
                    {
                        "summary": "Fix login bug",
                        "description": "text",
                        "status": "new",
                        "_ts": "1788093861682305",
                    },
                ],
            )

            result = self._run(_handle_get(client, {"ticket_id": 42}))

            assert result.structuredContent["_ts"] == "1788093861682305"
            assert "1788093861682305" in result.content[0].text

    def test_get_returns_stored_tracwiki(self):
        """The description comes back as stored, with no `raw` argument
        to ask for it -- the Markdown read path is gone (ticket #69)."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        modified = datetime(2026, 1, 15, 14, 30, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client,
                [
                    1,
                    created,
                    modified,
                    {
                        "summary": "Raw test",
                        "description": "= TracWiki heading =",
                        "status": "new",
                        "owner": "bob",
                        "type": "task",
                        "priority": "normal",
                        "component": "core",
                        "milestone": "",
                    },
                ],
            )

            result = self._run(_handle_get(client, {"ticket_id": 1}))

            assert isinstance(result, types.CallToolResult)
            text = result.content[0].text
            assert "= TracWiki heading =" in text

    def test_get_missing_ticket_id(self):
        """Missing ticket_id returns validation error."""
        client = self._client()

        result = self._run(_handle_get(client, {}))

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "Error (validation_error)" in result.content[0].text
        assert "ticket_id is required" in result.content[0].text

    def test_get_not_found(self):
        """Ticket not found returns structured error via dispatcher."""
        client = self._client()

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = xmlrpc.client.Fault(
                404, "Ticket 99999 not found"
            )

            registry = ToolRegistry(TICKET_READ_SPECS)
            result = self._run(
                registry.call_tool(
                    "ticket_get", {"ticket_id": 99999}, client
                )
            )

            assert isinstance(result, types.CallToolResult)
            assert result.isError is True
            assert "Error (not_found)" in result.content[0].text

    # --- comments in ticket_get (ticket #60) ---

    def _ticket(self, created, modified, description="Body."):
        return [
            7,
            created,
            modified,
            {
                "summary": "A ticket with a thread",
                "description": description,
                "status": "new",
                "owner": "agent_rpc",
            },
        ]

    def test_get_returns_comments_by_default(self):
        """One ticket_get is the whole ticket: comments come back with
        no flag set, which is the point of ticket #60 -- the operator's
        answer in a comment must not be invisible to a default read."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        modified = datetime(2026, 1, 15, 14, 30, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client,
                self._ticket(created, modified),
                [
                    self._comment("1", "alice", "First note."),
                    self._comment("2", "operator", "Yes, use the cap."),
                ],
            )

            result = self._run(_handle_get(client, {"ticket_id": 7}))

            text = result.content[0].text
            assert "## Comments (2 total)" in text
            assert "comment:2 -- operator" in text
            assert "Yes, use the cap." in text
            structured = result.structuredContent
            assert structured["comment_count"] == 2
            assert structured["comments_included"] is True
            assert structured["comments_truncated"] is False
            assert [c["number"] for c in structured["comments"]] == [
                "1",
                "2",
            ]
            assert structured["comments"][1]["author"] == "operator"

    def test_get_comment_numbers_come_from_changelog_oldvalue(self):
        """The number reported is the changelog entry's oldvalue -- the
        same key ticket_render_check's `comment` parameter is keyed on,
        so it is usable without guessing. Numbering skips field-only
        entries, so it is not the comment's ordinal position."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client,
                self._ticket(created, created),
                [
                    [created, "alice", "status", "new", "assigned", 1],
                    self._comment("4", "alice", "Fourth."),
                    [created, "bob", "priority", "major", "minor", 1],
                    self._comment("6", "bob", "Sixth."),
                ],
            )

            result = self._run(_handle_get(client, {"ticket_id": 7}))

            numbers = [
                c["number"]
                for c in result.structuredContent["comments"]
            ]
            assert numbers == ["4", "6"]

    def test_get_drops_field_only_changelog_entries(self):
        """Field changes never reach the response, so a description edit
        does not drag two full copies of the description along with it
        (the cost that ruled out proxying ticket_changelog wholesale)."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        big = "x" * 5000

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client,
                self._ticket(created, created, description="short"),
                [
                    [created, "alice", "description", big, big, 1],
                    self._comment("1", "bob", "Tiny."),
                ],
            )

            result = self._run(_handle_get(client, {"ticket_id": 7}))

            assert result.structuredContent["comment_count"] == 1
            assert big not in result.content[0].text
            assert "Tiny." in result.content[0].text

    def test_get_include_comments_false_skips_the_fetch(self):
        """The documented opt-out makes no changelog call at all, and
        says plainly that the response is not the whole ticket."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        calls = []

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:

            def side_effect(func, *args, **kwargs):
                calls.append(func)
                return self._ticket(created, created)

            mock_run_sync.side_effect = side_effect

            result = self._run(
                _handle_get(
                    client,
                    {"ticket_id": 7, "include_comments": False},
                )
            )

            assert client.get_ticket_changelog not in calls
            assert "include_comments=false" in result.content[0].text
            structured = result.structuredContent
            assert structured["comments_included"] is False
            assert "comments" not in structured

    def test_get_returns_stored_comment_bodies(self):
        """Comment bodies come back as stored, like the description --
        they reach the response by a different route (the changelog), so
        a fix applied only to the description would pass a
        description-only test (ticket #69)."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client,
                self._ticket(created, created, description="plain"),
                [self._comment("1", "alice", "= Heading =\n\n * item")],
            )

            result = self._run(_handle_get(client, {"ticket_id": 7}))

            body = result.structuredContent["comments"][0]["body"]
            assert body == "= Heading =\n\n * item"

    def test_get_cap_fires_loudly_on_a_long_thread(self):
        """Seeded-defect check for the cap itself: a thread ABOVE the cap
        must produce a visible truncation notice and an exact total. A
        silent cut would recreate ticket #60 in miniature."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        changelog = [
            self._comment(str(n), "alice", f"Comment {n}.", minute=n)
            for n in range(1, 13)
        ]

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client, self._ticket(created, created), changelog
            )

            result = self._run(
                _handle_get(client, {"ticket_id": 7, "max_comments": 4})
            )

            text = result.content[0].text
            assert "12 total, 4 shown, 8 omitted" in text
            assert "8 comment(s) omitted from the middle" in text
            assert "comment:3 through comment:10" in text
            assert "NOT the full comment history" in text
            structured = result.structuredContent
            assert structured["comment_count"] == 12
            assert structured["comments_shown"] == 4
            assert structured["comments_truncated"] is True
            assert structured["omitted_comment_numbers"] == [
                str(n) for n in range(3, 11)
            ]

    def test_get_cap_keeps_head_and_tail(self):
        """Head+tail retention: a long thread usually has its scope set
        in the earliest comments and corrected in the latest."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        changelog = [
            self._comment(str(n), "alice", f"Comment {n}.", minute=n)
            for n in range(1, 13)
        ]

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client, self._ticket(created, created), changelog
            )

            result = self._run(
                _handle_get(client, {"ticket_id": 7, "max_comments": 5})
            )

            kept = [
                c["number"]
                for c in result.structuredContent["comments"]
            ]
            assert kept == ["1", "2", "3", "11", "12"]

    def test_get_cap_of_one_still_shows_the_notice(self):
        """max_comments=1 leaves no tail half; the omitted-middle notice
        must still be emitted rather than silently dropped."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        changelog = [
            self._comment(str(n), "alice", f"Comment {n}.", minute=n)
            for n in range(1, 4)
        ]

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client, self._ticket(created, created), changelog
            )

            result = self._run(
                _handle_get(client, {"ticket_id": 7, "max_comments": 1})
            )

            text = result.content[0].text
            assert "2 comment(s) omitted" in text
            assert result.structuredContent["comment_count"] == 3

    def test_get_thread_at_the_cap_is_not_truncated(self):
        """Exactly at the cap: everything shown, no notice."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        changelog = [
            self._comment(str(n), "alice", f"Comment {n}.", minute=n)
            for n in range(1, 5)
        ]

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client, self._ticket(created, created), changelog
            )

            result = self._run(
                _handle_get(client, {"ticket_id": 7, "max_comments": 4})
            )

            assert "omitted" not in result.content[0].text
            assert (
                result.structuredContent["comments_truncated"] is False
            )

    def test_get_comment_without_a_number_says_so(self):
        """An entry carrying no comment number reports that, rather than
        inventing an ordinal that ticket_render_check would reject."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client,
                self._ticket(created, created),
                [self._comment("", "alice", "Numberless.")],
            )

            result = self._run(_handle_get(client, {"ticket_id": 7}))

            assert "number unavailable" in result.content[0].text
            assert (
                result.structuredContent["comments"][0]["number"]
                is None
            )

    def _get_comments(self, rows):
        client = _spec_client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client, self._ticket(created, created), rows
            )
            return self._run(_handle_get(client, {"ticket_id": 7}))

    def test_get_threaded_comment_reports_its_own_number(self):
        """Trac #131. oldvalue "2.5" is comment 5, a reply to 2 -- the
        key ticket_render_check's `comment` parameter matches, since it
        comes from the HTML's trac-change-5- id. in_reply_to appears
        on threaded rows only."""
        result = self._get_comments(
            [
                self._comment("1", "alice", "Flat."),
                self._comment("2.5", "bob", "Threaded."),
            ]
        )
        flat, threaded = result.structuredContent["comments"]
        assert flat["number"] == "1"
        assert "in_reply_to" not in flat
        assert threaded["number"] == "5"
        assert threaded["in_reply_to"] == "2"
        text = result.content[0].text
        assert "### comment:5 (reply to 2) -- bob" in text
        assert "### comment:1 -- alice" in text
        assert (
            "reply to 2"
            not in text.split("### comment:1")[1].split(
                "### comment:5"
            )[0]
        )

    def test_get_flat_comment_dict_is_unchanged(self):
        result = self._get_comments([self._comment("3", "a", "Body.")])
        (comment,) = result.structuredContent["comments"]
        assert set(comment) == {"number", "author", "timestamp", "body"}
        assert comment["number"] == "3"

    def test_get_non_numeric_and_none_oldvalue_give_no_number(self):
        result = self._get_comments(
            [
                self._comment("abc", "alice", "Junk."),
                self._comment(None, "alice", "None."),
                self._comment("1.2.3", "alice", "Deep."),
                self._comment("\u00b2", "alice", "Super."),
            ]
        )
        comments = result.structuredContent["comments"]
        assert [c["number"] for c in comments] == [None] * 4
        assert all("in_reply_to" not in c for c in comments)

    def test_get_attachment_pseudo_row_is_not_a_comment(self):
        created = datetime(2026, 1, 20, 10, 0, 0)
        result = self._get_comments(
            [
                [created, "alice", "attachment", None, "f.txt", 1],
                self._comment("2.5", "bob", "Threaded."),
            ]
        )
        comments = result.structuredContent["comments"]
        assert [c["number"] for c in comments] == ["5"]

    def test_get_omitted_numbers_use_the_comments_own_number(self):
        rows = [
            self._comment(str(n), "a", f"c{n}") for n in range(1, 7)
        ]
        rows[2] = self._comment("1.3", "a", "threaded three")
        client = _spec_client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client, self._ticket(created, created), rows
            )
            result = self._run(
                _handle_get(client, {"ticket_id": 7, "max_comments": 2})
            )
        assert result.structuredContent["omitted_comment_numbers"] == [
            "2",
            "3",
            "4",
            "5",
        ]

    def test_get_omitted_notice_is_plain_but_heading_keeps_reply_to(
        self,
    ):
        """The heading of a shown threaded comment says "(reply to M)";
        the omitted-comments notice names its range by plain
        comment:N refs, threaded ends included."""
        rows = [
            self._comment(str(n), "a", f"c{n}") for n in range(1, 7)
        ]
        rows[1] = self._comment("1.2", "a", "threaded two")
        rows[4] = self._comment("3.5", "a", "threaded five")
        rows[5] = self._comment("4.6", "a", "threaded six")
        client = _spec_client()
        created = datetime(2026, 1, 10, 9, 0, 0)
        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client, self._ticket(created, created), rows
            )
            result = self._run(
                _handle_get(client, {"ticket_id": 7, "max_comments": 2})
            )
        text = result.content[0].text
        assert "(comment:2 through comment:5)" in text
        assert "### comment:6 (reply to 4) -- a" in text
        notice = next(
            line for line in text.splitlines() if "omitted from" in line
        )
        assert "reply to" not in notice

    def test_get_reports_a_failed_comment_fetch(self):
        """A changelog failure degrades loudly: the ticket still comes
        back, but the response says the comments are missing."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:

            def side_effect(func, *args, **kwargs):
                if func is client.get_ticket_changelog:
                    raise xmlrpc.client.Fault(1, "changelog exploded")
                return self._ticket(created, created)

            mock_run_sync.side_effect = side_effect

            result = self._run(_handle_get(client, {"ticket_id": 7}))

            text = result.content[0].text
            assert "A ticket with a thread" in text
            assert "Comments could not be fetched" in text
            structured = result.structuredContent
            assert structured["comments_included"] is False
            assert "changelog exploded" in structured["comments_error"]

    def test_get_ticket_without_comments(self):
        """A ticket with no comments says so explicitly."""
        client = self._client()
        created = datetime(2026, 1, 10, 9, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = self._dispatch(
                client, self._ticket(created, created), []
            )

            result = self._run(_handle_get(client, {"ticket_id": 7}))

            assert "## Comments" in result.content[0].text
            assert "(none)" in result.content[0].text
            assert result.structuredContent["comment_count"] == 0


class TestHandleTicketChangelog:
    """Tests for _handle_changelog handler."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_changelog_success(self):
        """Changelog returns formatted change entries."""
        client = MagicMock(spec=TracClient)
        ts = datetime(2026, 1, 20, 10, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                [ts, "alice", "status", "new", "assigned", 1],
                [ts, "bob", "comment", "", "Fixed the bug", 1],
            ]

            result = self._run(
                _handle_changelog(client, {"ticket_id": 5})
            )

            assert isinstance(result, types.CallToolResult)
            text = result.content[0].text
            assert "Changelog for ticket #5" in text
            assert "alice" in text
            assert "status" in text
            assert "new" in text
            assert "assigned" in text
            assert "bob" in text
            assert "comment" in text
            assert "Fixed the bug" in text

    def test_changelog_returns_stored_tracwiki(self):
        """Comment content comes back as stored, with no `raw` argument
        to ask for it (ticket #69)."""
        client = MagicMock(spec=TracClient)
        ts = datetime(2026, 1, 20, 10, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                [ts, "alice", "comment", "", "= Wiki heading =", 1],
            ]

            result = self._run(
                _handle_changelog(client, {"ticket_id": 5})
            )

            assert isinstance(result, types.CallToolResult)
            text = result.content[0].text
            assert "= Wiki heading =" in text

    def test_changelog_empty(self):
        """Empty changelog returns appropriate message."""
        client = MagicMock(spec=TracClient)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = []

            result = self._run(
                _handle_changelog(client, {"ticket_id": 99})
            )

            assert isinstance(result, types.CallToolResult)
            text = result.content[0].text
            assert "No changelog" in text
            assert "#99" in text

    def test_changelog_missing_ticket_id(self):
        """Missing ticket_id returns validation error."""
        client = MagicMock(spec=TracClient)

        result = self._run(_handle_changelog(client, {}))

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "Error (validation_error)" in result.content[0].text
        assert "ticket_id is required" in result.content[0].text

    # --- structuredContent tests ---

    def test_changelog_structured_content_present(self):
        """structuredContent is present with changelog key and ticket_id."""
        client = MagicMock(spec=TracClient)
        ts = datetime(2026, 1, 20, 10, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                [ts, "alice", "status", "new", "assigned", 1],
            ]

            result = self._run(
                _handle_changelog(client, {"ticket_id": 7})
            )

            assert result.structuredContent is not None
            assert "changelog" in result.structuredContent
            assert "ticket_id" in result.structuredContent
            assert result.structuredContent["ticket_id"] == 7

    def test_changelog_structured_content_entry_keys(self):
        """Each entry in structuredContent changelog has required keys."""
        client = MagicMock(spec=TracClient)
        ts = datetime(2026, 2, 15, 14, 30, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                [ts, "alice", "status", "new", "assigned", 1],
                [ts, "bob", "comment", "", "Fixed it", 1],
            ]

            result = self._run(
                _handle_changelog(client, {"ticket_id": 5})
            )

            changelog = result.structuredContent["changelog"]
            assert len(changelog) == 2
            expected_keys = {
                "timestamp",
                "author",
                "field",
                "oldvalue",
                "newvalue",
            }
            for entry in changelog:
                assert set(entry.keys()) == expected_keys

    def test_changelog_structured_content_field_change(self):
        """Field-change entries preserve oldvalue/newvalue strings."""
        client = MagicMock(spec=TracClient)
        ts = datetime(2026, 3, 1, 9, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                [ts, "alice", "priority", "minor", "major", 1],
                [ts, "alice", "milestone", "", "v2.0", 1],
            ]

            result = self._run(
                _handle_changelog(client, {"ticket_id": 10})
            )

            changelog = result.structuredContent["changelog"]
            assert len(changelog) == 2

            # First entry: field change with both old and new
            assert changelog[0]["field"] == "priority"
            assert changelog[0]["oldvalue"] == "minor"
            assert changelog[0]["newvalue"] == "major"
            assert changelog[0]["author"] == "alice"

            # Second entry: field set (no oldvalue)
            assert changelog[1]["field"] == "milestone"
            assert changelog[1]["oldvalue"] == ""
            assert changelog[1]["newvalue"] == "v2.0"

    def test_changelog_structured_content_comment_stored_as_written(
        self,
    ):
        """structuredContent carries the stored comment bytes, matching
        the text output rather than diverging from it (ticket #69)."""
        client = MagicMock(spec=TracClient)
        ts = datetime(2026, 3, 5, 12, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                [
                    ts,
                    "bob",
                    "comment",
                    "",
                    "= Wiki heading =\n\nSome '''bold''' text",
                    1,
                ],
            ]

            result = self._run(
                _handle_changelog(client, {"ticket_id": 3})
            )

            changelog = result.structuredContent["changelog"]
            assert len(changelog) == 1
            assert changelog[0]["field"] == "comment"
            assert (
                changelog[0]["newvalue"]
                == "= Wiki heading =\n\nSome '''bold''' text"
            )

    def test_changelog_structured_content_empty(self):
        """Empty changelog returns empty array in structuredContent."""
        client = MagicMock(spec=TracClient)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = []

            result = self._run(
                _handle_changelog(client, {"ticket_id": 99})
            )

            assert result.structuredContent is not None
            assert result.structuredContent["changelog"] == []
            assert result.structuredContent["ticket_id"] == 99

    def test_changelog_structured_content_empty_comment(self):
        """Empty comment newvalue produces empty string in structuredContent."""
        client = MagicMock(spec=TracClient)
        ts = datetime(2026, 4, 1, 8, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                [ts, "alice", "comment", "", "", 1],
            ]

            result = self._run(
                _handle_changelog(client, {"ticket_id": 15})
            )

            changelog = result.structuredContent["changelog"]
            assert len(changelog) == 1
            assert changelog[0]["field"] == "comment"
            assert changelog[0]["newvalue"] == ""

    def test_changelog_text_output_unchanged_with_structured(self):
        """Text output format is unchanged when structuredContent is added."""
        client = MagicMock(spec=TracClient)
        ts = datetime(2026, 1, 20, 10, 0, 0)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                [ts, "alice", "status", "new", "assigned", 1],
                [ts, "bob", "comment", "", "Fixed the bug", 1],
            ]

            result = self._run(
                _handle_changelog(client, {"ticket_id": 5})
            )

            # Verify text output is still present and correct
            text = result.content[0].text
            assert "Changelog for ticket #5" in text
            assert "alice" in text
            assert "status" in text
            assert "bob" in text
            assert "comment" in text
            assert "Fixed the bug" in text

            # And structuredContent is also present
            assert result.structuredContent is not None
            assert len(result.structuredContent["changelog"]) == 2


class TestHandleTicketFields:
    """Tests for _handle_fields handler."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_fields_success(self):
        """Fields returns structured field definitions."""
        client = MagicMock(spec=TracClient)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                {
                    "name": "summary",
                    "type": "text",
                    "label": "Summary",
                    "custom": False,
                },
                {
                    "name": "status",
                    "type": "select",
                    "label": "Status",
                    "options": ["new", "assigned", "closed"],
                    "custom": False,
                },
            ]

            result = self._run(_handle_fields(client, {}))

            assert isinstance(result, types.CallToolResult)
            text = result.content[0].text
            assert "Ticket Fields (2 total)" in text
            assert "summary" in text
            assert "Standard Fields" in text
            # Structured content
            fields = result.structuredContent["fields"]
            assert len(fields) == 2
            assert fields[0]["name"] == "summary"

    def test_fields_includes_custom(self):
        """Custom fields appear in Custom Fields section."""
        client = MagicMock(spec=TracClient)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                {
                    "name": "summary",
                    "type": "text",
                    "label": "Summary",
                    "custom": False,
                },
                {
                    "name": "department",
                    "type": "select",
                    "label": "Department",
                    "options": ["eng", "sales"],
                    "custom": True,
                },
            ]

            result = self._run(_handle_fields(client, {}))

            text = result.content[0].text
            assert "Custom Fields" in text
            assert "department" in text
            assert "eng, sales" in text
            # Structured content
            fields = result.structuredContent["fields"]
            custom = [f for f in fields if f["custom"]]
            assert len(custom) == 1
            assert custom[0]["name"] == "department"


class TestHandleTicketActions:
    """Tests for _handle_actions handler."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_actions_success(self):
        """Actions returns formatted list of workflow actions."""
        client = MagicMock(spec=TracClient)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                ["leave", "leave as new", {}, []],
                ["accept", "accept ticket", {}, []],
                [
                    "resolve",
                    "resolve ticket",
                    {},
                    ["action_resolve_resolve_resolution"],
                ],
            ]

            result = self._run(
                _handle_actions(client, {"ticket_id": 10})
            )

            assert isinstance(result, types.CallToolResult)
            text = result.content[0].text
            assert "Available actions for ticket #10" in text
            assert "leave" in text
            assert "accept" in text
            assert "resolve" in text
            # Structured content
            actions = result.structuredContent["actions"]
            assert len(actions) == 3
            assert actions[0]["name"] == "leave"

    def test_actions_missing_ticket_id(self):
        """Missing ticket_id raises ValueError caught by dispatcher."""
        client = MagicMock(spec=TracClient)

        # _handle_actions raises ValueError when ticket_id missing,
        # registry catches it and returns validation_error
        registry = ToolRegistry(TICKET_READ_SPECS)
        result = self._run(
            registry.call_tool("ticket_actions", {}, client)
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "Error (validation_error)" in result.content[0].text
        assert "ticket_id is required" in result.content[0].text

    def test_actions_empty(self):
        """Empty actions list returns appropriate message."""
        client = MagicMock(spec=TracClient)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = []

            result = self._run(
                _handle_actions(client, {"ticket_id": 5})
            )

            assert isinstance(result, types.CallToolResult)
            text = result.content[0].text
            assert "No available actions" in text
            assert result.structuredContent["actions"] == []

    def test_actions_method_not_available(self):
        """getActions not available returns helpful error."""
        client = MagicMock(spec=TracClient)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = xmlrpc.client.Fault(
                1, "No such method 'ticket.getActions'"
            )

            result = self._run(
                _handle_actions(client, {"ticket_id": 5})
            )

            assert isinstance(result, types.CallToolResult)
            assert result.isError is True
            assert "method_not_available" in result.content[0].text

    def test_actions_with_hints_and_input_fields(self):
        """Actions with list hints and input fields are formatted correctly."""
        client = MagicMock(spec=TracClient)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                [
                    "resolve",
                    "resolve ticket",
                    ["set to closed"],
                    ["resolution"],
                ],
            ]

            result = self._run(
                _handle_actions(client, {"ticket_id": 10})
            )

            text = result.content[0].text
            assert "resolve" in text
            assert "set to closed" in text
            assert "requires: resolution" in text


class TestHandleTicketReadTool:
    """Tests for ToolRegistry dispatch of ticket read tools."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _registry(self):
        return ToolRegistry(TICKET_READ_SPECS)

    def _registry_with_mock(self, tool_name, mock_handler):
        """Create a registry with a mock handler for the given tool."""
        from trac_mcp_server.mcp.tools.registry import ToolSpec

        registry = self._registry()
        orig = registry._specs[tool_name]
        registry._specs[tool_name] = ToolSpec(
            tool=orig.tool,
            permissions=orig.permissions,
            handler=mock_handler,
        )
        return registry

    def test_routes_to_search(self):
        """Registry routes ticket_search to _handle_search."""
        client = MagicMock(spec=TracClient)
        mock_handler = AsyncMock()
        mock_handler.return_value = types.CallToolResult(
            content=[
                types.TextContent(type="text", text="search result")
            ],
        )
        registry = self._registry_with_mock(
            "ticket_search", mock_handler
        )

        result = self._run(
            registry.call_tool(
                "ticket_search", {"query": "status=new"}, client
            )
        )

        mock_handler.assert_awaited_once_with(
            client, {"query": "status=new"}
        )
        assert result.content[0].text == "search result"

    def test_routes_to_get(self):
        """Registry routes ticket_get to _handle_get."""
        client = MagicMock(spec=TracClient)
        mock_handler = AsyncMock()
        mock_handler.return_value = types.CallToolResult(
            content=[types.TextContent(type="text", text="get result")],
        )
        registry = self._registry_with_mock("ticket_get", mock_handler)

        self._run(
            registry.call_tool("ticket_get", {"ticket_id": 1}, client)
        )

        mock_handler.assert_awaited_once_with(client, {"ticket_id": 1})

    def test_routes_to_changelog(self):
        """Registry routes ticket_changelog to _handle_changelog."""
        client = MagicMock(spec=TracClient)
        mock_handler = AsyncMock()
        mock_handler.return_value = types.CallToolResult(
            content=[types.TextContent(type="text", text="changelog")]
        )
        registry = self._registry_with_mock(
            "ticket_changelog", mock_handler
        )

        self._run(
            registry.call_tool(
                "ticket_changelog", {"ticket_id": 1}, client
            )
        )

        mock_handler.assert_awaited_once_with(client, {"ticket_id": 1})

    def test_routes_to_fields(self):
        """Registry routes ticket_fields to _handle_fields."""
        client = MagicMock(spec=TracClient)
        mock_handler = AsyncMock()
        mock_handler.return_value = types.CallToolResult(
            content=[types.TextContent(type="text", text="fields")],
        )
        registry = self._registry_with_mock(
            "ticket_fields", mock_handler
        )

        self._run(registry.call_tool("ticket_fields", {}, client))

        mock_handler.assert_awaited_once_with(client, {})

    def test_routes_to_actions(self):
        """Registry routes ticket_actions to _handle_actions."""
        client = MagicMock(spec=TracClient)
        mock_handler = AsyncMock()
        mock_handler.return_value = types.CallToolResult(
            content=[types.TextContent(type="text", text="actions")],
        )
        registry = self._registry_with_mock(
            "ticket_actions", mock_handler
        )

        self._run(
            registry.call_tool(
                "ticket_actions", {"ticket_id": 1}, client
            )
        )

        mock_handler.assert_awaited_once_with(client, {"ticket_id": 1})

    def test_unknown_tool_raises(self):
        """Unknown tool name raises ValueError from registry."""
        client = MagicMock(spec=TracClient)

        import pytest

        with pytest.raises(ValueError, match="Unknown tool"):
            self._run(
                self._registry().call_tool("ticket_unknown", {}, client)
            )

    def test_xmlrpc_fault_translated(self):
        """XML-RPC fault from handler is translated to structured error."""
        client = MagicMock(spec=TracClient)
        mock_handler = AsyncMock()
        mock_handler.side_effect = xmlrpc.client.Fault(
            403, "Permission denied"
        )
        registry = self._registry_with_mock("ticket_get", mock_handler)

        result = self._run(
            registry.call_tool("ticket_get", {"ticket_id": 1}, client)
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "Error (permission_denied)" in result.content[0].text

    def test_generic_exception_translated(self):
        """Unexpected exception is caught and returned as server_error."""
        client = MagicMock(spec=TracClient)
        mock_handler = AsyncMock()
        mock_handler.side_effect = RuntimeError("connection reset")
        registry = self._registry_with_mock(
            "ticket_search", mock_handler
        )

        result = self._run(
            registry.call_tool("ticket_search", {}, client)
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "Error (server_error)" in result.content[0].text
        assert "connection reset" in result.content[0].text

    def test_get_invalid_ticket_data_format(self):
        """Invalid ticket data format from server returns error."""
        client = MagicMock(spec=TracClient)

        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            # Return invalid format (not a list with 4 elements)
            mock_run_sync.return_value = "unexpected"

            result = self._run(_handle_get(client, {"ticket_id": 1}))

            assert isinstance(result, types.CallToolResult)
            assert result.isError is True
            assert "Error (server_error)" in result.content[0].text
            assert (
                "Invalid ticket data format" in result.content[0].text
            )

    def test_xmlrpc_version_conflict_translated(self):
        """Version conflict fault is translated to version_conflict error."""
        client = MagicMock(spec=TracClient)
        mock_handler = AsyncMock()
        mock_handler.side_effect = xmlrpc.client.Fault(
            409, "Version conflict - not modified"
        )
        registry = self._registry_with_mock("ticket_get", mock_handler)

        result = self._run(
            registry.call_tool("ticket_get", {"ticket_id": 1}, client)
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "Error (version_conflict)" in result.content[0].text
