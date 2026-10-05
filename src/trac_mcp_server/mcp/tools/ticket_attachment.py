"""Ticket attachment tool handlers for MCP server.

This module exposes file-based MCP tools that wrap Trac's
``ticket.putAttachment`` / ``getAttachment`` / ``listAttachments`` /
``deleteAttachment`` XML-RPC methods.

The tool I/O was path-based (not inline base64) so that attachment bytes
never entered the conversation transcript: MCP clients serialize tool
arguments and results into it, so inlining binary payloads re-tokenizes
each attachment into the model's context. That only works while the
server shares its caller's filesystem. Over http it does not, so these
tools also take and return the bytes as base64 (ticket #111), and the
path forms are refused unless the server's ``file_access`` is ``local``.
The inline forms are meant for the ``trac-mcp`` CLI, which reads and
writes the caller's own files, so the bytes still stay out of a
transcript -- see ``file_io``.

This mirrors the ``wiki_file.py`` precedent (``wiki_file_push`` /
``wiki_file_pull`` also use ``file_path``).
"""

import logging
import xmlrpc.client
from typing import Any

import mcp.types as types

from ...core.async_utils import run_sync
from ...core.client import TracClient
from .attachment_common import coerce_attachment_payload
from .errors import build_error_response
from .file_io import (
    check_output_path,
    encode_base64,
    inline_too_large,
    read_binary_input,
    write_output,
)
from .registry import ToolSpec

logger = logging.getLogger(__name__)


# Tool definitions for list_tools()
TICKET_ATTACHMENT_TOOLS = [
    types.Tool(
        name="ticket_attachment_put",
        description=(
            "Upload an attachment to a Trac ticket, as content_base64 "
            "(or, only where this server's file_access is 'local', a "
            "file_path on the SERVER's filesystem). To attach a file "
            "from your own machine without putting its bytes in the "
            "transcript, run the trac-mcp CLI there: "
            "`trac-mcp attach-put --ticket N FILE`."
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
                "ticket_id": {
                    "type": "integer",
                    "description": "Ticket number to attach to",
                    "minimum": 1,
                },
                "content_base64": {
                    "type": "string",
                    "description": (
                        "The attachment bytes, standard base64, in place "
                        "of file_path. Requires filename."
                    ),
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Absolute path on the SERVER's filesystem. "
                        "Refused unless the server's file_access is "
                        "'local' (it is 'off' over http)."
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Attachment filename stored on the ticket. "
                        "Defaults to the basename of file_path; "
                        "required with content_base64."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "Attachment description",
                    "default": "",
                },
                "replace": {
                    "type": "boolean",
                    "description": (
                        "If true, overwrite an existing attachment with "
                        "the same filename. Default false."
                    ),
                    "default": False,
                },
            },
            "required": ["ticket_id"],
        },
    ),
    types.Tool(
        name="ticket_attachment_get",
        description=(
            "Download a ticket attachment. Without output_path the "
            "bytes come back base64 in structuredContent.content_base64; "
            "an output_path is on the SERVER's filesystem and is refused "
            "unless the server's file_access is 'local'. To save one on "
            "your own machine, run the trac-mcp CLI there: "
            "`trac-mcp attach-get --ticket N NAME FILE`."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "integer",
                    "description": "Ticket number the attachment belongs to",
                    "minimum": 1,
                },
                "filename": {
                    "type": "string",
                    "description": "Attachment filename to download",
                },
                "output_path": {
                    "type": "string",
                    "description": (
                        "Absolute path on the SERVER's filesystem; the "
                        "parent directory must already exist. Refused "
                        "unless the server's file_access is 'local'. "
                        "Omit it to get the bytes inline."
                    ),
                },
            },
            "required": ["ticket_id", "filename"],
        },
    ),
    types.Tool(
        name="ticket_attachment_list",
        description="List attachments on a Trac ticket.",
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
                    "description": "Ticket number to list attachments for",
                    "minimum": 1,
                },
            },
            "required": ["ticket_id"],
        },
    ),
    types.Tool(
        name="ticket_attachment_delete",
        description=(
            "Delete a ticket attachment permanently. Requires "
            "TICKET_ADMIN permission."
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
                "ticket_id": {
                    "type": "integer",
                    "description": "Ticket number the attachment belongs to",
                    "minimum": 1,
                },
                "filename": {
                    "type": "string",
                    "description": "Attachment filename to delete",
                },
            },
            "required": ["ticket_id", "filename"],
        },
    ),
]


