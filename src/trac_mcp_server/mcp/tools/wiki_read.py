"""Read-only wiki tool handlers for MCP server.

This module implements wiki read operations: get, search, and recent_changes.
All tools use async handlers with run_sync() to bridge synchronous TracClient calls,
automatic Markdown conversion, and structured error responses.
"""

import asyncio
import base64
import json
import time
import xmlrpc.client
from datetime import datetime, timedelta

import mcp.types as types

from ...core.async_utils import run_sync, run_sync_limited
from ...core.client import TracClient
from .errors import build_error_response, format_timestamp
from .registry import ToolSpec
from .source_format import reject_removed_conversion_args

# Tool definitions for list_tools()
WIKI_READ_TOOLS = [
    types.Tool(
        name="wiki_get",
        description="Get wiki page content as stored: TracWiki, byte-for-byte. Returns full content with metadata (version, author, modified date).",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "Wiki page name to retrieve (required)",
                },
                "version": {
                    "type": "integer",
                    "description": "Specific version to retrieve (optional, defaults to latest)",
                    "minimum": 1,
                },
            },
            "required": ["page_name"],
        },
    ),
    types.Tool(
        name="wiki_search",
        description="Search wiki pages by content with relevance ranking. Returns snippets showing matched text, as stored: TracWiki, byte-for-byte.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string (required)",
                },
                "prefix": {
                    "type": "string",
                    "description": "Filter to pages starting with this prefix (namespace filter, optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results per page (default: 10, max: 50)",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                },
                "cursor": {
                    "type": "string",
                    "description": "Pagination cursor from previous response (optional)",
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="wiki_recent_changes",
        description="Get recently modified wiki pages. Returns pages sorted by modification date (newest first). Useful for finding stale or recently updated documentation.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "since_days": {
                    "type": "integer",
                    "description": "Return pages modified within this many days",
                    "default": 30,
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 20, max: 100)",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": [],
        },
    ),
    types.Tool(
        name="wiki_get_history",
        description="Get wiki page revision history newest-first. Returns list of revisions with version, author, lastModified, and comment (commit message). Useful for detecting prior edits or scanning attribution markers in change comments.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "Wiki page name to retrieve history for (required)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum revisions to return, newest first. Omit for all revisions.",
                    "minimum": 1,
                },
            },
            "required": ["page_name"],
        },
    ),
]


def encode_cursor(offset: int, total: int) -> str:
    """Encode pagination cursor.

    Args:
        offset: Current offset into results
        total: Total number of results

    Returns:
        Base64-encoded cursor string
    """
    cursor_data = {"offset": offset, "total": total}
    cursor_json = json.dumps(cursor_data)
    cursor_bytes = cursor_json.encode("utf-8")
    return base64.b64encode(cursor_bytes).decode("utf-8")


def decode_cursor(cursor: str) -> tuple[int, int]:
    """Decode pagination cursor.

    Args:
        cursor: Base64-encoded cursor string

    Returns:
        Tuple of (offset, total)

    Raises:
        ValueError: If cursor is invalid
    """
    try:
        cursor_bytes = base64.b64decode(cursor.encode("utf-8"))
        cursor_json = cursor_bytes.decode("utf-8")
        cursor_data = json.loads(cursor_json)
        return cursor_data["offset"], cursor_data["total"]
    except (KeyError, json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"Invalid cursor: {e}") from e


async def _handle_get(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_get."""
    page_name = args.get("page_name")
    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )

    format_error = reject_removed_conversion_args(args)
    if format_error is not None:
        return format_error

    version = args.get("version")

    # Get page content and info in parallel (bounded by semaphore)
    content, info = await asyncio.gather(
        run_sync_limited(client.get_wiki_page, page_name, version),
        run_sync_limited(client.get_wiki_page_info, page_name, version),
    )

    # Returned as stored, so a read-edit-write round trip is byte-exact
    # (ticket #69).
    content_output = content

    # Extract metadata
    page_version = info.get("version", 1)
    author = info.get("author", "unknown")
    modified = info.get("lastModified", "")
    modified_str = format_timestamp(modified)

    # Format response with metadata header
    response_lines = [
        f"# {page_name}",
        f"Version: {page_version} | Author: {author} | Modified: {modified_str}",
        "----",
        "",
        content_output,
    ]

    # Build structured JSON
    wiki_json = {
        "name": page_name,
        "content": content_output,
        "version": page_version,
        "author": author,
        "lastModified": modified_str,
    }

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent=wiki_json,
    )


async def _handle_search(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_search."""
    query = args.get("query")
    if not query:
        return build_error_response(
            "validation_error",
            "query is required",
            "Provide query parameter.",
        )

    prefix = args.get("prefix")
    limit = args.get("limit", 10)
    cursor = args.get("cursor")

    format_error = reject_removed_conversion_args(args)
    if format_error is not None:
        return format_error

    # Ensure limit is within bounds
    limit = min(max(1, limit), 50)

    # Decode cursor or start at offset 0
    if cursor:
        try:
            offset, total = decode_cursor(cursor)
        except ValueError as e:
            return build_error_response(
                "validation_error",
                str(e),
                "Provide valid cursor from previous response.",
            )
    else:
        offset = 0

    # Search wiki pages
    results = await run_sync(client.search_wiki_pages_by_content, query)

    if not results:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text="No wiki pages found matching query.",
                )
            ]
        )

    # Apply prefix filter if provided
    if prefix:
        results = [
            r for r in results if r.get("name", "").startswith(prefix)
        ]

        if not results:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"No wiki pages found matching query with prefix '{prefix}'.",
                    )
                ]
            )

    # Calculate pagination
    total = len(results)
    results_page = results[offset : offset + limit]

    if not results_page:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=f"No more results (offset {offset} exceeds {total} total results).",
                )
            ]
        )

    # Format results with snippets
    formatted = []
    for result in results_page:
        name = result.get("name", "")
        snippet = result.get("snippet", "")
        formatted.append(f"**{name}**\n  ...{snippet}...")

    # Build response
    response_lines = [f"Found {total} wiki pages"]

    if total > limit:
        showing_start = offset + 1
        showing_end = offset + len(results_page)
        response_lines[0] = (
            f"Found {total} wiki pages "
            f"(showing {showing_start}-{showing_end})"
        )

    response_lines[0] += ":"
    response_lines.append("")
    response_lines.extend(formatted)

    # Add next cursor if there are more results
    if offset + limit < total:
        next_cursor = encode_cursor(offset + limit, total)
        response_lines.append("")
        response_lines.append(
            f"Use cursor '{next_cursor}' to get next page."
        )

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ]
    )


