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
import re

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
from ...preview.targets import (
    DEFAULT_TARGET_CAP,
    is_probeable_href,
    probe_targets,
)
from ...preview.verify import build_verify_warnings
from .errors import build_error_response
from .instances import local_intertrac_bases
from .registry import ToolSpec

logger = logging.getLogger(__name__)

# rendered_html cap, same rationale and value as convert_preview.py: the
# structured warnings/links already carry what matters, so the raw HTML
# defaults off here entirely (include_html=False) rather than just capped.
MAX_HTML_BYTES = 20_000

# Total HTML the text block carries across all sections. The per-section
# cap alone lets include_html on a long ticket (description plus dozens
# of comments) yield megabytes of text; structuredContent is unaffected.
MAX_TEXT_HTML_BYTES = 100_000

# Link text/href are printed on one line per entry, so they are collapsed
# to a single line and capped.
_MAX_LINK_TEXT_CHARS = 80
_MAX_LINK_HREF_CHARS = 200

# One `Ticket refs` entry (`#N` plus Trac's own title for the link) is
# capped like a link text, but longer: Trac already shortens the summary
# inside the title, and the type and status around it are the point.
_MAX_TICKET_REF_CHARS = 160

# The `Ticket refs` line lists at most this many entries. Each entry is
# capped by `_MAX_TICKET_REF_CHARS`, but the number of entries is not
# otherwise bounded: a document citing 300 distinct tickets would give a
# single ~49K-character line. Past the cap the line ends with a count of
# the rest; every ticket is still in `structuredContent` `ticket_refs`,
# which is neither capped nor shortened (ticket #152).
_MAX_TICKET_REFS = 50

# The ticket number a local ticket link points at, from its href. Trac
# writes `.../ticket/N`, with `#comment:M` or a query after it for the
# comment and query forms, which name the same ticket.
_TICKET_HREF_RE = re.compile(r"/ticket/(\d+)(?=[/?#]|$)")