async def _handle_put(
    client: TracClient, args: dict[str, Any]
) -> types.CallToolResult:
    """Handle ticket_attachment_put.

    Reads the file from disk, wraps the raw bytes in
    ``xmlrpc.client.Binary``, and uploads via ``ticket.putAttachment``.
    """
    ticket_id = args.get("ticket_id")

    if not ticket_id:
        return build_error_response(
            "validation_error",
            "ticket_id is required",
            "Provide ticket_id parameter.",
        )
    description = args.get("description", "")
    replace = bool(args.get("replace", False))

    source = await read_binary_input(
        args, "trac-mcp attach-put --ticket N FILE"
    )
    if isinstance(source, types.CallToolResult):
        return source
    data, default_name, source_label = source
    binary = xmlrpc.client.Binary(data)

    filename = args.get("filename") or default_name

    stored = await run_sync(
        client.put_ticket_attachment,
        ticket_id,
        filename,
        description,
        binary,
        replace,
    )

    # Trac's putAttachment typically returns the stored filename string;
    # surface whatever the server returns alongside the bytes uploaded.
    stored_name = stored if isinstance(stored, str) else filename
    bytes_uploaded = len(data)

    renamed_on_collision = stored_name != filename

    text = (
        f"Uploaded attachment '{stored_name}' to ticket #{ticket_id} "
        f"({bytes_uploaded} bytes, replace={replace})"
    )

    structured = {
        "ticket_id": ticket_id,
        "requested_filename": filename,
        "attached_filename": stored_name,
        "renamed_on_collision": renamed_on_collision,
        "file_path": args.get("file_path"),
        "source": source_label,
        "bytes_uploaded": bytes_uploaded,
        "replace": replace,
        "description": description,
    }

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
    )


async def _handle_get(
    client: TracClient, args: dict[str, Any]
) -> types.CallToolResult:
    """Handle ticket_attachment_get.

    Fetches the attachment via ``ticket.getAttachment`` and writes the
    raw bytes to ``output_path``. The bytes never enter the tool result
    payload.
    """
    ticket_id = args.get("ticket_id")
    filename = args.get("filename")
    output_path = args.get("output_path")

    if not ticket_id:
        return build_error_response(
            "validation_error",
            "ticket_id is required",
            "Provide ticket_id parameter.",
        )
    if not filename:
        return build_error_response(
            "validation_error",
            "filename is required",
            "Provide filename parameter.",
        )
    refusal = await check_output_path(
        args, "output_path", "trac-mcp attach-get --ticket N NAME FILE"
    )
    if refusal is not None:
        return refusal

    data = await run_sync(
        client.get_ticket_attachment, ticket_id, filename
    )

    payload = coerce_attachment_payload(data)

    structured: dict[str, Any] = {
        "ticket_id": ticket_id,
        "filename": filename,
    }
    if output_path is not None:
        structured.update(
            await write_output(args, "output_path", payload)
        )
        text = (
            f"Downloaded attachment '{filename}' from ticket #{ticket_id} "
            f"to {output_path} ({len(payload)} bytes)"
        )
    else:
        too_large = inline_too_large(
            len(payload), "trac-mcp attach-get --ticket N NAME FILE"
        )
        if too_large is not None:
            return too_large
        structured["content_base64"] = encode_base64(payload)
        structured["bytes"] = len(payload)
        text = (
            f"Fetched attachment '{filename}' from ticket #{ticket_id} "
            f"({len(payload)} bytes); the bytes are base64 in "
            "structuredContent.content_base64"
        )

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
    )


