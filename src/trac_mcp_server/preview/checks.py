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

# Realms recognized by `_PREFIX_REALM_RE` specifically -- narrower than
# TRACLINK_SCHEMES (which `_CODE_SPAN_LINK_RE` and `is_link_target` keep
# using unchanged; reusing it here was expedient, not deliberate). The
# full scheme list includes ordinary English words -- `search`, `comment`,
# `export`, `diff`, `timeline`, `browser` -- that false-positive on
# plausible prose (`svn:diff:123`, `see:search:Results`, `ref:comment:3`,
# `build:export:Artifacts`, all measured warning against the live daemon,
# ticket #57 comment 4). This list keeps every realm actually measured
# resolving through a configured InterTrac prefix (`wiki`, `ticket`,
# `report`, `changeset`, `source`, `attachment`, `milestone`), plus `log`,
# kept deliberately in spite of the `git:log:HEAD`-shaped-prose residual.
_INTERTRAC_REALMS = frozenset(
    {
        "wiki",
        "ticket",
        "report",
        "changeset",
        "source",
        "attachment",
        "milestone",
        "log",
    }
)

# A `prefix:realm:target` token -- the realm-form counterpart to
# `_PREFIX_TICKET_RE` (ticket #57). Keyed on `_INTERTRAC_REALMS` rather
# than any colon-shaped token, so prose like `TODO:fix:Later` or
# `note:see:Below` -- whose middle segment isn't a real Trac realm --
# never matches. A configured prefix in this shape rendered an anchor for
# every realm tried, measured against the live daemon (`auto_pm:ticket:87`,
# `auto_pm:report:1`, `auto_pm:changeset:1`, `auto_pm:attachment:foo` all
# resolved, per the ticket), so "realm-shaped token, no anchor" is exactly
# as sound an inference here as it is for the ticket form.
_PREFIX_REALM_RE = re.compile(
    r"\b[A-Za-z][\w.+-]*:(?:"
    + "|".join(sorted(_INTERTRAC_REALMS))
    + r"):\S+"
)

# Trailing punctuation a realm-form match's trailing `\S+` swallows when
# the token closes a sentence or sits inside other punctuation -- Trac's
# own renderer stops the link target at the punctuation, so leaving it on
# the token defeats every comparison below and false-positives a
# correctly configured link as unconfigured (ticket #57 comment 4, the
# false-positive counterpart to the title-fallback defects).
_TRAILING_PUNCT_RE = re.compile(r"[.,;:!?'\"\)\]}]+\Z")

# The zero-width glyph Trac injects ahead of an InterTrac anchor's visible
# text (`<span class="icon">​</span>`) -- stripped before any exact
# text comparison so the comparison can BE exact instead of a substring
# check (ticket #57 comment 4, change 4).
_ZERO_WIDTH_ICON = "\u200b"


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
                    f"Link labeled '{anchor.text}' targets "
                    f"{anchor.href or '(unknown href)'}, which does not "
                    "exist on this instance.",
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


def _bracket_label(tracwiki: str, token_end: int) -> str | None:
    """The label text of a `[token label]` construct whose token ends at
    `token_end`, or None if `token_end` isn't inside a bracket at all.
    Used only to scope the title fallback below to the shape it exists
    for (row 3) -- see ticket #57 comment 4, change 2."""
    close = tracwiki.find("]", token_end)
    if close == -1:
        return None
    return tracwiki[token_end:close].strip()


def _check_unconfigured_intertrac_prefix(
    tracwiki: str, facts: PreviewFacts
) -> list[dict]:
    """A `prefix:#N` or `prefix:realm:target` token whose prefix is not in
    the `[intertrac]` table renders as plain text, not a link (rows 16 and
    #57's realm-form fix) -- distinguished from an ordinary colon-shaped
    token that must stay silent (row 10) only by the token's shape itself;
    see the module docstrings on `_PREFIX_TICKET_RE` and `_PREFIX_REALM_RE`.

    Every comparison here is exact and scoped to the one token being
    checked, not a document-global substring scan -- the original
    implementation's substring checks let a typo'd prefix hide behind an
    unrelated anchor elsewhere in the same document (ticket #57 comment
    4's reopen: a typo paired with a correct reference to the SAME
    target, or even to a target whose number is a superstring of the
    typo's, went silent). Reusing a resolved anchor's rendered text/title
    to recognize a configured prefix is still correct -- it just has to
    be the anchor that resolved THIS token, not any anchor at all.
    """
    code_span_texts = list(facts.code_spans)
    warnings = []
    for pattern in (_PREFIX_TICKET_RE, _PREFIX_REALM_RE):
        for match in pattern.finditer(tracwiki):
            # Trac's own renderer stops a realm-form target at trailing
            # punctuation (`auto_pm:wiki:Index.` renders as `auto_pm:
            # wiki:Index` plus a literal period) -- match that by
            # trimming before any comparison, or a correctly configured
            # link at the end of a sentence false-positives (row 31).
            token = _TRAILING_PUNCT_RE.sub("", match.group(0))
            if any(token in t for t in code_span_texts):
                continue

            # A bracketed link with a custom label (`[auto_pm:wiki:Page
            # label]`, row 3) renders anchor text "label", not the token,
            # so the ordinary exact-text check below can never recognize
            # it -- only a bracketed occurrence needs this fallback; a
            # bare token in prose never does (row 27/28 relied on this
            # being applied too broadly).
            bracketed = (
                match.start() > 0 and tracwiki[match.start() - 1] == "["
            )
            if bracketed:
                label = _bracket_label(tracwiki, match.end())
                suffix = token.split(":", 1)[1]
                title_prefix = f"{suffix} in "
                configured = label is not None and any(
                    a.text.replace(_ZERO_WIDTH_ICON, "") == label
                    and a.title is not None
                    and a.title.startswith(title_prefix)
                    for a in facts.anchors
                )
            else:
                configured = any(
                    a.text.replace(_ZERO_WIDTH_ICON, "") == token
                    for a in facts.anchors
                )
            if configured:
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
            # Prefer the resolved title Trac attaches to an InterTrac
            # anchor (e.g. "wiki:Page in Automated Project Manager") over
            # the anchor's visible text: for a `[target label]`-style
            # link, `anchor.text` is the LABEL, not the target, and using
            # it here reads as "'label' targets a page that does not
            # exist" -- correct but confusing, since "label" isn't a
            # page name at all (found via live smoke test, ticket #56).
            target_desc = anchor.title or anchor.href or anchor.text
            warnings.append(
                _warning(
                    "missing_cross_instance_target",
                    "error",
                    f"Link labeled '{anchor.text}' targets "
                    f"{target_desc}, which does not exist on the "
                    "remote instance.",
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
