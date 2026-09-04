"""Pure warning rules for ``convert_preview`` (ticket #56).

``build_warnings(markdown_source, tracwiki, facts, probes, check_targets)``
takes no I/O of its own -- every acceptance-suite row is unit-testable
without a server. Warning codes map one-to-one onto ticket #56 comment 1's
19-row suite; each rule below names the row(s) it exists for.
"""

import re
from typing import Any
from urllib.parse import unquote

from ..converters.common import (
    TRACLINK_SCHEMES,
    blank_code_fences,
    blank_inline_code_spans,
    describe_indentation_loss,
    find_code_block_indentation_loss,
)
from .facts import PreviewFacts
from .targets import ERROR, MISSING, SKIPPED, is_probeable_href

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

# The RENDER-scanning counterparts of the two patterns above, moved here
# from `verify.py` by ticket #77 so the check they feed runs on every
# caller of `build_warnings` rather than only the verify path. The names
# carry the `_RENDER_` prefix because the source-scanning pair above
# already owns the plain names, and the two pairs answer opposite
# questions: those scan Markdown SOURCE for TracWiki syntax that won't
# convert, these scan a RENDER for markup that survived unconverted.
# Deliberately not merged with the source pair even where the regex is
# currently identical -- they must be free to diverge.
_RENDER_TRACWIKI_TABLE_RE = re.compile(r"\|=[^|\n]*=\|")
_RENDER_TRACWIKI_BLOCK_RE = re.compile(r"\{\{\{|\}\}\}")

# Markdown residue that should have been converted to TracWiki (bold,
# fenced code, inline link) but survived as literal text in the render --
# the inverse of `_TRACWIKI_BOLD_RE`/`_TRACWIKI_BLOCK_RE` above (e.g.
# hand-edited via `raw=true`, TracWiki-declared input carrying Markdown
# link syntax, or a converter defect that let Markdown straight through).
_MARKDOWN_BOLD_RE = re.compile(r"\*\*[^*\n]+\*\*")
_MARKDOWN_FENCE_RE = re.compile(r"```")
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]\n]+\]\([^)\n]+\)")

