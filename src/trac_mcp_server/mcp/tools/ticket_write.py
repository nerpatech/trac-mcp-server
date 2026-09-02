"""Write ticket tool handlers for MCP server.

This module implements ticket write operations: create, update, and delete.
All tools use async handlers with run_sync() to bridge synchronous TracClient calls,
and structured error responses. Content is TracWiki and is stored
byte-for-byte -- no converter sits in this path (ticket #69).
"""

import xmlrpc.client
from typing import Any

import mcp.types as types

from ...core.async_utils import run_sync
from ...core.client import (
    TicketCreateTimeout,
    TicketUpdateConflict,
    TracClient,
)
from .constants import DEFAULT_TICKET_TYPE, TICKET_TYPE_LIST
from .errors import build_error_response
from .registry import ToolSpec
from .source_format import reject_removed_conversion_args


def _build_ticket_create_tool() -> types.Tool:
    """Build ticket_create tool definition with hardcoded defaults."""
    default_type = DEFAULT_TICKET_TYPE
    type_list = TICKET_TYPE_LIST
    return types.Tool(
        name="ticket_create",
        description="Create a new ticket. The description is TracWiki and is stored byte-for-byte -- nothing is converted, so hand-authored markup survives exactly as written.",
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
                    "description": "Ticket body (required). TracWiki, stored verbatim.",
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
        description="Update ticket attributes and/or add comments. Comment and description are TracWiki and are stored byte-for-byte -- nothing is converted, so hand-authored markup survives exactly as written. Pass base_ts (the change token returned by ticket_get) to enable optimistic locking: the update is rejected with a version_conflict error, naming what changed, if the ticket was modified since that token was read. Omitting base_ts skips conflict detection entirely -- the write always succeeds even if the ticket changed underneath you, so always read the ticket first and pass its base_ts back. Set reply_to to quote an earlier comment (Trac's XML-RPC API has no comment edit/delete methods on this host, so existing comments can't be edited or deleted through this tool).",
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
                    "description": "Comment body (optional, max 10000 chars). TracWiki, stored verbatim.",
                },
                "summary": {
                    "type": "string",
                    "description": "New summary (ticket title)",
                },
                "description": {
                    "type": "string",
                    "description": "New description, replacing the ticket body. TracWiki, stored verbatim.",
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
                "reply_to": {
                    "type": "integer",
                    "description": "Comment number to reply to. Prepends Trac's standard \"Replying to [comment:N author]:\" quote block (quoting that comment's own text) before the new comment. Requires 'comment' to also be provided.",
                    "minimum": 1,
                },
                "base_ts": {
                    "type": "string",
                    "description": "Change token from a prior ticket_get() call's _ts field (a numeric string -- pass it through as-is, don't parse it as a number). When provided, the update is rejected with a version_conflict error (naming what changed) if the ticket was modified since that token was read. Strongly recommended -- without it, this write silently overwrites any concurrent change.",
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

    format_error = reject_removed_conversion_args(args)
    if format_error is not None:
        return format_error

    # Stored as written. There is no conversion step here any more
    # (#69) -- the author's bytes are the bytes that land.
    description_tracwiki = description

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

    format_error = reject_removed_conversion_args(args)
    if format_error is not None:
        return format_error

    # The comment and the description rewrite below are both stored as
    # written (#69). They used to convert at two separate call sites,
    # which is why they are still worth testing separately.
    comment = args.get("comment", "")

    # Reply-to: prepend Trac's standard "Replying to [comment:N author]:"
    # quote block before the new comment. Trac's XML-RPC API on this host
    # has no ticket.editComment/deleteComment methods (verified via
    # system.listMethods) -- existing comments can only be edited/deleted
    # via the Trac web UI, so this tool only ever adds new comments.
    reply_to = args.get("reply_to")
    if reply_to is not None:
        if not comment:
            return build_error_response(
                "validation_error",
                "comment is required when reply_to is set",
                "Provide a comment parameter along with reply_to.",
            )
        changelog = await run_sync(
            client.get_ticket_changelog, ticket_id
        )
        quoted_author = None
        quoted_text = None
        for entry in changelog or []:
            if entry[2] == "comment" and str(entry[3]) == str(reply_to):
                quoted_author = entry[1]
                quoted_text = entry[4]
        if quoted_text is None:
            return build_error_response(
                "not_found",
                f"Comment #{reply_to} not found on ticket #{ticket_id}",
                "Use ticket_changelog to see valid comment numbers.",
            )
        quote_lines = "\n".join(
            f"> {line}" for line in quoted_text.strip().split("\n")
        )
        comment = (
            f"Replying to [comment:{reply_to} {quoted_author}]:\n"
            f"{quote_lines}\n\n{comment}"
        )

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
    # Description rewrite: stored as written, mirroring the comment and
    # the create-ticket description (#69).
    if "description" in args:
        attributes["description"] = args["description"]
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
    resolution_remap_warning = None
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
            remapped = False
            for entry in actions or []:
                if not isinstance(entry, (list, tuple)) or not entry:
                    continue
                if entry[0] != action:
                    continue
                input_fields = entry[3] if len(entry) > 3 else []
                # Each entry in input_fields is [name, default, options],
                # not a field name -- read the name out rather than
                # stringifying the whole list (ticket #49). The name is
                # already fully qualified (e.g.
                # "action_resolve_resolve_resolution"), so it is used
                # as-is rather than re-prefixed.
                for field in input_fields or []:
                    name = (
                        field[0]
                        if isinstance(field, (list, tuple)) and field
                        else field
                    )
                    if str(name).endswith("resolution"):
                        attributes[str(name)] = attributes.pop(
                            "resolution"
                        )
                        remapped = True
                        break
                break
            if not remapped and "resolution" in attributes:
                resolution_remap_warning = (
                    f"WARNING: could not find an input field for "
                    f"action '{action}' ending in 'resolution' -- the "
                    f"plain 'resolution' attribute was sent as-is and "
                    f"may be silently overwritten by the workflow "
                    f"action."
                )

    # Update ticket. base_ts, when supplied, is forwarded verbatim as
    # Trac's optimistic-lock token -- see TracClient.update_ticket (#50).
    base_ts = args.get("base_ts")
    try:
        await run_sync(
            client.update_ticket,
            ticket_id,
            comment,
            attributes,
            False,
            base_ts,
        )
    except TicketUpdateConflict as e:
        if e.changes:
            what_changed = "; ".join(
                f"{c['field']} by {c['author']} at {c['timestamp']}"
                for c in e.changes
            )
        else:
            what_changed = "unable to determine the exact changes"
        return build_error_response(
            "version_conflict",
            f"Ticket #{ticket_id} was modified since base_ts={base_ts}: "
            f"{what_changed}",
            f"Re-read with ticket_get(ticket_id={ticket_id}) and "
            f"ticket_changelog(ticket_id={ticket_id}) to see the current "
            f"state, merge your update with it, then retry ticket_update "
            f"with the fresh base_ts from that ticket_get response.",
        )

    # Build summary of changes
    changes = []
    if comment:
        changes.append("added comment")
    if attributes:
        changes.append(f"updated {len(attributes)} field(s)")

    change_summary = ", ".join(changes) if changes else "no changes"
    if resolution_remap_warning:
        change_summary = f"{change_summary}; {resolution_remap_warning}"

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
