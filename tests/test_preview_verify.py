"""Table-driven suite for ``preview.verify`` (ticket #55).

Fixtures in ``tests/fixtures/render_check/`` are captured from the live
daemon's ``wiki.wikiToHtml`` (same renderer a live ticket/wiki page uses),
per ``Rules/testing/RealSubstrateNotMocks`` -- ``manifest.json`` records
the exact TracWiki input and expected warning codes for each row.

Row 8 is the seeded-defect-first pin required by the ticket: the same
literal markup as row 7, but inside a real code block documenting the
syntax, must stay SILENT -- proving the check scans ``prose_text`` (which
excludes ``<pre>``/``<code>`` subtrees) and not ``plain_text``.

Ticket #77 added rows 12/13 (the same seed/pin pair on the Markdown-link
shape it measured) and, more importantly, the cross-assembler equality
test at the bottom of this file. The literal-markup check used to live in
``verify.py`` and so ran on the post-write path only; every fixture here
now goes through BOTH assemblers with its code sets compared for
equality, because the defect that ticket fixed *was* a difference between
the two, which no single-path fixture can see.
"""

import json
from pathlib import Path

import pytest

from trac_mcp_server.preview.checks import build_warnings
from trac_mcp_server.preview.facts import extract_facts
from trac_mcp_server.preview.targets import EXISTS, MISSING
from trac_mcp_server.preview.verify import build_verify_warnings

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "render_check"
MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text())


def _fixture_html(name: str) -> str:
    return (FIXTURES_DIR / f"{name}.html").read_text()


def _fixture_tracwiki(name: str) -> str:
    return (FIXTURES_DIR / f"{name}.tracwiki.txt").read_text()


def _codes_for(name: str, probes: dict | None = None) -> list[str]:
    facts = extract_facts(_fixture_html(name))
    warnings = build_verify_warnings(
        tracwiki=_fixture_tracwiki(name),
        facts=facts,
        probes=probes or {},
        check_targets=probes is not None,
    )
    return [w["code"] for w in warnings]


class TestBuildVerifyWarningsSuite:
    """Every row not requiring a live probe: manifest-driven pass/fail."""

    @pytest.mark.parametrize(
        "name,row",
        [
            (n, r)
            for n, r in MANIFEST.items()
            if not r["requires_check_targets"]
        ],
    )
    def test_row_produces_expected_codes(self, name, row):
        codes = _codes_for(name)
        for expected in row["must_warn"]:
            assert expected in codes, (
                f"{name}: expected '{expected}' in {codes} "
                f"({row['note']})"
            )
        if not row["must_warn"]:
            assert codes == [], (
                f"{name}: expected zero warnings, got {codes} "
                f"({row['note']})"
            )

    def test_row08_pin_stays_silent_despite_row07_sharing_the_markup(
        self,
    ):
        """The over-correction pin, stated explicitly: row 7 and row 8
        contain the identical literal text `|=h=|` -- row 7 in prose
        (must warn), row 8 inside a `<pre>` block documenting it (must
        NOT warn). A checker scanning `plain_text` instead of
        `prose_text` would warn on both."""
        assert "literal_markup_in_render" in _codes_for(
            "row07_literal_markup_table"
        )
        assert "literal_markup_in_render" not in _codes_for(
            "row08_literal_markup_in_code_block_pin"
        )


class TestBuildVerifyWarningsRequiresCheckTargets:
    """Row 3 needs a live probe result to distinguish from a real
    target -- InterTrac dispatcher links render identically either way
    (ticket #55's core motivating trap)."""

    def test_missing_cross_instance_target_warns_when_probe_says_missing(
        self,
    ):
        facts = extract_facts(
            _fixture_html("row03_missing_cross_instance_target")
        )
        href = facts.anchors[0].href
        probes = {href: {"status": MISSING, "resolved_url": None}}
        codes = _codes_for(
            "row03_missing_cross_instance_target", probes=probes
        )
        assert "missing_cross_instance_target" in codes

    def test_same_href_stays_silent_when_probe_says_exists(self):
        """Same render, opposite probe outcome -- proves the warning is
        driven by the probe result, not by anything in the render
        itself (the render is identical either way)."""
        facts = extract_facts(
            _fixture_html("row03_missing_cross_instance_target")
        )
        href = facts.anchors[0].href
        assert href is not None
        probes = {
            href: {
                "status": EXISTS,
                "resolved_url": href.replace(
                    "/intertrac/wiki%3A", "/wiki/"
                ),
            }
        }
        codes = _codes_for(
            "row03_missing_cross_instance_target", probes=probes
        )
        assert "missing_cross_instance_target" not in codes


