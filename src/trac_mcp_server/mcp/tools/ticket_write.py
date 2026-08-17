"""Write ticket tool handlers for MCP server.

This module implements ticket write operations: create, update, and delete.
All tools use async handlers with run_sync() to bridge synchronous TracClient calls,
automatic Markdown conversion, and structured error responses.
"""

import xmlrpc.client
from typing import Any

import mcp.types as types

from ...converters import markdown_to_tracwiki
from ...core.async_utils import run_sync
from ...core.client import TicketCreateTimeout, TracClient
from .constants import DEFAULT_TICKET_TYPE, TICKET_TYPE_LIST
from .errors import build_error_response
from .registry import ToolSpec


def _build_ticket_create_tool() -> types.Tool:
    """Build ticket_create tool definition with hardcoded defaults."""
    default_type = DEFAULT_TICKET_TYPE
    type_list = TICKET_TYPE_LIST
    return types.Tool(
        name="ticket_create",
        description="Create a new ticket. Accepts Markdown for description (auto-converted to TracWiki).",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Ticket title (required)",
                },
                "description": {
                    "type": "string",
                    "description": "Ticket body in Markdown (will be converted to TracWiki)",
                },
                "ticket_type": {
                    "type": "string",
                    "description": f"Ticket type (default: {default_type}). Available types: {type_list}.",
                    "default": default_type,
                },
                "priority": {
                    "type": "string",
                    "description": "Priority level",
                },
                "severity": {
                    "type": "string",
                    "description": "Severity level",
                },
                "component": {
                    "type": "string",
                    "description": "Component name",
                },
                "milestone": {
                    "type": "string",
                    "description": "Target milestone",
                },
                "owner": {
                    "type": "string",
                    "description": "Assignee username",
                },
                "cc": {
                    "type": "string",
                    "description": "CC email addresses",
                },
                "keywords": {
                    "type": "string",
                    "description": "Keywords/tags",
                },
            },
            "required": ["summary", "description"],
        },
    )


# Tool definitions for list_tools()
TICKET_WRITE_TOOLS = [
    _build_ticket_create_tool(),
    types.Tool(
        name="ticket_update",
        description="Update ticket attributes and/or add comments. Uses optimistic locking to prevent conflicts. Accepts Markdown for comments.",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "integer",
                    "description": "Ticket number to update",
                    "minimum": 1,
                },
                "comment": {
                    "type": "string",
                    "description": "Comment in Markdown (optional, max 10000 chars)",
                },
                "summary": {
                    "type": "string",
                    "description": "New summary (ticket title)",
                },
                "description": {
                    "type": "string",
                    "description": "New description in Markdown (replaces ticket body)",
                },
                "type": {
                    "type": "string",
                    "description": "New ticket type (e.g. defect, enhancement, task)",
                },
                "status": {
                    "type": "string",
                    "description": "New status. Note: Trac workflow gates direct status writes; prefer 'action' for transitions (e.g. action='accept' to move new->accepted).",
                },
                "action": {
                    "type": "string",
                    "description": "Trac workflow action to perform (e.g. 'accept', 'resolve', 'reopen', 'reassign'). The canonical way to transition a ticket through its workflow. Action-specific input fields are passed via 'action_<action>_<action>_<field>' keys (e.g. action_resolve_resolve_resolution='fixed').",
                },
                "priority": {
                    "type": "string",
                    "description": "New priority",
                },
                "severity": {
                    "type": "string",
                    "description": "New severity",
                },
                "component": {
                    "type": "string",
                    "description": "New component",
                },
                "milestone": {
                    "type": "string",
                    "description": "New milestone",
                },
                "owner": {"type": "string", "description": "New owner"},
                "resolution": {
                    "type": "string",
                    "description": "Resolution (when closing)",
                },
                "cc": {
                    "type": "string",
                    "description": "CC email addresses",
                },
                "keywords": {
                    "type": "string",
                    "description": "Keywords/tags",
                },
            },
            "required": ["ticket_id"],
        },
    ),
    types.Tool(
        name="ticket_delete",
        description="Delete a ticket permanently. Warning: This cannot be undone. Requires TICKET_ADMIN permission and 'tracopt.ticket.deleter' enabled in trac.ini.",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "integer",
                    "description": "Ticket number to delete",
                    "minimum": 1,
                }
            },
            "required": ["ticket_id"],
        },
    ),
]


