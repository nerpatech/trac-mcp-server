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

from trac_mcp_server.preview.checks import build_warnings
from trac_mcp_server.preview.facts import extract_facts

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
]

# Seeded defect: row 1 rendered from ITS PRE-FIX TracWiki
# (`auto_pm:wiki:Rules/trac/!RenderVerify`, the escape ticket #44's fix at
# `2bf11d9`/`1ef59b3` removed). Rendered directly against the live daemon
# rather than re-derived from the (already-fixed) converter, so this
# fixture is immune to the converter itself changing.
SEEDED_DEFECT_ROW = ("row01_pre_fix", "escaped_link_target")


def _load(name: str):
    html = (FIXTURES_DIR / f"{name}.html").read_text()
    data = MANIFEST[name]
    tracwiki = data["tracwiki"]
    markdown_source = data["markdown_input"] or tracwiki
    return markdown_source, tracwiki, extract_facts(html)


@pytest.mark.parametrize("name", SILENT_ROWS)
def test_silent_rows_produce_no_warning(name):
    markdown_source, tracwiki, facts = _load(name)
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
    )
    codes = [
        w["code"]
        for w in warnings
        if w["code"] != "target_check_skipped"
    ]
    assert codes == [], f"{name} should be silent, got {codes}"


@pytest.mark.parametrize("name,expected_code", WARNING_ROWS)
def test_warning_rows_produce_expected_code(name, expected_code):
    markdown_source, tracwiki, facts = _load(name)
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
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
    markdown_source, tracwiki, facts = _load(name)
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
    )
    codes = [w["code"] for w in warnings]
    assert expected_code in codes


def test_row1_post_fix_does_not_warn_escaped_link_target():
    """The counterpart to the seeded-defect test: the CURRENT converter's
    row 1 output must NOT trip the same check the pre-fix fixture does --
    otherwise the check can't tell fixed from broken."""
    markdown_source, tracwiki, facts = _load("row01_intertrac_wiki")
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
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
    markdown_source, tracwiki, facts = _load(
        "row11_code_span_intertrac"
    )
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
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
    markdown_source, tracwiki, facts = _load("row47_bold_realm")
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
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
    markdown_source, tracwiki, facts = _load(
        "row48_bracket_same_label_pair"
    )
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
    )
    codes = [
        w["code"]
        for w in warnings
        if w["code"] != "target_check_skipped"
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
    markdown_source, tracwiki, facts = _load("row46_ticket_code_span")
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
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
    markdown_source, tracwiki, facts = _load(
        "row54_pragma_allows_code_span_ref"
    )
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
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
    markdown_source, tracwiki, facts = _load(
        "row55_pragma_is_code_scoped"
    )
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
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
    markdown_source, tracwiki, facts = _load(
        "row58_fence_plus_unconfigured_prose"
    )
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
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
    markdown_source, tracwiki, facts = _load(name)
    warnings = build_warnings(
        markdown_source, tracwiki, facts, probes={}, check_targets=False
    )
    codes = [w["code"] for w in warnings]
    assert "intertrac_target_captured_punctuation" in codes, codes
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
    # counted here -- see their comments).
    warning_count = len(WARNING_ROWS) + 1
    assert silent_count == 31
    assert warning_count == 27
    # "Roughly even" per the ticket's coverage note, not a bulk weighted
    # to positives: neither side outnumbers the other more than 2:1.
    assert (
        max(silent_count, warning_count)
        / min(silent_count, warning_count)
        <= 2.0
    )
