"""Tests for batch ticket tool handlers."""

import asyncio
import unittest
import xmlrpc.client
from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types as types

from trac_mcp_server.mcp.tools.registry import ToolRegistry, ToolSpec
from trac_mcp_server.mcp.tools.ticket_batch import (
    TICKET_BATCH_SPECS,
    TICKET_BATCH_TOOLS,
    _handle_batch_create,
    _handle_batch_delete,
    _handle_batch_update,
)


class TestTicketBatchToolSchemas(unittest.TestCase):
    """Tests for batch tool schema definitions."""

    def test_batch_tools_count(self):
        """There are exactly 3 batch tools."""
        self.assertEqual(len(TICKET_BATCH_TOOLS), 3)

    def test_batch_create_schema(self):
        """ticket_batch_create schema has required tickets array."""
        tool = next(
            t
            for t in TICKET_BATCH_TOOLS
            if t.name == "ticket_batch_create"
        )
        self.assertEqual(tool.name, "ticket_batch_create")
        schema = tool.inputSchema
        self.assertEqual(schema["required"], ["tickets"])
        tickets_prop = schema["properties"]["tickets"]
        self.assertEqual(tickets_prop["type"], "array")
        item_schema = tickets_prop["items"]
        self.assertEqual(item_schema["type"], "object")
        self.assertIn("summary", item_schema["required"])
        self.assertIn("description", item_schema["required"])

    def test_batch_delete_schema(self):
        """ticket_batch_delete schema has required ticket_ids array of integers."""
        tool = next(
            t
            for t in TICKET_BATCH_TOOLS
            if t.name == "ticket_batch_delete"
        )
        self.assertEqual(tool.name, "ticket_batch_delete")
        schema = tool.inputSchema
        self.assertEqual(schema["required"], ["ticket_ids"])
        ids_prop = schema["properties"]["ticket_ids"]
        self.assertEqual(ids_prop["type"], "array")
        self.assertEqual(ids_prop["items"]["type"], "integer")

    def test_batch_update_schema(self):
        """ticket_batch_update schema has required updates array."""
        tool = next(
            t
            for t in TICKET_BATCH_TOOLS
            if t.name == "ticket_batch_update"
        )
        self.assertEqual(tool.name, "ticket_batch_update")
        schema = tool.inputSchema
        self.assertEqual(schema["required"], ["updates"])
        updates_prop = schema["properties"]["updates"]
        self.assertEqual(updates_prop["type"], "array")
        item_schema = updates_prop["items"]
        self.assertEqual(item_schema["type"], "object")
        self.assertIn("ticket_id", item_schema["required"])


