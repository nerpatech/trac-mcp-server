"""``ticket_render_check`` / ``wiki_render_check`` MCP tools -- post-write
render verification against a live page (ticket #55).

Companion to ``convert_preview`` (ticket #56): that tool dry-runs a
candidate before a write; these check what Trac actually rendered after
one, closing the loop ``Rules/trac/RenderVerify`` mandates. They reuse
``preview.facts``/``preview.checks`` unchanged and add only what a live
page needs on top: fetching, scoping to the relevant section, and pairing
each section with its stored source (``preview.live``, ``preview.verify``).
"""

import logging

import mcp.types as types

from ...core.async_utils import run_sync
from ...core.client import TracClient
from ...preview.facts import Anchor, PreviewFacts, extract_facts
from ...preview.live import (
    RenderCheckError,
    RenderedSection,
    TicketNotFoundError,
    fetch_ticket_sections,
    fetch_wiki_render,
)
from ...preview.targets import is_probeable_wiki_href, probe_targets
from ...preview.verify import build_verify_warnings
from .errors import build_error_response
from .registry import ToolSpec

logger = logging.getLogger(__name__)

# rendered_html cap, same rationale and value as convert_preview.py: the
# structured warnings/links already carry what matters, so the raw HTML
# defaults off here entirely (include_html=False) rather than just capped.
MAX_HTML_BYTES = 20_000

TICKET_RENDER_CHECK_TOOL = types.Tool(
    name="ticket_render_check",
    description=(
        "Fetch a ticket's LIVE rendered page (description, and each "
        "comment by default) and return structured facts + warnings, "
        "replacing the curl+grep workaround Rules/trac/RenderVerify "
        "otherwise forces after every ticket write. Every outbound link "
        "grouped by realm (ticket/wiki/external) with its resolved "
        "target, which wiki links carry Trac's 'missing' class (dead "
        "target), code block counts, and the same defect checks "
        "convert_preview runs (escaped link targets, a cross-instance "
        "reference stuck in a code span -- realm form or `prefix:#N` "
        "short link -- an unconfigured InterTrac prefix, a link whose "
        "target swallowed trailing punctuation, and -- render-only -- "
        "markup that survived as literal text instead of converting; a "
        "page can opt out of a named code with a "
        "'preview-checks: allow <code>' line in its source). Use this "
        "right after any "
        "ticket_update/ticket_create write to verify what Trac actually "
        "rendered, not just what was stored."
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
            "ticket_id": {
                "type": "integer",
                "description": "Ticket number to render-check (required).",
                "minimum": 1,
            },
            "include_comments": {
                "type": "boolean",
                "description": (
                    "Also render-check every comment on the ticket, not "
                    "just the description. Default: true."
                ),
                "default": True,
            },
            "comment": {
                "type": "integer",
                "description": (
                    "Check only this one comment number instead of "
                    "every comment (still includes the description). "
                    "Ignored when include_comments is false."
                ),
                "minimum": 1,
            },
            "check_targets": {
                "type": "boolean",
                "description": (
                    "Live-probe cross-instance wiki targets found in "
                    "the render (capped, short timeout) to catch a "
                    "target that renders identically whether it exists "
                    "or not. Default: true."
                ),
                "default": True,
            },
            "include_html": {
                "type": "boolean",
                "description": (
                    "Include each section's rendered HTML in the "
                    "response (capped at 20KB per section, with "
                    "html_truncated set if cut). Default: false -- the "
                    "structured links/warnings are the point of this "
                    "tool; set true only to inspect raw markup."
                ),
                "default": False,
            },
        },
        "required": ["ticket_id"],
    },
)

