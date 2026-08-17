"""System tool handlers for MCP server.

This module implements system-level MCP tools: get_server_time for reliable
timestamp access from Trac server.
"""

import logging
import time
from datetime import datetime

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

    Uses wiki.getPageInfo("WikiStart") to retrieve server timestamp from a page
    that exists by default in Trac installations. Falls back to first available
    page if WikiStart doesn't exist.

    Returns:
        CallToolResult with ISO 8601 timestamp text and structured JSON with
        server_time (ISO), unix_timestamp (int), and timezone ("server")
    """
    try:
        # Try WikiStart first (default page in Trac)
        def get_page_info():
            try:
                return client.get_wiki_page_info("WikiStart")
            except Exception:
                # Fallback: get first available page
                pages = client.list_wiki_pages()
                if not pages:
                    raise RuntimeError(
                        "No wiki pages available to query server time"
                    ) from None
                return client.get_wiki_page_info(pages[0])

        page_info = await run_sync(get_page_info)

        # Extract lastModified field (string in format YYYYMMDDTHH:MM:SS)
        last_modified = page_info.get("lastModified")
        if not last_modified:
            return build_error_response(
                "server_error",
                "Server did not return lastModified timestamp",
                "This may indicate a Trac server configuration issue.",
            )

        # Parse timestamp - can be either string or DateTime object
        if isinstance(last_modified, str):
            # String format: 20260205T20:51:27
            dt = datetime.strptime(last_modified, "%Y%m%dT%H:%M:%S")
            unix_timestamp = int(dt.timestamp())
            iso_timestamp = dt.isoformat()
        else:
            # xmlrpc.client.DateTime object
            unix_timestamp = int(time.mktime(last_modified.timetuple()))
            dt = datetime.fromtimestamp(unix_timestamp)
            iso_timestamp = dt.isoformat()

        # Build response
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
