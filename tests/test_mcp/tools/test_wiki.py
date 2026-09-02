"""
Tests for wiki MCP tool handlers.

These tests verify wiki tool definitions, cursor encoding/decoding,
and handler behavior with mocked TracClient.
"""

import asyncio
import base64
import json
import unittest
import xmlrpc.client
from datetime import datetime
from unittest.mock import MagicMock, patch

from trac_mcp_server.config import Config
from trac_mcp_server.mcp.tools import WIKI_TOOLS
from trac_mcp_server.mcp.tools.errors import format_timestamp
from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.mcp.tools.wiki_read import (
    _handle_get,
    _handle_get_history,
    _handle_recent_changes,
    _handle_search,
    decode_cursor,
    encode_cursor,
)
from trac_mcp_server.mcp.tools.wiki_write import (
    WIKI_WRITE_SPECS,
    _handle_create,
    _handle_delete,
    _handle_update,
)


class TestCursorEncoding(unittest.TestCase):
    """Test cursor encoding and decoding."""

    def test_encode_cursor(self):
        """Test encode_cursor creates valid base64."""
        cursor = encode_cursor(10, 100)

        # Should be valid base64
        self.assertIsInstance(cursor, str)

        # Should be decodable
        decoded = base64.b64decode(cursor.encode("utf-8"))
        data = json.loads(decoded.decode("utf-8"))

        self.assertEqual(data["offset"], 10)
        self.assertEqual(data["total"], 100)

    def test_decode_cursor(self):
        """Test decode_cursor returns correct offset and total."""
        cursor = encode_cursor(20, 150)
        offset, total = decode_cursor(cursor)

        self.assertEqual(offset, 20)
        self.assertEqual(total, 150)

    def test_decode_cursor_invalid(self):
        """Test decode_cursor raises ValueError on invalid input."""
        with self.assertRaises(ValueError):
            decode_cursor("not-valid-base64")

        with self.assertRaises(ValueError):
            # Valid base64 but not valid JSON
            invalid = base64.b64encode(b"not json").decode("utf-8")
            decode_cursor(invalid)

        with self.assertRaises(ValueError):
            # Valid JSON but missing required keys
            invalid_json = json.dumps({"foo": "bar"})
            invalid = base64.b64encode(
                invalid_json.encode("utf-8")
            ).decode("utf-8")
            decode_cursor(invalid)


