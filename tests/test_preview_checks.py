"""Table-driven acceptance suite for ``preview.checks`` (ticket #56).

All 19 rows from ticket #56 comment 1, run against rendered-HTML fixtures
captured from the live daemon's ``wiki.wikiToHtml`` (``tests/fixtures/
convert_preview/``, `manifest.json` records the exact input for each --
see `Rules/testing/RealSubstrateNotMocks`: unit tests stay deterministic
without laundering the format, and a separate live-marked test in
``test_mcp/tools/test_convert_preview.py`` re-renders against the real
server so the fixtures can't silently drift from what Trac emits).

Two things the suite implies but a per-row pass/fail doesn't spell out,
per `Rules/testing/SeededDefectFirst`:

- The silent/warning **balance** is asserted, not just the individual
  rows -- a checker that warns about everything passes every "must warn"
  row and fails every "must stay silent" one on a single green run only
  if both halves are actually checked.
- The seeded defect (row 1's pre-fix TracWiki) is watched red at the
  pre-fix commit and green at the fix -- this file can only assert the
  post-fix (green) state; the historical (red) state is recorded as a
  comment for a human/agent to reproduce against `85d9595` if ever in
  doubt.
"""

import json
from pathlib import Path

import pytest

from trac_mcp_server.preview.checks import (
    _is_autolink_page_name,
    build_warnings,
)
from trac_mcp_server.preview.facts import extract_facts
from trac_mcp_server.preview.verify import build_verify_warnings

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "convert_preview"
MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text())

