"""
Integration tests for milestone tool dispatch in MCP server.

These tests verify that milestone tools are properly registered in handle_list_tools
and that milestone tool calls are correctly routed via ToolRegistry in server.py.
"""

import asyncio
import unittest
import xmlrpc.client
from unittest.mock import MagicMock, patch

from trac_mcp_server.mcp.server import (
    PING_SPEC,
    handle_call_tool,
    handle_list_tools,
    set_registry,
)
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import ToolRegistry


def _init_registry():
    """Initialize the global registry for testing."""
    registry = ToolRegistry([PING_SPEC] + ALL_SPECS)
    set_registry(registry)


def _clear_registry():
    """Clear the global registry after testing."""
    set_registry(None)


class TestMilestoneToolRegistration(unittest.TestCase):
    """Test milestone tools are registered in handle_list_tools."""

    def setUp(self):
        _init_registry()

    def tearDown(self):
        _clear_registry()

    def test_all_milestone_tools_registered(self):
        """Test all 5 milestone tools appear in handle_list_tools response."""
        tools = asyncio.run(handle_list_tools())
        tool_names = [t.name for t in tools]

        # Verify all 5 milestone tools are registered
        self.assertIn("milestone_list", tool_names)
        self.assertIn("milestone_get", tool_names)
        self.assertIn("milestone_create", tool_names)
        self.assertIn("milestone_update", tool_names)
        self.assertIn("milestone_delete", tool_names)

    def test_milestone_list_schema(self):
        """Test milestone_list has correct schema."""
        tools = asyncio.run(handle_list_tools())
        list_tool = next(t for t in tools if t.name == "milestone_list")

        # Should have no required parameters
        self.assertEqual(list_tool.inputSchema["type"], "object")
        self.assertEqual(list_tool.inputSchema["required"], [])

    def test_milestone_get_schema(self):
        """Test milestone_get has correct schema."""
        tools = asyncio.run(handle_list_tools())
        get_tool = next(t for t in tools if t.name == "milestone_get")

        # Should require name
        self.assertIn("name", get_tool.inputSchema["properties"])
        self.assertEqual(get_tool.inputSchema["required"], ["name"])

    def test_milestone_create_schema(self):
        """Test milestone_create has correct schema with attributes."""
        tools = asyncio.run(handle_list_tools())
        create_tool = next(
            t for t in tools if t.name == "milestone_create"
        )

        # Should require name, attributes is optional
        self.assertIn("name", create_tool.inputSchema["properties"])
        self.assertIn(
            "attributes", create_tool.inputSchema["properties"]
        )
        self.assertEqual(create_tool.inputSchema["required"], ["name"])

        # Attributes should have due, completed, description
        attrs_schema = create_tool.inputSchema["properties"][
            "attributes"
        ]
        self.assertIn("due", attrs_schema["properties"])
        self.assertIn("completed", attrs_schema["properties"])
        self.assertIn("description", attrs_schema["properties"])

    def test_milestone_update_schema(self):
        """Test milestone_update has correct schema."""
        tools = asyncio.run(handle_list_tools())
        update_tool = next(
            t for t in tools if t.name == "milestone_update"
        )

        # Should require name and attributes
        self.assertIn("name", update_tool.inputSchema["properties"])
        self.assertIn(
            "attributes", update_tool.inputSchema["properties"]
        )
        self.assertEqual(
            sorted(update_tool.inputSchema["required"]),
            ["attributes", "name"],
        )

    def test_milestone_delete_schema(self):
        """Test milestone_delete has correct schema."""
        tools = asyncio.run(handle_list_tools())
        delete_tool = next(
            t for t in tools if t.name == "milestone_delete"
        )

        # Should require name
        self.assertIn("name", delete_tool.inputSchema["properties"])
        self.assertEqual(delete_tool.inputSchema["required"], ["name"])

    def test_ticket_fields_registered(self):
        """Test ticket_fields tool is registered."""
        tools = asyncio.run(handle_list_tools())
        tool_names = [t.name for t in tools]
        self.assertIn("ticket_fields", tool_names)


