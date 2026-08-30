"""Read-only ticket tool handlers for MCP server.

This module implements ticket read operations: search, get, and changelog.
All tools use async handlers with run_sync() to bridge synchronous TracClient calls,
automatic Markdown conversion, and structured error responses.
"""

import xmlrpc.client
from typing import Any

import mcp.types as types

from ...converters import tracwiki_to_markdown
from ...core.async_utils import (
    gather_limited,
    run_sync,
    run_sync_limited,
)
from ...core.client import TracClient
from .errors import build_error_response, format_timestamp
from .registry import ToolSpec

# Tool definitions for list_tools()
TICKET_READ_TOOLS = [
    types.Tool(
        name="ticket_search",
        description="Search tickets with filtering by status, owner, and keywords. Returns ticket IDs with summaries.",
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
                    "description": "Trac query string (e.g., 'status=new', 'owner=alice', 'status!=closed&keywords~=urgent'). Default: 'status!=closed' (open tickets)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 10, max: 100)",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": [],
        },
    ),
    types.Tool(
        name="ticket_get",
        description="Get full ticket details including summary, description, status, and owner. Response includes a change token (_ts) -- pass it to ticket_update's base_ts to detect if the ticket changes before your write lands. Use ticket_changelog for history. Set raw=true to get description in original TracWiki format without conversion.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "integer",
                    "description": "Ticket number to retrieve",
                    "minimum": 1,
                },
                "raw": {
                    "type": "boolean",
                    "description": "If true, return description in original TracWiki format without converting to Markdown (default: false)",
                    "default": False,
                },
            },
            "required": ["ticket_id"],
        },
    ),
    types.Tool(
        name="ticket_changelog",
        description="Get ticket change history. Use this to investigate who changed what and when. Set raw=true to get comment content in original TracWiki format without conversion.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "integer",
                    "description": "Ticket number to get history for",
                    "minimum": 1,
                },
                "raw": {
                    "type": "boolean",
                    "description": "If true, return comment content in original TracWiki format without converting to Markdown (default: false)",
                    "default": False,
                },
            },
            "required": ["ticket_id"],
        },
    ),
    types.Tool(
        name="ticket_fields",
        description="Get all ticket field definitions (standard + custom fields). Returns field metadata including name, type, label, options (for select fields), and custom flag. Use to discover instance-specific ticket schema.",
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
        name="ticket_actions",
        description="Get valid workflow actions for a ticket's current state. Returns available state transitions (e.g., accept, resolve, reassign). Essential for agents to know which actions are possible before updating ticket status.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "integer",
                    "description": "Ticket number to retrieve actions for",
                    "minimum": 1,
                }
            },
            "required": ["ticket_id"],
        },
    ),
]