# Suite 1 (rows 1-9): the escape-regression pass from ticket #44 -- every
# row here MUST stay silent. Suite 2 (rows 11-13): the three defects that
# motivated this ticket, caught only by a manual round trip -- MUST warn.
# Suite 3 (rows 14, 16): live-state warnings that don't need a server
# round-trip to classify -- MUST warn. (Row 15 needs the live probe and is
# covered separately, in test_mcp/tools/test_convert_preview.py's live
# test -- it renders identically to row 1 without one.)
#
# Suite 4 (rows 17-21, ticket #57): the realm-form counterpart to row 16.
# Row 10 moves here from the silent half -- it is the reason #57 exists:
# `zzznotaprefix:wiki:SomePageName` was measured silent against the
# deployed build (no anchor, no warning), which is the defect. Rows 17-18
# repeat the same measured-silent-on-deployed-build defect for the
# `ticket` and `report` realms. Rows 19-21 are the must-stay-silent
# guardrails from the ticket: `TODO:fix:Later` and `note:see:Below` have
# a colon-shaped middle segment that isn't a real Trac realm, and
# `http://host:port/path` renders its own anchor so the no-anchor gate
# never fires. (`Note: SomeCamelWord` -- prose with a space after the
# colon, so the realm regex can't match at all -- is already covered by
# row08_prose_after_colon; no separate row needed for that shape.)
#
# Suite 5 (rows 22-35, ticket #57 comment 4 -- the reopen): the title
# fallback added alongside the realm-form fix suppressed a token whenever
# ITS SUFFIX APPEARED IN ANY ANCHOR TITLE ANYWHERE IN THE DOCUMENT, with
# no association between the token and that anchor -- a document-global
# substring match, not a scoped one. Rows 27/28/29 are the seeded
# defects: each pairs a typo'd prefix with a correctly-configured
# reference to the SAME (27/28) or a superstring (29) target elsewhere in
# the document, which silenced the typo on the deployed build. Row 30 is
# the same defect in the bracketed-label shape (the fallback's own
# reason to exist, row 3). Rows 22/23/26 (single occurrence) and
# rows 24/25 (paired with a DIFFERENT target, so the bug never had a
# chance to fire) are regression guards -- confirmed already correct
# pre-fix, must stay correct post-fix. Row 31 is the fourth defect found
# while reproducing the reopen: a realm-form link closing a sentence
# captures the trailing period in the token, so a *correctly configured*
# link false-positives as unconfigured -- the false-positive counterpart
# to rows 27/28/29's false negative. Rows 32-35 cover the second,
# lower-severity finding: TRACLINK_SCHEMES (18 realms, shared with
# is_link_target/code-span checks) is too wide for this check on its
# own -- ordinary English words like `diff`, `search`, `comment`,
# `export` false-positive on plausible prose (`svn:diff:123`,
# `see:search:Results`, `ref:comment:3`, `build:export:Artifacts`); the
# narrowed, check-local `_INTERTRAC_REALMS` excludes them. `log` stays
# in the narrowed list per go-ahead, so `git:log:HEAD`-shaped prose is
# accepted as a residual false positive rather than added as a row.
#
# Suite 6 (rows 36-48, ticket #57 comment 6/7/8): round 2's fix bounded a
# token by trimming a punctuation character class off it -- comment 6
# showed that class can never be complete (backtick, hyphen, `>` all
# trail a CONFIGURED link's true boundary and aren't in it), and comment
# 8 found the replacement mechanism (`_prefix_boundary_match`/
# `_code_span_contains`, keyed on Trac's OWN rendered text/title/code-span
# content instead of a guessed class) still had a direction bug for code
# spans and missed the label-less bracket shape (`[token]`, no space
# before `]`) for both forms. Rows 36/37 are the two reported trailing-
# character defects (hyphen, `>`); row 42 is the must-stay-silent
# regression they were checked against (`/`, which Trac itself includes
# in the target). Rows 38/39 are comment 8's code-span direction-bug
# regression guards -- a code span containing MORE than the token (prose
# around it) must stay silent, which a naive prefix check on the whole
# span would have broken. Row 46 is the ticket-form counterpart,
# unaffected either way (its `\b` boundary never swallows trailing
# characters) but covered here since nothing exercised a ticket ref
# inside a code span before. Rows 40/41 are the no-label-bracket defect
# comment 8 found live for both forms -- Trac renders the bare resolved
# suffix as the anchor text when a bracket carries no custom label, which
# neither round 1 nor round 2's bracket handling recognized. Rows 43-45
# round out comment 6/7's punctuation-plus-typo regression coverage
# specifically for realm-form (43, 45) and ticket-form (44), largely
# redundant with rows 22-30 but exercising `)` and a realm-form `.`
# together, which those rows didn't. Row 47 (bold-wrapped) and row 48
# (a same-label bracketed pair) are handled as standalone tests below,
# not table rows -- row 47 also legitimately trips
# `tracwiki_markup_in_markdown`, and row 48 documents a known, accepted
# residual rather than an assertion that it now passes; see
# `_check_unconfigured_intertrac_prefix`'s docstring.
#
# Suite 7 (rows 49-62, tickets #58 and #59): the two opposite-sign
# defects the seeded-defect pass on #55 found in this module, landed
# together because they touch `_PREFIX_TICKET_RE` from opposite
# directions and a shared suite is the only way one run sees both signs.
# Rows 49/50 plus the MOVE of row46 out of the silent half are #58's
# false negative -- a backticked `prefix:#N` short link, the shape
# `Rules/trac/HashNumberAutoLinks` names first, which the realm-anchored
# `_CODE_SPAN_LINK_RE` could never match. Rows 51-53 are that widening's
# calibration pins (a bare backticked number and a metavariable
# placeholder both stay silent -- they fall out of `_PREFIX_TICKET_RE`
# for free, and are asserted so a later tuning pass can't quietly widen
# them; row 53 is the multiword-span guard rows 38/39 already pin for
# the realm form). Rows 54/55 are the opt-out pragma the widening ships
# with -- see their standalone tests. Rows 56-58 are #59's false
# positive: `unconfigured_intertrac_prefix` scanned inside fenced
# blocks, where Trac renders no anchor at all, so its "no anchor
# therefore unconfigured" inference is unsound and it called a
# CONFIGURED prefix unconfigured; row 58 is the must-keep-firing half.
# Rows 59-62 are #59 comment 1: trailing text Trac captures INTO the
# dispatcher target. Measured -- `.`, `,`, `)`, `;` are not captured
# (rows 59/60, correct today and must stay silent), while `'s` and
# `-ish` are (rows 61/62), producing a live-looking anchor onto a
# ticket that does not exist. Those two warned already, but as
# `unconfigured_intertrac_prefix` on a prefix that is configured -- a
# right-signal/wrong-diagnosis case, which is why they get their own
# code rather than a widened message.
#
# Suite 8 (rows 63-65, ticket #61): the third scoping gap in the same
# check, at the delimiter #59 did not reach. `_PREFIX_REALM_RE`'s
# trailing `\S+` did not stop at a backtick, so a token whose code span
# is followed immediately by non-whitespace ran PAST the closing
# backtick -- and `_code_span_contains` then compared a token longer
# than any span, found no containment, and called a CONFIGURED prefix
# unconfigured. Row 63 is the shape it was found in (a table cell, where
# the delimiter follows the backtick with no space); row 64 is the same
# defect with no table at all, pinning that the trigger is the adjacency
# and not the table. Both are warning rows rather than silent ones,
# because a backticked link reference legitimately trips
# `link_ref_in_code_span` (row 11's shape, widened by #58) -- the defect
# here is the SECOND code they carried, pinned by
# `test_code_span_boundary_is_not_reported_as_unconfigured` below, the
# same present-code/absent-code shape #59 comment 1's rows use. Row 65
# is the must-keep-firing half in the same position: an UNCONFIGURED
# prefix in a cell, not backticked, must still warn -- the fix corrects
# a token boundary, it does not widen suppression to un-backticked
# prose.
#
# Suite 9 (rows 66-71, ticket #65): `tracwiki_markup_in_markdown`'s own
# scoping, the last check in this module still using the ORIGINAL
# `_strip_code_fences`. Measured before planning, two of the ticket's
# three claims did not survive: a `{{{` inside a FENCED block was
# already silent (rows 56/57 are standing evidence), so the ticket's
# proposed seed could not fail. The two real defects are elsewhere.
# Rows 66/67 are the false positive that actually fires -- TracWiki
# markup inside an INLINE CODE SPAN, i.e. deliberate quoting that
# renders as literal text ON PURPOSE, which is the very outcome this
# check warns *will* happen; warning about it is self-defeating, and it
# is the shape that fired repeatedly while #62 was being written.
# Row 68 is symptom A: markup correct for its DECLARED format, which the
# check ignored entirely -- paired with row 69, byte-identical content
# and an identical render, differing only in the declared format, so the
# pair asserts that the declaration is what decides and nothing else.
# Row 70 is the opposite sign and the more dangerous one: a fence opened
# and closed on ONE line made `_strip_code_fences` read the whole rest of
# the document as fence interior, so genuine TracWiki bold after it was
# never seen at all. That is a false NEGATIVE, and it is the reason the
# swap to `blank_code_fences` is the right change even though the
# ticket's stated reason for it (a fenced-block false positive) does not
# reproduce -- see that function's docstring, which documents this exact
# divergence as its reason to exist. Row 71 is the over-blanking catcher:
# a quoted occurrence and a genuine one in the same document must warn
# EXACTLY once and name the genuine one, asserted on the evidence rather
# than the code, since blanking too much silences the real defect while
# leaving the row superficially green.
SILENT_ROWS = [
    "row01_intertrac_wiki",
    "row02_intertrac_wiki_bcs",
    "row03_bracket_intertrac",
    "row04_md_link_absolute",
    "row05_intertrac_index",
    "row06_intertrac_ticket",
    "row07_prose_camelcase",
    "row08_prose_after_colon",
    "row09_prose_after_time",
    "row19_prose_fix_colon",
    "row20_prose_see_colon",
    "row21_url_with_port",
    "row31_realm_trailing_period",
    "row32_prose_svn_diff",
    "row33_prose_see_search",
    "row34_prose_ref_comment",
    "row35_prose_build_export",
    "row36_realm_trailing_hyphen",
    "row37_realm_trailing_angle_bracket",
    "row38_code_span_multiword_prose",
    "row39_code_span_multiword_command",
    "row40_bracket_no_label_realm",
    "row41_bracket_no_label_ticket",
    "row42_realm_trailing_slash",
    "row51_code_span_bare_ticket_number",
    "row52_code_span_placeholder_ticket",
    "row53_code_span_multiword_ticket",
    "row56_fence_short_link",
    "row57_fence_realm_link",
    "row59_ticket_trailing_period",
    "row60_ticket_trailing_paren",
    "row66_code_span_tracwiki_block",
    "row67_code_span_tracwiki_table",
    "row68_declared_tracwiki_bold",
    # Suite 8 (rows 73-76, tickets #70 and #78). Row 73 is the fenced
    # token beside a real one: the fence renders no anchor, so nothing
    # may attribute the real reference's anchor to it. Rows 74-76 are
    # #78 -- an ORDINARY external link whose label happens to start
    # with a short link. Rows 74/75 warned before the fix (74 with a
    # possessive, 75 with nothing but a space after the token, which is
    # why #78 is "any multi-word label"); row 76 is the control whose
    # label is not token-shaped and was already silent, so the pair
    # proves the fix removed the false positive rather than the check.
    "row73_fence_plus_real_ticket_ref",
    "row74_external_label_possessive",
    "row75_external_label_multiword",
    "row76_external_label_plain",
    # Suite 9 (rows 79a-79h, ticket #79). Row 79d is the escaped-word
    # pin: `!TracClient` produces NO anchor at all (measured on the
    # deployed daemon), so it is a different silence from the rest of
    # this list -- there is nothing for the check to classify, and it
    # must not be counted as an incidental auto-link either.
    "row79d_escaped_camelcase",
]