class TestWikiTools(unittest.TestCase):
    """Test WIKI_TOOLS definitions."""

    def test_eleven_tools_defined(self):
        """Test WIKI_TOOLS contains exactly 11 tools.

        4 read (wiki_get, wiki_search, wiki_recent_changes,
        wiki_get_history) + 3 write (create, update, delete) +
        4 attachment (put, get, list, delete).
        """
        self.assertEqual(len(WIKI_TOOLS), 11)

    def test_tool_names(self):
        """Test tool names are correct."""
        tool_names = [tool.name for tool in WIKI_TOOLS]

        self.assertIn("wiki_get", tool_names)
        self.assertIn("wiki_search", tool_names)
        self.assertIn("wiki_create", tool_names)
        self.assertIn("wiki_update", tool_names)
        self.assertIn("wiki_delete", tool_names)
        self.assertIn("wiki_recent_changes", tool_names)
        self.assertIn("wiki_get_history", tool_names)

    def test_wiki_get_history_schema(self):
        """Test wiki_get_history has correct required fields."""
        history_tool = next(
            t for t in WIKI_TOOLS if t.name == "wiki_get_history"
        )

        self.assertIn(
            "page_name", history_tool.inputSchema["properties"]
        )
        self.assertIn("limit", history_tool.inputSchema["properties"])
        self.assertEqual(
            history_tool.inputSchema["required"], ["page_name"]
        )

    def test_wiki_get_schema(self):
        """Test wiki_get has correct required fields."""
        get_tool = next(t for t in WIKI_TOOLS if t.name == "wiki_get")

        self.assertIn("page_name", get_tool.inputSchema["properties"])
        self.assertIn("version", get_tool.inputSchema["properties"])
        self.assertEqual(
            get_tool.inputSchema["required"], ["page_name"]
        )

    def test_wiki_search_schema(self):
        """Test wiki_search has correct required fields."""
        search_tool = next(
            t for t in WIKI_TOOLS if t.name == "wiki_search"
        )

        self.assertIn("query", search_tool.inputSchema["properties"])
        self.assertIn("prefix", search_tool.inputSchema["properties"])
        self.assertIn("limit", search_tool.inputSchema["properties"])
        self.assertIn("cursor", search_tool.inputSchema["properties"])
        self.assertEqual(search_tool.inputSchema["required"], ["query"])

    def test_wiki_create_schema(self):
        """Test wiki_create has correct required fields."""
        create_tool = next(
            t for t in WIKI_TOOLS if t.name == "wiki_create"
        )

        self.assertIn(
            "page_name", create_tool.inputSchema["properties"]
        )
        self.assertIn("content", create_tool.inputSchema["properties"])
        self.assertIn("comment", create_tool.inputSchema["properties"])
        self.assertEqual(
            create_tool.inputSchema["required"],
            ["page_name", "content"],
        )

    def test_wiki_update_schema(self):
        """Test wiki_update has correct required fields."""
        update_tool = next(
            t for t in WIKI_TOOLS if t.name == "wiki_update"
        )

        self.assertIn(
            "page_name", update_tool.inputSchema["properties"]
        )
        self.assertIn("content", update_tool.inputSchema["properties"])
        self.assertIn("version", update_tool.inputSchema["properties"])
        self.assertIn("comment", update_tool.inputSchema["properties"])
        self.assertEqual(
            update_tool.inputSchema["required"],
            ["page_name", "content", "version"],
        )

    def test_wiki_delete_schema(self):
        """Test wiki_delete has correct required fields."""
        delete_tool = next(
            t for t in WIKI_TOOLS if t.name == "wiki_delete"
        )

        self.assertIn(
            "page_name", delete_tool.inputSchema["properties"]
        )
        self.assertEqual(
            delete_tool.inputSchema["required"], ["page_name"]
        )
        # Verify description mentions warning about irreversibility
        self.assertIsNotNone(delete_tool.description)
        self.assertIn("cannot be undone", delete_tool.description or "")
        self.assertIn("WIKI_DELETE", delete_tool.description or "")


class TestFormatTimestamp(unittest.TestCase):
    """Test format_timestamp helper."""

    def test_datetime_input(self):
        """Test formatting datetime objects."""
        dt = datetime(2026, 2, 1, 14, 30, 0)
        result = format_timestamp(dt)

        self.assertEqual(result, "2026-02-01 14:30")

    def test_int_timestamp_input(self):
        """Test formatting integer timestamps."""
        # 2026-02-01 14:30:00 UTC
        timestamp = 1769982600
        result = format_timestamp(timestamp)

        # Should be formatted (local time may differ)
        self.assertIn("2026-02-01", result)

    def test_float_timestamp_input(self):
        """Test formatting float timestamps."""
        timestamp = 1769982600.5
        result = format_timestamp(timestamp)

        # Should be formatted
        self.assertIn("2026-02-01", result)

    def test_string_passthrough(self):
        """Test string values pass through."""
        result = format_timestamp("2026-02-01")

        self.assertEqual(result, "2026-02-01")


