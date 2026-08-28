"""Tests for ToolSpec, ToolRegistry, and load_permissions_file.

Covers:
- ToolSpec creation, immutability, and hashable permissions
- ToolRegistry filtering (no filter, permission filter, empty permissions)
- ToolRegistry list_tools, tool_count, call_tool
- load_permissions_file parsing, validation, and error cases
"""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import mcp.types as types

from trac_mcp_server.mcp.server import PING_SPEC
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import (
    ToolRegistry,
    ToolSpec,
    load_permissions_file,
)


def _make_spec(
    name: str,
    permissions: frozenset[str] | None = None,
    handler=None,
    read_only_hint: bool | None = None,
) -> ToolSpec:
    """Helper to create a ToolSpec for testing.

    read_only_hint mirrors Tool.annotations.readOnlyHint. Left as None
    (the default) produces a spec with no annotations at all -- exercises
    the "missing signal" case, distinct from an explicit readOnlyHint=False.
    """
    if permissions is None:
        permissions = frozenset()
    if handler is None:

        async def handler(client, args):
            return types.CallToolResult(
                content=[
                    types.TextContent(type="text", text=f"ok:{name}")
                ]
            )

    annotations = (
        types.ToolAnnotations(readOnlyHint=read_only_hint)
        if read_only_hint is not None
        else None
    )

    return ToolSpec(
        tool=types.Tool(
            name=name,
            description=f"Test tool {name}",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            annotations=annotations,
        ),
        permissions=permissions,
        handler=handler,
    )


class TestToolSpec(unittest.TestCase):
    """Test ToolSpec dataclass."""

    def test_creation(self):
        """ToolSpec can be created with required fields."""
        spec = _make_spec("test_tool", frozenset({"TICKET_VIEW"}))
        self.assertEqual(spec.tool.name, "test_tool")
        self.assertEqual(spec.permissions, frozenset({"TICKET_VIEW"}))
        self.assertIsNotNone(spec.handler)

    def test_frozen(self):
        """ToolSpec is immutable (frozen dataclass)."""
        spec = _make_spec("test_tool")
        with self.assertRaises(AttributeError):
            spec.permissions = frozenset({"NEW"})

    def test_empty_permissions(self):
        """ToolSpec with empty frozenset means no permission required."""
        spec = _make_spec("always_available", frozenset())
        self.assertEqual(spec.permissions, frozenset())
        self.assertEqual(len(spec.permissions), 0)