WARNING_ROWS = [
    ("row11_code_span_intertrac", "link_ref_in_code_span"),
    ("row12_tracwiki_table_in_md", "tracwiki_markup_in_markdown"),
    ("row13_hand_relative_url", "missing_local_target"),
    ("row14_bare_ticket_ref", "bare_ticket_ref"),
    (
        "row16_unconfigured_prefix_ticket",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row10_unknown_prefix_wiki",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row17_unconfigured_prefix_ticket_realm",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row18_unconfigured_prefix_report_realm",
        "unconfigured_intertrac_prefix",
    ),
    ("row22_typo_ticket_alone", "unconfigured_intertrac_prefix"),
    ("row23_typo_realm_alone", "unconfigured_intertrac_prefix"),
    (
        "row24_typo_ticket_diff_target",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row25_typo_realm_diff_target",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row26_typo_ticket_short_alone",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row27_typo_ticket_same_target_twice",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row28_typo_realm_same_target_twice",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row29_typo_ticket_substring_target",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row30_bracketed_typo_and_good_pair",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row43_typo_realm_trailing_period",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row44_typo_ticket_trailing_period",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row45_typo_realm_parens",
        "unconfigured_intertrac_prefix",
    ),
    # Moved out of SILENT_ROWS by ticket #58 -- this row is the defect:
    # a backticked, CONFIGURED short link is a reference meant to be a
    # link, rendered inert. It was added by #57 as a code-span guard
    # for the neighbouring check and marked silent because that check
    # must stay silent on it; the question of whether the code-span
    # check itself should fire was never asked. Same shape as #57's own
    # move of row 10.
    ("row46_ticket_code_span", "link_ref_in_code_span"),
    ("row49_code_span_short_link_tms", "link_ref_in_code_span"),
    ("row50_code_span_short_link_bcs", "link_ref_in_code_span"),
    (
        "row58_fence_plus_unconfigured_prose",
        "unconfigured_intertrac_prefix",
    ),
    (
        "row61_ticket_possessive",
        "intertrac_target_captured_punctuation",
    ),
    (
        "row62_ticket_hyphen_suffix",
        "intertrac_target_captured_punctuation",
    ),
    ("row63_code_span_table_cell", "link_ref_in_code_span"),
    ("row64_code_span_adjacent_word", "link_ref_in_code_span"),
    (
        "row65_table_cell_unconfigured_realm",
        "unconfigured_intertrac_prefix",
    ),
    ("row69_declared_markdown_bold", "tracwiki_markup_in_markdown"),
    (
        "row70_same_line_fence_then_bold",
        "tracwiki_markup_in_markdown",
    ),
    ("row71_code_span_and_bare_bold", "tracwiki_markup_in_markdown"),
    # Suite 9 (ticket #79). Deliberately one-sided towards must-warn,
    # for the reason #68's recall gate names: this ticket NARROWS a
    # check, and narrowing is exactly where true positives leave
    # quietly. Rows 79b/79e/79f are the authored population that must
    # keep its `error`; rows 79a/79g/79h are the incidental population
    # that must still be REPORTED, only at advisory severity. The one
    # new silent row (79d) is a third thing again -- no anchor at all.
    ("row79b_authored_dead_link", "missing_local_target"),
    ("row79e_dead_link_with_label", "missing_local_target"),
    ("row79f_non_autolink_page_name", "missing_local_target"),
    ("row79a_bare_prose_camelcase", "incidental_wiki_autolink"),
    ("row79g_bare_short_name", "incidental_wiki_autolink"),
    ("row79h_autolink_shape_probe", "incidental_wiki_autolink"),
]

# Seeded defect: row 1 rendered from ITS PRE-FIX TracWiki
# (`auto_pm:wiki:Rules/trac/!RenderVerify`, the escape ticket #44's fix at
# `2bf11d9`/`1ef59b3` removed). Rendered directly against the live daemon
# rather than re-derived from the (already-fixed) converter, so this
# fixture is immune to the converter itself changing.
SEEDED_DEFECT_ROW = ("row01_pre_fix", "escaped_link_target")


def _load(name: str):
    """Return (markdown_source, tracwiki, facts, source_format).

    ``source_format`` defaults to ``markdown`` -- the value every row
    predating ticket #65 was captured under, so adding the parameter
    left all of them byte-identical in behaviour. Only a row that
    deliberately declares TracWiki (row 68) sets it.
    """
    html = (FIXTURES_DIR / f"{name}.html").read_text()
    data = MANIFEST[name]
    tracwiki = data["tracwiki"]
    markdown_source = data["markdown_input"] or tracwiki
    source_format = data.get("source_format", "markdown")
    return markdown_source, tracwiki, extract_facts(html), source_format


@pytest.mark.parametrize("name", SILENT_ROWS)
def test_silent_rows_produce_no_warning(name):
    markdown_source, tracwiki, facts, source_format = _load(name)
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [
        w["code"]
        for w in warnings
        if w["code"] != "target_check_disabled"
    ]
    assert codes == [], f"{name} should be silent, got {codes}"


@pytest.mark.parametrize("name,expected_code", WARNING_ROWS)
def test_warning_rows_produce_expected_code(name, expected_code):
    markdown_source, tracwiki, facts, source_format = _load(name)
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [w["code"] for w in warnings]
    assert expected_code in codes, (
        f"{name} should warn {expected_code!r}, got {codes}"
    )


def test_seeded_defect_row1_pre_fix_warns_escaped_link_target():
    """Row 1's pre-fix TracWiki must trip `escaped_link_target`.

    Watch this test RED against `85d9595` (pre-fix) and GREEN at
    `1ef59b3`/HEAD (post-fix) -- `Rules/testing/SeededDefectFirst`.
    """
    name, expected_code = SEEDED_DEFECT_ROW
    markdown_source, tracwiki, facts, source_format = _load(name)
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [w["code"] for w in warnings]
    assert expected_code in codes


def test_row1_post_fix_does_not_warn_escaped_link_target():
    """The counterpart to the seeded-defect test: the CURRENT converter's
    row 1 output must NOT trip the same check the pre-fix fixture does --
    otherwise the check can't tell fixed from broken."""
    markdown_source, tracwiki, facts, source_format = _load(
        "row01_intertrac_wiki"
    )
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [w["code"] for w in warnings]
    assert "escaped_link_target" not in codes


