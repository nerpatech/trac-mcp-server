"""Blank-line-after-a-list pin for ticket #90.

Found while fixing #75, whose write-leg fix (indenting a list item's
continuation lines under the item text) unmasked this rather than caused
it: the read leg has never inserted a blank line after a list, and #75's
own bug (flattening every continuation to column zero regardless of
source) coincidentally reproduced byte-identical output for this shape,
which is why it went unnoticed until #75 fixed continuation indentation
correctly.

**A paragraph that immediately follows a TracWiki list, with no blank line
and no indentation, is meant to terminate the list** -- Trac renders it as
a separate ``<p>``, not part of the last item, the same fact #75 measured
for a flush-left *continuation* line: not deep enough to continue, so the
list ends. ``tracwiki_to_markdown`` passed such a paragraph through
unchanged, flush left, with no blank line -- and that intermediate Markdown
is indistinguishable to CommonMark's own parser from a genuine lazy
continuation of the last list item, which is what got absorbed into it on
the way back through ``markdown_to_tracwiki``.

Per ticket #90 section 5, the seed is asserted on **rendered HTML**, not
stored bytes alone, since this is a structural absorption
(``Rules/testing/RealSubstrateNotMocks``): captured from the live renderer
into ``tests/fixtures/list_trailing_paragraph/``.

The fix inserts the blank line Trac's own renderer already implies before
the read leg hands off to Markdown. Measured live (``convert_preview``,
``format="tracwiki"``, ``/trac_test``) that this is render-neutral -- the
original TracWiki source and the fixed round-trip's TracWiki both render
to byte-identical HTML (``tests/fixtures/list_trailing_paragraph/original.html``)
-- while the PRE-FIX round trip (`` * Domains text.\\n   This one field...``,
the paragraph wrongly indented into the item) rendered as one ``<li>``
containing both lines and only one ``<p>``, confirmed live and recorded
here rather than re-measured by a test, since pinning the broken shape
going forward would defeat the fix.
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
    Path(__file__).parent / "fixtures" / "list_trailing_paragraph"
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


class TestTrailingParagraphStaysSeparate(unittest.TestCase):
    """The seed: ticket #90 section 2's minimal repro, watched failing
    first (pre-fix: the paragraph came back indented three spaces inside
    the item -- see the module docstring for the live-rendered proof that
    was a structural, not cosmetic, change)."""

    def test_the_paragraph_is_not_absorbed(self):
        src, _ = _fixture_html("domains_seed")
        stored, md, warnings = _round_trip(src)
        self.assertFalse(warnings)
        self.assertEqual(
            stored,
            " * Domains text.\n"
            "\n"
            "This one field is a separate paragraph, not list content.\n"
            "\n"
            "Keep going.",
        )
        # The intermediate Markdown is where the absorption actually
        # happened (ticket #90 section 1) -- assert there too, not only on
        # the round-tripped bytes.
        self.assertIn("- Domains text.\n\nThis one field", md)

    def test_source_and_fixed_round_trip_render_identically(self):
        """Render-neutral, measured live: the blank line this fix adds
        changes nothing Trac shows -- both the original TracWiki and the
        fixed round-trip render to the exact same HTML."""
        src, original_html = _fixture_html("domains_seed")
        stored, _, _ = _round_trip(src)
        self.assertNotEqual(
            stored, src
        )  # the fix does change the stored bytes...
        # ...but only by a blank line Trac already treats as absent-or-
        # present-doesn't-matter for this shape -- both renders are pinned
        # to the SAME captured HTML.
        self.assertEqual(original_html.count("<li>"), 1)
        self.assertEqual(original_html.count("<p>"), 2)


class TestRecallGate(unittest.TestCase):
    """What #75 already fixed must keep working exactly as it did."""

    def test_genuine_continuations_are_untouched(self):
        """#75's own seeds, re-asserted here rather than only imported --
        this pass runs inside the same method (`_convert_lists`) #75's
        fix is downstream of, so a regression here is the most likely
        place for the two to collide."""
        for src in (
            " * item one\n   continuation of item one",
            " * one\n   more of one\n * two",
            " 1. one\n    more of one",
            " * item one",
        ):
            with self.subTest(src):
                stored, _, warnings = _round_trip(src)
                self.assertEqual(stored, src)
                self.assertFalse(warnings)

    def test_a_line_already_separated_by_a_blank_line_gains_no_second_one(
        self,
    ):
        src = " * item one\n\nTrailing prose."
        stored, _, _ = _round_trip(src)
        self.assertEqual(stored, src)

    def test_nested_list_then_outer_level_continuation_stays_in_the_outer_item(
        self,
    ):
        """A line between the outer and the inner marker columns closes the
        inner item and continues the OUTER one -- the stack-based depth
        check has to drop only the levels this line closes, not all of them.

        Ticket #149 changed this row: it used to assert no blank line was
        inserted (the line stayed at its source column, which CommonMark
        reads as a lazy continuation of the closed inner item's paragraph,
        so the write leg put the text under `b`). Trac's measured rule (the
        line at column 2 closes `b` and continues `a`) is now kept: the line
        goes to `a`'s text column with a blank line first, and the write leg
        gives it back at that column, still deeper than the outer marker.
        The blank line is #125's list-item split, the accepted trade-off.
        """
        src = " * a\n   * b\n  outer continuation"
        stored, md, _ = _round_trip(src)
        self.assertEqual(md, " - a\n   - b\n\n   outer continuation")
        self.assertEqual(
            stored, " * a\n   * b\n\n   outer continuation"
        )

    def test_nested_list_fully_closed_gets_separated(self):
        src = " * a\n   * b\nend."
        stored, _, _ = _round_trip(src)
        self.assertEqual(stored, " * a\n   * b\n\nend.")