class TestMilestoneToolDispatch(unittest.TestCase):
    """Test milestone tool calls are dispatched correctly."""

    def setUp(self):
        _init_registry()

    def tearDown(self):
        _clear_registry()

    @patch("trac_mcp_server.mcp.server.get_instances")
    @patch("trac_mcp_server.mcp.tools.milestone.run_sync")
    def test_list_milestones_dispatch(
        self, mock_run_sync, mock_get_instances
    ):
        """Test milestone_list is dispatched to handle_milestone_tool."""
        # Mock TracClient
        mock_client = MagicMock()
        mock_get_instances.return_value.get_client.return_value = (
            mock_client
        )

        # Mock run_sync to return milestone names
        async def mock_run_sync_impl(func, *args):
            return ["v1.0", "v2.0", "Future"]

        mock_run_sync.side_effect = mock_run_sync_impl

        # Call handle_call_tool
        result = asyncio.run(handle_call_tool("milestone_list", {}))

        # Verify result - now returns CallToolResult
        from mcp.types import CallToolResult

        self.assertIsInstance(result, CallToolResult)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0].type, "text")
        self.assertIn("v1.0", result.content[0].text)
        self.assertIn("v2.0", result.content[0].text)
        # Verify structured content
        self.assertIsNotNone(result.structuredContent)
        self.assertEqual(
            result.structuredContent["milestones"],
            ["v1.0", "v2.0", "Future"],
        )

    @patch("trac_mcp_server.mcp.server.get_instances")
    @patch("trac_mcp_server.mcp.tools.milestone.run_sync")
    def test_get_milestone_dispatch(
        self, mock_run_sync, mock_get_instances
    ):
        """Test milestone_get is dispatched to handle_milestone_tool."""
        # Mock TracClient
        mock_client = MagicMock()
        mock_get_instances.return_value.get_client.return_value = (
            mock_client
        )

        # Mock run_sync to return milestone data
        async def mock_run_sync_impl(func, *args):
            return {
                "name": "v1.0",
                "due": 0,
                "completed": 0,
                "description": "First release",
            }

        mock_run_sync.side_effect = mock_run_sync_impl

        # Call handle_call_tool
        result = asyncio.run(
            handle_call_tool("milestone_get", {"name": "v1.0"})
        )

        # Verify result - now returns CallToolResult
        from mcp.types import CallToolResult

        self.assertIsInstance(result, CallToolResult)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0].type, "text")
        self.assertIn("v1.0", result.content[0].text)
        self.assertIn("First release", result.content[0].text)
        # Verify structured content
        self.assertIsNotNone(result.structuredContent)
        self.assertEqual(result.structuredContent["name"], "v1.0")

    @patch("trac_mcp_server.mcp.server.get_instances")
    @patch("trac_mcp_server.mcp.tools.milestone.run_sync")
    def test_create_milestone_dispatch(
        self, mock_run_sync, mock_get_instances
    ):
        """Test milestone_create is dispatched to handle_milestone_tool."""
        # Mock TracClient
        mock_client = MagicMock()
        mock_get_instances.return_value.get_client.return_value = (
            mock_client
        )

        # Mock run_sync (create returns None)
        async def mock_run_sync_impl(func, *args):
            return None

        mock_run_sync.side_effect = mock_run_sync_impl

        # Call handle_call_tool
        result = asyncio.run(
            handle_call_tool(
                "milestone_create",
                {
                    "name": "v3.0",
                    "attributes": {"description": "Future work"},
                },
            )
        )

        # Verify result - now returns CallToolResult
        from mcp.types import CallToolResult

        self.assertIsInstance(result, CallToolResult)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0].type, "text")
        self.assertIn("Created milestone: v3.0", result.content[0].text)

    @patch("trac_mcp_server.mcp.server.get_instances")
    @patch("trac_mcp_server.mcp.tools.milestone.run_sync")
    def test_update_milestone_dispatch(
        self, mock_run_sync, mock_get_instances
    ):
        """Test milestone_update is dispatched to handle_milestone_tool."""
        # Mock TracClient
        mock_client = MagicMock()
        mock_get_instances.return_value.get_client.return_value = (
            mock_client
        )

        # Mock run_sync (update returns None)
        async def mock_run_sync_impl(func, *args):
            return None

        mock_run_sync.side_effect = mock_run_sync_impl

        # Call handle_call_tool
        result = asyncio.run(
            handle_call_tool(
                "milestone_update",
                {
                    "name": "v1.0",
                    "attributes": {
                        "completed": 0,
                        "description": "Updated",
                    },
                },
            )
        )

        # Verify result - now returns CallToolResult
        from mcp.types import CallToolResult

        self.assertIsInstance(result, CallToolResult)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0].type, "text")
        self.assertIn(
            "Updated milestone 'v1.0'", result.content[0].text
        )

    @patch("trac_mcp_server.mcp.server.get_instances")
    @patch("trac_mcp_server.mcp.tools.milestone.run_sync")
    def test_delete_milestone_dispatch(
        self, mock_run_sync, mock_get_instances
    ):
        """Test milestone_delete is dispatched to handle_milestone_tool."""
        # Mock TracClient
        mock_client = MagicMock()
        mock_get_instances.return_value.get_client.return_value = (
            mock_client
        )

        # Mock run_sync (delete returns None)
        async def mock_run_sync_impl(func, *args):
            return None

        mock_run_sync.side_effect = mock_run_sync_impl

        # Call handle_call_tool
        result = asyncio.run(
            handle_call_tool("milestone_delete", {"name": "old"})
        )

        # Verify result - now returns CallToolResult
        from mcp.types import CallToolResult

        self.assertIsInstance(result, CallToolResult)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0].type, "text")
        self.assertIn("Deleted milestone: old", result.content[0].text)

    @patch("trac_mcp_server.mcp.server.get_instances")
    @patch("trac_mcp_server.mcp.tools.ticket_read.run_sync")
    def test_ticket_fields_dispatch(
        self, mock_run_sync, mock_get_instances
    ):
        """Test ticket_fields is dispatched to handle_ticket_tool."""
        # Mock TracClient
        mock_client = MagicMock()
        mock_get_instances.return_value.get_client.return_value = (
            mock_client
        )

        # Mock run_sync to return field metadata
        async def mock_run_sync_impl(func, *args):
            return [
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
                    "options": ["new", "closed"],
                    "custom": False,
                },
                {
                    "name": "work_units",
                    "type": "text",
                    "label": "Work Units",
                    "custom": True,
                },
            ]

        mock_run_sync.side_effect = mock_run_sync_impl

        # Call handle_call_tool
        result = asyncio.run(handle_call_tool("ticket_fields", {}))

        # Verify result - now returns CallToolResult with structuredContent
        from mcp.types import CallToolResult

        self.assertIsInstance(result, CallToolResult)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0].type, "text")
        self.assertIn("summary", result.content[0].text)
        self.assertIn("Standard Fields:", result.content[0].text)
        self.assertIn("Custom Fields:", result.content[0].text)
        self.assertIn("work_units", result.content[0].text)
        # Verify structured content
        self.assertIsNotNone(result.structuredContent)
        self.assertIn("fields", result.structuredContent)
        self.assertEqual(len(result.structuredContent["fields"]), 3)