WIKI_RENDER_CHECK_TOOL = types.Tool(
    name="wiki_render_check",
    description=(
        "Fetch a wiki page's LIVE rendered HTML and return structured "
        "facts + warnings, replacing the curl+grep workaround "
        "Rules/trac/RenderVerify otherwise forces after every wiki "
        "write. Every outbound link grouped by realm (ticket/wiki/"
        "external) with its resolved target, which wiki links carry "
        "Trac's 'missing' class (dead target), code block counts, and "
        "the same defect checks convert_preview runs (escaped link "
        "targets, a cross-instance reference stuck in a code span -- "
        "realm form or `prefix:#N` short link -- an unconfigured "
        "InterTrac prefix, a link whose target swallowed trailing "
        "punctuation, and -- render-only -- markup that survived as "
        "literal text instead of converting; a page can opt out of a "
        "named code with a 'preview-checks: allow <code>' line in its "
        "source). Use this right after any wiki_update/wiki_create "
        "write."
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
                "description": "Wiki page name to render-check (required).",
            },
            "version": {
                "type": "integer",
                "description": (
                    "Specific version to check (optional, defaults to "
                    "latest)."
                ),
                "minimum": 1,
            },
            "check_targets": {
                "type": "boolean",
                "description": (
                    "Live-probe cross-instance wiki targets found in "
                    "the render (capped, short timeout) to catch a "
                    "target that renders identically whether it exists "
                    "or not. Default: true."
                ),
                "default": True,
            },
            "include_html": {
                "type": "boolean",
                "description": (
                    "Include the rendered HTML in the response (capped "
                    "at 20KB, with html_truncated set if cut). Default: "
                    "false -- the structured links/warnings are the "
                    "point of this tool; set true only to inspect raw "
                    "markup."
                ),
                "default": False,
            },
        },
        "required": ["page_name"],
    },
)

RENDER_CHECK_TOOLS = [TICKET_RENDER_CHECK_TOOL, WIKI_RENDER_CHECK_TOOL]


def _realm_for_anchor(anchor: Anchor) -> str:
    """Trac's own rendered class -- never the href -- decides the realm
    a link is grouped under (the ticket's own headline example: a
    hand-rolled href regex is what produced the false failure this tool
    exists to prevent)."""
    if "ext-link" in anchor.classes:
        return "external"
    if "wiki" in anchor.classes:
        return "wiki"
    if "ticket" in anchor.classes:
        return "ticket"
    return "other"


def _link_entry(anchor: Anchor, probes: dict[str, dict]) -> dict:
    probe = probes.get(anchor.href, {}) if anchor.href else {}
    return {
        "href": anchor.href,
        "resolved_url": probe.get("resolved_url"),
        "text": anchor.text,
        "classes": list(anchor.classes),
        "title": anchor.title,
        "missing": "missing" in anchor.classes,
    }


def _grouped_links(
    facts: PreviewFacts, probes: dict[str, dict]
) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {
        "ticket": [],
        "wiki": [],
        "external": [],
        "other": [],
    }
    for anchor in facts.anchors:
        grouped[_realm_for_anchor(anchor)].append(
            _link_entry(anchor, probes)
        )
    return grouped


def _section_result(
    section: RenderedSection,
    probes: dict[str, dict],
    check_targets: bool,
    include_html: bool,
) -> dict:
    facts = extract_facts(section.html)
    warnings = build_verify_warnings(
        tracwiki=section.tracwiki or "",
        facts=facts,
        probes=probes,
        check_targets=check_targets,
    )
    if not section.source_paired:
        warnings.append(
            {
                "code": "source_not_paired",
                "severity": "info",
                "message": (
                    "No stored source could be paired with this "
                    "section -- the InterTrac-prefix check ran against "
                    "an empty source and may have missed a defect "
                    "there. Not verified, not necessarily clean."
                ),
                "evidence": None,
            }
        )

    html_out: str | None = section.html
    html_truncated = False
    if include_html:
        encoded = section.html.encode("utf-8")
        if len(encoded) > MAX_HTML_BYTES:
            html_out = encoded[:MAX_HTML_BYTES].decode(
                "utf-8", errors="ignore"
            )
            html_truncated = True
    else:
        html_out = None

    return {
        "kind": section.kind,
        "ref": section.ref,
        "source_paired": section.source_paired,
        "links": _grouped_links(facts, probes),
        "code_blocks": [
            {
                "highlighted": cb.highlighted,
                "lines": len(cb.text.splitlines()),
            }
            for cb in facts.code_blocks
        ],
        "warnings": warnings,
        "html": html_out,
        "html_truncated": html_truncated,
        "_anchor_count": len(facts.anchors),
        "_facts": facts,
    }


async def _probe_all_sections(
    client: TracClient,
    sections: list[RenderedSection],
    check_targets: bool,
) -> dict[str, dict]:
    if not check_targets:
        return {}
    all_facts = [extract_facts(s.html) for s in sections]
    probeable: list[str] = []
    for facts in all_facts:
        probeable.extend(
            a.href
            for a in facts.anchors
            if a.href and is_probeable_wiki_href(a.href)
        )
    if not probeable:
        return {}
    return await run_sync(probe_targets, client, probeable)


