"""Wiki file tool handlers for MCP server.

This module defines MCP tools for file-based wiki operations: push local files
to Trac wiki pages, pull wiki pages to local files, and detect file formats.

Each tool takes its text either inline (``content``) or, only where the
server's ``file_access`` is ``local``, as a path on the server's own
filesystem -- see ``file_io`` (ticket #111).
"""

import logging
import re
import xmlrpc.client
from pathlib import Path
from typing import Any

import mcp.types as types

from ...converters.common import (
    auto_convert,
    describe_indentation_loss,
    detect_format_heuristic,
    find_code_block_indentation_loss,
)
from ...converters.tracwiki_to_markdown import tracwiki_to_markdown
from ...core.async_utils import run_sync
from ...core.client import TracClient
from ...file_handler import detect_file_format
from .errors import build_error_response
from .file_io import (
    check_output_path,
    inline_too_large,
    read_text_input,
    write_output,
)
from .registry import ToolSpec
from .write_gate import TARGET_CAP_SCHEMA, gate_or_refuse

logger = logging.getLogger(__name__)


# Tool definitions for list_tools()
WIKI_FILE_TOOLS = [
    types.Tool(
        name="wiki_file_push",
        description=(
            "Push a document to a Trac wiki page. Takes the text as "
            "content (or, only where this server's file_access is "
            "'local', a file_path on the SERVER's filesystem), "
            "auto-detects format (Markdown/TracWiki), converts if "
            "needed, and creates or updates the wiki page. To push a "
            "file from your own machine, run the trac-mcp CLI there: "
            "`trac-mcp wiki-push PAGE FILE`. Refuses the push when "
            "converting would strip a code block's indentation and "
            "store syntactically invalid content -- pass "
            'format="tracwiki" to push the text verbatim instead.'
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
                "content": {
                    "type": "string",
                    "description": (
                        "The document text to push, in place of "
                        "file_path."
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "With content: the source file's name (e.g. "
                        "notes.md), used only for extension-based "
                        "format detection."
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
                "page_name": {
                    "type": "string",
                    "description": "Target wiki page name",
                },
                "comment": {
                    "type": "string",
                    "description": "Change comment",
                },
                "format": {
                    "type": "string",
                    "enum": ["auto", "markdown", "tracwiki"],
                    "default": "auto",
                    "description": "Source format override. Default auto-detects from extension then content",
                },
                "target_cap": TARGET_CAP_SCHEMA,
                "strip_frontmatter": {
                    "type": "boolean",
                    "default": True,
                    "description": "Strip YAML frontmatter from .md files before pushing",
                },
            },
            "required": ["page_name"],
        },
    ),
    types.Tool(
        name="wiki_file_pull",
        description=(
            "Pull a Trac wiki page, converted to the requested format. "
            "Without file_path the text comes back in the result's "
            "structuredContent.content; a file_path is a path on the "
            "SERVER's filesystem and is refused unless the server's "
            "file_access is 'local'. To save a page on your own machine, "
            "run the trac-mcp CLI there: `trac-mcp wiki-pull PAGE FILE`."
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
                    "description": "Wiki page name to pull",
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Absolute output path on the SERVER's "
                        "filesystem. Omit it to get the text inline."
                    ),
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "tracwiki"],
                    "default": "markdown",
                    "description": "Output format for the local file",
                },
                "version": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Specific page version to pull",
                },
            },
            "required": ["page_name"],
        },
    ),
    types.Tool(
        name="wiki_file_detect_format",
        description=(
            "Detect whether text is Markdown or TracWiki. Uses the file "
            "extension first, then content-based heuristic detection. "
            "Takes content (plus an optional filename for the extension), "
            "or a file_path on the SERVER's filesystem where its "
            "file_access is 'local'. For a file on your own machine, "
            "`trac-mcp detect-format FILE` runs the same detection "
            "locally."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Text to analyze, in place of file_path",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "With content: the source file's name, used for "
                        "extension-based detection"
                    ),
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Absolute path on the SERVER's filesystem. "
                        "Refused unless the server's file_access is "
                        "'local'."
                    ),
                },
            },
            "required": [],
        },
    ),
]


_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def _strip_yaml_frontmatter(content: str) -> str:
    """Strip YAML frontmatter block from content.

    Matches a block starting with ``---\\n`` at the beginning of the string,
    ending with the next ``---\\n``.  Returns content with the block removed
    and leading whitespace stripped.  If no frontmatter is found, returns
    content unchanged.
    """
    m = _FRONTMATTER_RE.match(content)
    if m:
        return content[m.end() :].lstrip("\n")
    return content


_PUSH_HINT = "trac-mcp wiki-push PAGE FILE"
_PULL_HINT = "trac-mcp wiki-pull PAGE FILE"
_DETECT_HINT = "trac-mcp detect-format FILE"


def _detect(name: Path | None, content: str) -> str:
    """Extension first when there is a name to go on, else the heuristic."""
    if name is not None:
        return detect_file_format(name, content)
    return detect_format_heuristic(content)


