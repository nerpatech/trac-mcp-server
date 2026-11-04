"""Write ticket tool handlers for MCP server.

This module implements ticket write operations: create, update, and delete.
All tools use async handlers with run_sync() to bridge synchronous TracClient calls,
and structured error responses. Content is TracWiki and is stored
byte-for-byte -- no converter sits in this path (ticket #69).
"""

import asyncio
import xmlrpc.client
from dataclasses import dataclass
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
from .write_gate import TARGET_CAP_SCHEMA, gate_or_refuse

# Deadline for the changelog read-back that names the comment just posted
# (#124). The write has already landed by then, so a slow read costs the
# number, never the write.
_COMMENT_LOOKUP_TIMEOUT = 10.0


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
                "type": {
                    "type": "string",
                    "description": "Alias for ticket_type (accepted since ticket_update's own field is named 'type').",
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
                "target_cap": TARGET_CAP_SCHEMA,
            },
            "required": ["summary", "description"],
        },
    )


# Tool definitions for list_tools()
TICKET_WRITE_TOOLS = [
    _build_ticket_create_tool(),
    types.Tool(
        name="ticket_update",
        description="Update ticket attributes and/or add comments. Comment and description are TracWiki and are stored byte-for-byte -- nothing is converted, so hand-authored markup survives exactly as written. Pass base_ts (the change token returned by ticket_get) to enable optimistic locking: the update is rejected with a version_conflict error, naming what changed, if the ticket was modified since that token was read. Omitting base_ts skips conflict detection entirely -- the write always succeeds even if the ticket changed underneath you, so always read the ticket first and pass its base_ts back. The result carries the ticket's new change token (_ts, also in structuredContent): pass it as base_ts on your next ticket_update to the same ticket instead of calling ticket_get again. Any other write to the ticket moves _ts too without returning it (ticket_comment_reply, ticket_comment_edit, ticket_batch_update, another session), so after one of those, re-read with ticket_get. Set reply_to to quote an earlier comment. To correct or remove a comment you already posted, use ticket_comment_edit or ticket_comment_delete instead of posting a second comment; this tool only ever adds new ones. When a comment is added, the result names its comment number (comment_number, for ticket_render_check comment=N or ticket_comment_edit) and whether Trac stored it byte-for-byte as sent (comment_stored_matches, with the first difference if not), so no ticket_changelog or ticket_get read-back is needed. CR and CRLF line endings are folded to LF in transport before Trac stores the text; that is reported as comment_line_endings_folded, not as a mismatch. An update that stores nothing (a blank comment with no field change, or an unmoved _ts) says so and carries no comment_number.",
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
                "target_cap": TARGET_CAP_SCHEMA,
                "reply_to": {
                    "type": "integer",
                    "description": "Comment number to reply to. Prepends Trac's standard \"Replying to [comment:N author]:\" quote block (quoting that comment's own text) before the new comment. Requires 'comment' to also be provided.",
                    "minimum": 1,
                },
                "base_ts": {
                    "type": "string",
                    "description": "Change token from a prior ticket_get() call's _ts field, or the new change token a previous ticket_update on this ticket returned (a numeric string -- pass it through as-is, don't parse it as a number). When provided, the update is rejected with a version_conflict error (naming what changed) if the ticket was modified since that token was read. Strongly recommended -- without it, this write silently overwrites any concurrent change.",
                },
            },
            # Action-specific input fields (action_<action>_<action>_<field>,
            # e.g. action_resolve_resolve_resolution) can't be declared as
            # static properties -- the field name depends on which action is
            # chosen. additionalProperties: false only restricts keys not
            # covered by `properties` OR `patternProperties`, so this lets
            # every documented dynamic field through without reopening the
            # door to an actually-unknown key (ticket #99).
            "patternProperties": {
                r"^action_[A-Za-z0-9_]+$": {
                    "type": "string",
                    "description": "Action-specific input field, named action_<action>_<action>_<field> per the `action` property's own description.",
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

    refusal, gate_lines = await gate_or_refuse(
        client,
        {"description": description_tracwiki},
        args,
        recheck_with="ticket_render_check",
        new_ticket=True,
    )
    if refusal is not None:
        return refusal

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
                text="\n".join(
                    [f"Created ticket #{ticket_id}: {summary}"]
                    + gate_lines
                ),
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
    # quote block before the new comment. This tool only ever adds new
    # comments; correcting or removing one is ticket_comment_edit/delete
    # (the tracrpc_comment plugin, see ticket_comment.py).
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

    # The link gate (ticket #64), on the author's OWN text.
    #
    # `comment` may by now carry a quoted copy of an earlier comment,
    # prepended by reply_to. `args["comment"]` is what this author
    # actually wrote, and that is what is checked: refusing a reply
    # because the comment being QUOTED contains a broken link would
    # charge an author for text they did not write and did not choose to
    # keep -- a quoted comment is not theirs to edit. Ruling 4 says block on
    # everything in the submitted content; the quoted block is not
    # content this write is submitting, it is content it is repeating.
    refusal, gate_lines = await gate_or_refuse(
        client,
        {
            "description": attributes.get("description"),
            "comment": args.get("comment", ""),
        },
        args,
        recheck_with="ticket_render_check",
        ticket_id=ticket_id,
    )
    if refusal is not None:
        return refusal

    # Update ticket. base_ts, when supplied, is forwarded verbatim as
    # Trac's optimistic-lock token -- see TracClient.update_ticket (#50).
    base_ts = args.get("base_ts")
    try:
        update_result = await run_sync(
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

    # Every write moves _ts, so hand the new token back: a chained write
    # to the same ticket then needs no ticket_get just to refresh
    # base_ts (#127). The write has already landed, so a reply without
    # a token degrades to a warning, never to a failure.
    new_ts = _change_token_from_update(update_result)

    # Trac stores nothing for an update whose comment is empty after
    # whitespace stripping and which changes no field, and tracrpc then
    # returns the UNCHANGED _ts. Looking for "the comment just posted"
    # there would find the previous one, so say nothing was stored.
    token_unmoved = (
        base_ts is not None
        and new_ts is not None
        and new_ts == str(base_ts)
    )
    blank_no_fields = (
        comment.replace("\u200b", "").strip() == "" and not attributes
    )
    nothing_stored: str | None = None
    if token_unmoved or (
        base_ts is None and blank_no_fields and comment
    ):
        nothing_stored = (
            "Trac stored nothing: a comment that is empty after "
            "whitespace stripping, with no field changes, is not saved."
            if blank_no_fields
            else "Trac stored nothing: the change token did not move, "
            "so no change was saved."
        )

    # Name the comment just posted and check it was stored as sent
    # (#124). Skipped without a comment: nothing was posted to find.
    posted: _PostedComment | None = None
    comment_lines: list[str] = []
    if nothing_stored is not None:
        comment_lines.append(nothing_stored)
    elif comment:
        posted = await _find_posted_comment(
            client, ticket_id, comment, update_result, new_ts
        )
        if posted.failure is not None:
            comment_lines.append(
                f"WARNING: the comment was posted, but its number "
                f"could not be determined ({posted.failure}). Call "
                f"ticket_get(ticket_id={ticket_id}) on the same "
                f"instance; its comments are listed with their numbers."
            )
        elif posted.mismatch is not None:
            mismatch = posted.mismatch
            comment_lines.append(
                f"WARNING: comment {posted.number} was stored "
                f"differently from the text sent: first difference at "
                f"offset {mismatch['offset']} "
                f"(sent {mismatch['sent_length']} chars, "
                f"stored {mismatch['stored_length']}); "
                f"sent ...{mismatch['sent']!r}... "
                f"stored ...{mismatch['stored']!r}... "
                f"Use ticket_comment_edit to correct it."
            )
        elif posted.folded:
            comment_lines.append(
                f"Stored comment {posted.number} matches the text "
                f"sent, except that its CR/CRLF line endings were "
                f"folded to LF by the XML-RPC transport (Trac cannot "
                f"receive CR this way; nothing to correct)."
            )
        else:
            comment_lines.append(
                f"Stored comment {posted.number} matches the text "
                f"sent, byte-for-byte."
            )
    comment_number = posted.number if posted else None
    comment_mismatch = posted.mismatch if posted else None
    # The verdict is derived once: no number, no verdict.
    comment_matches: bool | None = (
        None if comment_number is None else comment_mismatch is None
    )
    comment_folded: bool | None = (
        None if comment_number is None else posted.folded
    )

    # Build summary of changes
    changes = []
    if nothing_stored is not None:
        changes.append("no change stored")
    elif comment:
        changes.append(
            f"added comment {comment_number}"
            if comment_number is not None
            else "added comment"
        )
    if attributes and nothing_stored is None:
        changes.append(f"updated {len(attributes)} field(s)")

    change_summary = ", ".join(changes) if changes else "no changes"
    if resolution_remap_warning:
        change_summary = f"{change_summary}; {resolution_remap_warning}"

    if new_ts is not None:
        token_line = (
            f"New change token (base_ts): {new_ts} -- pass this to "
            f"the next ticket_update's base_ts on this ticket; no "
            f"ticket_get needed unless another tool writes to it first."
        )
    else:
        token_line = (
            f"WARNING: the update landed, but Trac's reply carried no "
            f"change token. Call ticket_get(ticket_id={ticket_id}, "
            f"include_comments=false) on the same instance for a fresh "
            f"base_ts before the next write to this ticket."
        )

    message = "\n".join(
        [f"Updated ticket #{ticket_id} ({change_summary})", token_line]
        + comment_lines
        + gate_lines
    )
    # Claude Code shows structuredContent in preference to the text
    # (see instance_echo.py), so the dict carries the whole message:
    # without it the remap warning, the missing-token warning and the
    # write-gate notes would reach the model only on clients that read
    # the text block.
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)],
        structuredContent={
            "ticket_id": ticket_id,
            "_ts": new_ts,
            "comment_number": comment_number,
            "comment_stored_matches": comment_matches,
            "comment_mismatch": comment_mismatch,
            "comment_line_endings_folded": comment_folded,
            "message": message,
        },
    )


def _comment_mismatch(sent: str, stored: str) -> dict[str, Any] | None:
    """Where ``stored`` first departs from ``sent``, or None if equal.

    ``offset`` is the index of the first differing character (the
    shorter length when one string is a prefix of the other), counted in
    characters of the Python str. ``sent`` and ``stored`` are the 40
    characters either side of it, so a stripped trailing space can be
    seen without dumping both texts.
    """
    if sent == stored:
        return None
    offset = 0
    for a, b in zip(sent, stored, strict=False):
        if a != b:
            break
        offset += 1
    low = max(0, offset - 40)
    high = offset + 40
    return {
        "offset": offset,
        "sent": sent[low:high],
        "stored": stored[low:high],
        "sent_length": len(sent),
        "stored_length": len(stored),
    }


def _update_change_epoch(
    update_result: Any, new_ts: str | None
) -> int | None:
    """The whole second a ``ticket.update`` landed at, or None.

    Changelog timestamps have second resolution, so the match is made on
    whole seconds. ``new_ts`` is Trac's microsecond ``_ts``; failing
    that, ``time_changed`` out of the ``[id, created, changed, attrs]``
    reply is parsed.
    """
    if new_ts is not None:
        try:
            return int(new_ts) // 1_000_000
        except ValueError:
            pass
    if (
        isinstance(update_result, (list, tuple))
        and len(update_result) >= 3
    ):
        changed = TracClient._parse_trac_time(update_result[2])
        if changed is not None:
            return int(changed)
    return None


def _comment_cnum(entry: Any) -> int | None:
    """The flat comment number of a changeLog comment row, or None.

    Trac keeps it in the row's oldvalue: ``"5"``. A dotted ``"2.5"`` is a
    reply threaded under comment 2 -- never this tool's own write, since
    tracrpc's ``ticket.update`` saves without a ``replyto``. ``int()`` is
    the test, not ``isdigit()``: ``"\u00b2".isdigit()`` is True and
    ``int("\u00b2")`` raises.
    """
    text = str(entry[3])
    if "." in text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class _PostedComment:
    """What the changelog read-back found for a ``ticket.update`` comment.

    ``number`` is None when the comment could not be pinned down, with
    ``failure`` saying why; ``mismatch`` is None when the stored text is
    the text sent (``folded``: up to CR/CRLF folded to LF in transport).
    """

    number: int | None = None
    mismatch: dict[str, Any] | None = None
    folded: bool = False
    failure: str | None = None


async def _find_posted_comment(
    client: TracClient,
    ticket_id: int,
    comment: str,
    update_result: Any,
    new_ts: str | None,
) -> _PostedComment:
    """Find the comment a ``ticket.update`` just posted in the changelog.

    Never raises: the write has already landed, so a failed lookup is
    reported through ``failure``, not propagated.

    The number cannot be counted from the changelog, because a
    description-only edit consumes one too; it is read off the row.
    Candidates are this user's permanent, flat comment rows written in
    the update's second; if several remain (another write of ours in the
    same second) the text sent picks among them.
    """
    epoch = _update_change_epoch(update_result, new_ts)
    if epoch is None:
        return _PostedComment(failure="no change time in Trac's reply")
    try:
        changelog = await asyncio.wait_for(
            run_sync(client.get_ticket_changelog, ticket_id),
            timeout=_COMMENT_LOOKUP_TIMEOUT,
        )
    except TimeoutError:
        return _PostedComment(failure="changelog read timed out")
    except Exception:
        return _PostedComment(failure="changelog unreadable")

    # OIDC per-user auth: the client does not know the caller's Trac name,
    # its username is empty, so the time and the text have to do.
    username = getattr(client.config, "username", None)
    author = (
        username.strip().lower()
        if isinstance(username, str) and username.strip()
        else None
    )
    candidates = []
    for entry in changelog or []:
        if (
            not isinstance(entry, (list, tuple))
            or len(entry) < 6
            or entry[2] != "comment"
            # Attachment pseudo-rows are UNIONed in with permanent 0.
            or not entry[5]
            or _comment_cnum(entry) is None
        ):
            continue
        if author is not None and (
            not isinstance(entry[1], str)
            or entry[1].strip().lower() != author
        ):
            continue
        when = TracClient._parse_trac_time(entry[0])
        if when is not None and int(when) == epoch:
            candidates.append(entry)

    if not candidates:
        who = f" by {username.strip()}" if author is not None else ""
        return _PostedComment(
            failure=f"no comment entry{who} at the update's time"
        )

    # The XML-RPC transport folds CRLF and lone CR to LF before Trac
    # stores the text, so CR can never survive: compare against that.
    sent_norm = comment.replace("\r\n", "\n").replace("\r", "\n")
    chosen = candidates[0]
    if len(candidates) > 1:
        # Another write landed in the same second: go by the text sent.
        same_text = [e for e in candidates if (e[4] or "") == sent_norm]
        if not same_text:
            return _PostedComment(
                failure=(
                    "several comment entries at that time, none "
                    "matching the text sent"
                )
            )
        chosen = max(same_text, key=lambda e: _comment_cnum(e) or 0)

    # An empty <string></string> arrives as None, not "".
    stored = chosen[4] or ""
    return _PostedComment(
        number=_comment_cnum(chosen),
        mismatch=_comment_mismatch(sent_norm, stored),
        folded=stored == sent_norm and sent_norm != comment,
    )


def _change_token_from_update(result: Any) -> str | None:
    """The new ``_ts`` out of a ``ticket.update`` reply, or None.

    Trac returns ``[id, time_created, time_changed, attributes]``, and
    ``attributes["_ts"]`` is the ticket's token as tracrpc re-reads it
    straight after saving -- the same value a fresh ``ticket.get``
    reports (checked against /trac_test for #127). A different write
    landing inside that re-read's window would be absorbed into the
    token, exactly as it would be by a ticket_get made only to fetch
    ``_ts``; the window here is the narrower of the two. Anything
    else -- a reply of another shape, a missing or
    empty token -- yields None rather than raising, because the write
    this reply acknowledges has already been stored.
    """
    if not isinstance(result, (list, tuple)) or len(result) < 4:
        return None
    attrs = result[3]
    if not isinstance(attrs, dict):
        return None
    token = attrs.get("_ts")
    if token is None or token == "":
        return None
    return str(token)


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