class TestToolRegistry(unittest.TestCase):
    """Test ToolRegistry class."""

    def setUp(self):
        self.specs = [
            _make_spec("ping", frozenset()),
            _make_spec("get_server_time", frozenset()),
            _make_spec("ticket_search", frozenset({"TICKET_VIEW"})),
            _make_spec("ticket_create", frozenset({"TICKET_CREATE"})),
            _make_spec(
                "ticket_batch_create",
                frozenset({"TICKET_CREATE", "TICKET_BATCH_MODIFY"}),
            ),
            _make_spec("wiki_get", frozenset({"WIKI_VIEW"})),
            _make_spec("wiki_create", frozenset({"WIKI_CREATE"})),
            _make_spec("milestone_list", frozenset({"MILESTONE_VIEW"})),
            _make_spec("detect_format", frozenset()),
        ]

    def test_no_filter_all_tools_registered(self):
        """With None permissions, all specs are included."""
        registry = ToolRegistry(self.specs)
        self.assertEqual(registry.tool_count(), 9)

    def test_filter_by_permissions(self):
        """Only tools with matching or empty permissions are included."""
        registry = ToolRegistry(
            self.specs, frozenset({"TICKET_VIEW", "WIKI_VIEW"})
        )
        names = [t.name for t in registry.list_tools()]
        # Permission-free tools always included
        self.assertIn("ping", names)
        self.assertIn("get_server_time", names)
        self.assertIn("detect_format", names)
        # Granted permissions
        self.assertIn("ticket_search", names)
        self.assertIn("wiki_get", names)
        # Not granted
        self.assertNotIn("ticket_create", names)
        self.assertNotIn("wiki_create", names)
        self.assertNotIn("milestone_list", names)
        # Multi-permission: TICKET_CREATE not in allowed, so excluded
        self.assertNotIn("ticket_batch_create", names)

    def test_empty_permissions_always_included(self):
        """Specs with empty permissions pass any filter."""
        registry = ToolRegistry(self.specs, frozenset({"TICKET_VIEW"}))
        names = [t.name for t in registry.list_tools()]
        self.assertIn("ping", names)
        self.assertIn("get_server_time", names)
        self.assertIn("detect_format", names)

    def test_subset_check(self):
        """Multi-permission spec included only when ALL permissions are in allowed set."""
        # Only TICKET_CREATE granted, but TICKET_BATCH_MODIFY also needed
        registry = ToolRegistry(
            self.specs, frozenset({"TICKET_CREATE"})
        )
        names = [t.name for t in registry.list_tools()]
        self.assertNotIn("ticket_batch_create", names)
        self.assertIn("ticket_create", names)

        # Both granted
        registry2 = ToolRegistry(
            self.specs,
            frozenset({"TICKET_CREATE", "TICKET_BATCH_MODIFY"}),
        )
        names2 = [t.name for t in registry2.list_tools()]
        self.assertIn("ticket_batch_create", names2)

    def test_list_tools_returns_tool_objects(self):
        """list_tools() returns list of types.Tool."""
        registry = ToolRegistry(self.specs)
        tools = registry.list_tools()
        self.assertIsInstance(tools, list)
        for tool in tools:
            self.assertIsInstance(tool, types.Tool)

    def test_call_tool_dispatches_to_handler(self):
        """call_tool() invokes the spec's handler with (client, args)."""
        calls = []

        async def mock_handler(client, args):
            calls.append((client, args))
            return types.CallToolResult(
                content=[
                    types.TextContent(type="text", text="dispatched")
                ]
            )

        spec = _make_spec("test_dispatch", frozenset(), mock_handler)
        registry = ToolRegistry([spec])
        mock_client = MagicMock()

        result = asyncio.run(
            registry.call_tool(
                "test_dispatch", {"key": "val"}, mock_client
            )
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], mock_client)
        self.assertEqual(calls[0][1], {"key": "val"})
        self.assertEqual(result.content[0].text, "dispatched")

    def test_call_tool_none_arguments(self):
        """call_tool() converts None arguments to empty dict."""
        calls = []

        async def mock_handler(client, args):
            calls.append(args)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="ok")]
            )

        spec = _make_spec("test_none_args", frozenset(), mock_handler)
        registry = ToolRegistry([spec])

        asyncio.run(
            registry.call_tool("test_none_args", None, MagicMock())
        )

        self.assertEqual(calls[0], {})

    def test_call_tool_normalizes_page_alias(self):
        """call_tool() fills page_name from page for wiki_* tools."""
        calls = []

        async def mock_handler(client, args):
            calls.append(args)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="ok")]
            )

        spec = _make_spec("wiki_get", frozenset(), mock_handler)
        registry = ToolRegistry([spec])

        asyncio.run(
            registry.call_tool(
                "wiki_get", {"page": "Index"}, MagicMock()
            )
        )

        self.assertEqual(
            calls[0], {"page": "Index", "page_name": "Index"}
        )

    def test_call_tool_alias_does_not_override_canonical(self):
        """An explicit page_name always wins over the page alias."""
        calls = []

        async def mock_handler(client, args):
            calls.append(args)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="ok")]
            )

        spec = _make_spec("wiki_get", frozenset(), mock_handler)
        registry = ToolRegistry([spec])

        asyncio.run(
            registry.call_tool(
                "wiki_get",
                {"page": "Wrong", "page_name": "Right"},
                MagicMock(),
            )
        )

        self.assertEqual(calls[0]["page_name"], "Right")

    def test_call_tool_alias_not_applied_outside_wiki(self):
        """The page alias is scoped to wiki_* tools only."""
        calls = []

        async def mock_handler(client, args):
            calls.append(args)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="ok")]
            )

        spec = _make_spec("ticket_search", frozenset(), mock_handler)
        registry = ToolRegistry([spec])

        asyncio.run(
            registry.call_tool(
                "ticket_search", {"page": "Index"}, MagicMock()
            )
        )

        self.assertNotIn("page_name", calls[0])

    def test_call_tool_unknown_raises(self):
        """Calling unknown tool raises ValueError."""
        registry = ToolRegistry(self.specs)
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(
                registry.call_tool("nonexistent", {}, MagicMock())
            )
        self.assertIn("Unknown tool", str(ctx.exception))

    def test_call_tool_filtered_out_raises(self):
        """Tool filtered by permissions raises ValueError when called."""
        registry = ToolRegistry(self.specs, frozenset({"TICKET_VIEW"}))
        # ticket_create requires TICKET_CREATE, not granted
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(
                registry.call_tool("ticket_create", {}, MagicMock())
            )
        self.assertIn("Unknown tool", str(ctx.exception))


