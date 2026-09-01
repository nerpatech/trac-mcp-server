"""Pure warning rules for ``convert_preview`` (ticket #56).

``build_warnings(markdown_source, tracwiki, facts, probes, check_targets)``
takes no I/O of its own -- every acceptance-suite row is unit-testable
without a server. Warning codes map one-to-one onto ticket #56 comment 1's
19-row suite; each rule below names the row(s) it exists for.
"""

import re
from typing import Any

from ..converters.common import TRACLINK_SCHEMES, _strip_code_fences
from .facts import PreviewFacts
from .targets import ERROR, MISSING, SKIPPED, is_probeable_wiki_href

# A code span whose entire body is a cross-instance/TracLink reference --
# e.g. `auto_pm:wiki:Reference/trac/InterTrac` or bare `wiki:Page` -- rather
# than a link. Anchored on TRACLINK_SCHEMES (a known Trac realm), with an
# optional leading InterTrac prefix, so an ordinary code span documenting
# unrelated syntax (`foo.py`, `key: value`) never matches (row 11).
_CODE_SPAN_LINK_RE = re.compile(
    r"\A(?:[A-Za-z][\w.+-]*:)?"
    rf"(?:{'|'.join(sorted(TRACLINK_SCHEMES))}):\S+\Z"
)

# TracWiki table-row syntax (`|=Header=|`) pasted into Markdown, where it
# is not valid GFM table syntax and so survives conversion as literal text
# instead of becoming a table (row 12).
_TRACWIKI_TABLE_RE = re.compile(r"\|=[^|\n]*=\|")

# TracWiki bold/monospace-block syntax typed directly in Markdown -- valid
# nowhere in CommonMark, so it also survives verbatim.
_TRACWIKI_BOLD_RE = re.compile(r"'''[^'\n]+'''")
_TRACWIKI_BLOCK_RE = re.compile(r"\{\{\{")

# A `prefix:#N` token -- the only shape that distinguishes an unconfigured
# InterTrac ticket prefix (row 16, must warn) from an ordinary colon-shaped
# token that happens to produce no anchor (row 10, must stay silent). See
# ticket #56 comment 2's calibration note: the `[intertrac]` prefix table
# isn't exposed over XML-RPC, so this is deliberately narrow.
_PREFIX_TICKET_RE = re.compile(r"\b[A-Za-z][\w.+-]*:#\d+\b")


def _warning(
    code: str, severity: str, message: str, evidence: Any = None
) -> dict:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "evidence": evidence,
    }


def _check_escaped_link_targets(facts: PreviewFacts) -> list[dict]:
    """`!`/`%21` inside a link target -- an escape that should have been
    consumed by Trac never was, or leaked into the stored target (row 1
    pre-fix at `85d9595`, the seeded defect for this suite)."""
    warnings = []
    for anchor in facts.anchors:
        if anchor.href and "%21" in anchor.href:
            warnings.append(
                _warning(
                    "escaped_link_target",
                    "error",
                    "Link target contains an escaped '!' (stored as "
                    "%21) -- it will resolve to a dead page.",
                    {"href": anchor.href, "text": anchor.text},
                )
            )
    return warnings


def _check_link_ref_in_code_span(facts: PreviewFacts) -> list[dict]:
    """A cross-instance/TracLink reference sitting inside a code span:
    inert text where a link was intended (row 11 -- the highest-value
    warning in the suite, per the ticket)."""
    warnings = []
    for span in facts.code_spans:
        if _CODE_SPAN_LINK_RE.match(span.strip()):
            warnings.append(
                _warning(
                    "link_ref_in_code_span",
                    "error",
                    "A link-shaped reference is backticked, so it "
                    "renders as inert text instead of a link.",
                    {"code_span": span},
                )
            )
    return warnings


def _check_tracwiki_markup_in_markdown(
    markdown_source: str,
) -> list[dict]:
    """TracWiki markup (`|=header=|`, `'''bold'''`, `{{{ }}}`) pasted into
    a Markdown candidate, where it is not valid Markdown and so would
    round-trip into visibly broken markup (row 12)."""
    scan_text = _strip_code_fences(markdown_source)
    warnings = []
    for pattern, label in (
        (_TRACWIKI_TABLE_RE, "table"),
        (_TRACWIKI_BOLD_RE, "bold"),
        (_TRACWIKI_BLOCK_RE, "code block"),
    ):
        match = pattern.search(scan_text)
        if match:
            warnings.append(
                _warning(
                    "tracwiki_markup_in_markdown",
                    "warning",
                    f"TracWiki {label} syntax found in Markdown input "
                    "-- it is not valid Markdown and will store/render "
                    "as literal text.",
                    {"matched": match.group(0)},
                )
            )
    return warnings


def _check_missing_local_target(facts: PreviewFacts) -> list[dict]:
    """A dead local wiki-page target (`class="missing wiki"`), including
    a hand-built relative URL that resolved to a nonexistent page
    (row 13)."""
    warnings = []
    for anchor in facts.anchors:
        if "missing" in anchor.classes:
            warnings.append(
                _warning(
                    "missing_local_target",
                    "error",
                    f"Link target '{anchor.text}' does not exist on "
                    "this instance.",
                    {"href": anchor.href, "text": anchor.text},
                )
            )
    return warnings


