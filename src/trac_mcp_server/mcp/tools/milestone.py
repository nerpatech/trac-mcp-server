"""Milestone tool handlers for MCP server.

This module implements all milestone-related MCP tools: list, get, create, update, and delete.
All tools use async handlers with run_sync() to bridge synchronous TracClient calls, handle
date conversions, and provide structured error responses.
"""

import time
import xmlrpc.client
from datetime import datetime
from typing import Any

import mcp.types as types

from ...converters import tracwiki_to_markdown
from ...core.async_utils import run_sync
from ...core.client import TracClient
from .errors import build_error_response
from .registry import ToolSpec

# Tool definitions for list_tools()
MILESTONE_TOOLS = [
    types.Tool(
        name="milestone_list",
        description="List all milestone names. Returns array of milestone names (e.g., ['v1.0', 'v2.0', 'Future']). Requires TICKET_VIEW permission.",
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
    ),
    types.Tool(
        name="milestone_get",
        description="Get milestone details by name. Returns name, due date, completion date, and description. Requires TICKET_VIEW permission. Set raw=true to get description in original TracWiki format without conversion.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Milestone name (required)",
                },
                "raw": {
                    "type": "boolean",
                    "description": "If true, return description in original TracWiki format without converting to Markdown (default: false)",
                    "default": False,
                },
            },
            "required": ["name"],
        },
    ),
    types.Tool(
        name="milestone_create",
        description="Create a new milestone. Requires TICKET_ADMIN permission. Attributes: due (ISO 8601 date), completed (ISO 8601 date or 0), description (string).",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Milestone name (required)",
                },
                "attributes": {
                    "type": "object",
                    "description": "Milestone attributes",
                    "properties": {
                        "due": {
                            "type": "string",
                            "description": "Due date in ISO 8601 format (e.g., '2026-12-31T23:59:59')",
                        },
                        "completed": {
                            "description": "Completion date in ISO 8601 format or 0 for not completed"
                        },
                        "description": {
                            "type": "string",
                            "description": "Milestone description",
                        },
                    },
                },
            },
            "required": ["name"],
        },
    ),
    types.Tool(
        name="milestone_update",
        description="Update an existing milestone. Requires TICKET_ADMIN permission. Attributes: due (ISO 8601 date), completed (ISO 8601 date or 0), description (string).",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Milestone name (required)",
                },
                "attributes": {
                    "type": "object",
                    "description": "Milestone attributes to update",
                    "properties": {
                        "due": {
                            "type": "string",
                            "description": "Due date in ISO 8601 format (e.g., '2026-12-31T23:59:59')",
                        },
                        "completed": {
                            "description": "Completion date in ISO 8601 format or 0 for not completed"
                        },
                        "description": {
                            "type": "string",
                            "description": "Milestone description",
                        },
                    },
                },
            },
            "required": ["name", "attributes"],
        },
    ),
    types.Tool(
        name="milestone_delete",
        description="Delete a milestone by name. Requires TICKET_ADMIN permission. Warning: This cannot be undone.",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Milestone name (required)",
                }
            },
            "required": ["name"],
        },
    ),
]


async def _handle_list(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle milestone_list."""
    milestones = await run_sync(client.get_all_milestones)

    if not milestones:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text", text="No milestones found."
                )
            ],
            structuredContent={"milestones": []},
        )

    # Return newline-separated milestone names
    return types.CallToolResult(
        content=[
            types.TextContent(type="text", text="\n".join(milestones))
        ],
        structuredContent={"milestones": milestones},
    )


async def _handle_get(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle milestone_get."""
    name = args.get("name")
    if not name:
        return build_error_response(
            "validation_error",
            "name is required",
            "Provide name parameter.",
        )

    raw = args.get("raw", False)

    # Get milestone data
    milestone_data = await run_sync(client.get_milestone, name)

    # Extract fields
    milestone_name = milestone_data.get("name", name)
    due = milestone_data.get("due", 0)
    completed = milestone_data.get("completed", 0)
    description = milestone_data.get("description", "")

    # Convert description from TracWiki to Markdown unless raw mode is requested
    if description:
        if raw:
            description_output = description
        else:
            conversion_result = tracwiki_to_markdown(description)
            description_output = conversion_result.text
    else:
        description_output = "(No description)"

    # Format dates
    due_str = _format_date(due)
    completed_str = _format_date(completed)

    # Build response
    format_note = " (TracWiki)" if raw else ""
    response_lines = [
        f"Milestone: {milestone_name}",
        f"Due: {due_str}",
        f"Completed: {completed_str}",
        "",
        f"## Description{format_note}",
        description_output,
    ]

    # Build structured JSON
    milestone_json = {
        "name": milestone_name,
        "due": due_str,
        "completed": completed_str
        if completed_str != "Not set"
        else None,
        "description": description_output,
    }

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent=milestone_json,
    )