class TestToolRegistryReadOnlyFilter(unittest.TestCase):
    """Test ToolRegistry's read_only filter -- orthogonal to and
    combinable with the permissions filter above."""

    def setUp(self):
        self.specs = [
            _make_spec("ticket_search", read_only_hint=True),
            _make_spec("ticket_create", read_only_hint=False),
            _make_spec("wiki_get", read_only_hint=True),
            _make_spec("wiki_delete", read_only_hint=False),
            _make_spec("no_annotations_at_all"),  # read_only_hint=None
        ]

    def test_read_only_false_includes_everything(self):
        """Default (read_only=False) is unaffected -- pure regression
        guard against the new parameter changing existing behavior."""
        registry = ToolRegistry(self.specs)
        self.assertEqual(registry.tool_count(), 5)

    def test_read_only_true_excludes_write_tools(self):
        registry = ToolRegistry(self.specs, read_only=True)
        names = [t.name for t in registry.list_tools()]
        self.assertIn("ticket_search", names)
        self.assertIn("wiki_get", names)
        self.assertNotIn("ticket_create", names)
        self.assertNotIn("wiki_delete", names)

    def test_read_only_true_excludes_tools_with_no_annotations(self):
        """Missing readOnlyHint is treated as NOT read-only -- the safe
        default when the signal is absent is to exclude, never to
        accidentally admit a write tool through a gap in annotations."""
        registry = ToolRegistry(self.specs, read_only=True)
        names = [t.name for t in registry.list_tools()]
        self.assertNotIn("no_annotations_at_all", names)

    def test_read_only_combines_with_permissions_filter(self):
        """A tool must pass BOTH filters when both are active."""
        specs = [
            _make_spec(
                "ticket_search",
                frozenset({"TICKET_VIEW"}),
                read_only_hint=True,
            ),
            _make_spec(
                "wiki_get",
                frozenset({"WIKI_VIEW"}),
                read_only_hint=True,
            ),
        ]
        # read-only, but WIKI_VIEW not granted -> wiki_get still excluded
        registry = ToolRegistry(
            specs, frozenset({"TICKET_VIEW"}), read_only=True
        )
        names = [t.name for t in registry.list_tools()]
        self.assertIn("ticket_search", names)
        self.assertNotIn("wiki_get", names)

    def test_call_tool_read_only_filtered_out_raises(self):
        """A write tool excluded by read_only=True can't be dispatched by
        name either -- same "unknown tool" path as permission filtering,
        so there's no separate bypass to worry about."""
        registry = ToolRegistry(self.specs, read_only=True)
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(
                registry.call_tool("ticket_create", {}, MagicMock())
            )
        self.assertIn("Unknown tool", str(ctx.exception))


