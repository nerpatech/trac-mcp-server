"""``convert_preview`` MCP tool -- dry-run Markdown through the real
converter and renderer, with no write (ticket #56).

Companion to ticket #55 (post-write verification): this tool prevents,
that one detects. They share ``preview.facts`` -- the extractor built here
is reused there unchanged.
"""

import logging

import mcp.types as types

from ...converters.markdown_to_tracwiki import convert_with_warnings
from ...core.async_utils import run_sync
from ...core.client import TracClient
from ...preview.checks import build_warnings
from ...preview.facts import extract_facts
from ...preview.gate import suggestion_for
from ...preview.targets import (
    DEFAULT_TARGET_CAP,
    SKIPPED,
    is_probeable_href,
    probe_targets,
)
from .errors import build_error_response
from .instances import local_intertrac_bases, own_intertrac_prefix
from .registry import ToolSpec
from .write_gate import attachment_names, known_comment_numbers

logger = logging.getLogger(__name__)

# rendered_html cap: a full page's HTML otherwise lands in the caller's
# context on every preview call, most of which don't need to inspect the
# markup directly -- the structured `warnings` already carry the facts
# that matter. tracwiki/warnings are never truncated.
MAX_HTML_BYTES = 20_000

CONVERT_PREVIEW_TOOLS = [
    types.Tool(
        name="convert_preview",
        description=(
            "Dry-run a Markdown (or TracWiki) candidate through the real "
            "converter and Trac's own renderer, with no write. Returns "
            "the TracWiki that would be stored, the HTML Trac would "
            "render for it, and structured warnings for defects a plain "
            "render-verify would catch only after the fact: an escaped "
            "link target, a cross-instance reference accidentally left "
            "in a code span (realm form or `prefix:#N` short link), a "
            "link whose target swallowed trailing punctuation "
            "(a cross-instance short link, or a local 'ticket:N' or "
            "'wiki:Page' one), "
            "TracWiki markup pasted into Markdown, markup that "
            "survived as literal text in the render instead of "
            "converting, a "
            "dead local link (reported separately from a bare "
            "CamelCase word Trac auto-linked out of prose, which is "
            "advisory and carries the '!' escape as a fix), a "
            "GitHub-style 'PR #N' that links to a local ticket, a bare "
            "#N beside a '<prefix>:#N' for the same N, a code block whose indentation the "
            "conversion would strip (silent content loss, visible only "
            "in the stored bytes), a list item that continues after "
            "its nested list (Trac splits the list there), and (when check_targets is true) a "
            "cross-instance wiki target that does not exist. A "
            "candidate can opt out of a named code with a "
            "'preview-checks: allow <code>' line in its source. Use this "
            "in place of the create-scratch/push/read-back/diff/delete "
            "round trip before replacing a whole page or description. "
            "Optionally name the page_name or ticket_id the draft will "
            "be written to, so a bare attachment: link to a file the "
            "target holds (and, for a ticket, a comment:N reference) "
            "is checked in context instead of reported as dead."
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
                "content": {
                    "type": "string",
                    "description": "Candidate content to preview (required).",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "tracwiki"],
                    "description": (
                        "Format of `content` (default: markdown). "
                        "'tracwiki' skips conversion and renders the "
                        "input as-is."
                    ),
                    "default": "markdown",
                },
                "check_targets": {
                    "type": "boolean",
                    "description": (
                        "Live-probe cross-instance wiki targets found in "
                        "the render (capped, short timeout) to catch a "
                        "target that renders identically whether it "
                        "exists or not. Default: true."
                    ),
                    "default": True,
                },
                "target_cap": {
                    "type": "integer",
                    "description": (
                        "Maximum cross-instance targets to probe "
                        "(default: 50). The cap keeps the FIRST N in "
                        "document order, so anything beyond it -- the "
                        "END of the document -- is reported as "
                        "target_check_capped rather than checked. "
                        "Raise it only for a deliberate audit of an "
                        "unusually link-dense page; the default is "
                        "above anything measured in real content."
                    ),
                    "default": 50,
                    "minimum": 1,
                    "maximum": 500,
                },
                "include_html": {
                    "type": "boolean",
                    "description": (
                        "Include the rendered HTML in the response "
                        "(capped at 20KB, with html_truncated set if "
                        "cut). Default: true. Warnings are always "
                        "computed from the full render regardless of "
                        "this flag."
                    ),
                    "default": True,
                },
                "page_name": {
                    "type": "string",
                    "description": (
                        "The wiki page the draft will be written to. "
                        "Clears a bare attachment: link to a file that "
                        "page holds, as the write gate does. A failed "
                        "attachment lookup, or a page that does not "
                        "exist yet, keeps those links as errors "
                        "(target.attachments_checked is false). "
                        "Mutually exclusive with ticket_id."
                    ),
                },
                "ticket_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "The ticket the draft will be written to "
                        "(description or comment). Clears a bare "
                        "attachment: link to a file that ticket holds "
                        "and also runs dangling_comment_ref against its "
                        "comments. A failed attachment lookup keeps "
                        "those links as errors; a failed comment "
                        "lookup skips dangling_comment_ref and says so "
                        "(target.comments_checked is false). Mutually "
                        "exclusive with page_name."
                    ),
                },
            },
            "required": ["content"],
        },
    )
]