async def _handle_create(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_create."""
    summary = args.get("summary")
    description = args.get("description")

    if not summary:
        return build_error_response(
            "validation_error",
            "summary is required",
            "Provide summary parameter.",
        )
    if not description:
        return build_error_response(
            "validation_error",
            "description is required",
            "Provide description parameter.",
        )

    # Convert description from Markdown to TracWiki
    description_tracwiki = markdown_to_tracwiki(description)

    # Build attributes (hardcoded default for standalone server)
    ticket_type = args.get("ticket_type", DEFAULT_TICKET_TYPE)
    attributes: dict[str, Any] = {}

    # Add optional fields if provided
    if "priority" in args:
        attributes["priority"] = args["priority"]
    if "severity" in args:
        attributes["severity"] = args["severity"]
    if "component" in args:
        attributes["component"] = args["component"]
    if "milestone" in args:
        attributes["milestone"] = args["milestone"]
    if "owner" in args:
        attributes["owner"] = args["owner"]
    if "cc" in args:
        attributes["cc"] = args["cc"]
    if "keywords" in args:
        attributes["keywords"] = args["keywords"]

    # Create ticket
    try:
        ticket_id = await run_sync(
            client.create_ticket,
            summary,
            description_tracwiki,
            ticket_type,
            attributes,
        )
    except TicketCreateTimeout as e:
        # The create timed out but we know what happened to it. The generic
        # handler would say "retry later", which is exactly wrong when the
        # ticket already landed, so answer specifically.
        if e.ticket_id is not None:
            return build_error_response(
                "timeout_ticket_created",
                str(e),
                f"Do NOT retry -- that would create a duplicate. "
                f"Use ticket_get(ticket_id={e.ticket_id}) to confirm the "
                f"ticket, and ticket_update to correct it if needed.",
            )
        return build_error_response(
            "timeout_not_created",
            str(e),
            "Retrying ticket_create is most likely safe. To be certain "
            "first, use ticket_search to check whether a ticket with this "
            "summary already exists.",
        )

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=f"Created ticket #{ticket_id}: {summary}",
            )
        ]
    )


async def _handle_update(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_update."""
    ticket_id = args.get("ticket_id")
    if not ticket_id:
        return build_error_response(
            "validation_error",
            "ticket_id is required",
            "Provide ticket_id parameter.",
        )

    # Convert comment from Markdown to TracWiki if provided
    comment = args.get("comment", "")
    if comment:
        comment = markdown_to_tracwiki(comment)

    # Build attributes dict (skip None values)
    attributes: dict[str, Any] = {}

    if "status" in args:
        attributes["status"] = args["status"]
    if "priority" in args:
        attributes["priority"] = args["priority"]
    if "severity" in args:
        attributes["severity"] = args["severity"]
    if "component" in args:
        attributes["component"] = args["component"]
    if "milestone" in args:
        attributes["milestone"] = args["milestone"]
    if "owner" in args:
        attributes["owner"] = args["owner"]
    if "resolution" in args:
        attributes["resolution"] = args["resolution"]
    if "cc" in args:
        attributes["cc"] = args["cc"]
    if "keywords" in args:
        attributes["keywords"] = args["keywords"]
    if "summary" in args:
        attributes["summary"] = args["summary"]
    if "type" in args:
        attributes["type"] = args["type"]
    # Description rewrite: convert from Markdown to TracWiki, mirroring
    # the comment + create-ticket-description handling.
    if "description" in args:
        attributes["description"] = markdown_to_tracwiki(
            args["description"]
        )
    # Workflow action: trigger a Trac workflow transition (e.g. accept,
    # resolve, reopen). Action-specific input fields follow Trac's
    # ``action_<action>_<action>_<field>`` convention (e.g.
    # ``action_resolve_resolve_resolution``) and are forwarded by pattern.
    if "action" in args:
        attributes["action"] = args["action"]
    for key, value in args.items():
        if key.startswith("action_"):
            attributes[key] = value

    # A plain `resolution` and a workflow `action` silently conflict:
    # ConfigurableTicketWorkflow sets `resolution` from the action's own
    # input field (e.g. `action_resolve_resolve_resolution`), and that
    # assignment wins over the bare `resolution` attribute -- even though
    # `resolution` is the documented, discoverable parameter. The call
    # still reports success with the ticket closed but unresolved
    # (ticket #32). If the caller already supplied the action's own
    # field explicitly, leave it alone -- it already wins on the wire,
    # so remapping would just overwrite an intentional value. Otherwise
    # look up the action's real input fields via ticket_actions (rather
    # than guessing the "resolve" name) and remap the bare `resolution`
    # onto whichever field ends in "resolution", so the transition and
    # the resolution land in one call.
    action = args.get("action")
    if action and "resolution" in attributes:
        action_prefix = f"action_{action}_"
        already_explicit = any(
            key.startswith(action_prefix) and key.endswith("resolution")
            for key in attributes
        )
        if not already_explicit:
            try:
                actions = await run_sync(
                    client.get_ticket_actions, ticket_id
                )
            except Exception:
                actions = []
            for entry in actions or []:
                if not isinstance(entry, (list, tuple)) or not entry:
                    continue
                if entry[0] != action:
                    continue
                input_fields = entry[3] if len(entry) > 3 else []
                for field in input_fields or []:
                    if str(field).endswith("resolution"):
                        attributes[f"action_{action}_{field}"] = (
                            attributes.pop("resolution")
                        )
                        break
                break

    # Update ticket (client handles optimistic locking)
    await run_sync(client.update_ticket, ticket_id, comment, attributes)

    # Build summary of changes
    changes = []
    if comment:
        changes.append("added comment")
    if attributes:
        changes.append(f"updated {len(attributes)} field(s)")

    change_summary = ", ".join(changes) if changes else "no changes"

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=f"Updated ticket #{ticket_id} ({change_summary})",
            )
        ]
    )


