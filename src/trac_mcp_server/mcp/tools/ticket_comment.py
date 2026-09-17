"""Comment-level ticket tools: edit, delete, reply, history.

All four require the ``tracrpc_comment`` plugin on the Trac server (see
``plugins/`` in this repo). Stock XmlRpcPlugin exposes no comment-level
methods at all, so against a server without it these fail with a
``MethodNotFound`` fault, which is translated here into a message that says
so rather than leaving the caller to guess.

Comments are addressed by ``cnum`` exactly as ``ticket_changelog`` reports it.
Never infer one by counting rows: description edits consume comment numbers
without producing a comment, and deletes never renumber, so gaps are normal.
"""

import xmlrpc.client

import mcp.types as types

from ...core.async_utils import run_sync
from ...core.client import TracClient
from .errors import build_error_response
from .registry import ToolSpec

# Raised by Trac's RPC dispatcher when the plugin is not installed/enabled.
_MISSING_METHOD_HINT = (
    "This tool needs the 'tracrpc_comment' plugin on the Trac server. "
    "Install it from the trac-mcp-server repo's plugins/ directory and "
    "enable it with: trac-admin <env> config set components "
    "'tracrpc_comment.*' enabled"
)

_CNUM_SCHEMA = {
    "type": "integer",
    "description": (
        "Comment number as ticket_changelog reports it. Do not count rows -- "
        "numbering has gaps."
    ),
    "minimum": 1,
}

_TICKET_ID_SCHEMA = {
    "type": "integer",
    "description": "Ticket number",
    "minimum": 1,
}


TICKET_COMMENT_TOOLS = [
    types.Tool(
        name="ticket_comment_edit",
        description=(
            "Correct the text of an existing ticket comment. The previous "
            "text is kept as a revision (readable with "
            "ticket_comment_history), so this is a correction, not an "
            "erasure. TracWiki, stored byte-for-byte."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": _TICKET_ID_SCHEMA,
                "cnum": _CNUM_SCHEMA,
                "comment": {
                    "type": "string",
                    "description": "Replacement body. TracWiki, verbatim.",
                },
            },
            "required": ["ticket_id", "cnum", "comment"],
        },
    ),
    types.Tool(
        name="ticket_comment_delete",
        description=(
            "Delete a ticket comment permanently. Cannot be undone. Refuses "
            "when the comment was posted together with field changes, "
            "because Trac would also REVERT those changes -- deleting the "
            "comment that accompanied a close reopens the ticket. Pass "
            "force=true only after reading what the refusal names. Requires "
            "TICKET_ADMIN."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": _TICKET_ID_SCHEMA,
                "cnum": _CNUM_SCHEMA,
                "force": {
                    "type": "boolean",
                    "description": (
                        "Delete even though bundled field changes will be "
                        "reverted. Default false."
                    ),
                    "default": False,
                },
            },
            "required": ["ticket_id", "cnum"],
        },
    ),
    types.Tool(
        name="ticket_comment_reply",
        description=(
            "Post a threaded reply to a ticket comment. Unlike ticket_update, "
            "this records real threading, so the reply nests under its parent "
            "in the web UI. Set quote=true to also prepend the parent's text "
            "as a quote block. TracWiki, stored byte-for-byte."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": _TICKET_ID_SCHEMA,
                "cnum": {
                    **_CNUM_SCHEMA,
                    "description": (
                        "Comment number being replied to, as "
                        "ticket_changelog reports it."
                    ),
                },
                "comment": {
                    "type": "string",
                    "description": "Reply body. TracWiki, verbatim.",
                },
                "quote": {
                    "type": "boolean",
                    "description": (
                        "Prepend the parent comment as a quote block. "
                        "Default false."
                    ),
                    "default": False,
                },
            },
            "required": ["ticket_id", "cnum", "comment"],
        },
    ),
    types.Tool(
        name="ticket_comment_history",
        description=(
            "Read the edit revisions of a ticket comment, oldest first. A "
            "never-edited comment has exactly one revision, so an empty "
            "result means the comment does not exist."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": _TICKET_ID_SCHEMA,
                "cnum": _CNUM_SCHEMA,
            },
            "required": ["ticket_id", "cnum"],
        },
    ),
]


def _require(args: dict, *names: str):
    """Return the named args, or an error result if any is missing."""
    missing = [n for n in names if args.get(n) is None]
    if missing:
        return None, build_error_response(
            "validation_error",
            "%s is required" % ", ".join(missing),
            "Provide %s." % ", ".join(missing),
        )
    return [args.get(n) for n in names], None


