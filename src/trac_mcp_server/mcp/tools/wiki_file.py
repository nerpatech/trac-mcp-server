"""Wiki file tool handlers for MCP server.

This module defines MCP tools for file-based wiki operations: push local files
to Trac wiki pages, pull wiki pages to local files, and detect file formats.
"""

import logging
import re
import xmlrpc.client
from typing import Any

import mcp.types as types

from ...converters.common import auto_convert
from ...converters.tracwiki_to_markdown import tracwiki_to_markdown
from ...core.async_utils import run_sync
from ...core.client import TracClient
from ...file_handler import (
    detect_file_format,
    read_file_with_encoding,
    validate_file_path,
    validate_output_path,
    write_file,
)
from .errors import build_error_response
from .registry import ToolSpec

logger = logging.getLogger(__name__)


# Tool definitions for list_tools()
WIKI_FILE_TOOLS = [
    types.Tool(
        name="wiki_file_push",
        description="Push a local file to a Trac wiki page. Reads the file, auto-detects format (Markdown/TracWiki), converts if needed, and creates or updates the wiki page.",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to local file",
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
                "strip_frontmatter": {
                    "type": "boolean",
                    "default": True,
                    "description": "Strip YAML frontmatter from .md files before pushing",
                },
            },
            "required": ["file_path", "page_name"],
        },
    ),
    types.Tool(
        name="wiki_file_pull",
        description="Pull a Trac wiki page to a local file. Fetches page content, converts to the requested format, and writes to the specified path.",
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
                    "description": "Absolute path for output file",
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
            "required": ["page_name", "file_path"],
        },
    ),
    types.Tool(
        name="wiki_file_detect_format",
        description="Detect the format of a local file (Markdown or TracWiki). Uses file extension first, then content-based heuristic detection.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to file to analyze",
                },
            },
            "required": ["file_path"],
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


async def _handle_push(
    client: TracClient, args: dict[str, Any]
) -> types.CallToolResult:
    """Handle wiki_file_push.

    Reads a local file, optionally strips YAML frontmatter, detects or uses
    the specified format, converts to TracWiki if needed, and creates or
    updates the target wiki page with optimistic locking.
    """
    file_path = args.get("file_path")
    page_name = args.get("page_name")

    if not file_path:
        return build_error_response(
            "validation_error",
            "file_path is required",
            "Provide file_path parameter.",
        )
    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )

    comment = args.get("comment", "")
    fmt = args.get("format", "auto")
    strip_fm = args.get("strip_frontmatter", True)

    # Read file
    resolved = await run_sync(validate_file_path, file_path)
    content, encoding = await run_sync(
        read_file_with_encoding, resolved
    )

    # Strip frontmatter
    if strip_fm:
        content = _strip_yaml_frontmatter(content)

    # Detect format
    if fmt == "auto":
        source_format = detect_file_format(resolved, content)
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
    else:
        # Already TracWiki — pass through
        wiki_content = content

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

    structured = {
        "page_name": page_name,
        "action": action,
        "version": new_version,
        "source_format": source_format,
        "converted": converted,
        "file_path": str(file_path),
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
    Markdown, and writes the result to a local file.
    """
    page_name = args.get("page_name")
    file_path = args.get("file_path")

    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )
    if not file_path:
        return build_error_response(
            "validation_error",
            "file_path is required",
            "Provide file_path parameter.",
        )

    fmt = args.get("format", "markdown")
    version = args.get("version")

    # Validate output path (parent directory must exist)
    resolved = await run_sync(validate_output_path, file_path)

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

    # Write file
    bytes_written = await run_sync(write_file, resolved, output_content)

    # Build response
    text = (
        f"Pulled wiki page '{page_name}' (version {actual_version}) "
        f"to {file_path} ({bytes_written} bytes, format={fmt})"
    )

    structured = {
        "page_name": page_name,
        "file_path": str(file_path),
        "format": fmt,
        "version": actual_version,
        "bytes_written": bytes_written,
        "converted": converted,
    }

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
    )


async def _handle_detect_format(
    client: TracClient, args: dict[str, Any]
) -> types.CallToolResult:
    """Handle wiki_file_detect_format.

    Reads the file, detects encoding and format, returns metadata.
    """
    file_path = args.get("file_path")
    if not file_path:
        return build_error_response(
            "validation_error",
            "file_path is required",
            "Provide file_path parameter.",
        )

    resolved = await run_sync(validate_file_path, file_path)
    content, encoding = await run_sync(
        read_file_with_encoding, resolved
    )
    fmt = detect_file_format(resolved, content)
    size_bytes = resolved.stat().st_size

    text = f"File: {file_path}\nFormat: {fmt}\nEncoding: {encoding}\nSize: {size_bytes} bytes"

    structured = {
        "file_path": str(file_path),
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