class TestHandleGet(unittest.TestCase):
    """Test _handle_get handler."""

    def setUp(self):
        """Set up mock client."""
        self.mock_client = MagicMock()

    def test_handle_get_success(self):
        """Test _handle_get formats response correctly."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync_limited"
        ) as mock_run_sync_limited:
            # Set up run_sync_limited to return values directly (not coroutines)
            mock_run_sync_limited.side_effect = [
                "= Test Page =\n\nTest content.",  # get_wiki_page result
                {  # get_wiki_page_info result
                    "name": "TestPage",
                    "version": 5,
                    "author": "alice",
                    "lastModified": datetime(2026, 2, 1, 14, 0, 0),
                },
            ]

            # Call handler
            result = asyncio.run(
                _handle_get(self.mock_client, {"page_name": "TestPage"})
            )

            # Verify response - now returns CallToolResult
            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            self.assertEqual(len(result.content), 1)
            text = result.content[0].text

            self.assertIn("# TestPage", text)
            self.assertIn("Version: 5", text)
            self.assertIn("Author: alice", text)
            self.assertIn("2026-02-01 14:00", text)
            # Verify structured content
            self.assertIsNotNone(result.structuredContent)
            self.assertEqual(
                result.structuredContent["name"], "TestPage"
            )
            self.assertEqual(result.structuredContent["version"], 5)

    def test_handle_get_missing_page_name(self):
        """Test _handle_get returns error when page_name is missing."""
        result = asyncio.run(_handle_get(self.mock_client, {}))

        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        text = result.content[0].text

        self.assertIn("Error (validation_error)", text)
        self.assertIn("page_name is required", text)


class TestHandleSearch(unittest.TestCase):
    """Test _handle_search handler."""

    def setUp(self):
        """Set up mock client."""
        self.mock_client = MagicMock()

    def test_handle_search_success(self):
        """Test _handle_search returns paginated results."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync"
        ) as mock_run_sync:
            # Return search results directly
            mock_run_sync.return_value = [
                {
                    "name": "PageOne",
                    "snippet": "matched text in page one",
                },
                {
                    "name": "PageTwo",
                    "snippet": "matched text in page two",
                },
                {
                    "name": "PageThree",
                    "snippet": "matched text in page three",
                },
            ]

            # Call handler with limit=2
            result = asyncio.run(
                _handle_search(
                    self.mock_client, {"query": "test", "limit": 2}
                )
            )

            # Verify response
            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            self.assertEqual(len(result.content), 1)
            text = result.content[0].text

            self.assertIn("Found 3 wiki pages", text)
            self.assertIn("**PageOne**", text)
            self.assertIn("**PageTwo**", text)
            self.assertNotIn("**PageThree**", text)
            self.assertIn("cursor", text)  # Should have next cursor

    def test_handle_search_with_prefix(self):
        """Test _handle_search filters by prefix."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync"
        ) as mock_run_sync:
            # Return search results directly
            mock_run_sync.return_value = [
                {"name": "User/Alice", "snippet": "alice page"},
                {"name": "User/Bob", "snippet": "bob page"},
                {"name": "System/Config", "snippet": "config page"},
            ]

            # Call handler with prefix filter
            result = asyncio.run(
                _handle_search(
                    self.mock_client,
                    {"query": "test", "prefix": "User/"},
                )
            )

            # Verify response
            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            self.assertEqual(len(result.content), 1)
            text = result.content[0].text

            self.assertIn("Found 2 wiki pages", text)
            self.assertIn("**User/Alice**", text)
            self.assertIn("**User/Bob**", text)
            self.assertNotIn("**System/Config**", text)

    def test_handle_search_missing_query(self):
        """Test _handle_search returns error when query is missing."""
        result = asyncio.run(_handle_search(self.mock_client, {}))

        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        text = result.content[0].text

        self.assertIn("Error (validation_error)", text)
        self.assertIn("query is required", text)


class TestHandleRecentChanges(unittest.TestCase):
    """Test _handle_recent_changes handler."""

    def setUp(self):
        """Set up mock client."""
        self.mock_client = MagicMock()

    def test_recent_changes_success(self):
        """Test _handle_recent_changes returns formatted page list."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                {
                    "name": "PageA",
                    "author": "alice",
                    "lastModified": 1707900000,
                    "version": 3,
                },
                {
                    "name": "PageB",
                    "author": "bob",
                    "lastModified": 1707800000,
                    "version": 1,
                },
            ]

            result = asyncio.run(
                _handle_recent_changes(
                    self.mock_client, {"since_days": 30}
                )
            )

            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            text = result.content[0].text

            self.assertIn("PageA", text)
            self.assertIn("PageB", text)
            self.assertIn("alice", text)
            self.assertIn("bob", text)
            self.assertIn("30 days", text)

            # Verify structured content
            self.assertIsNotNone(result.structuredContent)
            self.assertEqual(len(result.structuredContent["pages"]), 2)
            self.assertEqual(result.structuredContent["since_days"], 30)

    def test_recent_changes_default_days(self):
        """Test _handle_recent_changes uses default since_days=30 when not provided."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                {
                    "name": "RecentPage",
                    "author": "charlie",
                    "lastModified": 1707900000,
                    "version": 2,
                },
            ]

            result = asyncio.run(
                _handle_recent_changes(self.mock_client, {})
            )

            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            text = result.content[0].text

            # Default is 30 days
            self.assertIn("30 days", text)
            self.assertEqual(result.structuredContent["since_days"], 30)

            # Verify run_sync was called (client.get_recent_wiki_changes)
            mock_run_sync.assert_called_once()

    def test_recent_changes_empty(self):
        """Test _handle_recent_changes with no results."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = []

            result = asyncio.run(
                _handle_recent_changes(
                    self.mock_client, {"since_days": 7}
                )
            )

            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            text = result.content[0].text

            self.assertIn("No wiki pages modified", text)
            self.assertIn("7 days", text)
            self.assertEqual(result.structuredContent["pages"], [])

    def test_recent_changes_with_limit(self):
        """Test _handle_recent_changes respects limit parameter."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync"
        ) as mock_run_sync:
            # Return more results than the limit
            mock_run_sync.return_value = [
                {
                    "name": f"Page{i}",
                    "author": "alice",
                    "lastModified": 1707900000 - i * 1000,
                    "version": 1,
                }
                for i in range(10)
            ]

            result = asyncio.run(
                _handle_recent_changes(self.mock_client, {"limit": 3})
            )

            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            text = result.content[0].text

            # Should show "showing 3 of 10"
            self.assertIn("showing 3 of 10", text)
            # Structured content should only have 3 pages
            self.assertEqual(len(result.structuredContent["pages"]), 3)

    def test_recent_changes_xmlrpc_datetime(self):
        """Test _handle_recent_changes handles xmlrpc.client.DateTime timestamps."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync"
        ) as mock_run_sync:
            # Use xmlrpc.client.DateTime like the real server returns
            xml_dt = xmlrpc.client.DateTime("20260201T14:00:00")
            mock_run_sync.return_value = [
                {
                    "name": "XmlRpcPage",
                    "author": "admin",
                    "lastModified": xml_dt,
                    "version": 5,
                },
            ]

            result = asyncio.run(
                _handle_recent_changes(
                    self.mock_client, {"since_days": 30}
                )
            )

            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            text = result.content[0].text

            self.assertIn("XmlRpcPage", text)
            self.assertIn("admin", text)
            # Should have formatted the DateTime properly
            self.assertIn("2026-02-01", text)


