"""Read-only ticket tool handlers for MCP server.

This module implements ticket read operations: search, get, and changelog.
All tools use async handlers with run_sync() to bridge synchronous TracClient calls,
automatic Markdown conversion, and structured error responses.
"""

import xmlrpc.client
from typing import Any

import mcp.types as types

from ...core.async_utils import (
    gather_limited,
    run_sync,
    run_sync_limited,
)
from ...core.client import TracClient
from .errors import build_error_response, format_timestamp
from .registry import ToolSpec
from .source_format import reject_removed_conversion_args

# Default cap on comment bodies returned by ticket_get. A thread longer
# than this keeps its head and tail and reports the omitted middle loudly --
# a silent truncation would recreate the bug ticket #60 is about.
DEFAULT_MAX_COMMENTS = 50

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
        description="Get full ticket details: summary, description, field values, and -- by default -- every comment with its number, author and timestamp, so one call covers the whole ticket. Set include_comments=false when you only need field values or the _ts change token before a write. Response includes a change token (_ts) -- pass it to ticket_update's base_ts to detect if the ticket changes before your write lands. Use ticket_changelog for full field-change history. The description and comment bodies are returned as stored: TracWiki, byte-for-byte.",
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
                "include_comments": {
                    "type": "boolean",
                    "description": "If true (the default), also return the ticket's comments -- number, author, timestamp and body. Set false only when you do not need them, e.g. fetching _ts before a write.",
                    "default": True,
                },
                "max_comments": {
                    "type": "integer",
                    "description": "Maximum comment bodies to return (default: 50). A longer thread keeps its oldest and newest comments, drops the middle, and says so explicitly, naming the omitted comment numbers; the reported total is always exact. Ignored when include_comments is false.",
                    "default": DEFAULT_MAX_COMMENTS,
                    "minimum": 1,
                },
            },
            "required": ["ticket_id"],
        },
    ),
    types.Tool(
        name="ticket_changelog",
        description="Get ticket change history. Use this to investigate who changed what and when. Comment content is returned as stored: TracWiki, byte-for-byte.",
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


def _extract_comments(changelog: list) -> list[dict[str, Any]]:
    """Pull just the comments out of a raw changelog.

    Field-only changelog entries (a status change, a description edit)
    are dropped here, before anything is formatted -- so a description
    edit's two full copies of the description never reach the response.

    Args:
        changelog: Raw changelog rows, [timestamp, author, field,
            oldvalue, newvalue, permanent].

    Returns:
        One dict per comment carrying number, author, timestamp and body,
        in changelog order. Trac stores a comment's number in the entry's
        ``oldvalue`` -- the same key ``ticket_render_check``'s ``comment``
        parameter is keyed on -- so the number reported here is the one
        that tool accepts. An entry without one yields ``number: None``
        rather than a fabricated ordinal.
    """
    comments: list[dict[str, Any]] = []
    for entry in changelog:
        if not isinstance(entry, (list, tuple)) or len(entry) < 5:
            continue
        timestamp, author, field, oldvalue, newvalue = entry[:5]
        if field != "comment" or not newvalue:
            continue

        body = newvalue
        number = str(oldvalue).strip() if oldvalue else ""
        comments.append(
            {
                "number": number or None,
                "author": author,
                "timestamp": format_timestamp(timestamp),
                "body": body,
            }
        )
    return comments


def _cap_comments(
    comments: list[dict[str, Any]], max_comments: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split comments into the ones kept and the ones dropped.

    Keeps the head and the tail, drops the middle: a long thread usually
    has its scope set in the earliest comments and corrected in the
    latest, and both ends matter to a reader deciding what to do next.

    Args:
        comments: All comments, in order.
        max_comments: Maximum number to keep (at least 1).

    Returns:
        ``(kept, omitted)``. ``omitted`` is empty when the thread fits.
    """
    max_comments = max(1, max_comments)
    if len(comments) <= max_comments:
        return comments, []

    head = (max_comments + 1) // 2
    tail = max_comments - head
    tail_start = len(comments) - tail if tail else len(comments)
    return (
        comments[:head] + comments[tail_start:],
        comments[head:tail_start],
    )


def _comment_ref(comment: dict[str, Any]) -> str:
    """Render a comment's number as a reference, or flag its absence."""
    number = comment.get("number")
    return (
        f"comment:{number}"
        if number
        else "comment (number unavailable)"
    )


def _omitted_notice(omitted: list[dict[str, Any]]) -> str:
    """Build the loud notice naming the comments that were dropped."""
    first = _comment_ref(omitted[0])
    last = _comment_ref(omitted[-1])
    return (
        f"*** {len(omitted)} comment(s) omitted from the middle of this "
        f"thread ({first} through {last}). This response is NOT the full "
        "comment history -- read the omitted comments with "
        "ticket_changelog, or raise max_comments. ***"
    )


def _format_comment_section(
    comments: list[dict[str, Any]],
    omitted: list[dict[str, Any]],
    total: int,
) -> list[str]:
    """Format the comments block of ticket_get's text response.

    The omitted-middle notice is deliberately loud: a caller who believes
    they read the ticket, having read half of it, is the failure this
    whole feature exists to prevent.

    Args:
        comments: The comments actually being shown, in order.
        omitted: The dropped middle, empty when nothing was dropped.
        total: Exact number of comments on the ticket, dropped included.

    Returns:
        Response lines, starting with a blank separator line.
    """
    if not total:
        return ["", "## Comments", "(none)"]

    heading = f"## Comments ({total} total"
    if omitted:
        heading += f", {len(comments)} shown, {len(omitted)} omitted"
    heading += ")"
    lines = ["", heading]

    # _cap_comments keeps ceil(max/2) at the head, so the notice belongs
    # at that index -- or after everything, when the tail half is empty.
    head_count = (len(comments) + 1) // 2 if omitted else len(comments)
    for index, comment in enumerate(comments):
        if omitted and index == head_count:
            lines.extend(["", _omitted_notice(omitted)])
        lines.extend(
            [
                "",
                f"### {_comment_ref(comment)} -- {comment['author']}, "
                f"{comment['timestamp']}",
                comment["body"],
            ]
        )
    if omitted and head_count >= len(comments):
        lines.extend(["", _omitted_notice(omitted)])
    return lines


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

    format_error = reject_removed_conversion_args(args)
    if format_error is not None:
        return format_error

    include_comments = args.get("include_comments", True)
    max_comments = args.get("max_comments", DEFAULT_MAX_COMMENTS)

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

    # Returned as stored. Nothing converts on this leg any more, so a
    # read-edit-write round trip is byte-exact (ticket #69).
    description_output = description

    # Format timestamps
    created_str = format_timestamp(created)
    modified_str = format_timestamp(modified)

    # Comments live in the changelog, not in the ticket attributes.
    # Fetching them by default is what makes one ticket_get the whole
    # ticket; a failure here degrades loudly rather than silently
    # dropping them (ticket #60).
    comments: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    comments_error: str | None = None
    if include_comments:
        try:
            changelog = await run_sync(
                client.get_ticket_changelog, ticket_id
            )
        except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
            comments_error = str(exc)
        else:
            comments, omitted = _cap_comments(
                _extract_comments(changelog or []), max_comments
            )
    comment_total = len(comments) + len(omitted)

    # Build response
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
        "## Description",
        description_output,
    ]

    if comments_error is not None:
        response_lines.extend(
            [
                "",
                "## Comments",
                "*** Comments could not be fetched, so this is NOT the "
                f"whole ticket: {comments_error}. Retry, or read them "
                "with ticket_changelog. ***",
            ]
        )
    elif include_comments:
        response_lines.extend(
            _format_comment_section(comments, omitted, comment_total)
        )
    else:
        response_lines.extend(
            [
                "",
                "## Comments",
                "(not fetched: include_comments=false -- this response "
                "is field values only, not the whole ticket)",
            ]
        )

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
        "comments_included": include_comments
        and comments_error is None,
    }

    if comments_error is not None:
        ticket_json["comments_error"] = comments_error
    elif include_comments:
        ticket_json["comments"] = comments
        ticket_json["comment_count"] = comment_total
        ticket_json["comments_shown"] = len(comments)
        ticket_json["comments_truncated"] = bool(omitted)
        if omitted:
            ticket_json["omitted_comment_numbers"] = [
                c["number"] for c in omitted
            ]

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

    format_error = reject_removed_conversion_args(args)
    if format_error is not None:
        return format_error

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
            # Comment content is in newvalue, returned as stored (#69)
            if newvalue:
                comment_text = newvalue
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

    response_text = f"Changelog for ticket #{ticket_id}:\n" + "\n".join(
        entries
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