async def _handle_list(
    client: TracClient, args: dict[str, Any]
) -> types.CallToolResult:
    """Handle ticket_attachment_list.

    Returns the raw attachment tuples plus a summary text rendering.
    Each tuple is ``[filename, description, size, time, author]``.
    """
    ticket_id = args.get("ticket_id")
    if not ticket_id:
        return build_error_response(
            "validation_error",
            "ticket_id is required",
            "Provide ticket_id parameter.",
        )

    raw = await run_sync(client.list_ticket_attachments, ticket_id)

    attachments: list[dict[str, Any]] = []
    for entry in raw or []:
        # Trac returns 5-tuples: (filename, description, size, time, author).
        # Defensively handle short/long tuples without crashing.
        if not isinstance(entry, (list, tuple)):
            continue
        filename = entry[0] if len(entry) > 0 else None
        description = entry[1] if len(entry) > 1 else ""
        size = entry[2] if len(entry) > 2 else None
        time_val = entry[3] if len(entry) > 3 else None
        author = entry[4] if len(entry) > 4 else None
        attachments.append(
            {
                "filename": filename,
                "description": description,
                "size": size,
                "time": str(time_val) if time_val is not None else None,
                "author": author,
            }
        )

    if attachments:
        lines = [
            f"Ticket #{ticket_id} has {len(attachments)} attachment(s):"
        ]
        for a in attachments:
            size_str = (
                f"{a['size']} bytes" if a["size"] is not None else "?"
            )
            lines.append(
                f"- {a['filename']} ({size_str}) by {a['author'] or '?'}"
            )
        text = "\n".join(lines)
    else:
        text = f"Ticket #{ticket_id} has no attachments."

    structured = {
        "ticket_id": ticket_id,
        "count": len(attachments),
        "attachments": attachments,
    }

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
    )


async def _handle_delete(
    client: TracClient, args: dict[str, Any]
) -> types.CallToolResult:
    """Handle ticket_attachment_delete."""
    ticket_id = args.get("ticket_id")
    filename = args.get("filename")

    if not ticket_id:
        return build_error_response(
            "validation_error",
            "ticket_id is required",
            "Provide ticket_id parameter.",
        )
    if not filename:
        return build_error_response(
            "validation_error",
            "filename is required",
            "Provide filename parameter.",
        )

    try:
        await run_sync(
            client.delete_ticket_attachment, ticket_id, filename
        )
    except xmlrpc.client.Fault as e:
        if (
            "permission" in e.faultString.lower()
            or "denied" in e.faultString.lower()
        ):
            return build_error_response(
                "permission_denied",
                e.faultString,
                "Deleting ticket attachments requires TICKET_ADMIN. "
                "Contact Trac administrator.",
            )
        raise

    text = f"Deleted attachment '{filename}' from ticket #{ticket_id}."
    structured = {
        "ticket_id": ticket_id,
        "filename": filename,
    }

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
    )


# ToolSpec list for registry-based dispatch
TICKET_ATTACHMENT_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=TICKET_ATTACHMENT_TOOLS[0],
        permissions=frozenset({"TICKET_APPEND"}),
        handler=_handle_put,
    ),
    ToolSpec(
        tool=TICKET_ATTACHMENT_TOOLS[1],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_get,
    ),
    ToolSpec(
        tool=TICKET_ATTACHMENT_TOOLS[2],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_list,
    ),
    ToolSpec(
        tool=TICKET_ATTACHMENT_TOOLS[3],
        permissions=frozenset({"TICKET_ADMIN"}),
        handler=_handle_delete,
    ),
]


__all__ = [
    "TICKET_ATTACHMENT_TOOLS",
    "TICKET_ATTACHMENT_SPECS",
]