async def _handle_create(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle milestone_create."""
    name = args.get("name")
    if not name:
        return build_error_response(
            "validation_error",
            "name is required",
            "Provide name parameter.",
        )

    # Build attributes with date conversion
    attributes = _convert_milestone_attributes(
        args.get("attributes", {})
    )

    # Create milestone
    await run_sync(client.create_milestone, name, attributes)

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text=f"Created milestone: {name}"
            )
        ]
    )


async def _handle_update(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle milestone_update."""
    name = args.get("name")
    if not name:
        return build_error_response(
            "validation_error",
            "name is required",
            "Provide name parameter.",
        )

    attributes = args.get("attributes")
    if not attributes:
        return build_error_response(
            "validation_error",
            "attributes is required",
            "Provide attributes parameter with fields to update.",
        )

    # Convert date strings to DateTime
    attributes = _convert_milestone_attributes(attributes)

    # Update milestone
    await run_sync(client.update_milestone, name, attributes)

    # Build summary of changes
    changes = list(attributes.keys())
    change_summary = (
        f"updated {len(changes)} field(s): {', '.join(changes)}"
    )

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=f"Updated milestone '{name}' ({change_summary})",
            )
        ]
    )


async def _handle_delete(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle milestone_delete."""
    name = args.get("name")
    if not name:
        return build_error_response(
            "validation_error",
            "name is required",
            "Provide name parameter.",
        )

    # Delete milestone
    await run_sync(client.delete_milestone, name)

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text=f"Deleted milestone: {name}"
            )
        ]
    )


def _convert_milestone_attributes(attributes: dict) -> dict:
    """Convert milestone attributes with ISO 8601 date strings to xmlrpc.client.DateTime.

    Args:
        attributes: Dict with optional keys: due, completed, description

    Returns:
        Dict with DateTime objects for due/completed dates
    """
    converted = attributes.copy()

    # Convert due date if present
    if (
        "due" in converted
        and converted["due"]
        and converted["due"] != 0
    ):
        converted["due"] = _parse_date(converted["due"])

    # Convert completed date if present
    if "completed" in converted:
        if converted["completed"] == 0 or converted["completed"] == "0":
            converted["completed"] = 0  # Not completed
        elif converted["completed"]:
            converted["completed"] = _parse_date(converted["completed"])

    return converted


def _parse_date(date_str: str) -> xmlrpc.client.DateTime:
    """Parse ISO 8601 date string to xmlrpc.client.DateTime.

    Args:
        date_str: Date string in ISO 8601 format. Accepts:
            - "YYYY-MM-DDTHH:MM:SS" (full datetime)
            - "YYYY-MM-DD" (date only, defaults to 00:00:00)

    Returns:
        xmlrpc.client.DateTime object

    Raises:
        ValueError: If date string format is invalid
    """
    # Try supported formats in order of specificity
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
        try:
            parsed = time.strptime(date_str, fmt)
            return xmlrpc.client.DateTime(parsed)
        except ValueError:
            continue

    raise ValueError(
        f"Invalid date format '{date_str}'. Expected YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS"
    )


def _format_date(date_value: Any) -> str:
    """Format date value for display.

    Args:
        date_value: DateTime object, timestamp, or 0 for not set

    Returns:
        Formatted date string or "(Not set)"
    """
    match date_value:
        case 0 | None:
            return "(Not set)"
        case xmlrpc.client.DateTime() as dt_val:
            return dt_val.value
        case int() | float() as ts:
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        case datetime() as dt:
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        case _:
            return str(date_value)


# ToolSpec list for registry-based dispatch
MILESTONE_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=MILESTONE_TOOLS[0],
        permissions=frozenset({"MILESTONE_VIEW"}),
        handler=_handle_list,
    ),
    ToolSpec(
        tool=MILESTONE_TOOLS[1],
        permissions=frozenset({"MILESTONE_VIEW"}),
        handler=_handle_get,
    ),
    ToolSpec(
        tool=MILESTONE_TOOLS[2],
        permissions=frozenset({"MILESTONE_CREATE"}),
        handler=_handle_create,
    ),
    ToolSpec(
        tool=MILESTONE_TOOLS[3],
        permissions=frozenset({"MILESTONE_MODIFY"}),
        handler=_handle_update,
    ),
    ToolSpec(
        tool=MILESTONE_TOOLS[4],
        permissions=frozenset({"MILESTONE_DELETE"}),
        handler=_handle_delete,
    ),
]