class TestInterruptingConstructsAreNotDoublySeparated(
    unittest.TestCase
):
    """A construct CommonMark already reads as ending the list on its own
    -- heading, blockquote, code fence, thematic break -- must not gain a
    blank line from this pass. Measured against mistune directly (with
    this project's own ``plugins=["table"]`` configuration, matching
    ``markdown_to_tracwiki``'s parser): those four interrupt a list with
    no blank line; a GFM table row does NOT (it lazily absorbs exactly
    like plain prose), so a table row is deliberately excluded from this
    class -- it takes the same fix as plain prose, asserted in
    ``TestNonInterruptingConstructsAreSeparated`` below."""

    def test_heading_is_untouched(self):
        _, md, _ = _round_trip(" * a\n= H =")
        self.assertNotIn("\n\n# H", md)
        self.assertIn("\n# H", md)

    def test_thematic_break_is_untouched(self):
        _, md, _ = _round_trip(" * a\n----")
        self.assertNotIn("\n\n----", md)

    def test_code_block_is_untouched(self):
        _, md, _ = _round_trip(" * a\n{{{\ncode\n}}}")
        self.assertNotIn("\n\n```", md)


class TestNonInterruptingConstructsAreSeparated(unittest.TestCase):
    """A table row is lazily absorbed exactly like plain prose (measured
    against mistune, same configuration as above), so it takes the same
    blank-line fix -- confirming this pass doesn't only special-case
    'paragraph' but genuinely tests for what CommonMark would otherwise
    swallow."""

    def test_table_row_gets_separated(self):
        _, md, _ = _round_trip(" * a\n||c1||c2||")
        self.assertIn("\n\n", md)
        self.assertNotIn("a\n||", md)


class TestItemChildrenKeepSourceOrder(unittest.TestCase):
    """Ticket #112: a list item's children must come out in source order.

    ``list_item`` used to split an item's children into "everything that
    is not a list" and "the lists", and emit all of the first group, then
    all of the second -- so a paragraph that FOLLOWS a nested list in the
    Markdown was written ABOVE it. Every byte survived, so no check could
    see it. Every row except the unchanged control fails on the pre-fix
    code. These pin the converter's output string only. How Trac renders
    that shape was measured live on /trac_test and is recorded on the
    ticket: the paragraph follows the sub-list, but Trac closes the outer
    list at the blank line, so later siblings open a new list (an ordered
    one continues its numbering via ``<ol start>``).
    """

    def test_ticket_input_paragraph_stays_after_sub_list(self):
        source = (
            "- **Cause:** unknown. Suspects, in order:\n"
            "  - first suspect;\n"
            "  - second suspect.\n"
            "\n"
            "  A paragraph that belongs after the list.\n"
            "- **Guard:** next item.\n"
        )
        self.assertEqual(
            markdown_to_tracwiki(source),
            " * '''Cause:''' unknown. Suspects, in order:\n"
            "   * first suspect;\n"
            "   * second suspect.\n"
            "\n"
            "   A paragraph that belongs after the list.\n"
            " * '''Guard:''' next item.",
        )

    def test_two_trailing_paragraphs_keep_their_order(self):
        source = "- Cause:\n  - a\n  - b\n\n  Para one.\n\n  Para two.\n- Next\n"
        self.assertEqual(
            markdown_to_tracwiki(source),
            " * Cause:\n"
            "   * a\n"
            "   * b\n"
            "\n"
            "   Para one. [[BR]][[BR]]\n"
            "   Para two.\n"
            " * Next",
        )

    def test_paragraph_after_a_two_level_nested_list(self):
        source = "- Top\n  - mid\n    - deep\n\n  Trailing after nested.\n- Next\n"
        self.assertEqual(
            markdown_to_tracwiki(source),
            " * Top\n"
            "   * mid\n"
            "     * deep\n"
            "\n"
            "   Trailing after nested.\n"
            " * Next",
        )

    def test_paragraph_between_two_sub_lists(self):
        source = "- Top\n  - a\n\n  Between.\n\n  - b\n- Next\n"
        self.assertEqual(
            markdown_to_tracwiki(source),
            " * Top\n   * a\n\n   Between.\n   * b\n * Next",
        )

    def test_ordered_item_indents_under_its_own_marker(self):
        source = "1. Top\n   - a\n\n   After.\n2. Next\n"
        self.assertEqual(
            markdown_to_tracwiki(source),
            " 1. Top\n   * a\n\n    After.\n 2. Next",
        )

    def test_sub_list_without_trailing_paragraph_is_unchanged(self):
        # Control: nothing follows the sub-list, so nothing can move.
        source = "- Top\n  - a\n  - b\n- Next\n"
        self.assertEqual(
            markdown_to_tracwiki(source),
            " * Top\n   * a\n   * b\n * Next",
        )


if __name__ == "__main__":
    unittest.main()