async def _handle_push(
    client: TracClient, args: dict[str, Any]
) -> types.CallToolResult:
    """Handle wiki_file_push.

    Takes the text inline or from a server-side file (``file_io``),
    optionally strips YAML frontmatter, detects or uses the specified
    format, converts to TracWiki if needed, and creates or updates the
    target wiki page with optimistic locking.
    """
    page_name = args.get("page_name")

    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )

    comment = args.get("comment", "")
    fmt = args.get("format", "auto")
    strip_fm = args.get("strip_frontmatter", True)

    source = await read_text_input(args, _PUSH_HINT)
    if isinstance(source, types.CallToolResult):
        return source
    content, encoding, name, source_label = source

    # Strip frontmatter
    if strip_fm:
        content = _strip_yaml_frontmatter(content)

    # Detect format
    if fmt == "auto":
        source_format = _detect(name, content)
    else:
        source_format = fmt

    # Convert to TracWiki if needed
    warnings: list[str] = []
    # read_file_with_encoding only falls back to charset-normalizer's guess
    # when the file doesn't decode as strict UTF-8, so a non-utf-8 result
    # here means detection genuinely disagrees with UTF-8. Surface that
    # instead of letting it stay silent (ticket #48) -- the guess can
    # still be wrong.
    if encoding != "utf-8":
        warnings.append(
            f"File encoding detected as '{encoding}', not UTF-8 -- "
            "verify the pushed content isn't corrupted."
        )
    converted = False
    if source_format == "markdown":
        # Pass source_format explicitly so auto_convert doesn't re-run the
        # content heuristic — a Markdown file containing TracWiki-formatted
        # examples inside code blocks (e.g. docs describing the converter
        # itself) would otherwise be mis-detected as TracWiki and pushed
        # through unchanged.
        try:
            conversion = await auto_convert(
                content,
                client.config,
                target_format="tracwiki",
                source_format="markdown",
            )
        except ValueError as e:
            # See convert_preview for why this is caught rather than left to
            # the dispatcher, which would report it as "unknown_tool".
            return build_error_response(
                "validation_error",
                str(e),
                'Remove the construct, or pass format="tracwiki" to push '
                "the file verbatim without conversion.",
            )
        wiki_content = conversion.text
        converted = conversion.converted
        warnings.extend(conversion.warnings)

        # Refuse rather than warn (ticket #68, operator decision).
        # A TracWiki `{{{ }}}` block in a Markdown-detected file has
        # its body indentation eaten by paragraph handling, storing
        # syntactically invalid code -- and every signal an author
        # checks stays clean: the call succeeds, the render looks
        # plausible, and `warnings` comes back empty. A warning on a
        # path that then stores the damaged bytes anyway would leave
        # the corruption in the page and the recovery to whoever
        # reads the response. This is the one remaining write path
        # that converts at all (#69 left the file tools converting
        # because they have a filename to go on, and removed the
        # Markdown path from every inline tool), so nothing else is
        # watching it.
        losses = find_code_block_indentation_loss(content, wiki_content)
        if losses:
            return build_error_response(
                "validation_error",
                "Refusing to push: conversion would strip code-block "
                "indentation and store syntactically invalid content. "
                + " ".join(
                    describe_indentation_loss(loss) for loss in losses
                ),
                'Pass format="tracwiki" to push the file verbatim '
                "without conversion, or rewrite the block as a "
                "Markdown fence.",
            )
    else:
        # Already TracWiki — pass through
        wiki_content = content

    # The link gate (ticket #64), on the bytes that will actually land
    # rather than on the file as read: this is the one write path that
    # still converts (#69 left the file tools converting because they
    # have a filename to go on), so the Markdown a caller wrote and the
    # TracWiki Trac will render are different documents. Checking the
    # input would check something nobody stores.
    #
    # Joins the #68 refusal a few lines above rather than replacing it:
    # that one catches indentation the CONVERTER strips, which is
    # invisible in a render and so unreachable from `facts`.
    refusal, gate_lines = await gate_or_refuse(
        client,
        {"content": wiki_content},
        args,
        recheck_with="wiki_render_check",
    )
    if refusal is not None:
        return refusal

    # Create or update page (client already provided)

    try:
        info = await run_sync(client.get_wiki_page_info, page_name)
        # Some Trac instances return 0 (int) instead of raising Fault
        # for non-existent pages — treat falsy/non-dict as "page not found"
        if not isinstance(info, dict) or not info:
            # Page doesn't exist — create
            result = await run_sync(
                client.put_wiki_page,
                page_name,
                wiki_content,
                comment,
                None,
            )
            action = "created"
        else:
            # Page exists — update with optimistic locking
            version = info.get("version")
            result = await run_sync(
                client.put_wiki_page,
                page_name,
                wiki_content,
                comment,
                version,
            )
            action = "updated"
    except xmlrpc.client.Fault as e:
        fault_lower = e.faultString.lower()
        if (
            "not found" in fault_lower
            or "does not exist" in fault_lower
        ):
            # Page doesn't exist — create
            result = await run_sync(
                client.put_wiki_page,
                page_name,
                wiki_content,
                comment,
                None,
            )
            action = "created"
        else:
            raise

    new_version = result.get("version", 1)

    # Build response
    text_parts = [
        f"{'Created' if action == 'created' else 'Updated'} wiki page '{page_name}' (version {new_version})"
    ]
    if warnings:
        text_parts.append("")
        text_parts.append("Conversion warnings:")
        for w in warnings:
            text_parts.append(f"- {w}")
    text_parts.extend(gate_lines)

    structured = {
        "page_name": page_name,
        "action": action,
        "version": new_version,
        "source_format": source_format,
        "converted": converted,
        "file_path": args.get("file_path"),
        "source": source_label,
        "warnings": warnings,
    }

    return types.CallToolResult(
        content=[
            types.TextContent(type="text", text="\n".join(text_parts))
        ],
        structuredContent=structured,
    )