class TestLoadPermissionsFile(unittest.TestCase):
    """Test load_permissions_file function."""

    def test_load_valid_file(self, tmp_path=None):
        """Loads valid permissions file with comments and blanks."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".permissions", delete=False
        ) as f:
            f.write("# Read-only permissions\n")
            f.write("TICKET_VIEW\n")
            f.write("\n")
            f.write("WIKI_VIEW\n")
            f.write("# Another comment\n")
            f.write("MILESTONE_VIEW\n")
            path = f.name

        try:
            result = load_permissions_file(path)
            self.assertEqual(
                result,
                frozenset(
                    {"TICKET_VIEW", "WIKI_VIEW", "MILESTONE_VIEW"}
                ),
            )
        finally:
            Path(path).unlink()

    def test_load_file_not_found(self):
        """FileNotFoundError for nonexistent file."""
        with self.assertRaises(FileNotFoundError):
            load_permissions_file("/nonexistent/path.permissions")

    def test_load_empty_file(self):
        """ValueError for file with no permissions."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".permissions", delete=False
        ) as f:
            f.write("# Only comments\n")
            f.write("\n")
            path = f.name

        try:
            with self.assertRaises(ValueError) as ctx:
                load_permissions_file(path)
            self.assertIn("No permissions found", str(ctx.exception))
        finally:
            Path(path).unlink()

    def test_load_invalid_permission(self):
        """ValueError for lowercase or invalid format."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".permissions", delete=False
        ) as f:
            f.write("TICKET_VIEW\n")
            f.write("lowercase_bad\n")
            path = f.name

        try:
            with self.assertRaises(ValueError) as ctx:
                load_permissions_file(path)
            self.assertIn("Invalid permission", str(ctx.exception))
            self.assertIn("lowercase_bad", str(ctx.exception))
        finally:
            Path(path).unlink()

    def test_comments_and_blanks_ignored(self):
        """Comments and blank lines do not appear in result."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".permissions", delete=False
        ) as f:
            f.write("# Comment\n")
            f.write("\n")
            f.write("  \n")
            f.write("TICKET_VIEW\n")
            f.write("  # Indented comment\n")
            path = f.name

        try:
            result = load_permissions_file(path)
            self.assertEqual(result, frozenset({"TICKET_VIEW"}))
        finally:
            Path(path).unlink()

    def test_duplicate_permissions_deduplicated(self):
        """Duplicate entries are deduplicated."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".permissions", delete=False
        ) as f:
            f.write("TICKET_VIEW\n")
            f.write("TICKET_VIEW\n")
            f.write("WIKI_VIEW\n")
            path = f.name

        try:
            result = load_permissions_file(path)
            self.assertEqual(
                result, frozenset({"TICKET_VIEW", "WIKI_VIEW"})
            )
        finally:
            Path(path).unlink()


class TestAllToolsCarryReadOnlyHint(unittest.TestCase):
    """Regression test for ticket #23.

    Every registered tool must declare ``annotations.readOnlyHint``
    explicitly. Without it, Claude Code plan mode can't prove a tool is
    read-only from the MCP listing alone, so it prompts for confirmation
    on every call even when the caller's permission allowlist already
    covers it -- defeating plan mode for exactly the read-only research
    tools (ticket_get, wiki_get, ...) it's most wanted for.
    """

    def test_every_tool_declares_read_only_hint(self):
        missing = [
            spec.tool.name
            for spec in [PING_SPEC] + ALL_SPECS
            if spec.tool.annotations is None
            or spec.tool.annotations.readOnlyHint is None
        ]
        self.assertEqual(
            missing,
            [],
            f"Tools missing annotations.readOnlyHint: {missing}",
        )

    def test_read_only_hint_matches_write_tool_naming(self):
        """Sanity-check the hint direction, not just its presence.

        A tool named *_create/_update/_delete must be readOnlyHint=False;
        every other registered tool must be readOnlyHint=True. Catches an
        annotation copy-pasted onto the wrong tool.
        """
        write_suffixes = ("_create", "_update", "_delete")
        for spec in [PING_SPEC] + ALL_SPECS:
            name = spec.tool.name
            assert spec.tool.annotations is not None, name
            read_only = spec.tool.annotations.readOnlyHint
            if name.endswith(write_suffixes) or name in {
                "ticket_attachment_put",
                "ticket_attachment_get",
                "wiki_attachment_put",
                "wiki_attachment_get",
                "wiki_file_push",
                "wiki_file_pull",
            }:
                self.assertFalse(
                    read_only, f"{name} should be readOnlyHint=False"
                )
            else:
                self.assertTrue(
                    read_only, f"{name} should be readOnlyHint=True"
                )


if __name__ == "__main__":
    unittest.main()