class TestHandleBatchCreate(unittest.TestCase):
    """Tests for _handle_batch_create handler."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.config.max_batch_size = 500

    def test_batch_create_success(self):
        """Batch create with 3 tickets returns all succeeded."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_rsl:
            mock_rsl.side_effect = [1, 2, 3]

            result = asyncio.run(
                _handle_batch_create(
                    self.mock_client,
                    {
                        "tickets": [
                            {
                                "summary": "Ticket A",
                                "description": "Desc A",
                            },
                            {
                                "summary": "Ticket B",
                                "description": "Desc B",
                            },
                            {
                                "summary": "Ticket C",
                                "description": "Desc C",
                            },
                        ]
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertIn("3/3 succeeded", result.content[0].text)
            self.assertIn("0 failed", result.content[0].text)
            sc = result.structuredContent
            self.assertEqual(sc["succeeded"], 3)
            self.assertEqual(sc["failed_count"], 0)
            self.assertEqual(len(sc["created"]), 3)
            self.assertEqual(sc["created"][0]["id"], 1)
            self.assertEqual(sc["created"][1]["id"], 2)
            self.assertEqual(sc["created"][2]["id"], 3)

    def test_batch_create_partial_failure(self):
        """Batch create with one xmlrpc fault reports partial failure."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_rsl:
            mock_rsl.side_effect = [
                1,
                xmlrpc.client.Fault(500, "Server error"),
            ]

            result = asyncio.run(
                _handle_batch_create(
                    self.mock_client,
                    {
                        "tickets": [
                            {
                                "summary": "Ticket A",
                                "description": "Desc A",
                            },
                            {
                                "summary": "Ticket B",
                                "description": "Desc B",
                            },
                        ]
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertIn("1/2 succeeded", result.content[0].text)
            self.assertIn("1 failed", result.content[0].text)
            sc = result.structuredContent
            self.assertEqual(sc["succeeded"], 1)
            self.assertEqual(sc["failed_count"], 1)
            self.assertEqual(len(sc["created"]), 1)
            self.assertEqual(len(sc["failed"]), 1)

    def test_batch_create_missing_tickets(self):
        """Missing tickets key returns validation error."""
        result = asyncio.run(_handle_batch_create(self.mock_client, {}))

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertIn("validation_error", result.content[0].text)
        self.assertIn(
            "tickets list is required", result.content[0].text
        )

    def test_batch_create_empty_list(self):
        """Empty tickets list returns validation error."""
        result = asyncio.run(
            _handle_batch_create(self.mock_client, {"tickets": []})
        )

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertIn("validation_error", result.content[0].text)

    def test_batch_create_exceeds_max_batch_size(self):
        """Batch exceeding max size returns validation error."""
        self.mock_client.config.max_batch_size = 2
        tickets = [
            {"summary": f"T{i}", "description": f"D{i}"}
            for i in range(3)
        ]
        result = asyncio.run(
            _handle_batch_create(self.mock_client, {"tickets": tickets})
        )

        self.assertIsInstance(result, types.CallToolResult)
        self.assertTrue(result.isError)
        self.assertIn("validation_error", result.content[0].text)
        self.assertIn(
            "Batch size 3 exceeds maximum 2", result.content[0].text
        )

    def test_batch_create_missing_summary(self):
        """Ticket missing summary appears in failed list."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_rsl:
            mock_rsl.return_value = 1

            result = asyncio.run(
                _handle_batch_create(
                    self.mock_client,
                    {
                        "tickets": [
                            {"description": "No summary here"},
                            {
                                "summary": "Good ticket",
                                "description": "With desc",
                            },
                        ]
                    },
                )
            )

            sc = result.structuredContent
            self.assertEqual(sc["succeeded"], 1)
            self.assertEqual(sc["failed_count"], 1)
            self.assertEqual(
                sc["failed"][0]["error"], "summary is required"
            )

    def test_batch_create_missing_description(self):
        """Ticket missing description appears in failed list."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_rsl:
            mock_rsl.return_value = 1

            result = asyncio.run(
                _handle_batch_create(
                    self.mock_client,
                    {
                        "tickets": [
                            {"summary": "No description"},
                            {
                                "summary": "Good ticket",
                                "description": "With desc",
                            },
                        ]
                    },
                )
            )

            sc = result.structuredContent
            self.assertEqual(sc["succeeded"], 1)
            self.assertEqual(sc["failed_count"], 1)
            self.assertEqual(
                sc["failed"][0]["error"], "description is required"
            )

    def test_batch_create_stores_each_description_as_written(self):
        """Every item in the batch is stored byte-for-byte, not just the
        first -- the loop body is where a per-item conversion would come
        back (ticket #69)."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_rsl:
            mock_rsl.side_effect = [10, 11]

            asyncio.run(
                _handle_batch_create(
                    self.mock_client,
                    {
                        "tickets": [
                            {
                                "summary": "A",
                                "description": "'''bold'''",
                            },
                            {
                                "summary": "B",
                                "description": "= heading =",
                            },
                        ]
                    },
                )
            )

            self.assertEqual(mock_rsl.call_count, 2)
            self.assertEqual(
                mock_rsl.call_args_list[0][0][2], "'''bold'''"
            )
            self.assertEqual(
                mock_rsl.call_args_list[1][0][2], "= heading ="
            )


class TestHandleBatchDelete(unittest.TestCase):
    """Tests for _handle_batch_delete handler."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.config.max_batch_size = 500

    def test_batch_delete_success(self):
        """Batch delete all succeed."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_rsl:
            mock_rsl.return_value = True

            result = asyncio.run(
                _handle_batch_delete(
                    self.mock_client, {"ticket_ids": [1, 2, 3]}
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertIn("3/3 succeeded", result.content[0].text)
            sc = result.structuredContent
            self.assertEqual(sc["succeeded"], 3)
            self.assertEqual(sc["failed_count"], 0)
            self.assertEqual(sc["deleted"], [1, 2, 3])

    def test_batch_delete_partial_failure(self):
        """One delete fails, others succeed."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_rsl:
            mock_rsl.side_effect = [
                True,
                xmlrpc.client.Fault(403, "Permission denied"),
                True,
            ]

            result = asyncio.run(
                _handle_batch_delete(
                    self.mock_client, {"ticket_ids": [10, 20, 30]}
                )
            )

            sc = result.structuredContent
            self.assertEqual(sc["succeeded"], 2)
            self.assertEqual(sc["failed_count"], 1)
            self.assertIn(10, sc["deleted"])
            self.assertIn(30, sc["deleted"])
            self.assertEqual(sc["failed"][0]["id"], 20)

    def test_batch_delete_missing_ids(self):
        """Missing ticket_ids returns validation error."""
        result = asyncio.run(_handle_batch_delete(self.mock_client, {}))

        self.assertTrue(result.isError)
        self.assertIn("validation_error", result.content[0].text)

    def test_batch_delete_empty_list(self):
        """Empty ticket_ids list returns validation error."""
        result = asyncio.run(
            _handle_batch_delete(self.mock_client, {"ticket_ids": []})
        )

        self.assertTrue(result.isError)
        self.assertIn("validation_error", result.content[0].text)

    def test_batch_delete_exceeds_max_batch_size(self):
        """Batch exceeding max size returns validation error."""
        self.mock_client.config.max_batch_size = 2
        result = asyncio.run(
            _handle_batch_delete(
                self.mock_client, {"ticket_ids": [1, 2, 3]}
            )
        )

        self.assertTrue(result.isError)
        self.assertIn(
            "Batch size 3 exceeds maximum 2", result.content[0].text
        )


class TestHandleBatchUpdate(unittest.TestCase):
    """Tests for _handle_batch_update handler."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.config.max_batch_size = 500

    def test_batch_update_success(self):
        """Batch update all succeed."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_rsl:
            mock_rsl.return_value = True

            result = asyncio.run(
                _handle_batch_update(
                    self.mock_client,
                    {
                        "updates": [
                            {"ticket_id": 1, "status": "closed"},
                            {"ticket_id": 2, "priority": "high"},
                        ]
                    },
                )
            )

            self.assertIsInstance(result, types.CallToolResult)
            self.assertIn("2/2 succeeded", result.content[0].text)
            sc = result.structuredContent
            self.assertEqual(sc["succeeded"], 2)
            self.assertEqual(sc["failed_count"], 0)
            self.assertEqual(sc["updated"], [1, 2])

    def test_batch_update_partial_failure(self):
        """One update fails, others succeed."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_rsl:
            mock_rsl.side_effect = [
                True,
                RuntimeError("connection lost"),
            ]

            result = asyncio.run(
                _handle_batch_update(
                    self.mock_client,
                    {
                        "updates": [
                            {"ticket_id": 5, "status": "assigned"},
                            {"ticket_id": 6, "status": "closed"},
                        ]
                    },
                )
            )

            sc = result.structuredContent
            self.assertEqual(sc["succeeded"], 1)
            self.assertEqual(sc["failed_count"], 1)
            self.assertIn(5, sc["updated"])
            self.assertEqual(sc["failed"][0]["id"], 6)
            self.assertIn("connection lost", sc["failed"][0]["error"])

    def test_batch_update_missing_updates(self):
        """Missing updates key returns validation error."""
        result = asyncio.run(_handle_batch_update(self.mock_client, {}))

        self.assertTrue(result.isError)
        self.assertIn("validation_error", result.content[0].text)

    def test_batch_update_empty_list(self):
        """Empty updates list returns validation error."""
        result = asyncio.run(
            _handle_batch_update(self.mock_client, {"updates": []})
        )

        self.assertTrue(result.isError)
        self.assertIn("validation_error", result.content[0].text)

    def test_batch_update_missing_ticket_id(self):
        """Update missing ticket_id appears in failed list."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_rsl:
            mock_rsl.return_value = True

            result = asyncio.run(
                _handle_batch_update(
                    self.mock_client,
                    {
                        "updates": [
                            {"status": "closed"},  # missing ticket_id
                            {"ticket_id": 1, "status": "closed"},
                        ]
                    },
                )
            )

            sc = result.structuredContent
            self.assertEqual(sc["succeeded"], 1)
            self.assertEqual(sc["failed_count"], 1)
            self.assertIn(
                "ticket_id is required", sc["failed"][0]["error"]
            )

    def test_batch_update_stores_comment_as_written(self):
        """Comments in updates are stored byte-for-byte (ticket #69)."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_rsl:
            mock_rsl.return_value = True

            asyncio.run(
                _handle_batch_update(
                    self.mock_client,
                    {
                        "updates": [
                            {
                                "ticket_id": 1,
                                "comment": "'''bold comment'''",
                            },
                        ]
                    },
                )
            )

            call_args = mock_rsl.call_args[0]
            self.assertEqual(call_args[2], "'''bold comment'''")

    def test_batch_update_exceeds_max_batch_size(self):
        """Batch exceeding max size returns validation error."""
        self.mock_client.config.max_batch_size = 1
        result = asyncio.run(
            _handle_batch_update(
                self.mock_client,
                {
                    "updates": [
                        {"ticket_id": 1, "status": "closed"},
                        {"ticket_id": 2, "status": "closed"},
                    ]
                },
            )
        )

        self.assertTrue(result.isError)
        self.assertIn(
            "Batch size 2 exceeds maximum 1", result.content[0].text
        )


class TestHandleTicketBatchToolDispatcher(unittest.TestCase):
    """Tests for ToolRegistry dispatch of ticket batch tools."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.config.max_batch_size = 500
        self.registry = ToolRegistry(TICKET_BATCH_SPECS)

    def _make_registry_with_mock(self, tool_name, mock_handler):
        """Build a ToolRegistry with a single mock-handler ToolSpec."""
        tool_def = next(
            s.tool
            for s in TICKET_BATCH_SPECS
            if s.tool.name == tool_name
        )
        spec = ToolSpec(
            tool=tool_def,
            permissions=frozenset(),
            handler=mock_handler,
        )
        return ToolRegistry([spec])

    def test_routes_to_batch_create(self):
        """Registry routes ticket_batch_create to _handle_batch_create."""
        mock_handler = AsyncMock(
            return_value=types.CallToolResult(
                content=[
                    types.TextContent(type="text", text="batch created")
                ]
            )
        )
        registry = self._make_registry_with_mock(
            "ticket_batch_create", mock_handler
        )

        result = asyncio.run(
            registry.call_tool(
                "ticket_batch_create",
                {"tickets": []},
                self.mock_client,
            )
        )

        mock_handler.assert_awaited_once_with(
            self.mock_client, {"tickets": []}
        )
        self.assertEqual(result.content[0].text, "batch created")

    def test_routes_to_batch_delete(self):
        """Registry routes ticket_batch_delete to _handle_batch_delete."""
        mock_handler = AsyncMock(
            return_value=types.CallToolResult(
                content=[
                    types.TextContent(type="text", text="batch deleted")
                ]
            )
        )
        registry = self._make_registry_with_mock(
            "ticket_batch_delete", mock_handler
        )

        result = asyncio.run(
            registry.call_tool(
                "ticket_batch_delete",
                {"ticket_ids": [1]},
                self.mock_client,
            )
        )

        mock_handler.assert_awaited_once_with(
            self.mock_client, {"ticket_ids": [1]}
        )
        self.assertEqual(result.content[0].text, "batch deleted")

    def test_routes_to_batch_update(self):
        """Registry routes ticket_batch_update to _handle_batch_update."""
        mock_handler = AsyncMock(
            return_value=types.CallToolResult(
                content=[
                    types.TextContent(type="text", text="batch updated")
                ]
            )
        )
        registry = self._make_registry_with_mock(
            "ticket_batch_update", mock_handler
        )

        result = asyncio.run(
            registry.call_tool(
                "ticket_batch_update",
                {"updates": []},
                self.mock_client,
            )
        )

        mock_handler.assert_awaited_once_with(
            self.mock_client, {"updates": []}
        )
        self.assertEqual(result.content[0].text, "batch updated")

    def test_unknown_batch_tool(self):
        """Unknown batch tool name raises ValueError."""
        with self.assertRaises(ValueError, msg="Unknown tool"):
            asyncio.run(
                self.registry.call_tool(
                    "ticket_batch_unknown", {}, self.mock_client
                )
            )

    def test_none_arguments_defaults_to_empty_dict(self):
        """Passing None arguments is converted to empty dict."""
        mock_handler = AsyncMock(
            return_value=types.CallToolResult(
                content=[types.TextContent(type="text", text="result")]
            )
        )
        registry = self._make_registry_with_mock(
            "ticket_batch_create", mock_handler
        )

        asyncio.run(
            registry.call_tool(
                "ticket_batch_create", None, self.mock_client
            )
        )

        mock_handler.assert_awaited_once_with(self.mock_client, {})
