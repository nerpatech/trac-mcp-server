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
from html import escape as html_escape
from pathlib import Path
from typing import Any

import mcp.types as types

from ...converters.common import (
    auto_convert,
    describe_indentation_loss,
    detect_format_heuristic,
    find_code_block_indentation_loss,
)
from ...converters.html_to_markdown import trac_html_to_markdown
from ...converters.markdown_to_tracwiki import (
    describe_list_item_split,
    find_list_item_splits,
)
from ...core.async_utils import run_sync
from ...core.client import (
    TracClient,
    WikiPageConflict,
    WikiPageNotModified,
    WikiPageStoredReadbackFailed,
)
from ...file_handler import detect_file_format
from ...link_rewrites import as_dict, validate_link_rewrites
from ...preview.checks import _allowed_codes
from .errors import build_error_response, classify_fault
from .file_io import (
    check_output_path,
    deliver_output,
    read_text_input,
)
from .registry import ToolSpec
from .wiki_write import possible_overwrite_warning
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
            'format="tracwiki" to push the text verbatim instead. An '
            "error result means nothing was stored. If the page was "
            "stored but its new version could not be read back, the "
            "result is not an error: structuredContent.readback_failed "
            "is true and version is null -- do not push again. If "
            "structuredContent.possible_overwrite is true, the update "
            "stored a version other than the one after the version it "
            "was based on: another write most likely landed just before "
            "it and was replaced (intervening_versions lists them; "
            "inspect with wiki_diff). The page was stored. Content "
            "identical to the stored page stores nothing and returns "
            'an "unchanged" result (structuredContent.action is '
            '"unchanged"), not an error.'
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
                        "format detection. With neither filename nor "
                        "format, inline content is stored as TracWiki."
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
            "Markdown is Trac's own rendered HTML for the page "
            "translated to Markdown: a one-way export, not meant to be "
            'pushed back. format="html" is Trac\'s render as a complete '
            "minimal HTML document (links absolute, no CSS); "
            'format="tracwiki" returns the '
            "stored source verbatim. Translator warnings come back in "
            "structuredContent.warnings (always a list). "
            "link_rewrites ({from, to} URL-prefix rules, longest prefix "
            "first) rewrites the links (never the images or in-page "
            "#anchors) of a Markdown pull: given, it replaces the "
            "server's configured map ([] = no rewriting), omitted the "
            "configured map applies; structuredContent.link_rewrites_"
            "source says which was used (call, config or none). "
            "Where the server's file_access is 'off' (the http default) "
            "the text comes back in structuredContent.content and a "
            "file_path is refused; where it is 'local', file_path (a path "
            "on the server's own filesystem) is required. To save a page "
            "on your own machine, "
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
                        "filesystem: required where file_access is "
                        "'local', refused where it is 'off'."
                    ),
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "html", "tracwiki"],
                    "default": "markdown",
                    "description": "Output format for the local file",
                },
                "version": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Specific page version to pull",
                },
                "link_rewrites": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from": {
                                "type": "string",
                                "minLength": 1,
                                "description": "URL prefix to replace",
                            },
                            "to": {
                                "type": "string",
                                "description": "Replacement prefix",
                            },
                        },
                        "required": ["from", "to"],
                        "additionalProperties": False,
                    },
                    "description": (
                        "Link-rewrite rules for format=markdown: a "
                        "link target starting with `from` has that "
                        "prefix replaced by `to` (longest `from` "
                        "first). Replaces the server's configured map "
                        "for this call; [] disables rewriting. Image "
                        "sources and in-page #anchors are never "
                        "rewritten. The schema refuses an empty `from` "
                        "or an extra key; a duplicate `from` is a "
                        "validation_error. Ignored for format=html and "
                        "format=tracwiki."
                    ),
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
    if fmt == "auto" and name is None:
        # Inline text with no filename has nothing to go on but its own
        # content, and a content re-detect is what #62/#69 removed from
        # every inline write: a Markdown document quoting TracWiki (or the
        # reverse) inverts it. So it is TracWiki, stored verbatim, like
        # every other inline write -- unless the caller says otherwise
        # with format= or a filename (ticket #111).
        source_format = "tracwiki"
    elif fmt == "auto":
        source_format = detect_file_format(name, content)
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

        # Ticket #125: advise, never refuse. The content survives; only
        # the list structure splits where a list item continues after
        # its nested list, which Trac cannot represent.
        # `preview-checks: allow list_item_split` opts out here as it
        # does in `build_warnings` (#58).
        if "list_item_split" not in _allowed_codes(
            content, wiki_content
        ):
            warnings.extend(
                describe_list_item_split(split)
                for split in find_list_item_splits(content)
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
        wiki_page=page_name,
    )
    if refusal is not None:
        return refusal

    # Create or update page (client already provided)

    # The existence probe, alone in its try: a not-found fault means the
    # page is absent ONLY when it comes from this probe. A fault raised
    # after putPage (its read-back) must never reach a "create it"
    # fallback, or the push writes twice -- recreating a page someone
    # just deleted (Trac /trac_mcp_server #140).
    try:
        info = await run_sync(client.get_wiki_page_info, page_name)
    except xmlrpc.client.Fault as e:
        if classify_fault(e) != "not_found":
            raise
        info = None
    # Some Trac instances return 0 (int) instead of raising Fault
    # for non-existent pages — treat falsy/non-dict as "page not found"
    if not isinstance(info, dict) or not info:
        # Page doesn't exist — create
        action = "created"
        version = None
    else:
        # Page exists — update with optimistic locking
        action = "updated"
        version = info.get("version")

    # The write: exactly one putPage, with no fallback.
    readback_error: str | None = None
    try:
        result = await run_sync(
            client.put_wiki_page,
            page_name,
            wiki_content,
            comment,
            version,
        )
    except WikiPageStoredReadbackFailed as e:
        # Stored, but the new version is unknown. Not an error result:
        # an error means nothing was stored, and a caller retrying on
        # one would write the page a second time.
        result = {}
        readback_error = str(e)
    except WikiPageNotModified as e:
        # Byte-identical to the stored page (ticket #174): Trac stored
        # nothing, which is a no-op, not a failure. The version is the
        # re-read current one; the probe's is the fallback, and on the
        # create path (probe found no page) there may be neither.
        result = {}
        action = "unchanged"
        version = (
            e.current_version
            if e.current_version is not None
            else version
        )
    except WikiPageConflict as e:
        # The page changed between the read above and the write (ticket
        # #120); put_wiki_page refused before posting anything.
        if e.current_version is None:
            return build_error_response(
                "version_conflict",
                f"Page '{page_name}' was deleted after it was read.",
                "Push again to recreate it.",
            )
        return build_error_response(
            "version_conflict",
            f"Page has been modified. Current version is {e.current_version}, you tried to update version {e.expected_version}.",
            f"Fetch current content with wiki_get(page_name='{page_name}'), then push again.",
        )

    # Trac /trac_mcp_server #130: only an update read back successfully
    # can be flagged; put_wiki_page never sets it on a create.
    possible_overwrite = bool(result.get("possible_overwrite"))
    if action == "unchanged":
        # Nothing was stored; `version` is what the page is at.
        new_version = version
        shown = "unknown" if version is None else version
        text_parts = [
            f"Wiki page '{page_name}' unchanged (version {shown}): "
            "content identical to the stored page. Trac stored nothing."
        ]
    elif readback_error is None:
        verb = "Created" if action == "created" else "Updated"
        new_version = result.get("version", 1)
        text_parts = [
            f"{verb} wiki page '{page_name}' (version {new_version})"
        ]
        if possible_overwrite and version is not None:
            overwrite = possible_overwrite_warning(
                page_name, version, result
            )
            if overwrite is not None:
                text_parts.append(overwrite)
    else:
        verb = "Created" if action == "created" else "Updated"
        new_version = None
        text_parts = [
            f"{verb} wiki page '{page_name}' (version unknown): the "
            "page WAS stored, but reading back its new version failed. "
            "Do not push again; check the page with "
            f"wiki_get(page_name='{page_name}').",
            f"Read-back error: {readback_error}",
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
        # Trac /trac_mcp_server #140: true when the page was stored but
        # its new version could not be read back (version is then None).
        "readback_failed": readback_error is not None,
        # Trac /trac_mcp_server #130: true when the update stored a
        # version other than the one after the version it was based on
        # -- another write most likely landed just before it and was
        # replaced. Still a success: the page was stored.
        "possible_overwrite": possible_overwrite,
        # Ticket #153: Claude Code shows structuredContent in preference
        # to the text block, so the gate's note (OFF line, or the
        # gate-on advisory lines) rides here too. Empty when none.
        "gate_notes": list(gate_lines),
    }
    if readback_error is not None:
        structured["readback_error"] = readback_error
    if possible_overwrite:
        structured["intervening_versions"] = list(
            result.get("intervening_versions") or []
        )

    return types.CallToolResult(
        content=[
            types.TextContent(type="text", text="\n".join(text_parts))
        ],
        structuredContent=structured,
    )