def test_row11_code_span_does_not_also_warn_unconfigured_prefix():
    """A backticked, CONFIGURED InterTrac reference must trip only
    `link_ref_in_code_span`, never `unconfigured_intertrac_prefix` too --
    the defect ticket #57 comment 6 found live: the realm regex's greedy
    `\\S+` swallows the closing backtick, and the old trim class didn't
    include it, so the code-span suppression's substring check missed and
    a correctly-configured reference (`auto_pm`) got double-warned as if
    its prefix were unconfigured. The existing warning-rows test only
    ever checked `link_ref_in_code_span`'s presence, never this code's
    absence -- which is exactly how the defect shipped unnoticed."""
    markdown_source, tracwiki, facts, source_format = _load(
        "row11_code_span_intertrac"
    )
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [w["code"] for w in warnings]
    assert "unconfigured_intertrac_prefix" not in codes, codes


def test_bold_wrapped_realm_link_stays_silent_on_prefix_check():
    """`'''auto_pm:wiki:Index'''` legitimately trips
    `tracwiki_markup_in_markdown` (TracWiki bold syntax typed into a
    Markdown candidate) -- that's an existing, correct warning, so this
    row can't go in SILENT_ROWS (which requires full silence). What it
    must NOT also do is warn `unconfigured_intertrac_prefix`: the link is
    configured and Trac renders it correctly (wrapped in `<strong>`), and
    the apostrophes trailing the token are exactly the kind of
    non-word-character boundary `_prefix_boundary_match` is meant to
    tolerate."""
    markdown_source, tracwiki, facts, source_format = _load(
        "row47_bold_realm"
    )
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [w["code"] for w in warnings]
    assert "tracwiki_markup_in_markdown" in codes
    assert "unconfigured_intertrac_prefix" not in codes, codes


def test_same_label_bracket_pair_is_a_known_accepted_residual():
    """`[auto-pm:#87 see]` (typo'd prefix) and `[auto_pm:#87 see]`
    (correct) share both their label text AND their resolved target, so
    nothing in the rendered output distinguishes which occurrence
    produced the one anchor Trac emits. This stays SILENT -- the typo
    goes unreported -- and that is accepted, not fixed: ticket #57
    comment 8 checked whether a source-order tie-break could resolve it
    and found it would attribute the warning to the WRONG (good)
    occurrence here, since the anchor is the second occurrence's while
    the first is the dead one. Resolving this for real needs source-
    position data `PreviewFacts` doesn't carry. This test exists so a
    future change to this function has to notice it's touching the
    known-open case, not silently start passing or failing it."""
    markdown_source, tracwiki, facts, source_format = _load(
        "row48_bracket_same_label_pair"
    )
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [
        w["code"]
        for w in warnings
        if w["code"] != "target_check_disabled"
    ]
    assert codes == [], (
        f"expected the documented residual (silent); got {codes} -- "
        "if this now warns, the residual may be fixed: update this "
        "test and the docstring on _check_unconfigured_intertrac_prefix"
    )


def test_row46_short_link_code_span_warns_once_not_twice():
    """The ticket-form counterpart to
    `test_row11_code_span_does_not_also_warn_unconfigured_prefix`
    (ticket #58). A backticked, CONFIGURED short link must trip
    `link_ref_in_code_span` and nothing else -- in particular not
    `unconfigured_intertrac_prefix`, whose code-span suppression is the
    only thing keeping the two checks from double-reporting one span.
    Widening the code-span check is exactly the change that could break
    that suppression without any "must warn" row noticing."""
    markdown_source, tracwiki, facts, source_format = _load(
        "row46_ticket_code_span"
    )
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [w["code"] for w in warnings]
    assert codes == ["link_ref_in_code_span"], codes


def test_pragma_suppresses_the_named_code():
    """Ticket #58's opt-out. A page whose SUBJECT is this syntax must
    backtick it, and so must an anti-pattern section showing what not to
    write -- measured, `auto_pm:wiki:Reference/trac/InterTrac` returns 8
    of these errors on entirely correct content, and the widening takes
    it to 13. The pragma is what makes that page's gate readable again.

    Can't go in SILENT_ROWS: with `markdown_input: null` the harness
    falls back to the stored TracWiki as the Markdown source, so the
    `#!comment` block itself legitimately trips
    `tracwiki_markup_in_markdown` -- the same reason row 47 is a
    standalone test."""
    markdown_source, tracwiki, facts, source_format = _load(
        "row54_pragma_allows_code_span_ref"
    )
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [w["code"] for w in warnings]
    assert "link_ref_in_code_span" not in codes, codes


def test_pragma_is_scoped_to_the_codes_it_names():
    """The pragma is an opt-out for named codes, not a document-wide
    mute. The same document carries a genuinely typo'd prefix in prose;
    allowing `link_ref_in_code_span` must not silence that. Without this
    row the feature could degenerate into "any page that opts out of one
    warning stops being checked at all", which is how a gate stops being
    a gate."""
    markdown_source, tracwiki, facts, source_format = _load(
        "row55_pragma_is_code_scoped"
    )
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [w["code"] for w in warnings]
    assert "link_ref_in_code_span" not in codes, codes
    assert "unconfigured_intertrac_prefix" in codes, codes


def test_fenced_document_warns_only_for_the_prose_token():
    """Ticket #59's must-keep-firing half, and the reason the fence fix
    can't just be "scan less".

    The document holds a CONFIGURED realm link inside a fenced block
    (silent after the fix -- Trac renders no anchor in a fence, so the
    check's "no anchor therefore unconfigured" inference is unsound
    there) and an UNCONFIGURED one in prose (must still warn). Asserting
    the count, not just the code's presence, is the point: this row is
    what catches a fix that blanks too much, which the `must warn` list
    alone would pass."""
    markdown_source, tracwiki, facts, source_format = _load(
        "row58_fence_plus_unconfigured_prose"
    )
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    prefix_warnings = [
        w
        for w in warnings
        if w["code"] == "unconfigured_intertrac_prefix"
    ]
    assert len(prefix_warnings) == 1, warnings
    assert (
        prefix_warnings[0]["evidence"]["token"]
        == "zzznotaprefix:wiki:SomePage"
    )


@pytest.mark.parametrize(
    "name", ["row61_ticket_possessive", "row62_ticket_hyphen_suffix"]
)
def test_captured_punctuation_is_not_reported_as_unconfigured(name):
    """Ticket #59 comment 1: right signal, wrong diagnosis.

    Trac's tokenizer swallows `'s` / `-ish` into the dispatcher target,
    so the anchor resolves to ticket "87's" -- a dead link that reads as
    live. The check was right that something was wrong and is the only
    reason it gets caught, but it reported the prefix as unconfigured
    when the prefix is configured and links correctly elsewhere. Acting
    on that message sends the reader to the `[intertrac]` table, which
    is fine, and then to conclude the check is broken, which it is not.
    Both halves are asserted: the new code fires AND the misdiagnosis
    does not."""
    markdown_source, tracwiki, facts, source_format = _load(name)
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [w["code"] for w in warnings]
    assert "intertrac_target_captured_punctuation" in codes, codes
    assert "unconfigured_intertrac_prefix" not in codes, codes