class TestBuildVerifyWarningsSkipsMarkdownRule:
    """``build_verify_warnings`` has no Markdown source to check -- must
    never attempt to run ``_check_tracwiki_markup_in_markdown`` (which
    would crash on ``None``), and must not emit its warning code."""

    def test_no_conversion_warning_code_appears(self):
        for name in MANIFEST:
            codes = _codes_for(name)
            assert "tracwiki_markup_in_markdown" not in codes


class TestCodeBlockExtractionThroughVerify:
    """``code_blocks``/``prose_text`` (facts.py's #55 extension) as
    exercised through a live-captured mixed render."""

    def test_highlighted_and_plain_blocks_both_extracted(self):
        facts = extract_facts(_fixture_html("row09_code_blocks_mixed"))
        self_check = [
            (cb.highlighted, len(cb.text.splitlines()))
            for cb in facts.code_blocks
        ]
        assert self_check == [(True, 2), (False, 2)]


def _codes_through_build_warnings(name: str) -> list[str]:
    """The same fixture through the OTHER assembler -- the one
    `convert_preview` calls. `source_format="tracwiki"` because these
    fixtures are stored TracWiki, which is what the pre-write gate sees
    on its tracwiki leg (the default on both legs since ticket #69)."""
    facts = extract_facts(_fixture_html(name))
    warnings = build_warnings(
        markdown_source=_fixture_tracwiki(name),
        tracwiki=_fixture_tracwiki(name),
        facts=facts,
        probes={},
        check_targets=False,
        source_format="tracwiki",
    )
    return [w["code"] for w in warnings]


class TestBothAssemblersAgree:
    """Ticket #77 -- the assertion the ticket lives or dies by.

    The defect WAS a difference between two code paths:
    `_check_literal_markup_in_render` was reachable only through
    `build_verify_warnings`, so `convert_preview` -- the one PRE-write
    gate -- was blind to markup that survived unconverted, while both
    post-write render-check tools saw it. A fixture exercising only one
    assembler cannot detect that, which is exactly how the split
    survived ticket #55.

    So this asserts the two code sets are EQUAL, not merely that each
    contains its expected code: a check that lands on one path only
    fails here whichever path it lands on, and so does a check that
    silently stops running on one of them.
    """

    @pytest.mark.parametrize(
        "name",
        sorted(
            n
            for n, r in MANIFEST.items()
            if not r["requires_check_targets"]
        ),
    )
    def test_verify_and_preview_paths_produce_identical_codes(
        self, name
    ):
        verify_codes = _codes_for(name)
        preview_codes = _codes_through_build_warnings(name)
        assert verify_codes == preview_codes, (
            f"{name}: verify path gave {verify_codes}, pre-write path "
            f"gave {preview_codes} -- the two assemblers have diverged"
        )


class TestLiteralMarkupReachesThePreWriteGate:
    """Ticket #77's seed and its over-correction pin, asserted through
    the PRE-write assembler specifically.

    Rows 7/8 are the same shape one layer up, but they pin the TracWiki
    TABLE form; row 12 is the shape actually measured on the deployed
    daemon -- a Markdown link whose target is an InterTrac short link
    rather than a URL, so the converter leaves it alone and the label
    survives as literal bracket text. Measured `warnings: []` through
    `convert_preview` at `d7823de`, so this genuinely fails before the
    fix.
    """

    def test_seed_warns_through_the_pre_write_assembler(self):
        assert (
            "literal_markup_in_render"
            in _codes_through_build_warnings(
                "row12_markdown_link_unconverted"
            )
        )

    def test_code_block_pin_stays_silent_through_both(self):
        """The identical bytes inside a `{{{ }}}` block. Both
        assemblers, because an over-correction that only reaches one of
        them is the same defect in the opposite direction."""
        name = "row13_markdown_link_in_code_block_pin"
        assert _codes_through_build_warnings(name) == []
        assert _codes_for(name) == []