# getPageHTML answers a whole ``<html><body>...</body></html>`` itself
# (seen on a live instance, and in every wiki capture of the #208 set);
# wikiToHtml and older Tracs answer a bare fragment.
_TRAC_HTML_WRAPPER = re.compile(
    r"\s*<html\s*>\s*<body\s*>(?P<inner>.*)</body\s*>\s*</html\s*>\s*",
    re.IGNORECASE | re.DOTALL,
)


def _html_document(page_name: str, fragment: str) -> str:
    """Wrap Trac's rendered HTML as a complete minimal document.

    Trac's own ``<html><body>`` wrapper, when present, is removed first
    so the document has exactly one of each; the inner HTML is never
    parsed or altered (Trac renders its links absolute), and a response
    with no such wrapper is used as it is. No CSS; only the page name,
    which is text, is escaped.
    """
    wrapped = _TRAC_HTML_WRAPPER.fullmatch(fragment)
    if wrapped is not None:
        fragment = wrapped.group("inner")
    return (
        "<!doctype html>\n"
        "<html>\n"
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{html_escape(page_name)}</title>\n"
        "</head>\n"
        "<body>\n"
        f"{fragment}"
        "</body></html>\n"
    )


async def _handle_pull(
    client: TracClient, args: dict[str, Any]
) -> types.CallToolResult:
    """Handle wiki_file_pull.

    Fetches a wiki page from Trac and returns it as TracWiki source or,
    for Markdown, as Trac's own rendered HTML translated to Markdown
    (Trac /trac_mcp_server #210), or, for html, as that render in a
    minimal document (#211). The text is returned inline or, where
    file_access is ``local``, written to a server-side file.
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

    def _not_found() -> types.CallToolResult:
        return build_error_response(
            "not_found",
            f"Wiki page '{page_name}' does not exist",
            "Use wiki_search to find available pages.",
        )

    # Ticket #213: the map is chosen before any Trac read, so a bad rule
    # costs no round trip. tracwiki and html do no link translation:
    # nothing to map.
    link_rewrites: tuple[tuple[str, str], ...] = ()
    link_rewrites_source = "none"
    if fmt == "markdown":
        if args.get("link_rewrites") is not None:
            try:
                link_rewrites = validate_link_rewrites(
                    args["link_rewrites"], "link_rewrites"
                )
            except ValueError as e:
                return build_error_response(
                    "validation_error",
                    str(e),
                    "Pass link_rewrites as a list of {from, to} "
                    "objects, or omit it to use the server's map.",
                )
            link_rewrites_source = "call"
        elif client.config.link_rewrites:
            link_rewrites = client.config.link_rewrites
            link_rewrites_source = "config"

    converted = False
    warnings: list[str] = []
    try:
        if fmt in ("markdown", "html"):
            # Read the version first and fetch the source and the HTML
            # *at that version*: without it, an edit landing between the
            # two reads would pair the HTML with another version's source.
            # (html reads no source, but is pinned the same way, so the
            # version reported is the one the HTML is of.)
            info = await run_sync(
                client.get_wiki_page_info, page_name, version
            )
            pinned = (
                info.get("version")
                if isinstance(info, dict) and info
                else None
            ) or version
            if pinned is None:
                # Some Trac instances answer with {} or a non-dict. The
                # pull still works, but both reads below are "latest":
                # an edit between them is the race pinning closes.
                if fmt == "markdown":
                    warnings.append(
                        f"wiki page {page_name}: its version could not"
                        " be read, so the source and the HTML reads were"
                        " not pinned to one version; an edit between"
                        " them would pair one version's source with"
                        " another's HTML"
                    )
                else:
                    warnings.append(
                        f"wiki page {page_name}: its version could not"
                        " be read, so the HTML read was not pinned to a"
                        " version and the version reported may not be"
                        " the one the HTML is of"
                    )
            if fmt == "markdown":
                content = await run_sync(
                    client.get_wiki_page, page_name, pinned
                )
            html = await run_sync(
                client.get_wiki_page_html, page_name, pinned
            )
            if fmt == "html":
                # Trac's own render, unchanged, in a minimal document.
                output_content = _html_document(page_name, html)
                converted = True
            else:
                # The page's own URL, as Trac renders a link into it
                # ("[#Frag x]" is "<this>#Frag"): those are in-page
                # anchors, written "#Frag" and never rewritten.
                # client.config is the routed instance's, so the base is
                # that instance's.
                document_url = (
                    f"{client.config.trac_url.rstrip('/')}"
                    f"/wiki/{page_name}"
                )
                conversion = trac_html_to_markdown(
                    html,
                    content,
                    link_map=as_dict(link_rewrites),
                    document_url=document_url,
                )
                output_content = conversion.text
                converted = conversion.converted
                warnings.extend(conversion.warnings)
        else:
            # tracwiki: the stored source, unchanged; no HTML read.
            content = await run_sync(
                client.get_wiki_page, page_name, version
            )
            info = await run_sync(
                client.get_wiki_page_info, page_name, version
            )
            output_content = content
    except xmlrpc.client.Fault as e:
        if classify_fault(e) == "not_found":
            return _not_found()
        raise  # re-raise other faults for outer handler

    # Handle falsy/non-dict return from some Trac instances
    actual_version = (
        info.get("version", 1) if isinstance(info, dict) and info else 1
    )

    encoded = output_content.encode("utf-8")
    structured: dict[str, Any] = {
        "page_name": page_name,
        "format": fmt,
        "version": actual_version,
        "converted": converted,
        "warnings": warnings,
        "link_rewrites_source": link_rewrites_source,
    }
    delivered = await deliver_output(
        args, "file_path", encoded, text=output_content
    )
    if isinstance(delivered, types.CallToolResult):
        return delivered
    structured.update(delivered)
    if file_path is not None:
        text = (
            f"Pulled wiki page '{page_name}' (version {actual_version}) "
            f"to {file_path} ({len(encoded)} bytes, format={fmt})"
        )
    else:
        text = (
            f"Pulled wiki page '{page_name}' (version {actual_version}, "
            f"{len(encoded)} bytes, format={fmt}); the text is in "
            "structuredContent.content"
        )
    if warnings:
        text += "\n\nConversion warnings:\n" + "\n".join(
            f"- {w}" for w in warnings
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
