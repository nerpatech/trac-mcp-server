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


def test_suite_balance_is_roughly_even():
    """Per the ticket's coverage note: silent and warning rows must be
    roughly balanced, or a checker that warns about everything (or never
    warns at all) would pass on a single green run. Row 15 (live-probe
    only) and the seeded-defect fixture are outside this static count."""
    silent_count = len(SILENT_ROWS)
    # Suite 3 also names row 15 (live-probe, covered by a separate live
    # test rather than this static list), bringing the full ticket table
    # to 17 silent / 18 warning once it's counted (ticket #57 moved row
    # 10 into the warning half and added rows 17-21, then comment 4's
    # reopen added rows 22-35).
    warning_count = len(WARNING_ROWS) + 1
    assert silent_count == 17
    assert warning_count == 18
    # "Roughly even" per the ticket's coverage note, not a bulk weighted
    # to positives: neither side outnumbers the other more than 2:1.
    assert (
        max(silent_count, warning_count)
        / min(silent_count, warning_count)
        <= 2.0
    )