def test_captured_punctuation_survives_a_correct_reference_alongside():
    """Ticket #70: the defect this whole check used to have no defence
    against -- a CORRECT reference to the same ticket elsewhere in the
    document silenced the broken one, and the document reported zero
    errors while carrying a dead link.

    Row 72 is that shape: `auto_pm:#87's` (dispatches on `#87's`, which
    does not exist) followed by a correct `auto_pm:#87`. Measured
    silent on the deployed daemon at `55d34c5`.

    The assertion is on the EVIDENCE, not just the count, per #65
    comment 3: both occurrences name the same ticket, so a test that
    only counted warnings could not tell which one was reported. The
    two anchors' hrefs differ (`%2387%27s` vs `%2387`), and that is
    what makes the report discriminable -- naming the good occurrence
    would be a worse failure than staying silent, since it sends the
    writer to correct a line that is already right."""
    markdown_source, tracwiki, facts, source_format = _load(
        "row72_ticket_possessive_and_correct_pair"
    )
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    captured = [
        w
        for w in warnings
        if w["code"] == "intertrac_target_captured_punctuation"
    ]
    assert len(captured) == 1, warnings
    href = captured[0]["evidence"]["href"]
    assert href.endswith("%2387%27s"), href
    assert captured[0]["evidence"]["resolved_as"] == "auto_pm:#87's"


@pytest.mark.parametrize(
    "name",
    ["row63_code_span_table_cell", "row64_code_span_adjacent_word"],
)
def test_code_span_boundary_is_not_reported_as_unconfigured(name):
    """Ticket #61: a configured prefix inside a code span whose closing
    backtick is followed immediately by non-whitespace.

    Before the fix the realm pattern's `\\S+` ran past that backtick, so
    `_code_span_contains` compared a token longer than any rendered span,
    found no containment, and reported a CONFIGURED prefix as
    unconfigured. `link_ref_in_code_span` firing here is correct and
    asserted as a warning row; what must not appear is the second code.
    """
    markdown_source, tracwiki, facts, source_format = _load(name)
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    codes = [w["code"] for w in warnings]
    assert "link_ref_in_code_span" in codes, codes
    assert "unconfigured_intertrac_prefix" not in codes, codes


def test_suite_balance_is_roughly_even():
    """Per the ticket's coverage note: silent and warning rows must be
    roughly balanced, or a checker that warns about everything (or never
    warns at all) would pass on a single green run. Row 15 (live-probe
    only) and the seeded-defect fixture are outside this static count."""
    silent_count = len(SILENT_ROWS)
    # Suite 3 also names row 15 (live-probe, covered by a separate live
    # test rather than this static list), bringing the full ticket table
    # to 31 silent / 27 warning once it's counted (ticket #57 moved row
    # 10 into the warning half and added rows 17-21, comment 4's reopen
    # added rows 22-35, and comment 6/7/8's reopen added rows 36-45;
    # tickets #58/#59 added rows 49-53 and 56-62 and moved row 46 into
    # the warning half; rows 47/48/54/55 are standalone tests, not
    # counted here -- see their comments; ticket #61 added rows 63-65;
    # ticket #65 added rows 66-71, three to each half -- deliberately
    # balanced, because that ticket both NARROWS the check (rows 66-68)
    # and WIDENS it (row 70), and a one-sided addition would have hidden
    # whichever direction went wrong).
    #
    # Tickets #70/#78 added rows 72-76, four of them silent. That is a
    # one-sided addition and it is deliberate: both tickets are about
    # this check firing on the WRONG anchor, and three of the four new
    # silent rows (74/75) plus their control (76) are the false positive
    # #78 measured. The must-warn half of that pair is row 72, which is
    # a standalone test rather than a table row for the same reason as
    # row 48 -- it asserts WHICH occurrence was named, not merely that
    # something warned, and a code-only table row cannot express that.
    #
    # Ticket #79 added rows 79a-79h: six must-warn, one silent, one
    # standalone (79c). One-sided again, and deliberately -- see the
    # note on suite 9 in WARNING_ROWS. Narrowing a check risks losing
    # true positives, so the new rows are weighted to the half that
    # proves the check still fires.
    warning_count = len(WARNING_ROWS) + 1
    assert silent_count == 39
    assert warning_count == 39
    # "Roughly even" per the ticket's coverage note, not a bulk weighted
    # to positives: neither side outnumbers the other more than 2:1.
    assert (
        max(silent_count, warning_count)
        / min(silent_count, warning_count)
        <= 2.0
    )


def test_declared_format_decides_identical_content():
    """Rows 68/69, ticket #65 symptom A.

    Byte-identical content, byte-identical render, opposite verdicts --
    the ONLY difference is the declared source format. TracWiki bold in
    TracWiki input is correct content; in Markdown input it is a defect.
    Asserted as a pair rather than as two independent rows because the
    point is the contrast: a check that ignored the declaration would
    pass one half and fail the other, and so would a check that
    suppressed everything -- only the pair pins both directions.
    """
    tw_src, tw_wiki, tw_facts, tw_fmt = _load(
        "row68_declared_tracwiki_bold"
    )
    md_src, md_wiki, md_facts, md_fmt = _load(
        "row69_declared_markdown_bold"
    )

    assert tw_src == md_src, "the pair must share identical content"
    assert tw_wiki == md_wiki, "the pair must share identical output"
    assert (tw_fmt, md_fmt) == ("tracwiki", "markdown")

    def codes_for(src, wiki, facts, fmt):
        return [
            w["code"]
            for w in build_warnings(
                src,
                wiki,
                facts,
                probes={},
                check_targets=False,
                source_format=fmt,
            )
        ]

    assert "tracwiki_markup_in_markdown" not in codes_for(
        tw_src, tw_wiki, tw_facts, tw_fmt
    )
    assert "tracwiki_markup_in_markdown" in codes_for(
        md_src, md_wiki, md_facts, md_fmt
    )


def _markup_warnings(name):
    src, wiki, facts, fmt = _load(name)
    warnings = build_warnings(
        src,
        wiki,
        facts,
        probes={},
        check_targets=False,
        source_format=fmt,
    )
    return src, [
        w
        for w in warnings
        if w["code"] == "tracwiki_markup_in_markdown"
    ]