class TestMilestoneToolErrors(unittest.TestCase):
    """Test milestone tool error handling."""

    def setUp(self):
        _init_registry()

    def tearDown(self):
        _clear_registry()

    @patch("trac_mcp_server.mcp.server.get_instances")
    @patch("trac_mcp_server.mcp.tools.milestone.run_sync")
    def test_milestone_not_found(
        self, mock_run_sync, mock_get_instances
    ):
        """Test error when milestone not found."""
        # Mock TracClient
        mock_client = MagicMock()
        mock_get_instances.return_value.get_client.return_value = (
            mock_client
        )

        # Mock run_sync to raise Fault
        async def mock_run_sync_impl(func, *args):
            raise xmlrpc.client.Fault(1, "Milestone not found")

        mock_run_sync.side_effect = mock_run_sync_impl

        # Call handle_call_tool
        result = asyncio.run(
            handle_call_tool("milestone_get", {"name": "missing"})
        )

        # Verify error response - now returns CallToolResult with isError=True
        from mcp.types import CallToolResult

        self.assertIsInstance(result, CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0].type, "text")
        self.assertIn("Error", result.content[0].text)
        self.assertIn("not found", result.content[0].text.lower())

    @patch("trac_mcp_server.mcp.server.get_instances")
    @patch("trac_mcp_server.mcp.tools.milestone.run_sync")
    def test_milestone_permission_error(
        self, mock_run_sync, mock_get_instances
    ):
        """Test error when user lacks TICKET_ADMIN permission."""
        # Mock TracClient
        mock_client = MagicMock()
        mock_get_instances.return_value.get_client.return_value = (
            mock_client
        )

        # Mock run_sync to raise permission Fault
        async def mock_run_sync_impl(func, *args):
            raise xmlrpc.client.Fault(403, "TICKET_ADMIN required")

        mock_run_sync.side_effect = mock_run_sync_impl

        # Call handle_call_tool
        result = asyncio.run(
            handle_call_tool(
                "milestone_create", {"name": "v4.0", "attributes": {}}
            )
        )

        # Verify error response - now returns CallToolResult with isError=True
        from mcp.types import CallToolResult

        self.assertIsInstance(result, CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0].type, "text")
        self.assertIn("Error", result.content[0].text)
        self.assertIn("permission", result.content[0].text.lower())

    @patch("trac_mcp_server.mcp.server.get_instances")
    def test_unknown_milestone_tool(self, mock_get_instances):
        """Test error for invalid milestone tool name."""
        # Mock TracClient
        mock_client = MagicMock()
        mock_get_instances.return_value.get_client.return_value = (
            mock_client
        )

        # Call with invalid tool name - should return error response, not raise exception
        result = asyncio.run(handle_call_tool("milestone_unknown", {}))

        # Verify error response - now returns CallToolResult with isError=True
        from mcp.types import CallToolResult

        self.assertIsInstance(result, CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0].type, "text")
        self.assertIn("Error", result.content[0].text)
        self.assertIn("Unknown", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