def _translate_missing_method(err: xmlrpc.client.Fault):
    """Turn 'no such method' into an actionable message, or re-raise.

    Without this the caller sees a bare MethodNotFound and has no way to know
    the server simply lacks the plugin -- which is the single most likely
    reason any of these tools fails.
    """
    text = err.faultString.lower()
    if "not found" in text and "method" in text:
        return build_error_response(
            "method_not_available",
            err.faultString,
            _MISSING_METHOD_HINT,
        )
    return None


async def _handle_edit(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_comment_edit."""
    values, error = _require(args, "ticket_id", "cnum", "comment")
    if error:
        return error
    ticket_id, cnum, comment = values

    try:
        await run_sync(
            client.edit_ticket_comment, ticket_id, cnum, comment
        )
    except xmlrpc.client.Fault as e:
        translated = _translate_missing_method(e)
        if translated:
            return translated
        raise

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=(
                    f"Edited comment {cnum} on ticket #{ticket_id}. "
                    f"The previous text is kept as a revision -- read it "
                    f"with ticket_comment_history."
                ),
            )
        ]
    )


async def _handle_delete(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_comment_delete."""
    values, error = _require(args, "ticket_id", "cnum")
    if error:
        return error
    ticket_id, cnum = values
    force = bool(args.get("force", False))

    try:
        await run_sync(
            client.delete_ticket_comment, ticket_id, cnum, force
        )
    except xmlrpc.client.Fault as e:
        translated = _translate_missing_method(e)
        if translated:
            return translated
        # The server's guard refusal. Surface it as a structured error with
        # the server's own wording -- it names the fields that would be
        # reverted, which is the whole point of the refusal.
        if "refusing to delete" in e.faultString.lower():
            return build_error_response(
                "bundled_change_refused",
                e.faultString,
                (
                    "Re-read the ticket and confirm the field changes may be "
                    "reverted, then retry with force=true. Prefer editing the "
                    "comment instead if the record should be kept."
                ),
            )
        raise

    suffix = " Bundled field changes were reverted." if force else ""
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=(
                    f"Deleted comment {cnum} on ticket #{ticket_id}."
                    f"{suffix}"
                ),
            )
        ]
    )


async def _handle_reply(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_comment_reply."""
    values, error = _require(args, "ticket_id", "cnum", "comment")
    if error:
        return error
    ticket_id, cnum, comment = values
    quote = bool(args.get("quote", False))

    try:
        new_cnum = await run_sync(
            client.reply_to_ticket_comment,
            ticket_id,
            cnum,
            comment,
            quote,
        )
    except xmlrpc.client.Fault as e:
        translated = _translate_missing_method(e)
        if translated:
            return translated
        raise

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=(
                    f"Posted comment {new_cnum} on ticket #{ticket_id} as a "
                    f"threaded reply to comment {cnum}."
                ),
            )
        ]
    )


async def _handle_history(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_comment_history."""
    values, error = _require(args, "ticket_id", "cnum")
    if error:
        return error
    ticket_id, cnum = values

    try:
        history = await run_sync(
            client.get_ticket_comment_history, ticket_id, cnum
        )
    except xmlrpc.client.Fault as e:
        translated = _translate_missing_method(e)
        if translated:
            return translated
        raise

    if not history:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=(
                        f"No comment {cnum} on ticket #{ticket_id}. Comment "
                        f"numbering has gaps -- check ticket_changelog."
                    ),
                )
            ]
        )

    lines = [
        f"Comment {cnum} on ticket #{ticket_id} -- {len(history)} "
        f"revision(s), oldest first:"
    ]
    for entry in history:
        rev, when, author, body = entry[0], entry[1], entry[2], entry[3]
        lines.append(f"\n--- revision {rev} by {author} at {when} ---")
        lines.append(str(body))

    return types.CallToolResult(
        content=[types.TextContent(type="text", text="\n".join(lines))]
    )


TICKET_COMMENT_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=TICKET_COMMENT_TOOLS[0],
        permissions=frozenset({"TICKET_EDIT_COMMENT"}),
        handler=_handle_edit,
    ),
    ToolSpec(
        tool=TICKET_COMMENT_TOOLS[1],
        permissions=frozenset({"TICKET_ADMIN"}),
        handler=_handle_delete,
    ),
    ToolSpec(
        tool=TICKET_COMMENT_TOOLS[2],
        permissions=frozenset({"TICKET_APPEND"}),
        handler=_handle_reply,
    ),
    ToolSpec(
        tool=TICKET_COMMENT_TOOLS[3],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_history,
    ),
]