async def _handle_search(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_search."""
    query = args.get("query", "status!=closed")
    max_results = args.get("max_results", 10)

    # Ensure max_results is within bounds
    max_results = min(max(1, max_results), 100)

    # Search for tickets
    ticket_ids = await run_sync(client.search_tickets, query)

    if not ticket_ids:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text", text="No tickets found matching query."
                )
            ],
            structuredContent={
                "tickets": [],
                "total": 0,
                "showing": 0,
                "failed_ids": [],
            },
        )

    # Limit results
    total = len(ticket_ids)
    ticket_ids = ticket_ids[:max_results]

    # Fetch basic info for each ticket in parallel (bounded by semaphore)
    failed_ids: list[int] = []

    async def _fetch_ticket(tid: int) -> dict[str, Any] | None:
        """Fetch a single ticket, returning None on failure."""
        try:
            ticket_data = await run_sync_limited(client.get_ticket, tid)
            attrs = ticket_data[
                3
            ]  # [id, created, modified, attributes]
            return {
                "id": tid,
                "summary": attrs.get("summary", ""),
                "status": attrs.get("status", ""),
                "owner": attrs.get("owner", ""),
            }
        except Exception:
            failed_ids.append(tid)
            return None

    fetched = await gather_limited(
        [_fetch_ticket(tid) for tid in ticket_ids]
    )

    results = []
    tickets_json = []
    for item in fetched:
        if item is None:
            continue
        results.append(
            f"- #{item['id']}: {item['summary']} (status: {item['status']}, owner: {item['owner']})"
        )
        tickets_json.append(item)

    # Format response
    header = f"Found {total} tickets"
    if total > max_results:
        header += f" (showing {max_results})"
    header += ":"

    response_text = header + "\n" + "\n".join(results)
    if total > max_results:
        response_text += "\n\nUse max_results to see more."
    if failed_ids:
        failed_list = ", ".join(f"#{tid}" for tid in failed_ids)
        response_text += (
            f"\n\nWarning: failed to load {len(failed_ids)} "
            f"ticket(s) that matched the search: {failed_list}. "
            "The counts above do not include them; retry the search "
            "to try loading them again."
        )

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=response_text)],
        structuredContent={
            "tickets": tickets_json,
            "total": total,
            "showing": len(tickets_json),
            "failed_ids": failed_ids,
        },
    )


async def _handle_get(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_get."""
    ticket_id = args.get("ticket_id")
    if not ticket_id:
        return build_error_response(
            "validation_error",
            "ticket_id is required",
            "Provide ticket_id parameter.",
        )

    raw = args.get("raw", False)

    # Get ticket data
    ticket_data = await run_sync(client.get_ticket, ticket_id)

    # Parse response: [id, created, modified, attributes]
    if not isinstance(ticket_data, list) or len(ticket_data) < 4:
        return build_error_response(
            "server_error",
            "Invalid ticket data format",
            "Contact Trac administrator.",
        )

    ticket_id_resp = ticket_data[0]
    created = ticket_data[1]
    modified = ticket_data[2]
    attrs = ticket_data[3]

    # Extract fields
    summary = attrs.get("summary", "")
    description = attrs.get("description", "")
    status = attrs.get("status", "")
    owner = attrs.get("owner", "")
    reporter = attrs.get("reporter", "")
    ticket_type = attrs.get("type", "")
    priority = attrs.get("priority", "")
    component = attrs.get("component", "")
    milestone = attrs.get("milestone", "")
    keywords = attrs.get("keywords", "")
    cc = attrs.get("cc", "")
    resolution = attrs.get("resolution", "")
    change_token = attrs.get("_ts")

    # Convert description from TracWiki to Markdown unless raw mode is requested
    if raw:
        description_output = description
    else:
        conversion_result = tracwiki_to_markdown(description)
        description_output = conversion_result.text

    # Format timestamps
    created_str = format_timestamp(created)
    modified_str = format_timestamp(modified)

    # Build response
    format_note = " (TracWiki)" if raw else ""
    response_lines = [
        f"Ticket #{ticket_id_resp}: {summary}",
        f"Status: {status} | Owner: {owner} | Reporter: {reporter} | Type: {ticket_type}",
        f"Priority: {priority} | Component: {component} | Milestone: {milestone}",
        f"Keywords: {keywords} | Cc: {cc}"
        + (f" | Resolution: {resolution}" if resolution else ""),
        f"Created: {created_str} | Modified: {modified_str}",
        f"Change token (base_ts): {change_token}"
        " -- pass this to ticket_update's base_ts to detect if the "
        "ticket changes before your write lands.",
        "",
        f"## Description{format_note}",
        description_output,
    ]

    # Build structured JSON (use json.dumps with default=str for datetime serialization)
    ticket_json = {
        "id": ticket_id_resp,
        "summary": summary,
        "description": description_output,
        "status": status,
        "owner": owner,
        "reporter": reporter,
        "type": ticket_type,
        "priority": priority,
        "component": component,
        "milestone": milestone,
        "keywords": keywords,
        "cc": cc,
        "resolution": resolution,
        "created": created_str,
        "modified": modified_str,
        "_ts": change_token,
    }

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent=ticket_json,
    )