TICKET_RENDER_CHECK_TOOL = types.Tool(
    name="ticket_render_check",
    description=(
        "Fetch a ticket's LIVE rendered page (description, and each "
        "comment by default) and return structured facts + warnings, "
        "replacing the curl+grep workaround Rules/trac/RenderVerify "
        "otherwise forces after every ticket write. Every outbound link "
        "grouped by realm (ticket/wiki/external) with its resolved "
        "target, which wiki links carry Trac's 'missing' class (an "
        "authored dead target, reported apart from a bare CamelCase "
        "word Trac auto-linked out of prose), code block counts, and "
        "the same defect checks "
        "convert_preview runs (escaped link targets, a cross-instance "
        "reference stuck in a code span -- realm form or `prefix:#N` "
        "short link -- an unconfigured InterTrac prefix, a link whose "
        "target swallowed trailing punctuation, and markup that "
        "survived as literal text instead of converting; a "
        "page can opt out of a named code with a "
        "'preview-checks: allow <code>' line in its source). Use this "
        "right after any "
        "ticket_update/ticket_create write to verify what Trac actually "
        "rendered, not just what was stored. The text result also lists "
        "each local ticket linked (once, with its summary, so a "
        "mistyped number shows up as the wrong summary), each missing "
        "link, the code blocks, and the rendered HTML when "
        "include_html is true, for clients that only see text."
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
            "target_cap": {
                "type": "integer",
                "description": (
                    "Maximum cross-instance targets to probe (default: "
                    "50). The cap keeps the FIRST N in document order, "
                    "so anything beyond it -- the END of the document "
                    "-- is reported as target_check_capped rather "
                    "than checked. Raise it only for a deliberate "
                    "audit of an unusually link-dense page; the "
                    "default is above anything measured in real "
                    "content."
                ),
                "default": 50,
                "minimum": 1,
                "maximum": 500,
            },
            "include_html": {
                "type": "boolean",
                "description": (
                    "Include each section's rendered HTML in the "
                    "response, structured and text (capped at 20KB per "
                    "section, with html_truncated set if cut). Default: "
                    "false -- the links/warnings are the point of this "
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
        "Trac's 'missing' class (an authored dead target, reported "
        "apart from a bare CamelCase word Trac auto-linked out of "
        "prose), code block counts, and "
        "the same defect checks convert_preview runs (escaped link "
        "targets, a cross-instance reference stuck in a code span -- "
        "realm form or `prefix:#N` short link -- an unconfigured "
        "InterTrac prefix, a link whose target swallowed trailing "
        "punctuation, and markup that survived as literal text "
        "instead of converting; a page can opt out of a "
        "named code with a 'preview-checks: allow <code>' line in its "
        "source). Use this right after any wiki_update/wiki_create "
        "write. The text result also lists each local ticket linked "
        "(once, with its summary, so a mistyped number shows up as the "
        "wrong summary), each missing link, the code blocks, and the "
        "rendered HTML when include_html is true, for clients that "
        "only see text."
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
            "target_cap": {
                "type": "integer",
                "description": (
                    "Maximum cross-instance targets to probe (default: "
                    "50). The cap keeps the FIRST N in document order, "
                    "so anything beyond it -- the END of the document "
                    "-- is reported as target_check_capped rather "
                    "than checked. Raise it only for a deliberate "
                    "audit of an unusually link-dense page; the "
                    "default is above anything measured in real "
                    "content."
                ),
                "default": 50,
                "minimum": 1,
                "maximum": 500,
            },
            "include_html": {
                "type": "boolean",
                "description": (
                    "Include the rendered HTML in the response, structured "
                    "and text (capped at 20KB, with html_truncated set "
                    "if cut). Default: false -- the links/warnings are "
                    "the point of this tool; set true only to inspect "
                    "raw markup."
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
        local_intertrac_bases=local_intertrac_bases(),
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
    target_cap: int = DEFAULT_TARGET_CAP,
) -> dict[str, dict]:
    if not check_targets:
        return {}
    all_facts = [extract_facts(s.html) for s in sections]
    probeable: list[str] = []
    for facts in all_facts:
        probeable.extend(
            a.href
            for a in facts.anchors
            if a.href and is_probeable_href(a.href)
        )
    if not probeable:
        return {}
    return await run_sync(probe_targets, client, probeable, target_cap)


def _section_name(section: dict) -> str:
    """``page`` / ``description`` / ``comment 3`` -- a section's name
    without the brackets ``_section_label`` puts around it."""
    if section["ref"] is None:
        return section["kind"]
    return f"{section['kind']} {section['ref']}"


def _section_label(section: dict) -> str:
    """``[page]`` / ``[description]`` / ``[comment 3]`` -- the one label
    every part of the text block uses for a section."""
    return f"[{_section_name(section)}]"


def _one_line(value: str, limit: int) -> str:
    """Collapse every whitespace run (newlines included) to one space
    and cap at ``limit`` characters, so an entry stays on one line."""
    collapsed = " ".join(value.split())
    if len(collapsed) > limit:
        return collapsed[:limit] + "…"
    return collapsed


def _missing_links_lines(section_results: list[dict]) -> list[str]:
    """Every link Trac rendered with the ``missing`` class, across all
    sections. Always emits a line, so a text-only client (which never
    sees structuredContent) can tell the check ran."""
    missing: list[str] = []
    for s in section_results:
        for realm, links in s["links"].items():
            for link in links:
                if not link["missing"]:
                    continue
                href = (
                    _one_line(link["href"], _MAX_LINK_HREF_CHARS)
                    if link["href"]
                    else "(no href)"
                )
                text = _one_line(link["text"], _MAX_LINK_TEXT_CHARS)
                missing.append(
                    f'  {_section_label(s)} {realm}: {href} "{text}"'
                )
    if not missing:
        return ["Missing links: none."]
    return [f"Missing links ({len(missing)}):", *missing]


def _ticket_ref_key(link: dict) -> str:
    """``#N`` for a link whose href names ticket N, else its text -- so
    ``#18``, ``ticket:18`` and ``[ticket:18 the bug]`` count as one."""
    match = _TICKET_HREF_RE.search(link["href"] or "")
    if match:
        return f"#{match.group(1)}"
    return _one_line(link["text"], _MAX_LINK_TEXT_CHARS)


def _ticket_ref_summary(key: str, title: str | None) -> str | None:
    """Trac's title for the link without its leading ``#N: `` (or, for a
    ``comment:`` link, ``Comment M for #N: ``), which would only repeat
    the key. ``None`` when there is no title."""
    if not title:
        return None
    summary = " ".join(title.split())
    prefix = f"{key}: "
    if summary.startswith(prefix):
        summary = summary[len(prefix) :]
    else:
        summary = re.sub(
            rf"^Comment \d+ for {re.escape(key)}: ", "", summary
        )
    return summary or None


def _ticket_refs(section_results: list[dict]) -> list[dict]:
    """Every local ticket the document links to, listed once, in the
    order first seen across all sections, with Trac's own summary for it
    (tickets #151, #152).

    This is the one place that de-duplicates: the text ``Ticket refs``
    line (``_ticket_refs_line``) and ``structuredContent`` ``ticket_refs``
    are both built from its result, so they cannot drift apart.

    A missing ticket is left out: that stays ``missing_local_target`` at
    ``error`` and is listed by ``_missing_links_lines``.

    When the same ticket is linked twice, the first link's summary wins,
    unless it had no ``#N: ...`` title (Trac titles a ``comment:`` link
    "Comment M for #N: <type>: <summary> (<status>)") and a later link
    does. ``first_section`` stays the section where the ticket was first
    seen either way.

    Each entry is ``{"ticket", "ref", "summary", "first_section"}``:
    ``ref`` is the key the text line prints (``#105``, or the link text
    when the href has no ``/ticket/N``), ``ticket`` the number as an int
    (``None`` in that fallback case), ``summary`` Trac's title without
    its leading ``#N: `` / ``Comment M for #N: `` (``None`` when there
    is no title), ``first_section`` a section name as in
    ``_section_name``. Nothing is capped here.
    """
    # key -> (entry, whether its summary came from a `#N: ...` title)
    refs: dict[str, tuple[dict, bool]] = {}
    for s in section_results:
        for link in s["links"]["ticket"]:
            if link["missing"]:
                continue
            key = _ticket_ref_key(link)
            summary = _ticket_ref_summary(key, link["title"])
            shaped = (link["title"] or "").startswith(f"{key}: ")
            if key not in refs:
                match = _TICKET_HREF_RE.search(link["href"] or "")
                refs[key] = (
                    {
                        "ticket": int(match.group(1))
                        if match
                        else None,
                        "ref": key,
                        "summary": summary,
                        "first_section": _section_name(s),
                    },
                    shaped,
                )
            elif shaped and not refs[key][1]:
                refs[key][0]["summary"] = summary
                refs[key] = (refs[key][0], True)
    return [entry for entry, _ in refs.values()]


def _ticket_refs_line(refs: list[dict]) -> str:
    """The ``Ticket refs`` text line for ``_ticket_refs``' list.

    It is NOT a finding: it does not count toward the error or warning
    totals. It is here for the one case no check can catch -- a mistyped
    number that happens to hit a real ticket -- which only the summary
    next to the number reveals.

    Only the line is capped: at ``_MAX_TICKET_REFS`` entries, each at
    ``_MAX_TICKET_REF_CHARS``. The complete list is in
    ``structuredContent`` ``ticket_refs``, which the line names when it
    leaves entries off.
    """
    if not refs:
        return "Ticket refs: none."
    entries = [
        _one_line(
            f"{r['ref']} {r['summary']}" if r["summary"] else r["ref"],
            _MAX_TICKET_REF_CHARS,
        )
        for r in refs[:_MAX_TICKET_REFS]
    ]
    line = f"Ticket refs ({len(refs)}): {'; '.join(entries)}"
    if len(refs) > _MAX_TICKET_REFS:
        line += (
            f"; ... and {len(refs) - _MAX_TICKET_REFS} more "
            "(all in structuredContent ticket_refs)"
        )
    return line


def _code_block_lines(section_results: list[dict]) -> list[str]:
    blocks = [
        f"  {_section_label(s)} {cb['lines']} line(s)"
        f"{', highlighted' if cb['highlighted'] else ''}"
        for s in section_results
        for cb in s["code_blocks"]
    ]
    if not blocks:
        return ["Code blocks: none."]
    return [f"Code blocks ({len(blocks)}):", *blocks]


def _html_lines(section_results: list[dict]) -> list[str]:
    """The (already capped) HTML of each section that carries it, i.e.
    when ``include_html`` was requested, in order, until the total
    would pass ``MAX_TEXT_HTML_BYTES``; the rest are named, not shown."""
    lines: list[str] = []
    total = 0
    omitted: list[str] = []
    for s in section_results:
        if s["html"] is None:
            continue
        size = len(s["html"].encode("utf-8"))
        if omitted or total + size > MAX_TEXT_HTML_BYTES:
            omitted.append(_section_label(s))
            continue
        total += size
        header = f"HTML {_section_label(s)}"
        if s["html_truncated"]:
            header += f" (truncated at {MAX_HTML_BYTES} bytes)"
        lines.extend(["", f"{header}:", s["html"]])
    if omitted:
        lines.extend(
            [
                "",
                f"HTML omitted for {len(omitted)} more section(s) "
                f"({', '.join(omitted)}): over the "
                f"{MAX_TEXT_HTML_BYTES}-byte text budget; re-run with "
                "comment=<n> for one comment.",
            ]
        )
    return lines


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

    ticket_refs = _ticket_refs(section_results)

    response_lines = [
        f"{target}: {total_errors} error(s), {total_warnings} warning(s) "
        f"across {len(section_results)} section(s).",
        "",
    ]
    for s in section_results:
        label = _section_label(s)
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
        "external, "
        f"{sum(len(s['links']['other']) for s in section_results)} other."
    )
    response_lines.append(_ticket_refs_line(ticket_refs))
    response_lines.extend(_missing_links_lines(section_results))
    response_lines.extend(_code_block_lines(section_results))
    response_lines.extend(_html_lines(section_results))

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent={
            "target": target,
            "sections": sections_out,
            "ticket_refs": ticket_refs,
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
    target_cap = args.get("target_cap", DEFAULT_TARGET_CAP)
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

    probes = await _probe_all_sections(
        client, sections, check_targets, target_cap
    )
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
    target_cap = args.get("target_cap", DEFAULT_TARGET_CAP)
    include_html = args.get("include_html", False)

    section = await run_sync(
        fetch_wiki_render, client, page_name, version
    )
    sections = [section]
    probes = await _probe_all_sections(
        client, sections, check_targets, target_cap
    )
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
