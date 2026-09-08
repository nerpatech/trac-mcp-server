"""List-item continuation-indent round-trip pin for ticket #75.

Found while fixing #73, alongside #74 and #76, and verified byte-identical to
``master`` before that fix -- pre-existing, not a regression from it.

The write leg emitted a list item's continuation line (a soft break inside
the item, or the second-plus paragraph of a loose item) at *column zero*.
Trac's list grammar only continues an item when the following line is
indented deeper than the marker; flush against the margin, the continuation
breaks out of the ``<li>`` into a sibling ``<p>`` -- a change of document
structure, not just whitespace, and silent: the warning list is empty on
every row (the #68 shape).

**The seeds were watched failing first.** Measured on ``master`` before this
ticket's fix: every row below round-tripped through ``tw -> md -> tw`` with
the continuation line dedented to column zero, splitting the ``<li>`` in
Trac's own renderer.

Per ticket #75 section 5, the four seed rows are additionally asserted on
**rendered HTML**, not stored bytes alone, because the byte diff (a few
leading spaces) understates a structural change. Those renders are captured
from the live renderer into ``tests/fixtures/list_continuation_indent/`` per
``Rules/testing/RealSubstrateNotMocks``.
"""

import json
import unittest
from pathlib import Path

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    tracwiki_to_markdown,
)

FIXTURES_DIR = (
    Path(__file__).parent / "fixtures" / "list_continuation_indent"
)
MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text())


def _round_trip(source):
    """Return (stored bytes after tw -> md -> tw, intermediate md, warnings)."""
    result = tracwiki_to_markdown(source)
    return (
        markdown_to_tracwiki(result.text),
        result.text,
        result.warnings,
    )


def _row(name):
    for row in MANIFEST["rows"]:
        if row["name"] == name:
            return row
    raise AssertionError(f"no manifest row named {name!r}")


def _fixture_html(name):
    row = _row(name)
    return row["tracwiki"], (FIXTURES_DIR / row["html"]).read_text()


class TestContinuationStaysInsideItem(unittest.TestCase):
    """The seeds: section 2's four damaged rows.

    Each must round-trip so the continuation stays inside the item's
    ``<li>`` -- not necessarily byte-identical to the source (ticket #76's
    territory, deliberately separate), but structurally intact, which is
    what the fixture HTML (captured from the live renderer) confirms.
    """

    def test_aligned_continuation_round_trips_byte_identical(self):
        """Section 2 row 1: continuation already aligned in the source."""
        src, html = _fixture_html("item_one_continuation_aligned")
        stored, _, warnings = _round_trip(src)
        self.assertEqual(stored, src)
        self.assertFalse(warnings)
        self.assertEqual(html.count("<li>"), 1)
        self.assertNotIn("<p>", html)

    def test_unaligned_source_continuation_normalises_and_stays_inside_the_item(
        self,
    ):
        """Section 2 row 2: a 2-space source continuation isn't aligned
        under 'item one', but is still deeper than the marker. The fixed
        write leg normalises it to 3 spaces -- not byte-identical to the
        source, per ticket #75 section 4's slack -- and it still renders
        as one <li>, which is the fixture pinned here."""
        source = " * item one\n  continuation"
        pinned, html = _fixture_html("item_one_continuation_unaligned")
        stored, _, warnings = _round_trip(source)
        self.assertEqual(stored, pinned)
        self.assertFalse(warnings)
        self.assertEqual(html.count("<li>"), 1)
        self.assertNotIn("<p>", html)

    def test_continuation_in_the_middle_of_a_list_stays_with_its_item(
        self,
    ):
        """Section 2 row 3, the row worth reading twice: before the fix
        this split the list in two around an absorbed paragraph, not just
        lost a tail line."""
        src, html = _fixture_html("one_more_of_one_two")
        stored, _, warnings = _round_trip(src)
        self.assertEqual(stored, src)
        self.assertFalse(warnings)
        self.assertEqual(html.count("<li>"), 2)
        self.assertNotIn("<p>", html)

    def test_ordered_list_continuation_stays_inside_the_item(self):
        """Section 2 row 4: same defect in an ordered list."""
        src, html = _fixture_html("ordered_one_more_of_one")
        stored, _, warnings = _round_trip(src)
        self.assertEqual(stored, src)
        self.assertFalse(warnings)
        self.assertEqual(html.count("<li>"), 1)
        self.assertNotIn("<p>", html)


class TestRecallGate(unittest.TestCase):
    """Constructs pinned by earlier tickets that this fix must not disturb."""

    def test_single_line_item_is_unaffected(self):
        src = " * item one"
        stored, _, _ = _round_trip(src)
        self.assertEqual(stored, src)

    def test_nested_list_is_unaffected(self):
        """#73's TestListsAreNotQuotes control: nested lists round-trip
        byte-for-byte, and a naive 'indent every line' rule would land on
        the nested item's own already-indented output."""
        src = " * a\n   * b"
        stored, _, _ = _round_trip(src)
        self.assertEqual(stored, src)

    def test_numbered_list_is_unaffected(self):
        src = " 1. a"
        stored, _, _ = _round_trip(src)
        self.assertEqual(stored, src)

    def test_list_of_several_single_line_items_is_unaffected(self):
        src = " * a\n * b\n * c"
        stored, _, _ = _round_trip(src)
        self.assertEqual(stored, src)

    def test_absorbed_paragraph_still_separates_from_the_next_real_paragraph(
        self,
    ):
        """Ticket #52's territory, the interaction ticket #75 section 4
        flags explicitly: a paragraph with no blank line before it in the
        Markdown source is a lazy continuation of the *last* list item
        (confirmed on mistune's own AST), so this fix correctly indents it
        to keep it inside that item -- and `list()`'s own trailing blank
        line must still separate it from the real sibling paragraph that
        follows, rather than being swallowed by this fix."""
        markdown = (
            "- alpha\n"
            "- beta\n"
            "**Bold lead:** first paragraph.\n"
            "\n"
            "Second paragraph.\n"
        )
        result = markdown_to_tracwiki(markdown)
        self.assertIn(
            "'''Bold lead:''' first paragraph.\n\nSecond paragraph.",
            result,
        )
        self.assertIn(
            " * beta\n   '''Bold lead:''' first paragraph.\n\n", result
        )

    def test_known_gap_a_flush_left_tracwiki_paragraph_after_a_list_gets_absorbed(
        self,
    ):
        """Documents ticket #90, filed while fixing this one: a TracWiki
        paragraph that follows a list with no blank line and no indentation
        is meant to terminate the list (Trac renders it as a separate
        ``<p>``, confirmed on the live renderer) -- but the read leg never
        inserts a blank line to stop CommonMark's lazy continuation from
        absorbing it into the last item on the way through. This fix
        correctly indents whatever the AST says is a continuation; it
        cannot by itself distinguish that shape from a paragraph that was
        never meant to be list content. Not this ticket's bug (see #75
        comment 1) -- pinned here so a future change to either leg doesn't
        silently alter this known, tracked gap without noticing."""
        src = (
            " * one\n"
            " * two\n"
            "This paragraph was not meant to be part of the list."
        )
        stored, _, _ = _round_trip(src)
        self.assertNotEqual(stored, src)
        self.assertEqual(
            stored,
            " * one\n"
            " * two\n"
            "   This paragraph was not meant to be part of the list.",
        )