async def _handle_convert_preview(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle convert_preview."""
    content = args.get("content")
    if not content:
        return build_error_response(
            "validation_error",
            "content is required",
            "Provide content parameter.",
        )

    fmt = args.get("format", "markdown")
    if fmt not in ("markdown", "tracwiki"):
        return build_error_response(
            "validation_error",
            f"format must be 'markdown' or 'tracwiki', got '{fmt}'",
            "Provide format='markdown' or format='tracwiki'.",
        )

    check_targets = args.get("check_targets", True)
    include_html = args.get("include_html", True)

    page_name = args.get("page_name")
    ticket_id = args.get("ticket_id")
    # Each argument's own shape first, so a malformed value is named as
    # such rather than reported as an exclusivity clash.
    if ticket_id is not None and (
        isinstance(ticket_id, bool)
        or not isinstance(ticket_id, int)
        or ticket_id < 1
    ):
        return build_error_response(
            "validation_error",
            f"ticket_id must be an integer >= 1, got {ticket_id!r}",
            "Provide ticket_id as a positive integer.",
        )
    if page_name is not None and (
        not isinstance(page_name, str) or not page_name.strip()
    ):
        return build_error_response(
            "validation_error",
            "page_name must be a non-empty string",
            "Provide the wiki page name the draft will be written to.",
        )
    if page_name is not None and ticket_id is not None:
        return build_error_response(
            "validation_error",
            "page_name and ticket_id are mutually exclusive",
            "Pass only one: page_name for a wiki page, ticket_id for a "
            "ticket description or comment.",
        )

    conversion_warnings: list[str] = []
    if fmt == "tracwiki":
        tracwiki = content
    else:
        try:
            conversion = convert_with_warnings(content)
        except ValueError as e:
            # The converter refuses input it cannot represent (ticket #63).
            # Caught here rather than left to propagate: the dispatcher maps
            # a bare ValueError to "unknown_tool", which would tell the
            # caller to run list_tools -- actively misleading for a content
            # problem they can fix.
            return build_error_response(
                "validation_error",
                str(e),
                'Remove the construct, or pass format="tracwiki" so the '
                "content is stored verbatim without conversion.",
            )
        tracwiki = conversion.text
        conversion_warnings = conversion.warnings

    rendered_html = await run_sync(client.wiki_to_html, tracwiki)
    facts = extract_facts(rendered_html)

    probes: dict[str, dict] = {}
    if check_targets:
        probeable_hrefs = [
            a.href for a in facts.anchors if is_probeable_href(a.href)
        ]
        if probeable_hrefs:
            probes = await run_sync(
                probe_targets,
                client,
                probeable_hrefs,
                args.get("target_cap", DEFAULT_TARGET_CAP),
            )

    has_target = page_name is not None or ticket_id is not None
    attached_names: frozenset[str] | None = None
    if has_target and "attachment:" in tracwiki:
        attached_names = await attachment_names(
            client,
            wiki_page=page_name,
            ticket_id=ticket_id,
            new_ticket=False,
        )
    comment_numbers: frozenset[int] | None = None
    if ticket_id is not None and "comment:" in tracwiki:
        comment_numbers = await known_comment_numbers(client, ticket_id)

    warnings = build_warnings(
        markdown_source=content,
        tracwiki=tracwiki,
        facts=facts,
        probes=probes,
        check_targets=check_targets,
        # The caller already told us what it wrote; pass it on rather
        # than letting the checks assume Markdown (ticket #65).
        source_format=fmt,
        local_intertrac_bases=local_intertrac_bases(),
        own_prefix=own_intertrac_prefix(client),
        known_comment_numbers=comment_numbers,
        attachment_names=attached_names,
    )
    for message in conversion_warnings:
        warnings.append(
            {
                "code": "conversion_warning",
                "severity": "warning",
                "message": message,
                "evidence": None,
            }
        )

    html_truncated = False
    html_out: str | None = rendered_html
    if include_html:
        encoded = rendered_html.encode("utf-8")
        if len(encoded) > MAX_HTML_BYTES:
            html_out = encoded[:MAX_HTML_BYTES].decode(
                "utf-8", errors="ignore"
            )
            html_truncated = True
    else:
        html_out = None

    stats = {
        "anchors": len(facts.anchors),
        "code_spans": len(facts.code_spans),
        "warnings": len(warnings),
        # Counts targets actually FETCHED, not entries in the probes
        # dict: a SKIPPED entry means "not verified" (ticket #80), and
        # counting it here would report a capped run as a fully checked
        # one -- the same shape the SKIPPED status exists to prevent.
        "targets_checked": sum(
            1 for p in probes.values() if p.get("status") != SKIPPED
        ),
    }

    response_lines = [
        f"convert_preview: {len(warnings)} warning(s), "
        f"{stats['targets_checked']} cross-instance target(s) checked.",
        "",
    ]
    target: dict | None = None
    if has_target:
        if ticket_id is not None:
            target = {"realm": "ticket", "id": ticket_id}
            label = f"ticket {ticket_id}"
        else:
            target = {"realm": "wiki", "id": page_name}
            label = f"wiki page {page_name}"
        # Tri-state: None = the lookup was not needed, True = it ran
        # and returned a set, False = it ran and failed.
        attachment_lookup_ran = "attachment:" in tracwiki
        comment_lookup_ran = (
            ticket_id is not None and "comment:" in tracwiki
        )
        target["attachments_checked"] = (
            attached_names is not None
            if attachment_lookup_ran
            else None
        )
        target["comments_checked"] = (
            comment_numbers is not None if comment_lookup_ran else None
        )
        if attached_names is not None:
            label += f" ({len(attached_names)} attachment(s))"
        elif attachment_lookup_ran:
            label += (
                " (attachment list unavailable: bare attachment: "
                "links stay errors)"
            )
        if comment_lookup_ran and comment_numbers is None:
            label += " (comment list unavailable: comment: links not checked)"
        response_lines.insert(1, f"Checked against: {label}")
    if warnings:
        for w in warnings:
            response_lines.append(
                f"- [{w['severity']}] {w['code']}: {w['message']}"
            )
            # Ticket #64 section 5: show the corrected string where a
            # check has one. It was already in `evidence`, where only a
            # caller that thought to look would find it -- and the whole
            # argument for suggestions is that the author does NOT think
            # to look up a separate thing.
            suggestion = suggestion_for(w)
            if suggestion:
                response_lines.append(f"  Write instead: {suggestion}")
    else:
        response_lines.append("No warnings.")
    response_lines.append("")
    response_lines.append("--- TracWiki ---")
    response_lines.append(tracwiki)

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent={
            "tracwiki": tracwiki,
            "rendered_html": html_out,
            "html_truncated": html_truncated,
            "warnings": warnings,
            "stats": stats,
            "target": target,
        },
    )


CONVERT_PREVIEW_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=CONVERT_PREVIEW_TOOLS[0],
        permissions=frozenset(),
        handler=_handle_convert_preview,
    ),
]