async def _handle_recent_changes(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_recent_changes."""
    since_days = args.get("since_days", 30)
    limit = args.get("limit", 20)

    # Ensure parameters are within bounds
    since_days = max(1, since_days)
    limit = min(max(1, limit), 100)

    # Calculate timestamp cutoff
    since_ts = int(time.time()) - int(
        timedelta(days=since_days).total_seconds()
    )

    # Get recent changes
    changes = await run_sync(client.get_recent_wiki_changes, since_ts)

    if not changes:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=f"No wiki pages modified in the last {since_days} days.",
                )
            ],
            structuredContent={"pages": [], "since_days": since_days},
        )

    # Sort by lastModified descending (most recent first)
    changes.sort(key=lambda x: x.get("lastModified", 0), reverse=True)

    # Limit results
    total = len(changes)
    changes = changes[:limit]

    # Format response
    response_lines = [f"Wiki pages modified in last {since_days} days:"]
    if total > limit:
        response_lines[0] += f" (showing {limit} of {total})"
    response_lines.append("")

    pages_json = []
    for change in changes:
        page_name = change.get("name", "Unknown")
        author = change.get("author", "unknown")
        last_modified = change.get("lastModified", 0)
        page_version = change.get("version", 1)

        # Format timestamp
        match last_modified:
            case xmlrpc.client.DateTime() as dt_val:
                dt = datetime.fromtimestamp(
                    time.mktime(dt_val.timetuple())
                )
                modified_str = dt.strftime("%Y-%m-%d %H:%M")
            case int() | float() as ts:
                dt = datetime.fromtimestamp(ts)
                modified_str = dt.strftime("%Y-%m-%d %H:%M")
            case _:
                modified_str = str(last_modified)

        response_lines.append(
            f"- {page_name} (modified: {modified_str} by {author})"
        )

        # Build JSON object
        pages_json.append(
            {
                "name": page_name,
                "author": author,
                "lastModified": modified_str,
                "version": page_version,
            }
        )

    if total > limit:
        response_lines.append("")
        response_lines.append(
            "Use limit parameter to see more (up to 100)."
        )

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent={
            "pages": pages_json,
            "since_days": since_days,
        },
    )


async def _handle_get_history(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_get_history.

    Walks the page's version history newest-first, fetching per-version
    metadata via ``client.get_wiki_page_info(page_name, version)``. The
    ``comment`` field (Trac XmlRpcPlugin post trac-hacks #1864) is
    preserved for attribution-marker scanning by auto-pm's edit workflow.
    """
    page_name = args.get("page_name")
    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )

    limit = args.get("limit")

    # Fetch current version to determine the walk range
    try:
        current_info = await run_sync_limited(
            client.get_wiki_page_info, page_name
        )
    except xmlrpc.client.Fault as err:
        from .errors import translate_xmlrpc_error

        return translate_xmlrpc_error(err, "wiki", page_name)

    current_version = current_info.get("version", 1)
    if not isinstance(current_version, int) or current_version < 1:
        current_version = 1

    # Range newest-first: current, current-1, ..., 1
    versions = list(range(current_version, 0, -1))
    if limit is not None and limit > 0:
        versions = versions[:limit]

    revisions: list[dict] = []
    for v in versions:
        try:
            info = await run_sync_limited(
                client.get_wiki_page_info, page_name, v
            )
        except xmlrpc.client.Fault:
            # Skip revisions we can't fetch (permissions, gaps) but
            # keep going — partial history is better than none.
            continue

        revisions.append(
            {
                "version": v,
                "author": info.get("author", "unknown"),
                "lastModified": format_timestamp(
                    info.get("lastModified", "")
                ),
                "comment": info.get("comment", "") or "",
            }
        )

    # Format human-readable text output
    if revisions:
        response_lines = [f"# {page_name} history", ""]
        for rev in revisions:
            comment_str = rev["comment"] or "(no comment)"
            response_lines.append(
                f"- v{rev['version']} by {rev['author']} at {rev['lastModified']}: {comment_str}"
            )
    else:
        response_lines = [
            f"# {page_name} history",
            "",
            "(no revisions found)",
        ]

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent={
            "page_name": page_name,
            "revisions": revisions,
        },
    )


# ToolSpec list for registry-based dispatch
WIKI_READ_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=WIKI_READ_TOOLS[0],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_get,
    ),
    ToolSpec(
        tool=WIKI_READ_TOOLS[1],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_search,
    ),
    ToolSpec(
        tool=WIKI_READ_TOOLS[2],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_recent_changes,
    ),
    ToolSpec(
        tool=WIKI_READ_TOOLS[3],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_get_history,
    ),
]