class TestHandleGetHistory(unittest.TestCase):
    """Test _handle_get_history handler."""

    def setUp(self):
        """Set up mock client."""
        self.mock_client = MagicMock()

    def _build_info_responses(self, current_version: int) -> list:
        """Build fake get_wiki_page_info responses.

        Returns (current_info, v5_info, v4_info, v3_info, v2_info, v1_info).
        The first call is the unversioned lookup, then each per-version call.
        """
        current = {
            "name": "WikiStart",
            "version": current_version,
            "author": f"alice{current_version}",
            "lastModified": datetime(2026, 3, 1, 14, 0, 0),
            "comment": f"revision {current_version} comment",
        }
        versioned = [
            {
                "name": "WikiStart",
                "version": v,
                "author": f"alice{v}",
                "lastModified": datetime(2026, 3, 1, 14, 0, 0),
                "comment": f"revision {v} comment",
            }
            for v in range(current_version, 0, -1)
        ]
        return [current] + versioned

    def test_handle_get_history_success(self):
        """_handle_get_history returns revisions newest-first."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync_limited"
        ) as mock_run_sync_limited:
            mock_run_sync_limited.side_effect = (
                self._build_info_responses(5)
            )

            result = asyncio.run(
                _handle_get_history(
                    self.mock_client, {"page_name": "WikiStart"}
                )
            )

            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            self.assertFalse(getattr(result, "isError", False))

            structured = result.structuredContent
            self.assertIsNotNone(structured)
            self.assertEqual(structured["page_name"], "WikiStart")

            revisions = structured["revisions"]
            self.assertEqual(len(revisions), 5)
            # Newest first
            self.assertEqual(revisions[0]["version"], 5)
            self.assertEqual(revisions[-1]["version"], 1)
            # Comment preserved
            self.assertEqual(
                revisions[0]["comment"], "revision 5 comment"
            )
            self.assertEqual(
                revisions[-1]["comment"], "revision 1 comment"
            )
            # Text content has header
            text = result.content[0].text
            self.assertIn("# WikiStart history", text)
            self.assertIn("v5 by alice5", text)
            self.assertIn("revision 5 comment", text)

    def test_handle_get_history_with_limit(self):
        """_handle_get_history respects limit parameter."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync_limited"
        ) as mock_run_sync_limited:
            # Current version lookup + 3 versioned calls (limit=3)
            responses = self._build_info_responses(5)
            # Trim to current + first 3 versions only
            mock_run_sync_limited.side_effect = responses[:4]

            result = asyncio.run(
                _handle_get_history(
                    self.mock_client,
                    {"page_name": "WikiStart", "limit": 3},
                )
            )

            revisions = result.structuredContent["revisions"]
            self.assertEqual(len(revisions), 3)
            self.assertEqual(
                [r["version"] for r in revisions], [5, 4, 3]
            )

    def test_handle_get_history_preserves_comment_field(self):
        """_handle_get_history preserves comment field in structuredContent."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync_limited"
        ) as mock_run_sync_limited:
            mock_run_sync_limited.side_effect = [
                {
                    "name": "WikiStart",
                    "version": 2,
                    "author": "alice",
                    "lastModified": datetime(2026, 3, 1, 14, 0, 0),
                    "comment": "initial",
                },
                {
                    "name": "WikiStart",
                    "version": 2,
                    "author": "bob",
                    "lastModified": datetime(2026, 3, 1, 15, 0, 0),
                    "comment": "auto-pm: ticket #42",
                },
                {
                    "name": "WikiStart",
                    "version": 1,
                    "author": "alice",
                    "lastModified": datetime(2026, 3, 1, 14, 0, 0),
                    "comment": "first edit",
                },
            ]

            result = asyncio.run(
                _handle_get_history(
                    self.mock_client, {"page_name": "WikiStart"}
                )
            )

            revisions = result.structuredContent["revisions"]
            self.assertEqual(len(revisions), 2)
            # Attribution marker preserved exactly
            self.assertEqual(
                revisions[0]["comment"], "auto-pm: ticket #42"
            )
            self.assertEqual(revisions[1]["comment"], "first edit")

    def test_handle_get_history_missing_page_name(self):
        """_handle_get_history returns validation error when page_name missing."""
        result = asyncio.run(_handle_get_history(self.mock_client, {}))

        self.assertTrue(result.isError)
        text = result.content[0].text
        self.assertIn("validation_error", text)
        self.assertIn("page_name is required", text)

    def test_handle_get_history_empty_comment_coerced_to_string(self):
        """_handle_get_history returns empty string for missing/None comment."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync_limited"
        ) as mock_run_sync_limited:
            mock_run_sync_limited.side_effect = [
                {
                    "name": "WikiStart",
                    "version": 1,
                    "author": "alice",
                    "lastModified": datetime(2026, 3, 1, 14, 0, 0),
                    # No comment field at all (old Trac XmlRpcPlugin)
                },
                {
                    "name": "WikiStart",
                    "version": 1,
                    "author": "alice",
                    "lastModified": datetime(2026, 3, 1, 14, 0, 0),
                    "comment": None,
                },
            ]

            result = asyncio.run(
                _handle_get_history(
                    self.mock_client, {"page_name": "WikiStart"}
                )
            )

            revisions = result.structuredContent["revisions"]
            self.assertEqual(len(revisions), 1)
            self.assertEqual(revisions[0]["comment"], "")