def test_code_span_blanking_does_not_silence_the_genuine_occurrence():
    """Row 71, ticket #65 -- the over-blanking catcher.

    A quoted occurrence and a genuine one in the same document. Both
    failure modes here produce a plausible-looking result: blanking
    nothing reports the QUOTED one (right code, wrong defect -- what
    happens pre-fix), and blanking too much silences the genuine one
    while leaving the table row green. So this asserts on WHICH
    occurrence was found, not merely that something was.

    The two carry different words on purpose. An earlier draft used the
    same word for both and could not tell them apart at all -- the
    evidence dict carries the matched text, not its offset, so a lookup
    always found the first occurrence and the test passed or failed for
    the wrong reason.
    """
    src, matches = _markup_warnings("row71_code_span_and_bare_bold")
    assert len(matches) == 1, matches
    assert matches[0]["evidence"]["matched"] == "'''beta'''", (
        "expected the genuine occurrence; "
        f"got {matches[0]['evidence']['matched']!r}"
    )
    assert "alpha" in src, "fixture must still contain the quoted one"


def test_same_line_fence_does_not_blind_the_rest_of_the_document():
    """Row 70, ticket #65 -- the false negative, and the sharper half.

    `_strip_code_fences` never closes a fence opened and closed on one
    line, so everything after such a line read as fence interior and
    genuine TracWiki markup following it was never seen at all. Silent
    pre-fix. This is the direction a fence-scoping change is most likely
    to get backwards, so it is asserted on its own rather than only
    through the table row.
    """
    _, matches = _markup_warnings("row70_same_line_fence_then_bold")
    assert len(matches) == 1, matches
    assert matches[0]["evidence"]["matched"] == "'''bold'''"


# ---------------------------------------------------------------------------
# code_block_indentation_loss (ticket #68)
#
# The one rule in this suite about damage that is INVISIBLE. It reads the
# Markdown source and the converted TracWiki and nothing else -- not the
# render, not `facts` -- so these tests pass an empty render rather than a
# captured fixture. That is the correct null input for a source-vs-output
# comparison, not a laundered one: the whole reason this check exists is
# that the render was clean while the content was being destroyed, so a
# render fixture could only mislead about what is being asserted.
#
# The seeds' pre-fix (red) state and the detector's own controls live in
# `tests/test_converter_indent_loss.py`. What is asserted here is the
# wiring: that the code reaches `build_warnings` at the right severity,
# that the declared-format gate turns it off, and -- the half that
# matters most for a NEW firing rule -- that it fires on nothing in the
# existing corpus.
# ---------------------------------------------------------------------------

_INDENT_LOSS_SEEDS = {
    "processor_block": (
        "{{{#!python\ndef f(x):\n    return 1\n}}}\n",
        "{{{#!python\ndef f(x):\nreturn 1\n}}}",
    ),
    "plain_block": (
        "{{{\n  two\n    four\n}}}\n",
        "{{{\ntwo\nfour\n}}}",
    ),
}

_NO_RENDER = extract_facts("")


def _indent_loss_codes(source, tracwiki, source_format="markdown"):
    return [
        w
        for w in build_warnings(
            source,
            tracwiki,
            _NO_RENDER,
            probes={},
            check_targets=False,
            source_format=source_format,
        )
        if w["code"] == "code_block_indentation_loss"
    ]


@pytest.mark.parametrize("name", sorted(_INDENT_LOSS_SEEDS))
def test_indentation_loss_seed_reaches_build_warnings(name):
    """Ticket #68 section 2's two measured shapes, through the suite.

    Severity is asserted alongside the code: `error`, not `warning`.
    Every other rule here describes content that is visibly wrong; this
    one describes content that is gone, and #59 set the precedent when
    `intertrac_target_captured_punctuation` became an error for naming
    a genuinely dead link.
    """
    source, tracwiki = _INDENT_LOSS_SEEDS[name]
    found = _indent_loss_codes(source, tracwiki)
    assert len(found) == 1, found
    assert found[0]["severity"] == "error"
    assert found[0]["evidence"]["converted_indent"] == 0


@pytest.mark.parametrize("name", sorted(_INDENT_LOSS_SEEDS))
def test_indentation_loss_is_off_for_tracwiki_declared_input(name):
    """The gate ticket #65 made possible.

    TracWiki-declared input is stored verbatim -- no conversion runs, so
    there is nothing to lose and the check must not fire. Asserted per
    seed rather than once: this is the same declared-format contrast
    `test_declared_format_decides_identical_content` pins for the markup
    rule, and a gate that only half-works looks identical to one that
    works until the untested half is exercised.
    """
    source, tracwiki = _INDENT_LOSS_SEEDS[name]
    assert _indent_loss_codes(source, tracwiki) != []
    assert (
        _indent_loss_codes(source, tracwiki, source_format="tracwiki")
        == []
    )


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_no_existing_row_gains_the_indentation_loss_code(name):
    """The recall gate, per ticket #68 section 5.

    A loss check is a new FIRING rule rather than a suppression, so the
    usual risk is inverted: the danger is over-firing on correct
    content, and #57 and #59 are this project's evidence that an
    over-firing check gets muted and takes its true positives with it.
    Every row already in the corpus must stay exactly as it was.
    """
    markdown_source, tracwiki, facts, source_format = _load(name)
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    assert [
        w
        for w in warnings
        if w["code"] == "code_block_indentation_loss"
    ] == []


# ---------------------------------------------------------------------
# Ticket #79: separating an authored dead link from a CamelCase word
# Trac auto-linked out of ordinary prose.
# ---------------------------------------------------------------------

#: The auto-link shape, measured against the deployed daemon at
#: `b874c81` and reproduced on ticket #79 comment 2. Each word was fed
#: through `convert_preview(format="tracwiki")` on its own line; True
#: means Trac emitted a `wiki` anchor for it, False means it rendered
#: as plain text. This is Trac's OWN statement of the rule -- the
#: regex in `checks` is checked against it rather than the other way
#: round, because #37 is this project's evidence that a hand-reasoned
#: CamelCase rule goes wrong in both directions (`PyVISA` is that
#: ticket's word, and it is in here as a False row for that reason).
MEASURED_AUTOLINK_SHAPES = [
    ("CamelCase", True),
    ("PyVISA", False),
    ("WiFi", True),
    ("LoRa", True),
    ("KiCad", True),
    ("GitHub", True),
    ("TracClient", True),
    ("ValueError", True),
    ("ToolSpec", True),
    ("CommonMark", True),
    ("ABCDef", False),
    ("Abc", False),
    ("AbcDef2", False),
    ("Abc2Def", False),
    ("A1B2", False),
    ("McDonald", True),
    ("DeadPage/SubPage", True),
    ("Rules/RenderVerify", True),
    ("iPhone", False),
    ("XMLParser", False),
]


def _codes(name):
    markdown_source, tracwiki, facts, source_format = _load(name)
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    return [w["code"] for w in warnings]


