"""Ticket #102, gap 2 (comment:2 review): the two dispatch sites that read
the caller's identity must not blow up when called outside an HTTP
request -- e.g. every existing unit test that calls handle_call_tool()
directly, the way test_server_wiki_tools.py does. ``server.request_context``
raises ``LookupError`` in that shape, not just ``None``, so
``_caller_identity()`` has to catch it specifically.

The end-to-end "does the identity actually reach a real request" case is
covered by tests/test_mcp/test_http_identity.py's real-uvicorn harness;
this file only pins the direct-call fallback these dispatch sites rely on.
"""

import asyncio
from unittest.mock import MagicMock, patch

import mcp.types as types

from trac_mcp_server.mcp.server import (
    PING_SPEC,
    _caller_identity,
    handle_call_tool,
    set_registry,
)
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import ToolRegistry


def _init_registry():
    set_registry(ToolRegistry([PING_SPEC] + ALL_SPECS))


def _clear_registry():
    set_registry(None)


class TestCallerIdentityOutsideARequest:
    def test_caller_identity_returns_none_outside_a_request(self):
        assert _caller_identity() is None

    def setup_method(self):
        _init_registry()

    def teardown_method(self):
        _clear_registry()

    @patch("trac_mcp_server.mcp.server.get_registry")
    @patch("trac_mcp_server.mcp.server.get_instances")
    def test_handle_call_tool_passes_identity_none(
        self, mock_get_instances, mock_get_registry
    ):
        """A direct handle_call_tool() call -- no HTTP request, no
        BearerAuthMiddleware -- must resolve identity=None, not raise."""
        mock_client = MagicMock()
        mock_get_instances.return_value.get_client.return_value = (
            mock_client
        )
        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry

        expected = types.CallToolResult(
            content=[types.TextContent(type="text", text="ok")]
        )

        async def fake_call_tool(name, args, client):
            return expected

        mock_registry.call_tool = MagicMock(side_effect=fake_call_tool)

        result = asyncio.run(handle_call_tool("ping", {}))

        mock_get_instances.return_value.get_client.assert_called_once_with(
            None, identity=None
        )
        assert not result.isError
