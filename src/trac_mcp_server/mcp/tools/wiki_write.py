"""Write wiki tool handlers for MCP server.

This module implements wiki write operations: create, update, and delete.
All tools use async handlers with run_sync() to bridge synchronous TracClient calls,
automatic Markdown conversion, and structured error responses.
"""

import logging
import xmlrpc.client

import mcp.types as types

from ...converters.common import auto_convert
from ...core.async_utils import run_sync
from ...core.client import TracClient
from .errors import build_error_response
from .registry import ToolSpec
from .source_format import FORMAT_PROPERTY, resolve_source_format

logger = logging.getLogger(__name__)


# Tool definitions for list_tools()
WIKI_WRITE_TOOLS = [
    types.Tool(
        name="wiki_create",
        description="Create a new wiki page. Content is Markdown by default (converted to TracWiki); pass format='tracwiki' to store hand-authored TracWiki verbatim. Fails if the page exists (use wiki_update instead).",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "Wiki page name to create (required)",
                },
                "content": {
                    "type": "string",
                    "description": "Page content (required). Markdown by default (converted to TracWiki); set format='tracwiki' to supply TracWiki that is stored unchanged.",
                },
                "comment": {
                    "type": "string",
                    "description": "Change comment (optional)",
                },
                "format": FORMAT_PROPERTY,
            },
            "required": ["page_name", "content"],
        },
    ),
    types.Tool(
        name="wiki_update",
        description="Update an existing wiki page with optimistic locking (requires version for conflict detection). Content is Markdown by default (converted to TracWiki); pass format='tracwiki' to store hand-authored TracWiki verbatim.",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "Wiki page name to update (required)",
                },
                "content": {
                    "type": "string",
                    "description": "Page content (required). Markdown by default (converted to TracWiki); set format='tracwiki' to supply TracWiki that is stored unchanged.",
                },
                "version": {
                    "type": "integer",
                    "description": "Current page version for optimistic locking (required)",
                    "minimum": 1,
                },
                "comment": {
                    "type": "string",
                    "description": "Change comment (optional)",
                },
                "format": FORMAT_PROPERTY,
            },
            "required": ["page_name", "content", "version"],
        },
    ),
    types.Tool(
        name="wiki_delete",
        description="Delete a wiki page. Warning: This cannot be undone. Requires WIKI_DELETE permission.",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "Wiki page name to delete (required)",
                }
            },
            "required": ["page_name"],
        },
    ),
]