class TestHandleCreate(unittest.TestCase):
    """Test _handle_create handler."""

    def setUp(self):
        """Set up mock client and config."""
        self.mock_client = MagicMock()
        self.mock_config = Config(
            trac_url="http://test", username="test", password="test"
        )
        self.mock_client.config = self.mock_config

    def test_handle_create_success(self):
        """Test _handle_create creates the page."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            # Set up side effect - first call raises not found, second returns success
            call_count = [0]

            def side_effect_func(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call is get_wiki_page_info (check if exists)
                    raise xmlrpc.client.Fault(404, "Page not found")
                else:
                    # Second call is put_wiki_page (create)
                    return {
                        "name": "NewPage",
                        "version": 1,
                        "author": "alice",
                    }

            mock_run_sync.side_effect = side_effect_func

            # Call handler
            result = asyncio.run(
                _handle_create(
                    self.mock_client,
                    {"page_name": "NewPage", "content": "# New Page"},
                )
            )

            # Verify response
            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            self.assertEqual(len(result.content), 1)
            text = result.content[0].text

            self.assertIn("Created wiki page 'NewPage'", text)
            self.assertIn("version 1", text)

    def test_handle_create_already_exists(self):
        """Test _handle_create detects existing page."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            # Return page info (page exists)
            mock_run_sync.return_value = {
                "name": "ExistingPage",
                "version": 3,
            }

            # Call handler
            result = asyncio.run(
                _handle_create(
                    self.mock_client,
                    {"page_name": "ExistingPage", "content": "content"},
                )
            )

            # Verify response
            self.assertTrue(result.isError)
            self.assertEqual(len(result.content), 1)
            text = result.content[0].text

            self.assertIn("Error (already_exists)", text)
            self.assertIn("already exists", text)
            self.assertIn("wiki_update", text)