# The same shape with its halves named, for the fix suggestion (ticket
# #64 section 5). Kept separate from `_MARKDOWN_LINK_RE` rather than
# adding groups to it: that one is used with `.search` against a whole
# render and its job is detection, this one is used with `.fullmatch`
# against a single already-matched token and its job is rewriting.
_MARKDOWN_LINK_PARTS_RE = re.compile(
    r"\[(?P<label>[^\]\n]+)\]\((?P<target>[^)\n]+)\)"
)

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
# The trailing class excludes a backtick (ticket #61). `\S+` did not
# stop at one, so a token whose inline code span was followed
# immediately by non-whitespace -- a table cell delimiter is the common
# case, since `||` never has a space before it -- ran past the closing
# backtick and swallowed the next cell. `_code_span_contains` then
# compared a token longer than any rendered span, found no containment,
# and the check called a CONFIGURED prefix unconfigured. Trac ends an
# inline code span at the backtick, so ending the token there too makes
# the two agree by construction rather than by recovery. This is the
# same class as #59's fenced-block gap, at the other delimiter.
_PREFIX_REALM_RE = re.compile(
    r"\b[A-Za-z][\w.+-]*:(?:"
    + "|".join(sorted(_INTERTRAC_REALMS))
    + r"):[^\s`]+"
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
                    # `warning`, not `error`, since ticket #64: severity
                    # is now the blocking column, and #64 section 4 puts
                    # this code in the advisory one. Its legitimate
                    # population is a page documenting link syntax --
                    # measured on `Reference/trac/InterTrac`, which
                    # returns 8 of these on entirely correct content
                    # (see `_PRAGMA_RE`). Refusing that write is worse
                    # than reporting it.
                    "link_ref_in_code_span",
                    "warning",
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
    round-trip into visibly broken markup (row 12).

    Only called for Markdown-declared input -- see ``build_warnings``.
    On TracWiki-declared input this markup is correct content, and
    warning about it fires on essentially every write (ticket #65).

    Two scoping steps, both ticket #65, both blanking rather than
    stripping so the reported ``match.group(0)`` comes from a document
    the same shape as the caller's:

    - ``blank_code_fences`` rather than ``_strip_code_fences``. The old
      function never closes a fence opened and closed on ONE line, so
      everything after such a line read as fence interior and genuine
      markup following it was never seen -- a false NEGATIVE, and the
      more dangerous sign. (The fenced-block false POSITIVE that ticket
      originally reported never existed: fences were always scoped out.)
    - ``blank_inline_code_spans``, second, once the fences' own
      backticks are gone. Markup inside backticks is quoted on purpose
      and renders as literal text by intent, which is the very outcome
      this check warns about.
    """
    scan_text = blank_inline_code_spans(
        blank_code_fences(markdown_source)
    )
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


def _check_code_block_indentation_loss(
    markdown_source: str, tracwiki: str
) -> list[dict]:
    """A code block whose body lost leading whitespace on the way
    through the converter (ticket #68).

    The only rule in this suite about damage that is INVISIBLE. Every
    other one describes content a reader can see is wrong -- literal
    markup, a dead link, a reference stuck in a code span. This one
    describes content that is gone, with a plausible render and an
    otherwise-empty warnings list over the top of it, which is why the
    severity is `error` rather than `warning` (the precedent is #59,
    where `intertrac_target_captured_punctuation` became an error for
    naming a genuinely dead link).

    `tracwiki_markup_in_markdown` cannot be widened to cover this. It
    runs `blank_code_fences` first, and that blanks a `{{{ ... }}}`
    region INCLUDING its delimiters -- so the `{{{` it scans for is
    erased before the scan reaches it, and the shape that gets
    destroyed is precisely the shape it is built to ignore. The two are
    complements, not overlapping.

    Detection lives in `converters.common` rather than here so the
    write path (`wiki_file_push`) and the standalone `trac-convert`
    binary can reach it too -- `preview` is not packaged into that
    binary, and #68 comment 4 measured `wiki_file.py` storing damaged
    bytes without ever calling `build_warnings`.
    """
    return [
        _warning(
            "code_block_indentation_loss",
            "error",
            describe_indentation_loss(loss),
            loss,
        )
        for loss in find_code_block_indentation_loss(
            markdown_source, tracwiki
        )
    ]


# -- Ticket #79: telling an authored dead link apart from a CamelCase
# word Trac auto-linked out of ordinary prose. Both render as
# `<a class="missing wiki" href=".../wiki/<Name>">`, so `facts` alone
# cannot separate them; the discriminating information is in the SOURCE.

# A page name Trac's WikiFormatting engine will auto-link when it
# appears bare in prose: two or more humps of `[A-Z][a-z]+`, optionally
# joined by `/`.
#
# DERIVED FROM THE DEPLOYED DAEMON, not from Trac's source and not from
# reasoning about CamelCase -- see `MEASURED_AUTOLINK_SHAPES` in
# `tests/test_preview_checks.py`, which is the authority this pattern is
# checked against. Ticket #37 is why: that ticket's escape regex
# false-positived on `PyVISA`, and an acronym tail is exactly the shape
# a hand-reasoned rule gets wrong. Measured here, `PyVISA`, `XMLParser`,
# `ABCDef`, `iPhone`, `Abc`, `AbcDef2`, `Abc2Def` and `A1B2` are all left
# alone by Trac, while `WiFi`, `LoRa`, `McDonald` and `Rules/RenderVerify`
# are linked.
#
# The safety direction is asymmetric. Too NARROW leaves an anchor
# reported as `missing_local_target` at `error` -- today's behaviour, and
# a false positive that already exists. Too WIDE downgrades a genuinely
# broken link to advisory, which is the failure #70 and #77 were
# prerequisites about. So where the measurement is ambiguous this stays
# narrow.
_AUTOLINK_PAGE_NAME_RE = re.compile(r"[A-Z][a-z]+(?:/?[A-Z][a-z]+)+")

# The same shape as a scanner over source text, with the boundaries the
# `fullmatch` above gets for free. `!` in the lookbehind is the escape
# pin: `!TracClient` renders no anchor at all, so an escaped word must
# not be counted as a bare occurrence -- counting it would let an escaped
# mention mask a genuinely authored dead link to the same page. `/` in
# both lookarounds keeps `RenderVerify` from matching inside
# `Rules/trac/RenderVerify`, which Trac does NOT auto-link (a lowercase
# path segment breaks the rule, measured).
_BARE_AUTOLINK_RE = re.compile(
    r"(?<![\w/!])[A-Z][a-z]+(?:/?[A-Z][a-z]+)+(?![\w/])"
)

# The page name segment of a local wiki href, e.g.
# `.../auto_pm/wiki/TracClient` -> `TracClient`. Deliberately the FIRST
# `/wiki/`, not the last: `row13_hand_relative_url`'s hand-built relative
# URL resolves to `.../wiki/wiki/Page`, and taking the first yields
# `wiki/Page`, which does not equal the anchor's text and so keeps that
# row an error.
_WIKI_HREF_PAGE_RE = re.compile(r"/wiki/(.+)\Z")

# Link constructs blanked out of the source before the bare-occurrence
# scan, because a name inside one of these was TYPED as a link rather
# than linkified by Trac. `[[...]]` first: blanking single brackets first
# would eat `[[X]` and leave a stray `]`.
#
# Over-blanking here is the SAFE direction -- it can only lower the bare
# count, which turns an advisory back into an error and so returns the
# anchor to today's behaviour. Under-blanking is what would hide a real
# dead link.
_MACRO_LINK_RE = re.compile(r"\[\[.*?\]\]", re.DOTALL)
_BRACKET_LINK_RE = re.compile(r"\[[^\]\n]*\]")
_REALM_REF_RE = re.compile(
    r"\b(?:[A-Za-z][\w.+-]*:)?"
    rf"(?:{'|'.join(sorted(TRACLINK_SCHEMES))}):\S+"
)


def _is_autolink_page_name(name: str) -> bool:
    """Whether Trac would auto-link ``name`` appearing bare in prose."""
    return _AUTOLINK_PAGE_NAME_RE.fullmatch(name) is not None


def _blank_preserving_layout(match: re.Match[str]) -> str:
    """Spaces for every character but newlines -- same length, same line
    structure, so nothing downstream sees a document of a different
    shape (the convention ``blank_code_fences`` established)."""
    return "".join("\n" if ch == "\n" else " " for ch in match.group(0))


def _bare_autolink_counts(tracwiki: str) -> dict[str, int]:
    """How many times each auto-linkable page name occurs BARE in the
    source -- outside code, outside link syntax, and unescaped.

    Trac does not linkify inside a code span or a fenced block, and a
    name inside ``[[...]]``, ``[target label]`` or a ``wiki:`` realm
    reference was typed as a link by a person. What survives all of that
    and still matches the auto-link shape is a word Trac linkified on
    its own.
    """
    scan = blank_inline_code_spans(blank_code_fences(tracwiki))
    for pattern in (_MACRO_LINK_RE, _BRACKET_LINK_RE, _REALM_REF_RE):
        scan = pattern.sub(_blank_preserving_layout, scan)

    counts: dict[str, int] = {}
    for match in _BARE_AUTOLINK_RE.finditer(scan):
        counts[match.group(0)] = counts.get(match.group(0), 0) + 1
    return counts


def _autolinked_page_name(anchor: Any) -> str | None:
    """The page name this anchor would have if Trac auto-linked it out
    of prose, or None if its shape rules that out.

    Two of the three gates live here. The anchor's text must EQUAL the
    page name in its href -- an auto-link's text is its target, so a
    label (``[wiki:DeadPage some words]``) can never be incidental --
    and the name must be one Trac would auto-link at all. The third
    gate, whether it actually occurs bare in the source, is the
    caller's.
    """
    if not anchor.href or "wiki" not in anchor.classes:
        return None
    match = _WIKI_HREF_PAGE_RE.search(unquote(anchor.href))
    if not match:
        return None
    page = match.group(1)
    if anchor.text.replace(_ZERO_WIDTH_ICON, "") != page:
        return None
    if not _is_autolink_page_name(page):
        return None
    return page


def _check_missing_local_target(
    facts: PreviewFacts, tracwiki: str
) -> list[dict]:
    """A dead local wiki-page target (`class="missing wiki"`), including
    a hand-built relative URL that resolved to a nonexistent page
    (row 13) -- split by ticket #79 into the population somebody
    authored and the population Trac auto-linked out of prose.

    Two entirely different things produce a `missing wiki` anchor, and
    THE RENDER CANNOT TELL THEM APART: an authored link to a page that
    does not exist, and an ordinary CamelCase word Trac linkified
    whether or not anybody meant it as a reference. Ticket #79 measured
    291 findings across two stores, of which 122 were the second kind
    -- 48 correct documents that #64's blocking error column would have
    refused, including one comment whose offending text was
    `"WiFi"/"LoRa"` inside a sentence quoting this exact defect.

    So the incidental population keeps its signal at `warning` and
    stops refusing writes, while `missing_local_target` keeps its name,
    its `error` severity and the 169 authored findings.

    THREE GATES, not one. Ticket #79 section 5 proposed a single test
    -- does the anchor's text still occur bare once link syntax is
    blanked -- and that test alone introduces a false negative, which
    is the one direction this ticket must not move in. `See [[Page]].
    The Page is missing.` renders exactly ONE anchor, from the authored
    `[[Page]]`, because `Page` is a single hump Trac does not
    auto-link; but `Page` does occur bare in the remainder, so the
    one-test form downgrades a genuinely dead link. The shape gate in
    `_autolinked_page_name` is what keeps that row an error.

    COUNTING, not a boolean, so a mixed document reports both. A
    document carrying `[[TracClient]]` AND the prose word `TracClient`
    renders two identical anchors; a boolean answers "yes, it occurs
    bare" for the whole document and downgrades both, losing the dead
    link while reporting zero errors. That is ticket #70's residual
    arriving through a new door. Counting bare occurrences per target
    and spending them one anchor at a time reports one of each.

    Which anchor of a matched pair carries which code is arbitrary --
    they are byte-identical in `facts`, so the evidence dicts are
    indistinguishable and only the counts carry meaning. Accepted the
    way #59 comment 8's same-label residual was, and for the same
    reason: separating them needs source positions the render does not
    have.

    NO SOURCE MEANS NO DOWNGRADE. `build_verify_warnings` passes
    `tracwiki=""` when `render_check` could not pair a source with the
    render. With no source there is no discriminator, so every anchor
    stays an error -- unchanged behaviour -- rather than being
    downgraded for lack of evidence, which would fail silent.
    """
    budget = _bare_autolink_counts(tracwiki) if tracwiki else {}
    warnings = []
    for anchor in facts.anchors:
        if "missing" not in anchor.classes:
            continue
        page = _autolinked_page_name(anchor)
        if page is not None and budget.get(page, 0) > 0:
            budget[page] -= 1
            warnings.append(
                _warning(
                    "incidental_wiki_autolink",
                    "warning",
                    f"'{page}' is a bare CamelCase word, so Trac "
                    "auto-linked it to a page that does not exist -- "
                    "nothing here was authored as a link. Write "
                    f"'!{page}' to keep it as plain text.",
                    {
                        "href": anchor.href,
                        "text": anchor.text,
                        "suggestion": f"!{page}",
                    },
                )
            )
            continue
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
    this case).

    Note the limit of that stripping, and why it is acceptable: it gives
    up at the first alphanumeric or underscore, so it cannot walk back
    across a whole word. It therefore cannot recover from a token that
    over-ran a code span's closing delimiter and swallowed the following
    text -- which is precisely what ticket #61 measured. That is fixed at
    the boundary instead: `_PREFIX_REALM_RE` no longer crosses a
    backtick, so a token that starts inside a code span now ends inside
    it, and this helper is never asked to recover from that case."""
    candidate = raw
    while candidate:
        if candidate in code_span_text:
            return True
        tail = candidate[-1]
        if tail.isalnum() or tail == "_":
            return False
        candidate = candidate[:-1]
    return False


# The target segment of an InterTrac dispatcher href, percent-encoded --
# e.g. ".../intertrac/%2387" (`#87`) or ".../intertrac/%2387%27s"
# (`#87's`). This segment is Trac's OWN resolved dispatch target: it is
# literally what the link dispatches on, which is why ticket #70's fix
# reads it instead of comparing a source token against anchor text.
_INTERTRAC_HREF_TARGET_RE = re.compile(r"/intertrac/([^/?#]+)\Z")

# A clean short-link target: `#` and digits, nothing else.
_CLEAN_TICKET_TARGET_RE = re.compile(r"\A#\d+\Z")

# The leading short-link part of a target; whatever follows it is text
# Trac swallowed in.
_TICKET_TARGET_PREFIX_RE = re.compile(r"\A#\d+")


def _check_captured_punctuation(facts: PreviewFacts) -> list[dict]:
    """A short link whose dispatcher target swallowed trailing text, so
    it renders as a live link onto a ticket that does not exist (ticket
    #59 comment 1, rebuilt on ticket #70).

    '''Judged from the anchor's href, not by pairing it with a source
    token.''' The href's `/intertrac/` segment is Trac's own dispatch
    target, so `#87's` is a dead reference no matter what else the
    document contains. That removes the document-global comparison this
    check used to depend on, and with it two measured defects:

    * ticket #70 -- the previous implementation asked whether ANY anchor
      in the document matched the token, so a CORRECT reference to the
      same ticket elsewhere satisfied the match and silenced the broken
      one, reporting zero errors on a document carrying a dead link;
    * ticket #78 -- and in the other direction, a token in the LABEL of
      an ordinary external link was compared against that link's own
      anchor text, reporting a dead InterTrac dispatch for a link that
      never dispatched at all. An external href simply is not an
      InterTrac dispatcher href, so that shape is now unreachable.

    Scoped to the ticket form deliberately. The realm form's greedy
    token match pulls the captured text into the token itself, so this
    check was always silent on it; what catches that shape is
    `_check_target_probes`, which live-probes each anchor and is
    per-anchor already. Measured on the deployed daemon 2026-09-03:
    `auto_pm:wiki:Index's` reports `missing_cross_instance_target`.

    The `\\d+`-then-remainder split is what keeps a genuinely different,
    longer ticket out: `#871` is a clean target, not `#87` plus a `1`.
    """
    warnings = []
    for anchor in facts.anchors:
        match = _INTERTRAC_HREF_TARGET_RE.search(anchor.href or "")
        if not match:
            continue
        target = unquote(match.group(1))
        if _CLEAN_TICKET_TARGET_RE.match(target):
            continue
        prefix = _TICKET_TARGET_PREFIX_RE.match(target)
        if not prefix:
            continue
        tail = target[prefix.end() :]
        text = anchor.text.replace(_ZERO_WIDTH_ICON, "")
        # The reference the author meant is the rendered text without
        # the swallowed tail -- the same `token` value the pre-#70
        # implementation reported, so nothing downstream changes shape.
        token = text[: -len(tail)] if text.endswith(tail) else text
        evidence: dict[str, Any] = {
            "token": token,
            "resolved_as": text,
            "href": anchor.href,
        }
        suggestion = _bracket_form_for_captured_token(token, tail)
        if suggestion is not None:
            evidence["suggestion"] = suggestion
        warnings.append(
            _warning(
                "intertrac_target_captured_punctuation",
                "error",
                f"'{token}' rendered as a link, but Trac captured "
                f"trailing text into the target: it dispatches on "
                f"'{text}', not the reference you wrote. The prefix is "
                "fine; the link is dead. Rephrasing so the reference "
                "ends the clause avoids the capture entirely.",
                evidence,
            )
        )
    return warnings


def _bracket_form_for_captured_token(
    token: str, tail: str
) -> str | None:
    """The bracket form that stops Trac swallowing `tail` into the
    target, or None when the token is not the expected shape.

    `auto_pm:#87's` becomes `[auto_pm:#87 #87]'s`: the bracket form
    from auto_pm:wiki:Rules/trac/PreferInterTracLinks's decision table
    ends the target at the space, so the possessive stays outside it.
    The label is the token's own suffix (`#87`), which keeps the
    rendered text as close to what the author typed as the fix allows.

    **Verified against the live daemon before being emitted as advice**
    (2026-09-04): the defect line reports
    `intertrac_target_captured_punctuation` and this exact replacement
    renders silent, with a live anchor. A suggestion that had never
    been rendered would be the same guess the check already refuses to
    make about a diagnosis.
    """
    if ":" not in token or not tail:
        return None
    label = token.split(":", 1)[1]
    if not label:
        return None
    return f"[{token} {label}]{tail}"


def _captured_punctuation_anchor(raw: str, anchors) -> Any | None:
    """The anchor whose resolved target is `raw` with trailing text Trac
    swallowed into it, or None (ticket #59 comment 1).

    '''Suppression only since ticket #70.''' The error itself is now
    reported by `_check_captured_punctuation` from the anchor's href.
    This is kept because the unconfigured-prefix check still needs to
    know not to misdiagnose such a token: the anchor's text is the token
    PLUS the swallowed tail, so `_prefix_boundary_match` cannot match it
    and the token would otherwise be reported as an unconfigured prefix
    -- the right-signal/wrong-diagnosis case ticket #59 removed. A false
    suppression here costs at most a missed `unconfigured_intertrac_
    prefix` warning, never a wrong error.

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
                    # Suppression only (ticket #70): the error is raised
                    # by `_check_captured_punctuation` from the href.
                    # All this decides is that the prefix is NOT
                    # unconfigured, so the token does not fall through
                    # to the wrong diagnosis below.
                    if _captured_punctuation_anchor(raw, anchors):
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


def _check_literal_markup_in_render(facts: PreviewFacts) -> list[dict]:
    """Markup that should have been converted but survived as literal
    text in the rendered page -- scoped to ``facts.prose_text`` (NOT
    ``facts.plain_text``), which excludes ``<pre>``/``<code>`` subtrees.
    A page that legitimately documents this syntax inside a code block
    must stay silent; scanning ``plain_text`` instead would warn on every
    such block (the over-correction pin ticket #55 calls out).

    Added by ticket #55 and reachable only through the verify path until
    ticket #77 moved it here: it is the one check that looks at what came
    OUT of the render rather than what went in, and `convert_preview` --
    the only PRE-write gate -- was therefore blind to it. It takes only
    ``facts``, which every caller of `build_warnings` already has, so it
    lives in the shared assembly with a single call site. Two call sites
    is precisely how the blind spot arose.
    """
    warnings = []
    for pattern, label in (
        (_RENDER_TRACWIKI_TABLE_RE, "TracWiki table"),
        (_RENDER_TRACWIKI_BLOCK_RE, "TracWiki code-block delimiter"),
        (_MARKDOWN_BOLD_RE, "Markdown bold"),
        (_MARKDOWN_FENCE_RE, "Markdown code fence"),
        (_MARKDOWN_LINK_RE, "Markdown link"),
    ):
        match = pattern.search(facts.prose_text)
        if match:
            evidence: dict[str, Any] = {"matched": match.group(0)}
            suggestion = _bracket_form_for_markdown_link(match.group(0))
            if suggestion is not None:
                evidence["suggestion"] = suggestion
            warnings.append(
                _warning(
                    "literal_markup_in_render",
                    # `error`, not `warning`, since ticket #64:
                    # section 4's table puts this in the blocking
                    # column and the code did not agree. Markup that
                    # did not convert is provable breakage -- the
                    # reader sees punctuation where a link should be.
                    "error",
                    f"{label} syntax ('{match.group(0)}') appears "
                    "as literal text in the rendered page instead "
                    "of being rendered -- it did not convert "
                    "cleanly.",
                    evidence,
                )
            )
    return warnings


def _bracket_form_for_markdown_link(matched: str) -> str | None:
    """`[label](target)` rewritten as TracWiki's `[target label]`, or
    None when the match is not a Markdown link (ticket #64 section 5).

    The highest-value suggestion in the suite, because this shape is
    the one auto_pm:wiki:Rules/trac/PreferInterTracLinks calls out as
    rendering *three* wrong things from one reference: `[label]` becomes
    a dead LOCAL wiki link, the parenthesised target auto-links on its
    own, and the brackets survive as visible punctuation. That rule
    names the pair of codes it produces -- `literal_markup_in_render`
    plus `missing_local_target` -- so an author who fixes this one
    finding clears both.

    Emitting the corrected string rather than a description is #64
    section 5's point: #59 comment 1 is a check that reported real
    breakage with the wrong diagnosis, and a suggestion showing the
    corrected text cannot mislead that way.
    """
    match = _MARKDOWN_LINK_PARTS_RE.fullmatch(matched)
    if not match:
        return None
    label = match.group("label").strip()
    target = match.group("target").strip()
    if not label or not target:
        return None
    return f"[{target} {label}]"


def _check_target_probes(
    facts: PreviewFacts, probes: dict[str, dict], check_targets: bool
) -> list[dict]:
    """Live-probe results for cross-instance InterTrac targets (row 15),
    plus an explicit note whenever a probeable target existed but wasn't
    actually checked -- capped, disabled, or network-failed must never
    look like a clean pass.

    Both realms since ticket #82: a cross-instance TICKET reference used
    to be invisible here, because it was never probeable and so could not
    reach any branch below. A ticket-realm target whose instance could
    not be confirmed reachable arrives as ERROR -- uncertainty, never a
    broken link.

    **Three codes, one per reason, since ticket #83.** They used to be
    one ``target_check_skipped``, and the three want different answers
    from ticket #64's blocking gate, so no single severity is right for
    the merged code:

    ``target_check_disabled``
        The caller passed ``check_targets=false``. Nobody asked for this
        check; that is the caller's deliberate choice, not a finding.
    ``target_check_capped``
        More probeable targets than the cap. The DOCUMENT is denser than
        the gate handles, and the author can act -- raise ``target_cap``
        or split the document -- so this is the candidate for #64's
        error column.
    ``target_check_failed``
        The probe could not reach the instance. The CHECKER could not do
        its job, the author can do nothing about it, and blocking here
        would stop all writes while a remote instance is down and charge
        the author for an outage they did not cause. Stays advisory.
    """
    probeable_hrefs: list[str] = [
        a.href
        for a in facts.anchors
        if a.href and is_probeable_href(a.href)
    ]
    if not probeable_hrefs:
        return []

    warnings: list[dict] = []

    if not check_targets:
        warnings.append(
            _warning(
                "target_check_disabled",
                "info",
                f"{len(set(probeable_hrefs))} cross-instance target(s) "
                "found but not checked (check_targets=false).",
                {"hrefs": sorted(set(probeable_hrefs))},
            )
        )
        return warnings

    capped: list[str] = []
    failed: list[str] = []
    for anchor in facts.anchors:
        if not anchor.href or not is_probeable_href(anchor.href):
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
        elif status == SKIPPED:
            capped.append(anchor.href)
        elif status == ERROR or status is None:
            # `None` is "no result at all" for a target that was
            # supposed to be probed -- the checker's problem, like a
            # failed fetch, and not something the author can act on.
            failed.append(anchor.href)

    if capped:
        warnings.append(
            _warning(
                "target_check_capped",
                # `error`, not `info`, since ticket #64 ruling 2 --
                # which confirmed what the docstring above already
                # argued. This is the only one of the three that the
                # AUTHOR can act on, and letting it pass would mean a
                # link-dense document reports no errors while part of
                # it was never looked at: "no errors" degrading from
                # "nothing was found" to "this was certified clean",
                # which is the exact failure #64 section 3 refuses to
                # ship. Measured population on real content is zero
                # since #80 raised the cap to 50.
                "error",
                f"{len(set(capped))} cross-instance target(s) beyond "
                "the probe cap and not checked -- not verified, not "
                "necessarily clean. Raise target_cap, or split the "
                "document.",
                {"hrefs": sorted(set(capped))},
            )
        )

    if failed:
        warnings.append(
            _warning(
                "target_check_failed",
                "info",
                f"{len(set(failed))} cross-instance target(s) could "
                "not be reached (network failure, or the instance did "
                "not answer a liveness control) -- not verified, not "
                "necessarily clean.",
                {"hrefs": sorted(set(failed))},
            )
        )

    return warnings


def build_warnings(
    markdown_source: str | None,
    tracwiki: str,
    facts: PreviewFacts,
    probes: dict[str, dict],
    check_targets: bool,
    source_format: str = "markdown",
) -> list[dict]:
    """Run every warning rule and return the combined list.

    Args:
        markdown_source: The caller's original Markdown input, or None
            when there is no Markdown source to check against -- the
            verify path (ticket #55) checks a live render, which has no
            corresponding Markdown candidate. The two rules keyed off
            Markdown source rather than ``facts``/``tracwiki`` --
            ``_check_tracwiki_markup_in_markdown`` and
            ``_check_code_block_indentation_loss`` -- are skipped in
            that case.
        tracwiki: The converted TracWiki text (what would be stored).
        facts: Extracted from the rendered HTML (what Trac would display).
        probes: Live-probe results for cross-instance targets, from
            :func:`trac_mcp_server.preview.targets.probe_targets`. Ignored
            when ``check_targets`` is False.
        check_targets: Whether the live probe actually ran.
        source_format: The format the caller DECLARED its input to be,
            'markdown' (the default, and what every caller predating
            ticket #65 meant) or 'tracwiki'. Consulted by the two
            Markdown-source rules, and only to skip them. TracWiki
            markup in TracWiki-declared input is correct content, not a
            defect, and warning about it fires on nearly every write
            once TracWiki authoring is possible (#62); TracWiki-declared
            input is stored verbatim, so there is no conversion step for
            ``_check_code_block_indentation_loss`` to lose anything in
            (ticket #68). Never used to GUESS the format -- the
            declaration is the caller's, and #47 is this project's
            evidence that sniffing content instead is unreliable.

    Returns:
        List of warning dicts, each ``{code, severity, message,
        evidence}``. Empty list means clean input, not "not checked" --
        pair with ``target_check_capped``/``target_check_failed``
        for the latter.

        Includes ``literal_markup_in_render``, which reads ``facts``
        only and so applies to every caller, pre-write and post-write
        alike (ticket #77).

        Codes named by a ``preview-checks: allow ...`` pragma in either
        source are dropped last, after every rule has run (ticket #58) --
        see ``_PRAGMA_RE``. Scoped to the codes it names, so a page that
        opts out of one warning is still checked for the rest.
    """
    warnings: list[dict] = []
    warnings.extend(_check_escaped_link_targets(facts))
    warnings.extend(_check_link_ref_in_code_span(facts))
    if markdown_source is not None and source_format != "tracwiki":
        warnings.extend(
            _check_tracwiki_markup_in_markdown(markdown_source)
        )
        warnings.extend(
            _check_code_block_indentation_loss(
                markdown_source, tracwiki
            )
        )
    warnings.extend(_check_missing_local_target(facts, tracwiki))
    warnings.extend(_check_bare_ticket_ref(facts))
    warnings.extend(_check_captured_punctuation(facts))
    warnings.extend(
        _check_unconfigured_intertrac_prefix(tracwiki, facts)
    )
    warnings.extend(_check_target_probes(facts, probes, check_targets))
    # Last (ticket #77), so the verify path's warning ordering stays
    # byte-identical to what `build_verify_warnings` produced when it
    # appended this itself.
    warnings.extend(_check_literal_markup_in_render(facts))

    allowed = _allowed_codes(markdown_source, tracwiki)
    if allowed:
        warnings = [w for w in warnings if w["code"] not in allowed]
    return warnings