def _document_response(
    target: str,
    section_results: list[dict],
    probes: dict[str, dict],
) -> types.CallToolResult:
    total_warnings = sum(len(s["warnings"]) for s in section_results)
    total_errors = sum(
        1
        for s in section_results
        for w in s["warnings"]
        if w["severity"] == "error"
    )
    total_anchors = sum(s["_anchor_count"] for s in section_results)
    targets_checked = sum(
        1 for p in probes.values() if p.get("status") != "skipped"
    )
    targets_skipped = len(probes) - targets_checked

    sections_out = []
    for s in section_results:
        entry = dict(s)
        del entry["_anchor_count"]
        del entry["_facts"]
        sections_out.append(entry)

    response_lines = [
        f"{target}: {total_errors} error(s), {total_warnings} warning(s) "
        f"across {len(section_results)} section(s).",
        "",
    ]
    for s in section_results:
        label = (
            f"[{s['kind']}]"
            if s["ref"] is None
            else f"[{s['kind']} {s['ref']}]"
        )
        if not s["warnings"]:
            response_lines.append(f"{label} No warnings.")
            continue
        for w in s["warnings"]:
            response_lines.append(
                f"{label} [{w['severity']}] {w['code']}: {w['message']}"
            )
    response_lines.append("")
    response_lines.append(
        f"Links: {sum(len(s['links']['ticket']) for s in section_results)} "
        f"ticket, {sum(len(s['links']['wiki']) for s in section_results)} "
        f"wiki, "
        f"{sum(len(s['links']['external']) for s in section_results)} "
        "external."
    )

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent={
            "target": target,
            "sections": sections_out,
            "stats": {
                "sections": len(section_results),
                "anchors": total_anchors,
                "warnings": total_warnings,
                "errors": total_errors,
                "targets_checked": targets_checked,
                "targets_skipped": targets_skipped,
            },
        },
    )


async def _handle_ticket_render_check(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_render_check."""
    ticket_id = args.get("ticket_id")
    if not ticket_id:
        return build_error_response(
            "validation_error",
            "ticket_id is required",
            "Provide ticket_id parameter.",
        )

    include_comments = args.get("include_comments", True)
    only_comment = args.get("comment")
    check_targets = args.get("check_targets", True)
    include_html = args.get("include_html", False)

    try:
        sections = await run_sync(
            fetch_ticket_sections, client, ticket_id, include_comments
        )
    except TicketNotFoundError as e:
        return build_error_response(
            "not_found",
            str(e),
            "Use ticket_search to verify ticket exists.",
        )
    except RenderCheckError as e:
        return build_error_response(
            "server_error",
            str(e),
            "Contact Trac administrator -- the ticket page template may "
            "have changed in a way this tool doesn't recognize.",
        )

    if only_comment is not None:
        comment_str = str(only_comment)
        sections = [
            s
            for s in sections
            if s.kind == "description" or s.ref == comment_str
        ]
        if not any(s.kind == "comment" for s in sections):
            return build_error_response(
                "not_found",
                f"Comment {only_comment} not found on ticket "
                f"#{ticket_id}.",
                "Use ticket_changelog to see which comment numbers exist.",
            )

    probes = await _probe_all_sections(client, sections, check_targets)
    section_results = [
        _section_result(s, probes, check_targets, include_html)
        for s in sections
    ]
    return _document_response(
        f"ticket/{ticket_id}", section_results, probes
    )


async def _handle_wiki_render_check(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_render_check."""
    page_name = args.get("page_name")
    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )

    version = args.get("version")
    check_targets = args.get("check_targets", True)
    include_html = args.get("include_html", False)

    section = await run_sync(
        fetch_wiki_render, client, page_name, version
    )
    sections = [section]
    probes = await _probe_all_sections(client, sections, check_targets)
    section_results = [
        _section_result(section, probes, check_targets, include_html)
    ]
    return _document_response(
        f"wiki/{page_name}", section_results, probes
    )


RENDER_CHECK_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=TICKET_RENDER_CHECK_TOOL,
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_ticket_render_check,
    ),
    ToolSpec(
        tool=WIKI_RENDER_CHECK_TOOL,
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_wiki_render_check,
    ),
]