async def _handle_create(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_create."""
    page_name = args.get("page_name")
    content = args.get("content")

    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )
    if not content:
        return build_error_response(
            "validation_error",
            "content is required",
            "Provide content parameter.",
        )

    comment = args.get("comment", "")

    source_format, format_error = resolve_source_format(args)
    if format_error is not None:
        return format_error

    # Convert content using auto_convert with server capability detection.
    # source_format is passed explicitly rather than left to re-detect: a
    # marker-poor Markdown document (e.g. a bare table with no
    # heading/bold/fence/link) scores as TracWiki and passes through
    # unconverted (ticket #47). When the caller declares TracWiki the
    # converter is skipped outright rather than configured to pass
    # through (ticket #62) -- same shape as wiki_file_push.
    warnings: list[str] = []
    if source_format == "markdown":
        conversion = await auto_convert(
            content,
            client.config,
            target_format="tracwiki",
            source_format="markdown",
        )
        wiki_content = conversion.text
        warnings = conversion.warnings
    else:
        # Already TracWiki -- store the author's bytes unchanged
        wiki_content = content

    # Log warnings for agent visibility
    if warnings:
        logger.warning(f"Conversion warnings: {', '.join(warnings)}")

    # Check if page already exists
    # Note: get_wiki_page raises exceptions for non-existent pages,
    # while get_wiki_page_info does not (Trac XML-RPC quirk)
    try:
        await run_sync(client.get_wiki_page, page_name)
        # Page exists - return error
        return build_error_response(
            "already_exists",
            f"Page '{page_name}' already exists",
            "Use wiki_update to modify existing page, or choose a different name.",
        )
    except xmlrpc.client.Fault as e:
        # Page doesn't exist - this is expected, continue
        fault_lower = e.faultString.lower()
        if (
            "not found" not in fault_lower
            and "does not exist" not in fault_lower
        ):
            # Different error - re-raise
            raise

    # Create the page (version=None creates new page)
    result = await run_sync(
        client.put_wiki_page, page_name, wiki_content, comment, None
    )

    # Extract version from result
    new_version = result.get("version", 1)

    # Build response
    response_lines = [
        f"Created wiki page '{page_name}' (version {new_version})"
    ]

    # Add warnings if any
    if warnings:
        response_lines.append("")
        response_lines.append("Conversion warnings:")
        for warning in warnings:
            response_lines.append(f"- {warning}")

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ]
    )


async def _handle_update(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_update."""
    page_name = args.get("page_name")
    content = args.get("content")
    version = args.get("version")

    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )
    if not content:
        return build_error_response(
            "validation_error",
            "content is required",
            "Provide content parameter.",
        )
    if not version:
        return build_error_response(
            "validation_error",
            "version is required",
            "Provide version parameter for optimistic locking.",
        )

    comment = args.get("comment", "")

    source_format, format_error = resolve_source_format(args)
    if format_error is not None:
        return format_error

    # Convert content, or skip the converter entirely when the caller
    # declares TracWiki -- see the matching comment in _handle_create
    # (tickets #47 and #62).
    warnings: list[str] = []
    if source_format == "markdown":
        conversion = await auto_convert(
            content,
            client.config,
            target_format="tracwiki",
            source_format="markdown",
        )
        wiki_content = conversion.text
        warnings = conversion.warnings
    else:
        # Already TracWiki -- store the author's bytes unchanged
        wiki_content = content

    # Log warnings for agent visibility
    if warnings:
        logger.warning(f"Conversion warnings: {', '.join(warnings)}")

    # Update the page with version check
    try:
        result = await run_sync(
            client.put_wiki_page,
            page_name,
            wiki_content,
            comment,
            version,
        )
    except xmlrpc.client.Fault as e:
        # Check for version conflict
        if (
            "version" in e.faultString.lower()
            or "not modified" in e.faultString.lower()
        ):
            # Fetch current version for error message
            try:
                current_info = await run_sync(
                    client.get_wiki_page_info, page_name
                )
                current_version = current_info.get("version", 0)
                return build_error_response(
                    "version_conflict",
                    f"Page has been modified. Current version is {current_version}, you tried to update version {version}.",
                    f"Fetch current content with wiki_get(page_name='{page_name}'), then retry update with version={current_version}.",
                )
            except Exception:
                # Couldn't get current version, just report the conflict
                return build_error_response(
                    "version_conflict",
                    e.faultString,
                    f"Fetch current version with wiki_get(page_name='{page_name}'), then retry update.",
                )
        else:
            # Different error - re-raise
            raise

    # Extract new version from result
    new_version = result.get("version", version + 1)

    # Build response
    response_lines = [
        f"Updated wiki page '{page_name}' to version {new_version}"
    ]

    # Add warnings if any
    if warnings:
        response_lines.append("")
        response_lines.append("Conversion warnings:")
        for warning in warnings:
            response_lines.append(f"- {warning}")

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ]
    )


async def _handle_delete(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_delete."""
    page_name = args.get("page_name")
    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )

    # Check if page exists before attempting deletion
    # Note: get_wiki_page raises exceptions for non-existent pages,
    # while get_wiki_page_info does not (Trac XML-RPC quirk)
    try:
        await run_sync(client.get_wiki_page, page_name)
    except xmlrpc.client.Fault as e:
        fault_lower = e.faultString.lower()
        if (
            "not found" in fault_lower
            or "does not exist" in fault_lower
        ):
            return build_error_response(
                "not_found",
                f"Wiki page '{page_name}' does not exist",
                "Use wiki_search to find available pages.",
            )
        raise

    # Delete the page
    await run_sync(client.delete_wiki_page, page_name)

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text=f"Deleted wiki page '{page_name}'."
            )
        ]
    )


# ToolSpec list for registry-based dispatch
WIKI_WRITE_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=WIKI_WRITE_TOOLS[0],
        permissions=frozenset({"WIKI_CREATE"}),
        handler=_handle_create,
    ),
    ToolSpec(
        tool=WIKI_WRITE_TOOLS[1],
        permissions=frozenset({"WIKI_MODIFY"}),
        handler=_handle_update,
    ),
    ToolSpec(
        tool=WIKI_WRITE_TOOLS[2],
        permissions=frozenset({"WIKI_DELETE"}),
        handler=_handle_delete,
    ),
]