async def _handle_delete(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_delete."""
    ticket_id = args.get("ticket_id")
    if not ticket_id:
        return build_error_response(
            "validation_error",
            "ticket_id is required",
            "Provide ticket_id parameter.",
        )

    # Verify ticket exists before attempting deletion
    await run_sync(client.get_ticket, ticket_id)

    # Delete the ticket
    try:
        await run_sync(client.delete_ticket, ticket_id)
    except xmlrpc.client.Fault as e:
        # Provide specific guidance for permission errors
        if (
            "permission" in e.faultString.lower()
            or "denied" in e.faultString.lower()
        ):
            return build_error_response(
                "permission_denied",
                e.faultString,
                "This tool requires TICKET_ADMIN permission and 'tracopt.ticket.deleter' enabled in trac.ini. Contact Trac administrator.",
            )
        raise

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text=f"Deleted ticket #{ticket_id}."
            )
        ]
    )


# ToolSpec list for registry-based dispatch
TICKET_WRITE_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=TICKET_WRITE_TOOLS[0],
        permissions=frozenset({"TICKET_CREATE"}),
        handler=_handle_create,
    ),
    ToolSpec(
        tool=TICKET_WRITE_TOOLS[1],
        permissions=frozenset({"TICKET_MODIFY"}),
        handler=_handle_update,
    ),
    ToolSpec(
        tool=TICKET_WRITE_TOOLS[2],
        permissions=frozenset({"TICKET_ADMIN"}),
        handler=_handle_delete,
    ),
]