async def _handle_changelog(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_changelog."""
    ticket_id = args.get("ticket_id")
    if not ticket_id:
        return build_error_response(
            "validation_error",
            "ticket_id is required",
            "Provide ticket_id parameter.",
        )

    raw = args.get("raw", False)

    # Get changelog
    changelog = await run_sync(client.get_ticket_changelog, ticket_id)

    if not changelog:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=f"No changelog entries for ticket #{ticket_id}",
                )
            ],
            structuredContent={"changelog": [], "ticket_id": ticket_id},
        )

    # Format entries
    # Changelog format: [[timestamp, author, field, oldvalue, newvalue, permanent], ...]
    entries = []
    changelog_json = []
    for entry in changelog:
        timestamp = entry[0]
        author = entry[1]
        field = entry[2]
        oldvalue = entry[3]
        newvalue = entry[4]

        timestamp_str = format_timestamp(timestamp)

        if field == "comment":
            # Comment content is in newvalue (TracWiki format)
            if newvalue:
                # Convert comment content unless raw mode is requested
                if raw:
                    comment_text = newvalue
                else:
                    conversion_result = tracwiki_to_markdown(newvalue)
                    comment_text = conversion_result.text
                # Indent multiline comments for readability
                comment_lines = comment_text.strip().split("\n")
                if len(comment_lines) > 1:
                    indented_comment = "\n    ".join(comment_lines)
                    entries.append(
                        f"- {timestamp_str} by {author}: comment:\n    {indented_comment}"
                    )
                else:
                    entries.append(
                        f"- {timestamp_str} by {author}: comment: {comment_lines[0]}"
                    )
                newvalue_text = comment_text
            else:
                entries.append(
                    f"- {timestamp_str} by {author}: comment added"
                )
                newvalue_text = ""
        else:
            newvalue_text = newvalue
            if oldvalue and newvalue:
                entries.append(
                    f"- {timestamp_str} by {author}: {field} changed from '{oldvalue}' to '{newvalue}'"
                )
            elif newvalue:
                entries.append(
                    f"- {timestamp_str} by {author}: {field} set to '{newvalue}'"
                )
            elif oldvalue:
                entries.append(
                    f"- {timestamp_str} by {author}: {field} removed (was '{oldvalue}')"
                )
            else:
                entries.append(
                    f"- {timestamp_str} by {author}: {field} modified"
                )

        changelog_json.append(
            {
                "timestamp": timestamp_str,
                "author": author,
                "field": field,
                "oldvalue": oldvalue,
                "newvalue": newvalue_text,
            }
        )

    format_note = " (TracWiki format)" if raw else ""
    response_text = (
        f"Changelog for ticket #{ticket_id}{format_note}:\n"
        + "\n".join(entries)
    )

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=response_text)],
        structuredContent={
            "changelog": changelog_json,
            "ticket_id": ticket_id,
        },
    )


async def _handle_fields(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_fields."""
    # Get field metadata
    fields = await run_sync(client.get_ticket_fields)

    if not fields:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text", text="No ticket fields found."
                )
            ],
            structuredContent={"fields": []},
        )

    # Separate standard and custom fields for text display
    standard_fields = []
    custom_fields = []
    fields_json = []

    for field in fields:
        name = field.get("name", "")
        field_type = field.get("type", "")
        label = field.get("label", "")
        options = field.get("options", [])
        custom = field.get("custom", False)

        # Build JSON object
        field_json = {
            "name": name,
            "type": field_type,
            "label": label,
            "custom": custom,
        }
        if options:
            field_json["options"] = options
        fields_json.append(field_json)

        # Format field entry for text
        if field_type == "select" and options:
            field_str = f"- {name} ({field_type}): {label} [{', '.join(options)}]"
        else:
            field_str = f"- {name} ({field_type}): {label}"

        if custom:
            custom_fields.append(field_str)
        else:
            standard_fields.append(field_str)

    # Build response
    response_lines = [f"Ticket Fields ({len(fields)} total):", ""]

    if standard_fields:
        response_lines.append("Standard Fields:")
        response_lines.extend(standard_fields)
        response_lines.append("")

    if custom_fields:
        response_lines.append("Custom Fields:")
        response_lines.extend(custom_fields)

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent={"fields": fields_json},
    )


async def _handle_actions(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_actions."""
    ticket_id = args.get("ticket_id")

    if not ticket_id:
        raise ValueError("ticket_id is required")

    # Get available actions for ticket
    try:
        actions = await run_sync(client.get_ticket_actions, ticket_id)
    except xmlrpc.client.Fault as e:
        # If getActions is not available, provide helpful error
        if (
            "not found" in str(e).lower()
            or "no such method" in str(e).lower()
        ):
            return build_error_response(
                "method_not_available",
                "ticket.getActions() not available on this Trac instance",
                "This Trac instance may not support workflow introspection via XML-RPC. Check Trac version and enabled components.",
            )
        raise

    if not actions:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=f"No available actions for ticket #{ticket_id}.",
                )
            ],
            structuredContent={"actions": []},
        )

    # Format actions list
    response_lines = [f"Available actions for ticket #{ticket_id}:", ""]
    actions_json = []

    for action in actions:
        # Action tuple format: [action_name, label, hints, input_fields]
        if isinstance(action, (list, tuple)) and len(action) >= 2:
            action_name = action[0]
            label = action[1]
            hints = action[2] if len(action) > 2 else []
            input_fields = action[3] if len(action) > 3 else []

            # Build JSON object
            action_json: dict[str, Any] = {
                "name": action_name,
                "label": label,
            }
            if hints:
                action_json["hints"] = (
                    hints if isinstance(hints, dict) else {}
                )
            if input_fields:
                action_json["input_fields"] = (
                    input_fields
                    if isinstance(input_fields, list)
                    else []
                )
            actions_json.append(action_json)

            # Format basic action line
            action_line = f"- {action_name}: {label}"

            # Add hints if available (status transitions, etc.)
            if hints and isinstance(hints, list):
                hint_text = ", ".join(str(h) for h in hints)
                action_line += f" ({hint_text})"

            # Add required input fields if any
            if input_fields and isinstance(input_fields, list):
                fields_text = ", ".join(str(f) for f in input_fields)
                action_line += f" [requires: {fields_text}]"

            response_lines.append(action_line)
        else:
            # Fallback for unexpected format
            response_lines.append(f"- {str(action)}")
            actions_json.append({"raw": str(action)})

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent={"actions": actions_json},
    )


# ToolSpec list for registry-based dispatch
TICKET_READ_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=TICKET_READ_TOOLS[0],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_search,
    ),
    ToolSpec(
        tool=TICKET_READ_TOOLS[1],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_get,
    ),
    ToolSpec(
        tool=TICKET_READ_TOOLS[2],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_changelog,
    ),
    ToolSpec(
        tool=TICKET_READ_TOOLS[3],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_fields,
    ),
    ToolSpec(
        tool=TICKET_READ_TOOLS[4],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_actions,
    ),
]