class TestHandleUpdate(unittest.TestCase):
    """Test _handle_update handler."""

    def setUp(self):
        """Set up mock client and config."""
        self.mock_client = MagicMock()
        self.mock_config = Config(
            trac_url="http://test", username="test", password="test"
        )
        self.mock_client.config = self.mock_config

    def test_handle_update_success(self):
        """Test _handle_update updates page successfully."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            # Set up mocks
            mock_run_sync.return_value = {
                "name": "TestPage",
                "version": 6,
                "author": "alice",
            }

            # Call handler
            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "page_name": "TestPage",
                        "content": "# Updated",
                        "version": 5,
                    },
                )
            )

            # Verify response
            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            self.assertEqual(len(result.content), 1)
            text = result.content[0].text

            self.assertIn("Updated wiki page 'TestPage'", text)
            self.assertIn("version 6", text)

    def test_handle_update_version_conflict(self):
        """Test _handle_update handles version conflict."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            # Set up side effect - first call raises conflict, second returns current version
            call_count = [0]

            def side_effect_func(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call is put_wiki_page
                    raise xmlrpc.client.Fault(
                        409, "Version conflict - page not modified"
                    )
                else:
                    # Second call is get_wiki_page_info
                    return {"name": "TestPage", "version": 7}

            mock_run_sync.side_effect = side_effect_func

            # Call handler
            result = asyncio.run(
                _handle_update(
                    self.mock_client,
                    {
                        "page_name": "TestPage",
                        "content": "# Updated",
                        "version": 5,
                    },
                )
            )

            # Verify response
            self.assertTrue(result.isError)
            self.assertEqual(len(result.content), 1)
            text = result.content[0].text

            self.assertIn("Error (version_conflict)", text)
            self.assertIn("Current version is 7", text)
            self.assertIn("you tried to update version 5", text)
            self.assertIn("version=7", text)


class TestHandleDelete(unittest.TestCase):
    """Test _handle_delete handler."""

    def setUp(self):
        """Set up mock client."""
        self.mock_client = MagicMock()

    def test_handle_delete_success(self):
        """Test _handle_delete deletes page successfully."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            # Return page content for existence check, True for deletion
            mock_run_sync.return_value = True

            # Call handler
            result = asyncio.run(
                _handle_delete(
                    self.mock_client, {"page_name": "TestPage"}
                )
            )

            # Verify response
            from mcp.types import CallToolResult

            self.assertIsInstance(result, CallToolResult)
            self.assertEqual(len(result.content), 1)
            text = result.content[0].text

            self.assertIn("Deleted wiki page 'TestPage'", text)

            # Verify get_wiki_page (existence check) and delete_wiki_page were called
            self.assertEqual(mock_run_sync.call_count, 2)
            # First call: existence check with get_wiki_page
            first_call_args = mock_run_sync.call_args_list[0][0]
            self.assertEqual(first_call_args[1], "TestPage")
            # Second call: delete_wiki_page
            second_call_args = mock_run_sync.call_args_list[1][0]
            self.assertEqual(second_call_args[1], "TestPage")

    def test_handle_delete_missing_page_name(self):
        """Test _handle_delete returns error when page_name is missing."""
        result = asyncio.run(_handle_delete(self.mock_client, {}))

        self.assertTrue(result.isError)
        self.assertEqual(len(result.content), 1)
        text = result.content[0].text

        self.assertIn("Error (validation_error)", text)
        self.assertIn("page_name is required", text)

    def test_handle_delete_page_not_found(self):
        """Test _handle_delete handles page not found error."""
        config = Config(
            trac_url="http://test", username="test", password="test"
        )
        mock_client = MagicMock()
        mock_client.config = config

        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            # Raise not found error
            mock_run_sync.side_effect = xmlrpc.client.Fault(
                404, "Page not found"
            )

            # Call through ToolRegistry to test error translation
            registry = ToolRegistry(WIKI_WRITE_SPECS)
            result = asyncio.run(
                registry.call_tool(
                    "wiki_delete",
                    {"page_name": "NonExistentPage"},
                    mock_client,
                )
            )

            # Verify error response
            self.assertTrue(result.isError)
            self.assertEqual(len(result.content), 1)
            text = result.content[0].text

            self.assertIn("Error (not_found)", text)
            # Error message may say "not found" or "does not exist"
            self.assertTrue(
                "not found" in text.lower()
                or "does not exist" in text.lower()
            )

    def test_handle_delete_permission_denied(self):
        """Test _handle_delete handles permission denied error."""
        config = Config(
            trac_url="http://test", username="test", password="test"
        )
        mock_client = MagicMock()
        mock_client.config = config

        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            # Raise permission denied error
            mock_run_sync.side_effect = xmlrpc.client.Fault(
                403, "Permission denied"
            )

            # Call through ToolRegistry to test error translation
            registry = ToolRegistry(WIKI_WRITE_SPECS)
            result = asyncio.run(
                registry.call_tool(
                    "wiki_delete",
                    {"page_name": "ProtectedPage"},
                    mock_client,
                )
            )

            # Verify error response
            self.assertTrue(result.isError)
            self.assertEqual(len(result.content), 1)
            text = result.content[0].text

            self.assertIn("Error (permission_denied)", text)
            self.assertIn("permission", text.lower())


class TestHandleWikiTool(unittest.TestCase):
    """Test wiki tool dispatch via ToolRegistry."""

    def test_unknown_tool(self):
        """Test ToolRegistry raises ValueError for unknown tool."""
        mock_client = MagicMock()

        registry = ToolRegistry(WIKI_WRITE_SPECS)

        # ToolRegistry raises ValueError for unregistered tool names
        with self.assertRaises(ValueError):
            asyncio.run(
                registry.call_tool("wiki_unknown", {}, mock_client)
            )


if __name__ == "__main__":
    unittest.main()
