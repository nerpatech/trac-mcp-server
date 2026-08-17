"""System tool handlers for MCP server.

This module implements system-level MCP tools: get_server_time for reliable
timestamp access from Trac server.
"""

import logging

import mcp.types as types

from ...core.async_utils import run_sync
from ...core.client import TracClient
from .errors import build_error_response
from .registry import ToolSpec

logger = logging.getLogger(__name__)


# Tool definitions for list_tools()
SYSTEM_TOOLS = [
    types.Tool(
        name="get_server_time",
        description="Get current Trac server time for temporal reasoning and coordination. Returns server timestamp in both ISO 8601 and Unix timestamp formats.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
]


async def _handle_get_server_time(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle get_server_time tool.

    Reads the Trac server's current wall-clock time from the HTTP Date
    response header of a lightweight RPC round trip. Previously this
    inferred "current time" from a wiki page's lastModified timestamp,
    which only reflects when that page was last edited -- observed
    ~4 months stale in production once WikiStart went that long without
    an edit (ticket #33).

    Returns:
        CallToolResult with ISO 8601 timestamp text and structured JSON with
        server_time (ISO), unix_timestamp (int), and timezone ("server")
    """
    try:
        dt = await run_sync(client.get_server_time)
        iso_timestamp = dt.isoformat()
        unix_timestamp = int(dt.timestamp())

        text_content = f"Server time: {iso_timestamp}"

        structured_json = {
            "server_time": iso_timestamp,
            "unix_timestamp": unix_timestamp,
            "timezone": "server",
        }

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text_content)],
            structuredContent=structured_json,
        )

    except Exception as e:
        logger.error("Error getting server time: %s", e)
        return build_error_response(
            "server_error",
            f"Failed to get server time: {str(e)}",
            "Check Trac server connectivity and permissions.",
        )


# ToolSpec list for registry-based dispatch
SYSTEM_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=SYSTEM_TOOLS[0],
        permissions=frozenset(),
        handler=_handle_get_server_time,
    ),
]
