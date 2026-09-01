"""Pure warning rules for ``convert_preview`` (ticket #56).

``build_warnings(markdown_source, tracwiki, facts, probes, check_targets)``
takes no I/O of its own -- every acceptance-suite row is unit-testable
without a server. Warning codes map one-to-one onto ticket #56 comment 1's
19-row suite; each rule below names the row(s) it exists for.
"""

import re
from typing import Any

from ..converters.common import (
    TRACLINK_SCHEMES,
    _strip_code_fences,
    blank_code_fences,
)
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
#
# Ticket #58 gave this a second job: it is also the InterTrac SHORT-LINK
# shape, which `_CODE_SPAN_LINK_RE` structurally cannot match (that one
# requires a TRACLINK_SCHEMES realm segment, and a short link has no
# realm). Its narrowness is what makes the two calibration rows fall out
# for free -- a bare backticked `#87` has no prefix, and a placeholder
# `prefix:#N` has no digits.
_PREFIX_TICKET_RE = re.compile(r"\b[A-Za-z][\w.+-]*:#\d+\b")

# Opt-out pragma (ticket #58). A document that DOCUMENTS this syntax has
# to quote it, and an anti-pattern section has to show what not to write
# -- measured, `auto_pm:wiki:Reference/trac/InterTrac` returns 8
# `link_ref_in_code_span` errors on entirely correct content, and #58's
# widening takes it to 13. Only 2 of those 8 have any mechanical signal
# (a metavariable placeholder, a deliberately-unconfigured example
# prefix), so recognising placeholder targets -- the ticket's own first
# suggestion -- would have left that page at 11 of 13. An author-declared
# opt-out is the only mechanism that reaches the other 6.
#
# Recognised anywhere in the source. The placement that renders as
# nothing is inside a Trac `#!comment` block (verified live: the block
# produces no output at all), which is also how this store already
# carries agent-facing directives on wiki pages. Note that Markdown
# cannot emit that block -- a Markdown fence around it nests it as a
# visible code block -- so on a Markdown-authored page the pragma goes in
# via a formatting-only TracWiki repair, or lives as a visible line.
#
# Scoped to the codes it names, never a document-wide mute: a page that
# opts out of one warning must still be checked for everything else.
_PRAGMA_RE = re.compile(
    r"^[ \t>]*preview-checks:[ \t]*allow[ \t]+([A-Za-z0-9_,][A-Za-z0-9_, \t]*?)[ \t]*$",
    re.MULTILINE,
)

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
    warning in the suite, per the ticket).

    Two shapes, not one (ticket #58). The realm form
    (`auto_pm:wiki:Page`) matches `_CODE_SPAN_LINK_RE`; the InterTrac
    SHORT-LINK form (`auto_pm:#87`) cannot -- that regex is anchored on a
    TRACLINK_SCHEMES realm segment and a short link has no realm, so no
    prefix could ever match it. The short link is the higher-value half:
    `Rules/trac/HashNumberAutoLinks` leads with exactly that shape, and
    the shape the rule names first was the one the checker could not see.

    Both are matched against the WHOLE stripped span, not searched
    within it -- a span carrying more than the token is prose quoting a
    reference, not a stranded link (rows 38/39/53).
    """
    warnings = []
    for span in facts.code_spans:
        body = span.strip()
        is_realm_form = _CODE_SPAN_LINK_RE.match(body) is not None
        is_short_link = _PREFIX_TICKET_RE.fullmatch(body) is not None
        if is_realm_form or is_short_link:
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


def _prefix_boundary_match(raw: str, candidate: str) -> bool:
    """True if `candidate` is `raw` verbatim, or `raw` with trailing
    punctuation the token regex's greedy `\\S+` glued on -- i.e.
    `candidate` is a prefix of `raw` and whatever follows isn't a
    continuation of the same identifier. `candidate` comes from Trac's
    OWN render (an anchor's text, or the resolved-form prefix of its
    title) -- using that as the true boundary, instead of guessing it
    from a punctuation character class, is what makes this robust to
    trailing backtick/hyphen/angle-bracket/etc without enumerating them
    (ticket #57 comment 6: the class can never be complete; comment 7's
    fix). Rejects a genuinely different, longer token (more digits, more
    path) rather than just anything that happens to start the same way
    -- comment 8's `tail` check is what makes this a boundary match
    rather than a bare prefix check."""
    if not raw.startswith(candidate):
        return False
    if len(raw) == len(candidate):
        return True
    tail = raw[len(candidate)]
    return not (tail.isalnum() or tail == "_")


def _code_span_contains(raw: str, code_span_text: str) -> bool:
    """True if `code_span_text` contains `raw`, or contains `raw` with
    trailing punctuation stripped one character at a time (the same
    greedy-`\\S+`-glued-on garbage `_prefix_boundary_match` handles for
    anchors). Containment, not a prefix check -- a code span legitimately
    contains MORE than the token (surrounding prose, e.g. `` `see
    auto_pm:wiki:Index here` ``), which a naive `_prefix_boundary_match`
    call would have broken (comment 8 caught this: `_prefix_boundary_match`
    asks whether `code_span_text` is a prefix of `raw`, backwards for
    this case)."""
    candidate = raw
    while candidate:
        if candidate in code_span_text:
            return True
        tail = candidate[-1]
        if tail.isalnum() or tail == "_":
            return False
        candidate = candidate[:-1]
    return False


def _captured_punctuation_anchor(raw: str, anchors) -> Any | None:
    """The anchor whose resolved target is `raw` with trailing text Trac
    swallowed into it, or None (ticket #59 comment 1).

    The exact mirror of `_prefix_boundary_match`: there the ANCHOR's text
    is a prefix of the token; here the TOKEN is a prefix of the anchor's
    text, and what follows is not a continuation of the same identifier.
    Measured against the live daemon: `.`, `,`, `)` and `;` after a short
    link are NOT captured -- Trac stops the token, the anchor text equals
    the token, and those stay correctly silent. `'s` and `-ish` ARE
    captured, producing `intertrac/%2387%27s` -- a link that reads as
    live and dispatches on a ticket that does not exist.

    The `isalnum()`/underscore tail test is what keeps a genuinely
    different, longer token out: an anchor for `auto_pm:#871` starts with
    `auto_pm:#87` too, but continues with a digit, so it is a different
    reference rather than this defect.

    Non-bracketed occurrences only -- its caller's bracketed branch
    compares against a label or a resolved suffix, not the token, so
    "the anchor text starts with the token" carries no meaning there.
    """
    for anchor in anchors:
        text = anchor.text.replace(_ZERO_WIDTH_ICON, "")
        if not text.startswith(raw) or len(text) == len(raw):
            continue
        tail = text[len(raw)]
        if not (tail.isalnum() or tail == "_"):
            return anchor
    return None


def _allowed_codes(*sources: str | None) -> set[str]:
    """Warning codes a `preview-checks: allow ...` pragma opts out of
    (ticket #58). See `_PRAGMA_RE` for why this exists and where the
    pragma goes."""
    allowed: set[str] = set()
    for source in sources:
        if not source:
            continue
        for match in _PRAGMA_RE.finditer(source):
            for code in re.split(r"[,\s]+", match.group(1)):
                if code:
                    allowed.add(code)
    return allowed


def _bracket_label(
    tracwiki: str, raw: str, token_end: int
) -> str | None:
    """The label text of a `[token label]` construct whose token ends at
    `token_end`, or None if `token_end` isn't inside a bracket at all.

    Handles the no-label shape (`[token]`, nothing between the target and
    the closing bracket) as its own case: the realm regex's greedy `\\S+`
    swallows the `]` into `raw` itself when there's no space before it, so
    `token_end` lands PAST the bracket and a forward search for `]` finds
    the wrong one (or none) -- this warned a correctly configured,
    label-less bracket as unconfigured until ticket #57 comment 8 caught
    it live. Trac renders a label-less bracket's anchor text as the bare
    resolved suffix (`wiki:Index`, not `auto_pm:wiki:Index`), which the
    caller already computes separately -- signalled here by returning ""
    rather than searching further, so the caller knows to compare against
    the suffix instead of a real label."""
    if raw.endswith("]"):
        return ""
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

    Every comparison here is scoped to the one token being checked, not a
    document-global scan -- the original implementation's substring
    checks let a typo'd prefix hide behind an unrelated anchor elsewhere
    in the same document (comment 4's reopen). And every comparison uses
    Trac's own rendered output (an anchor's text/title, a code span's
    content) as the ground truth for where a token actually ends, rather
    than guessing the boundary from a punctuation character class first
    and then comparing guesses (comment 6's reopen: the class can never
    enumerate every character Trac's tokenizer might stop at; comment 7/8
    settled on `_prefix_boundary_match`/`_code_span_contains` instead).

    Known accepted residual (comment 8): two bracketed occurrences with
    the SAME label and the SAME resolved target -- e.g. `[auto-pm:#87
    see]` and `[auto_pm:#87 see]` -- are textually indistinguishable from
    each other, so a typo'd one there stays silent. Resolving that needs
    actual source-position data this function doesn't have; deferred
    rather than risk a heuristic that could misattribute the warning to
    the GOOD occurrence instead (which order-based tie-breaking was
    checked and found to do here, since the anchor is the second
    occurrence's while the first is the dead one).
    """
    code_span_texts = list(facts.code_spans)
    anchors = facts.anchors
    # Fenced blocks are scanned OUT, not scanned over (ticket #59).
    # Inside a fence Trac renders no anchor at all, so this check's core
    # inference -- "prefix-shaped token, no anchor, therefore the prefix
    # is unconfigured" -- is unsound there, and it reported CONFIGURED
    # prefixes as unconfigured on any page carrying a fenced example of
    # InterTrac syntax (which includes bug reports about InterTrac
    # syntax, so it misfired on its own problem domain). Only inline
    # code spans were ever excluded, via `_code_span_contains` below.
    #
    # `blank_code_fences`, not `_strip_code_fences`: the offsets below
    # index back into `tracwiki` for the bracketed-label logic, so the
    # scanned copy has to be the same length as the original.
    scan_text = blank_code_fences(tracwiki)
    warnings = []
    for pattern in (_PREFIX_TICKET_RE, _PREFIX_REALM_RE):
        for match in pattern.finditer(scan_text):
            raw = match.group(0)

            if any(
                _code_span_contains(raw, s) for s in code_span_texts
            ):
                continue

            bracketed = (
                match.start() > 0 and tracwiki[match.start() - 1] == "["
            )
            if bracketed:
                label = _bracket_label(tracwiki, raw, match.end())
                if label == "":
                    # No custom label. For the realm form, `raw` includes
                    # the closing `]` the greedy regex swallowed -- drop
                    # it before deriving the suffix. The ticket form's
                    # `\b` never swallows it (`_bracket_label` found ""
                    # via the ordinary forward search instead), so only
                    # strip when it's actually there.
                    if raw.endswith("]"):
                        raw = raw[:-1]
                    expected_text = raw.split(":", 1)[1]
                else:
                    expected_text = label
                suffix = raw.split(":", 1)[1]
                configured = expected_text is not None and any(
                    a.text.replace(_ZERO_WIDTH_ICON, "")
                    == expected_text
                    and a.title is not None
                    and _prefix_boundary_match(
                        suffix, a.title.split(" in ", 1)[0]
                    )
                    for a in anchors
                )
            else:
                configured = any(
                    _prefix_boundary_match(
                        raw, a.text.replace(_ZERO_WIDTH_ICON, "")
                    )
                    for a in anchors
                )
                if not configured:
                    overrun = _captured_punctuation_anchor(raw, anchors)
                    if overrun is not None:
                        warnings.append(
                            _warning(
                                "intertrac_target_captured_punctuation",
                                "error",
                                f"'{raw}' rendered as a link, but Trac "
                                f"captured trailing text into the "
                                f"target: it dispatches on "
                                f"'{overrun.text.replace(_ZERO_WIDTH_ICON, '')}'"
                                ", not the reference you wrote. The "
                                "prefix is fine; the link is dead.",
                                {
                                    "token": raw,
                                    "resolved_as": overrun.text.replace(
                                        _ZERO_WIDTH_ICON, ""
                                    ),
                                    "href": overrun.href,
                                },
                            )
                        )
                        continue
            if configured:
                continue

            warnings.append(
                _warning(
                    "unconfigured_intertrac_prefix",
                    "warning",
                    f"'{raw}' does not match any configured InterTrac "
                    "prefix -- it will render as plain text, not a link.",
                    {"token": raw},
                )
            )
    return warnings


def _check_target_probes(
    facts: PreviewFacts, probes: dict[str, dict], check_targets: bool
) -> list[dict]:
    """Live-probe results for cross-instance InterTrac wiki targets
    (row 15), plus an explicit note whenever a probeable target existed
    but wasn't actually checked -- capped, disabled, or network-failed
    must never look like a clean pass."""
    probeable_hrefs: list[str] = [
        a.href
        for a in facts.anchors
        if a.href and is_probeable_wiki_href(a.href)
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

    skipped_or_failed: list[str] = []
    for anchor in facts.anchors:
        if not anchor.href or not is_probeable_wiki_href(anchor.href):
            continue
        outcome = probes.get(anchor.href, {})
        status = outcome.get("status")
        if status == MISSING:
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
        elif status in (SKIPPED, ERROR) or status is None:
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
    markdown_source: str | None,
    tracwiki: str,
    facts: PreviewFacts,
    probes: dict[str, dict],
    check_targets: bool,
) -> list[dict]:
    """Run every warning rule and return the combined list.

    Args:
        markdown_source: The caller's original Markdown input, or None
            when there is no Markdown source to check against -- the
            verify path (ticket #55) checks a live render, which has no
            corresponding Markdown candidate; ``_check_tracwiki_markup_
            in_markdown`` is skipped in that case, since it is the only
            rule keyed off Markdown source rather than ``facts``/
            ``tracwiki``.
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

        Codes named by a ``preview-checks: allow ...`` pragma in either
        source are dropped last, after every rule has run (ticket #58) --
        see ``_PRAGMA_RE``. Scoped to the codes it names, so a page that
        opts out of one warning is still checked for the rest.
    """
    warnings: list[dict] = []
    warnings.extend(_check_escaped_link_targets(facts))
    warnings.extend(_check_link_ref_in_code_span(facts))
    if markdown_source is not None:
        warnings.extend(
            _check_tracwiki_markup_in_markdown(markdown_source)
        )
    warnings.extend(_check_missing_local_target(facts))
    warnings.extend(_check_bare_ticket_ref(facts))
    warnings.extend(
        _check_unconfigured_intertrac_prefix(tracwiki, facts)
    )
    warnings.extend(_check_target_probes(facts, probes, check_targets))

    allowed = _allowed_codes(markdown_source, tracwiki)
    if allowed:
        warnings = [w for w in warnings if w["code"] not in allowed]
    return warnings