def test_seeded_pair_separates_authored_from_incidental():
    """Ticket #79's seeded defect, asserted as a PAIR (section 6).

    Watch this RED at `b874c81`: both rows report
    `missing_local_target` at `error` there, which is the defect --
    `The TracClient class owns the XML-RPC session.` is a correct
    sentence about a class, and refusing a write over it is what #64's
    error column would have done to 48 correct documents.

    The pair matters more than either row. Asserting only the
    incidental half passes just as well for a check that stopped firing
    altogether, and #59 and #27 are this project's evidence that this
    is how a check quietly dies. The rendered anchors are structurally
    identical (`class="missing wiki"`, `href=".../wiki/<Name>"`, text
    equal to the name), so nothing in `facts` separates them -- only
    the source does.
    """
    incidental = _codes("row79a_bare_prose_camelcase")
    authored = _codes("row79b_authored_dead_link")

    assert incidental == ["incidental_wiki_autolink"], incidental
    assert authored == ["missing_local_target"], authored


def test_mixed_document_reports_one_of_each():
    """A bare occurrence and an authored `[[...]]` for the SAME target,
    in one document (row 79c).

    This is #70's residual in a new place. A boolean "does the text
    still occur bare" answers yes for the whole document and downgrades
    BOTH anchors, so the dead link vanishes into the advisory column
    while the document reports zero errors -- the exact shape #79
    exists to prevent on the other side of the split. Counting per
    target instead reports one of each.

    Measured on the deployed daemon: this source renders two anchors,
    byte-identical to each other.
    """
    codes = _codes("row79c_mixed_same_target")
    assert codes.count("missing_local_target") == 1, codes
    assert codes.count("incidental_wiki_autolink") == 1, codes


def test_non_autolinkable_page_name_stays_an_error():
    """Row 79f -- the counter-example that makes the shape gate
    load-bearing rather than belt-and-braces.

    `See [[Page]]. The Page is missing.` renders exactly ONE anchor
    (measured), from the authored `[[Page]]`: `Page` is a single hump
    and Trac does not auto-link it. But `Page` DOES still occur bare
    once link syntax is blanked, so #79 section 5's one-test
    remediation would classify that single anchor incidental and
    downgrade a genuinely dead link.

    Nothing here is auto-linkable, so nothing may be downgraded.
    """
    codes = _codes("row79f_non_autolink_page_name")
    assert codes == ["missing_local_target"], codes


def test_escaped_word_is_counted_as_neither():
    """The escaped-word pin, ticket #79 section 6.

    `!TracClient` renders with NO anchor at all (measured: zero
    anchors), so the silence here is a different fact from the silence
    of a correctly-resolving link -- there is nothing to classify. The
    row is in SILENT_ROWS for the no-warning half; this asserts the
    other half, that the escape did not merely move the finding into
    the advisory column.
    """
    codes = _codes("row79d_escaped_camelcase")
    assert codes == [], codes


def test_advisory_carries_the_escape_suggestion():
    """#64 section 5's argument applied here: emit the corrected string,
    not just a diagnosis.

    The advisory knows the exact word Trac linkified, so it can name the
    one-character edit that silences it. `!`-escaping is what #27
    section 3 prescribes and what the store already does by hand.
    """
    markdown_source, tracwiki, facts, source_format = _load(
        "row79a_bare_prose_camelcase"
    )
    warnings = build_warnings(
        markdown_source,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format=source_format,
    )
    assert len(warnings) == 1, warnings
    assert warnings[0]["evidence"]["suggestion"] == "!TracClient"


@pytest.mark.parametrize("word,links", MEASURED_AUTOLINK_SHAPES)
def test_autolink_shape_matches_the_measured_probe_table(word, links):
    """The shape gate, pinned against Trac rather than against
    reasoning about Trac.

    The safety direction here is asymmetric and worth stating: a gate
    that is too NARROW leaves an anchor reported as `error`, which is
    today's behaviour and a false positive we already have. A gate that
    is too WIDE downgrades a real broken link. So this table is the
    authority, and where it and the regex disagree the regex is wrong.
    """
    assert _is_autolink_page_name(word) is links


def test_no_source_falls_back_to_error():
    """`build_verify_warnings` passes `tracwiki=""` when `render_check`
    could not pair a source with the render.

    With no source there is no discriminator, so the check must fall
    back to `missing_local_target` -- unchanged behaviour -- rather
    than guess. Asserted explicitly because the alternative fails
    SILENT: an anchor downgraded for lack of evidence looks exactly
    like one downgraded because the evidence said so.
    """
    _, _, facts, _ = _load("row79a_bare_prose_camelcase")
    warnings = build_warnings(
        None, "", facts, probes={}, check_targets=False
    )
    assert [w["code"] for w in warnings] == ["missing_local_target"]


@pytest.mark.parametrize(
    "name",
    [
        "row79a_bare_prose_camelcase",
        "row79b_authored_dead_link",
        "row79c_mixed_same_target",
    ],
)
def test_both_assemblers_agree(name):
    """Per ticket #77: the new code must appear identically through
    `build_warnings` and `build_verify_warnings`.

    #77 is the precedent -- a check reachable from only one of the two
    assemblers left the pre-write gate blind to it, and two call sites
    is precisely how that blind spot arose. `build_verify_warnings`
    delegates today, so this is a pin on that continuing to be true,
    not a discovery.
    """
    _, tracwiki, facts, _ = _load(name)
    direct = build_warnings(
        None,
        tracwiki,
        facts,
        probes={},
        check_targets=False,
        source_format="tracwiki",
    )
    via_verify = build_verify_warnings(
        tracwiki, facts, probes={}, check_targets=False
    )
    assert [w["code"] for w in direct] == [
        w["code"] for w in via_verify
    ]


@pytest.mark.parametrize(
    "name", sorted(n for n in MANIFEST if not n.startswith("row79"))
)
def test_no_pre_existing_row_is_downgraded(name):
    """The recall gate, in the form this repo can check in.

    Ticket #79 section 6's real gate is the two-store sweep -- all 169
    authored findings still firing, run with `scripts/store_sweep.py`
    against a locally cached corpus. That corpus cannot live in this
    repo (it is public; the `auto_pm` half carries internal host
    addresses and home-directory paths), so what is checked in is the
    weaker but still load-bearing half: no row that existed before this
    ticket may acquire the new advisory code.

    `row13_hand_relative_url` is the row this actually guards. Its
    anchor text IS its page name, which is the shape an auto-link has,
    and only the source scan keeps it an error.
    """
    assert "incidental_wiki_autolink" not in _codes(name)


