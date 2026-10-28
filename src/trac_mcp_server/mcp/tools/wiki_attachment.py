"""Wiki attachment tool handlers for MCP server.

This module exposes file-based MCP tools that wrap Trac's
``wiki.putAttachmentEx`` / ``getAttachment`` / ``listAttachments`` /
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

This mirrors the ``ticket_attachment.py`` precedent. Note that the
underlying wiki XML-RPC API uses *paths* of the form
``"PageName/filename"`` rather than separate ``(page, filename)``
arguments. For ergonomics these tools accept ``page_name`` and
``filename`` separately and concatenate internally; the wire-level
form is documented in each tool description.
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
    deliver_output,
    read_binary_input,
)
from .registry import ToolSpec

logger = logging.getLogger(__name__)


def _build_page_path(page_name: str, filename: str) -> str:
    """Join page name and filename into a Trac wiki attachment path.

    Trac's wiki attachment XML-RPC methods address attachments by
    ``"PageName/filename"`` paths. We strip a single trailing slash on
    page_name and a single leading slash on filename so callers can pass
    either ``("WikiStart", "diagram.png")`` or ``("WikiStart/", "/diagram.png")``
    and get the same wire-level path.
    """
    page = page_name.rstrip("/")
    name = filename.lstrip("/")
    return f"{page}/{name}"


# Tool definitions for list_tools()
WIKI_ATTACHMENT_TOOLS = [
    types.Tool(
        name="wiki_attachment_put",
        description=(
            "Upload an attachment to a Trac wiki page, as "
            "content_base64 (or, only where this server's file_access is "
            "'local', a file_path on the SERVER's filesystem). To attach "
            "a file from your own machine without putting its bytes in "
            "the transcript, run the trac-mcp CLI there: "
            "`trac-mcp attach-put --page PAGE FILE`. "
            "Internally calls wiki.putAttachmentEx with a "
            "'page_name/filename' path. "
            "Target wiki page must exist (use `wiki_create` or "
            "`wiki_file_push` first)."
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
                "page_name": {
                    "type": "string",
                    "description": (
                        "Wiki page name to attach to. The page must "
                        "already exist."
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Attachment filename stored on the page. "
                        "Defaults to the basename of file_path; "
                        "required with content_base64."
                    ),
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
                "description": {
                    "type": "string",
                    "description": "Attachment description",
                    "default": "",
                },
                "replace": {
                    "type": "boolean",
                    "description": (
                        "If true, overwrite an existing attachment with "
                        "the same filename. If false and a collision "
                        "occurs, Trac stores the file under a renamed "
                        "path; see attached_filename in the response. "
                        "Default false."
                    ),
                    "default": False,
                },
            },
            "required": ["page_name"],
        },
    ),
    types.Tool(
        name="wiki_attachment_get",
        description=(
            "Download a wiki attachment. Where the server's file_access "
            "is 'off' (the http default) the bytes come back base64 in "
            "structuredContent.content_base64 and an output_path is "
            "refused; where it is 'local', output_path (a path on the "
            "server's own filesystem) is required. To save one on "
            "your own machine, run `trac-mcp attach-get --page PAGE NAME "
            "FILE` there. Internally calls wiki.getAttachment with a "
            "'page_name/filename' path."
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
                "page_name": {
                    "type": "string",
                    "description": "Wiki page name the attachment belongs to",
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
                        "unless the server's file_access is 'local', and "
                        "required there. Where file_access is 'off', omit "
                        "it to get the bytes inline."
                    ),
                },
            },
            "required": ["page_name", "filename"],
        },
    ),
    types.Tool(
        name="wiki_attachment_list",
        description=(
            "List attachments on a Trac wiki page. Returns a flat list "
            "of 'page_name/filename' path strings (NOT tuples — this "
            "differs from ticket_attachment_list)."
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
                "page_name": {
                    "type": "string",
                    "description": "Wiki page name to list attachments for",
                },
            },
            "required": ["page_name"],
        },
    ),
    types.Tool(
        name="wiki_attachment_delete",
        description=(
            "Delete a wiki attachment permanently. Requires WIKI_DELETE "
            "permission. Internally calls wiki.deleteAttachment with a "
            "'page_name/filename' path."
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
                "page_name": {
                    "type": "string",
                    "description": "Wiki page name the attachment belongs to",
                },
                "filename": {
                    "type": "string",
                    "description": "Attachment filename to delete",
                },
            },
            "required": ["page_name", "filename"],
        },
    ),
]


async def _handle_put(
    client: TracClient, args: dict[str, Any]
) -> types.CallToolResult:
    """Handle wiki_attachment_put.

    Reads the file from disk, wraps the raw bytes in
    ``xmlrpc.client.Binary``, and uploads via ``wiki.putAttachmentEx``.
    """
    page_name = args.get("page_name")

    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )
    description = args.get("description", "")
    replace = bool(args.get("replace", False))

    source = await read_binary_input(
        args, "trac-mcp attach-put --page PAGE FILE"
    )
    if isinstance(source, types.CallToolResult):
        return source
    data, default_name, source_label = source
    binary = xmlrpc.client.Binary(data)

    filename = args.get("filename") or default_name

    stored_raw = await run_sync(
        client.put_wiki_attachment,
        page_name,
        filename,
        description,
        binary,
        replace,
    )

    # putAttachmentEx returns the stored filename (some versions return
    # a "PageName/filename" path; defensively handle both shapes by
    # extracting the basename after the final slash).
    if isinstance(stored_raw, str) and stored_raw:
        stored_name = stored_raw.rsplit("/", 1)[-1]
    else:
        stored_name = filename

    stored_path = _build_page_path(page_name, stored_name)
    bytes_uploaded = len(data)
    renamed_on_collision = stored_name != filename

    text = (
        f"Uploaded attachment '{stored_name}' to wiki page "
        f"'{page_name}' ({bytes_uploaded} bytes, replace={replace})"
    )

    structured = {
        "page_name": page_name,
        "requested_filename": filename,
        "attached_filename": stored_name,
        "renamed_on_collision": renamed_on_collision,
        "page_path": stored_path,
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
    """Handle wiki_attachment_get.

    Fetches the attachment via ``wiki.getAttachment`` and writes the raw
    bytes to ``output_path``. The bytes never enter the tool result
    payload.
    """
    page_name = args.get("page_name")
    filename = args.get("filename")
    output_path = args.get("output_path")

    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )
    if not filename:
        return build_error_response(
            "validation_error",
            "filename is required",
            "Provide filename parameter.",
        )
    refusal = await check_output_path(
        args, "output_path", "trac-mcp attach-get --page PAGE NAME FILE"
    )
    if refusal is not None:
        return refusal

    page_path = _build_page_path(page_name, filename)
    data = await run_sync(client.get_wiki_attachment, page_path)

    payload = coerce_attachment_payload(data)

    structured: dict[str, Any] = {
        "page_name": page_name,
        "filename": filename,
        "page_path": page_path,
    }
    delivered = await deliver_output(args, "output_path", payload)
    if isinstance(delivered, types.CallToolResult):
        return delivered
    structured.update(delivered)
    if output_path is not None:
        text = (
            f"Downloaded attachment '{filename}' from wiki page '{page_name}' "
            f"to {output_path} ({len(payload)} bytes)"
        )
    else:
        text = (
            f"Fetched attachment '{filename}' from wiki page '{page_name}' "
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
    """Handle wiki_attachment_list.

    Returns the raw list of ``"PageName/filename"`` path strings as
    returned by Trac. Unlike the ticket equivalent, this is a flat
    ``list[str]`` and not a list of tuples — preserved here intentionally
    because the wire format is asymmetric.
    """
    page_name = args.get("page_name")
    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )

    raw = await run_sync(client.list_wiki_attachments, page_name)

    # Defensively coerce each entry to a string (Trac returns
    # list[str], but be tolerant of variants).
    attachments: list[str] = []
    for entry in raw or []:
        if isinstance(entry, str):
            attachments.append(entry)
        else:
            # Some plugins might wrap entries; stringify defensively.
            attachments.append(str(entry))

    if attachments:
        lines = [
            f"Wiki page '{page_name}' has {len(attachments)} attachment(s):"
        ]
        for path in attachments:
            lines.append(f"- {path}")
        text = "\n".join(lines)
    else:
        text = f"Wiki page '{page_name}' has no attachments."

    structured = {
        "page_name": page_name,
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
    """Handle wiki_attachment_delete."""
    page_name = args.get("page_name")
    filename = args.get("filename")

    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )
    if not filename:
        return build_error_response(
            "validation_error",
            "filename is required",
            "Provide filename parameter.",
        )

    page_path = _build_page_path(page_name, filename)

    try:
        await run_sync(client.delete_wiki_attachment, page_path)
    except xmlrpc.client.Fault as e:
        if (
            "permission" in e.faultString.lower()
            or "denied" in e.faultString.lower()
        ):
            return build_error_response(
                "permission_denied",
                e.faultString,
                "Deleting wiki attachments requires WIKI_DELETE. "
                "Contact Trac administrator.",
            )
        raise

    text = (
        f"Deleted attachment '{filename}' from wiki page '{page_name}'."
    )
    structured = {
        "page_name": page_name,
        "filename": filename,
        "page_path": page_path,
    }

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
    )


# ToolSpec list for registry-based dispatch
WIKI_ATTACHMENT_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=WIKI_ATTACHMENT_TOOLS[0],
        permissions=frozenset({"WIKI_MODIFY"}),
        handler=_handle_put,
    ),
    ToolSpec(
        tool=WIKI_ATTACHMENT_TOOLS[1],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_get,
    ),
    ToolSpec(
        tool=WIKI_ATTACHMENT_TOOLS[2],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_list,
    ),
    ToolSpec(
        tool=WIKI_ATTACHMENT_TOOLS[3],
        permissions=frozenset({"WIKI_DELETE"}),
        handler=_handle_delete,
    ),
]


__all__ = [
    "WIKI_ATTACHMENT_TOOLS",
    "WIKI_ATTACHMENT_SPECS",
]