async def _handle_pull(
    client: TracClient, args: dict[str, Any]
) -> types.CallToolResult:
    """Handle wiki_file_pull.

    Fetches a wiki page from Trac, optionally converts from TracWiki to
    Markdown, and returns the text inline or, where file_access is
    ``local``, writes it to a server-side file.
    """
    page_name = args.get("page_name")
    file_path = args.get("file_path")

    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )
    refusal = await check_output_path(args, "file_path", _PULL_HINT)
    if refusal is not None:
        return refusal

    fmt = args.get("format", "markdown")
    version = args.get("version")

    # Fetch page from Trac (client already provided)

    try:
        content = await run_sync(
            client.get_wiki_page, page_name, version
        )
    except xmlrpc.client.Fault as e:
        fault_lower = e.faultString.lower()
        if (
            "not found" in fault_lower
            or "does not exist" in fault_lower
        ):
            return build_error_response(
                "not_found",
                f"Wiki page '{page_name}' does not exist",
                "Use wiki_search to find available pages.",
            )
        raise  # re-raise other faults for outer handler

    info = await run_sync(client.get_wiki_page_info, page_name, version)
    # Handle falsy/non-dict return from some Trac instances
    actual_version = (
        info.get("version", 1) if isinstance(info, dict) and info else 1
    )

    # Convert format
    converted = False
    if fmt == "markdown":
        conversion = tracwiki_to_markdown(content)
        output_content = conversion.text
        converted = conversion.converted
    else:
        # tracwiki — pass through unchanged
        output_content = content

    encoded = output_content.encode("utf-8")
    structured: dict[str, Any] = {
        "page_name": page_name,
        "format": fmt,
        "version": actual_version,
        "converted": converted,
    }
    if file_path is not None:
        structured.update(
            await write_output(args, "file_path", encoded)
        )
        text = (
            f"Pulled wiki page '{page_name}' (version {actual_version}) "
            f"to {file_path} ({len(encoded)} bytes, format={fmt})"
        )
    else:
        too_large = inline_too_large(len(encoded), _PULL_HINT)
        if too_large is not None:
            return too_large
        structured["content"] = output_content
        structured["bytes"] = len(encoded)
        text = (
            f"Pulled wiki page '{page_name}' (version {actual_version}, "
            f"{len(encoded)} bytes, format={fmt}); the text is in "
            "structuredContent.content"
        )

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
    )


async def _handle_detect_format(
    client: TracClient, args: dict[str, Any]
) -> types.CallToolResult:
    """Handle wiki_file_detect_format.

    Resolves the text (``file_io``), detects encoding and format, returns
    metadata.
    """
    file_path = args.get("file_path")
    source = await read_text_input(args, _DETECT_HINT)
    if isinstance(source, types.CallToolResult):
        return source
    content, encoding, name, source_label = source

    fmt = _detect(name, content)
    if file_path is not None and name is not None:
        size_bytes = name.stat().st_size
    else:
        size_bytes = len(content.encode("utf-8"))

    text = f"File: {source_label}\nFormat: {fmt}\nEncoding: {encoding}\nSize: {size_bytes} bytes"

    structured = {
        "file_path": file_path,
        "source": source_label,
        "format": fmt,
        "encoding": encoding,
        "size_bytes": size_bytes,
    }

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
    )


# ToolSpec list for registry-based dispatch
WIKI_FILE_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=WIKI_FILE_TOOLS[0],
        permissions=frozenset({"WIKI_CREATE", "WIKI_MODIFY"}),
        handler=_handle_push,
    ),
    ToolSpec(
        tool=WIKI_FILE_TOOLS[1],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_pull,
    ),
    ToolSpec(
        tool=WIKI_FILE_TOOLS[2],
        permissions=frozenset(),
        handler=_handle_detect_format,
    ),
]