# --- Ticket #83: three codes where there was one --------------------
#
# `target_check_skipped` merged three unrelated outcomes -- the caller
# disabled the check, the document overflowed the cap, the probe could
# not reach the instance -- and they want different answers from ticket
# #64's blocking gate, so no single severity was right for it.

_TICKET_HREF = "http://192.168.10.4:8000/auto_pm/intertrac/%2387"
_OTHER_HREF = "http://192.168.10.4:8000/auto_pm/intertrac/%2388"

_TWO_TARGETS_HTML = (
    '<p><a class="ext-link" href="' + _TICKET_HREF + '"'
    ' title="#87 in Automated Project Manager">auto_pm:#87</a> and '
    '<a class="ext-link" href="' + _OTHER_HREF + '"'
    ' title="#88 in Automated Project Manager">auto_pm:#88</a></p>'
)


def _probe_codes(probes, check_targets=True):
    warnings = build_warnings(
        markdown_source=None,
        tracwiki="auto_pm:#87 and auto_pm:#88",
        facts=extract_facts(_TWO_TARGETS_HTML),
        probes=probes,
        check_targets=check_targets,
        source_format="tracwiki",
    )
    return [w["code"] for w in warnings]


def test_capped_and_failed_are_different_codes():
    """The pair, in one assertion (ticket #83 section 5).

    A capped target and an unreachable one used to report the SAME
    code. Asserting either alone passes just as well for a rename, so
    both are asserted here, together, and asserted DIFFERENT.
    """
    capped = _probe_codes(
        {
            _TICKET_HREF: {"status": "skipped", "resolved_url": None},
            _OTHER_HREF: {"status": "exists", "resolved_url": None},
        }
    )
    failed = _probe_codes(
        {
            _TICKET_HREF: {"status": "error", "resolved_url": None},
            _OTHER_HREF: {"status": "exists", "resolved_url": None},
        }
    )
    assert capped != failed, (capped, failed)
    assert "target_check_capped" in capped, capped
    assert "target_check_failed" in failed, failed


def test_neither_branch_goes_silent():
    """A split is exactly where one branch quietly stops firing, and
    the whole point of this family is that an unchecked target must
    never look like a checked one. So each code is asserted PRESENT,
    not merely different from the other."""
    assert "target_check_capped" in _probe_codes(
        {_TICKET_HREF: {"status": "skipped", "resolved_url": None}}
    )
    assert "target_check_failed" in _probe_codes(
        {_TICKET_HREF: {"status": "error", "resolved_url": None}}
    )
    assert "target_check_disabled" in _probe_codes(
        {}, check_targets=False
    )


def test_a_target_with_no_probe_result_counts_as_failed():
    """`None` -- a probeable target the probe returned nothing for --
    is the checker's problem, like a failed fetch, and not something
    the author can act on."""
    codes = _probe_codes({})
    assert "target_check_failed" in codes, codes
    assert "target_check_capped" not in codes, codes


def test_capped_and_failed_can_both_fire_on_one_document():
    """They are counted per target, not decided per document: a run
    that overflows the cap AND fails a fetch has both things to say,
    and collapsing to one would lose whichever came second."""
    codes = _probe_codes(
        {
            _TICKET_HREF: {"status": "skipped", "resolved_url": None},
            _OTHER_HREF: {"status": "error", "resolved_url": None},
        }
    )
    assert "target_check_capped" in codes, codes
    assert "target_check_failed" in codes, codes


def test_the_merged_code_is_gone():
    """`target_check_skipped` is deleted, not aliased: leaving it on
    any one of the three paths puts the ambiguity back where #64 has to
    rule on it."""
    for probes, check in (
        ({_TICKET_HREF: {"status": "skipped"}}, True),
        ({_TICKET_HREF: {"status": "error"}}, True),
        ({}, False),
    ):
        assert "target_check_skipped" not in _probe_codes(probes, check)


# ---------------------------------------------------------------------
# Fix suggestions (ticket #64 section 5).
#
# Every string asserted here was rendered through the live daemon before
# being shipped as advice (2026-09-04): all three come back with an
# anchor and zero warnings. A suggestion nobody had rendered would be
# the same guess #59 comment 1 caught a check making about a diagnosis
# -- confidently wrong, and pointing the author somewhere useless.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "row,expected",
    [
        ("row61_ticket_possessive", "[auto_pm:#87 #87]'s"),
        ("row62_ticket_hyphen_suffix", "[auto_pm:#87 #87]-ish"),
    ],
)
def test_captured_punctuation_suggests_the_bracket_form(row, expected):
    """The bracket form ends the target at the space, so the swallowed
    tail stays outside it -- the fix the decision table on
    `Rules/trac/PreferInterTracLinks` prescribes.

    Both suffixes are asserted because they are different shapes of the
    same defect: an apostrophe and a hyphen. A suggestion builder keyed
    on punctuation class rather than on the measured tail would pass one
    and fail the other.
    """
    markdown_source, tracwiki, facts, source_format = _load(row)
    found = [
        w
        for w in build_warnings(
            markdown_source,
            tracwiki,
            facts,
            probes={},
            check_targets=False,
            source_format=source_format,
        )
        if w["code"] == "intertrac_target_captured_punctuation"
    ]
    assert len(found) == 1, found
    assert found[0]["evidence"]["suggestion"] == expected


def test_markdown_link_suggests_the_tracwiki_bracket_form():
    """The highest-value suggestion in the suite.

    `Rules/trac/PreferInterTracLinks` calls this shape out as rendering
    THREE wrong things from one reference -- a dead local wiki link, a
    stray auto-link, and visible punctuation -- and names the two codes
    it produces as a pair. An author who applies this one suggestion
    clears both.
    """
    facts = extract_facts(
        "<p>See [the ticket](auto_pm:#87) for detail.</p>"
    )
    found = [
        w
        for w in build_warnings(
            None, "", facts, probes={}, check_targets=False
        )
        if w["code"] == "literal_markup_in_render"
    ]
    assert len(found) == 1, found
    assert (
        found[0]["evidence"]["suggestion"] == "[auto_pm:#87 the ticket]"
    )


def test_non_link_literal_markup_carries_no_suggestion():
    """The other four patterns this check scans for -- a stray fence, a
    TracWiki table, bold, a block delimiter -- have no mechanical
    rewrite, so they must carry no `suggestion` key at all rather than
    an empty or guessed one.

    Stated as its own row because "absent" and "present but useless"
    look identical to a caller that only checks `.get`.
    """
    facts = extract_facts("<p>A stray fence ``` in prose.</p>")
    found = [
        w
        for w in build_warnings(
            None, "", facts, probes={}, check_targets=False
        )
        if w["code"] == "literal_markup_in_render"
    ]
    assert len(found) == 1, found
    assert "suggestion" not in found[0]["evidence"]
