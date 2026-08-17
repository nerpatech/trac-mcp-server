"""
Tests for system MCP tool handlers.

These tests verify system tool definitions and handler behavior with mocked TracClient.
"""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.mcp.tools.system import (
    SYSTEM_SPECS,
    SYSTEM_TOOLS,
)


class TestSystemTools(unittest.TestCase):
    """Test SYSTEM_TOOLS definitions."""

    def test_one_tool_defined(self):
        """Test SYSTEM_TOOLS contains exactly 1 tool."""
        self.assertEqual(len(SYSTEM_TOOLS), 1)

    def test_tool_name(self):
        """Test tool name is correct."""
        self.assertEqual(SYSTEM_TOOLS[0].name, "get_server_time")

    def test_get_server_time_schema(self):
        """Test get_server_time has empty schema (no parameters)."""
        get_tool = SYSTEM_TOOLS[0]

        self.assertEqual(get_tool.inputSchema["type"], "object")
        self.assertEqual(get_tool.inputSchema["properties"], {})
        self.assertEqual(get_tool.inputSchema["required"], [])


class TestGetServerTimeHandler(unittest.TestCase):
    """Test get_server_time handler behavior.

    ticket #33: get_server_time used to infer "current time" from a
    wiki page's lastModified timestamp -- stale the moment nothing on
    that page changes -- instead of the server's actual clock. The
    fixed handler just awaits client.get_server_time() (TracClient.
    get_server_time, exercised separately in test_client.py) and
    formats whatever datetime it returns.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.mock_client = MagicMock()

    @patch("trac_mcp_server.mcp.tools.system.run_sync")
    def test_get_server_time_success(self, mock_run_sync):
        """get_server_time returns the client's reported timestamp."""
        server_dt = datetime(2026, 8, 17, 17, 6, 7, tzinfo=timezone.utc)
        mock_run_sync.return_value = server_dt

        result = asyncio.run(
            ToolRegistry(SYSTEM_SPECS).call_tool(
                "get_server_time", {}, self.mock_client
            )
        )

        self.assertTrue(hasattr(result, "content"))
        self.assertTrue(hasattr(result, "structuredContent"))

        text_content = result.content[0].text
        self.assertIn("Server time:", text_content)

        structured = result.structuredContent
        self.assertEqual(
            structured["server_time"], "2026-08-17T17:06:07+00:00"
        )
        self.assertEqual(
            structured["unix_timestamp"], int(server_dt.timestamp())
        )
        self.assertEqual(structured["timezone"], "server")

        # Verify run_sync was handed client.get_server_time, not the old
        # wiki-page lastModified lookup.
        mock_run_sync.assert_called_once_with(
            self.mock_client.get_server_time
        )

    @patch("trac_mcp_server.mcp.tools.system.run_sync")
    def test_get_server_time_no_date_header(self, mock_run_sync):
        """A response with no Date header surfaces as a server_error,
        not a silently wrong timestamp.
        """
        mock_run_sync.side_effect = RuntimeError(
            "Trac server response did not include an HTTP Date header"
        )

        result = asyncio.run(
            ToolRegistry(SYSTEM_SPECS).call_tool(
                "get_server_time", {}, self.mock_client
            )
        )

        self.assertTrue(result.isError)
        self.assertIn("Error", result.content[0].text)
        self.assertIn("Date header", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