def _check_bare_ticket_ref(facts: PreviewFacts) -> list[dict]:
    """A bare `#N` auto-links to *this* instance's own ticket N -- name
    the resolved summary so the author can see whether that's actually
    what was meant (row 14). Restricted to local ticket anchors: an
    InterTrac ticket reference (`auto_pm:#87`) also carries a `ticket`-
    adjacent target but renders as `class="ext-link"`, not `class="...
    ticket"` -- see row 6, which must stay silent."""
    warnings = []
    for anchor in facts.anchors:
        if (
            "ticket" in anchor.classes
            and "ext-link" not in anchor.classes
        ):
            warnings.append(
                _warning(
                    "bare_ticket_ref",
                    "warning",
                    f"'{anchor.text}' resolves to: "
                    f"{anchor.title or '(no title)'}",
                    {"href": anchor.href, "title": anchor.title},
                )
            )
    return warnings


def _check_unconfigured_intertrac_prefix(
    tracwiki: str, facts: PreviewFacts
) -> list[dict]:
    """A `prefix:#N` token whose prefix is not in the `[intertrac]` table
    renders as plain text, not a link (row 16) -- distinguished from an
    ordinary colon-shaped token that must stay silent (row 10) only by
    the `:#N` shape itself; see the module docstring on `_PREFIX_TICKET_RE`."""
    # Substring, not exact-equality: a resolved InterTrac anchor's text
    # content includes a leading icon glyph Trac injects ahead of the
    # visible link text (confirmed against the live daemon), so an exact
    # `token == anchor.text` match would spuriously fire on row 6.
    anchor_texts = [a.text for a in facts.anchors]
    code_span_texts = [s for s in facts.code_spans]
    warnings = []
    for match in _PREFIX_TICKET_RE.finditer(tracwiki):
        token = match.group(0)
        if any(token in t for t in anchor_texts) or any(
            token in t for t in code_span_texts
        ):
            continue
        warnings.append(
            _warning(
                "unconfigured_intertrac_prefix",
                "warning",
                f"'{token}' does not match any configured InterTrac "
                "prefix -- it will render as plain text, not a link.",
                {"token": token},
            )
        )
    return warnings


def _check_target_probes(
    facts: PreviewFacts, probes: dict[str, str], check_targets: bool
) -> list[dict]:
    """Live-probe results for cross-instance InterTrac wiki targets
    (row 15), plus an explicit note whenever a probeable target existed
    but wasn't actually checked -- capped, disabled, or network-failed
    must never look like a clean pass."""
    probeable_hrefs = [
        a.href for a in facts.anchors if is_probeable_wiki_href(a.href)
    ]
    if not probeable_hrefs:
        return []

    warnings: list[dict] = []

    if not check_targets:
        warnings.append(
            _warning(
                "target_check_skipped",
                "info",
                f"{len(set(probeable_hrefs))} cross-instance target(s) "
                "found but not checked (check_targets=false).",
                {"hrefs": sorted(set(probeable_hrefs))},
            )
        )
        return warnings

    skipped_or_failed = []
    for anchor in facts.anchors:
        if not is_probeable_wiki_href(anchor.href):
            continue
        outcome = probes.get(anchor.href)
        if outcome == MISSING:
            warnings.append(
                _warning(
                    "missing_cross_instance_target",
                    "error",
                    f"'{anchor.text}' targets a page that does not "
                    "exist on the remote instance.",
                    {"href": anchor.href, "text": anchor.text},
                )
            )
        elif outcome in (SKIPPED, ERROR) or outcome is None:
            skipped_or_failed.append(anchor.href)

    if skipped_or_failed:
        warnings.append(
            _warning(
                "target_check_skipped",
                "info",
                f"{len(set(skipped_or_failed))} cross-instance target(s) "
                "could not be checked (capped or network-failed) -- not "
                "verified, not necessarily clean.",
                {"hrefs": sorted(set(skipped_or_failed))},
            )
        )

    return warnings


def build_warnings(
    markdown_source: str,
    tracwiki: str,
    facts: PreviewFacts,
    probes: dict[str, str],
    check_targets: bool,
) -> list[dict]:
    """Run every warning rule and return the combined list.

    Args:
        markdown_source: The caller's original Markdown input.
        tracwiki: The converted TracWiki text (what would be stored).
        facts: Extracted from the rendered HTML (what Trac would display).
        probes: Live-probe results for cross-instance targets, from
            :func:`trac_mcp_server.preview.targets.probe_targets`. Ignored
            when ``check_targets`` is False.
        check_targets: Whether the live probe actually ran.

    Returns:
        List of warning dicts, each ``{code, severity, message,
        evidence}``. Empty list means clean input, not "not checked" --
        pair with ``target_check_skipped`` for the latter.
    """
    warnings: list[dict] = []
    warnings.extend(_check_escaped_link_targets(facts))
    warnings.extend(_check_link_ref_in_code_span(facts))
    warnings.extend(_check_tracwiki_markup_in_markdown(markdown_source))
    warnings.extend(_check_missing_local_target(facts))
    warnings.extend(_check_bare_ticket_ref(facts))
    warnings.extend(
        _check_unconfigured_intertrac_prefix(tracwiki, facts)
    )
    warnings.extend(_check_target_probes(facts, probes, check_targets))
    return warnings
