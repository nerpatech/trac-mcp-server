"""Indented-TracWiki round-trip pin for ticket #73.

Indentation in TracWiki is a blockquote.  The converter modelled exactly one
width -- two spaces -- and every other width fell through to Markdown, where
four-space indentation means something else entirely.  A quoted paragraph came
back as a paragraph at one and three spaces, and as a literal ``{{{ }}}`` code
block at four spaces and at a tab: a change of *content type*, silently.

**The grammar was measured against Trac's own renderer**, not taken from the
ticket -- ``convert_preview`` with ``format="tracwiki"`` on ``/trac_test``,
reading the returned HTML.  That measurement contradicted the ticket's own
section 1, which is recorded here because the correction is what the fix is
built on (see comment 1 on ticket #73, and the operator's agreement in
comment 2):

===============================================  =========================
source                                           Trac renders
===============================================  =========================
`` one`` alone                                   1 x ``<blockquote>``
``  two`` alone                                  1 x ``<blockquote>``
``   three`` alone                               1 x ``<blockquote>``
``    four`` alone                               1 x ``<blockquote>``
tab alone                                        1 x ``<blockquote>``
`` one`` / ``  two`` / ``   three`` consecutive  3 x *nested*
``   deep`` then ``  shallower``                 2 x *sibling*, depth 1
`` one``, blank line, ``  two``                  depth 1, then depth **2**
===============================================  =========================

So indentation is **relative, not absolute**: an indent stack, one nested
blockquote per *increase* in width regardless of the size of the increase, a
pop on a dedent, and a reset on column-zero content.  The ticket's "one
``<blockquote>`` per space" was a ladder being read as a scale.

That correction rules out the ticket's own remediation.  Mapping *n* spaces to
*n* ``>`` markers would render a four-space quote -- one blockquote in Trac --
as four nested quotes in Markdown, which is precisely the "Markdown that means
something different from the TracWiki it came from" the ticket forbids.  And
underneath it sits an impossibility: **Markdown blockquote syntax carries depth
but not width**, so only one indent width per depth can survive byte-for-byte.

Hence the rule this file pins: **convert only when the write leg would
reproduce the source byte-for-byte, and otherwise take ticket #63's verbatim
fallback.**  The write leg emits two spaces per nesting level, so depth *d* is
canonically 2*d* spaces; anything else is unrepresentable and travels verbatim,
warned rather than silent.

Per ticket #73 section 5 every assertion is on the **stored bytes after a full
tw -> md -> tw cycle**, and the four-space row is additionally asserted on the
**rendered HTML**, because comparing bytes alone understates a change of
content type.  Those renders are captured from the live renderer into
``tests/fixtures/blockquote_indent/`` per ``Rules/testing/RealSubstrateNotMocks``.
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

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "blockquote_indent"
MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text())

INDENT_WARNING = "Indented block"


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


class TestIndentedQuoteRoundTrip(unittest.TestCase):
    """The seeds: every indent width must return byte-for-byte.

    All five rows are the ticket's section 2 table.  Four of them are broken
    before the fix -- two by a stripped indent, two by promotion to a code
    block -- so all four genuinely fail first, as
    ``Rules/testing/SeededDefectFirst`` requires.  The two-space row is the
    recall gate: it already works and must keep working.
    """

    # (label, source, must this row warn?)
    WIDTHS = [
        ("one space", " Quoted prose.", True),
        ("two spaces", "  Quoted prose.", False),
        ("three spaces", "   Quoted prose.", True),
        ("four spaces", "    Quoted prose.", True),
        ("a tab", "\tQuoted prose.", True),
        ("column zero (control)", "Quoted prose.", False),
    ]

    def test_stored_bytes_are_unchanged(self):
        for label, src, _ in self.WIDTHS:
            with self.subTest(label):
                stored, _, _ = _round_trip(src)
                self.assertEqual(stored, src)

    def test_no_width_becomes_a_code_block(self):
        """The serious half of the defect: content changing type."""
        for label, src, _ in self.WIDTHS:
            with self.subTest(label):
                stored, _, _ = _round_trip(src)
                self.assertNotIn("{{{", stored)
                self.assertNotIn("}}}", stored)

    def test_unrepresentable_widths_warn(self):
        """The silence is half the defect -- #68's shape, concretely."""
        for label, src, should_warn in self.WIDTHS:
            with self.subTest(label):
                _, _, warnings = _round_trip(src)
                warned = any(INDENT_WARNING in w for w in warnings)
                self.assertEqual(warned, should_warn)

    def test_multi_line_quote_keeps_every_line_indented(self):
        """A two-line quotation must not return as a two-line paragraph."""
        src = " line one\n line two"
        stored, _, _ = _round_trip(src)
        self.assertEqual(stored, src)


class TestCanonicalDepthIsConverted(unittest.TestCase):
    """The canonical widths take the real conversion, not the fallback.

    Two spaces per nesting level is what ``block_quote()`` emits, so these are
    the widths Markdown can carry losslessly.  They must travel as ordinary
    ``>`` prose -- a fallback here would be a regression in readability even
    though the bytes would still match.
    """

    def test_two_space_quote_uses_markdown_blockquote(self):
        stored, md, warnings = _round_trip("  Quoted prose.")
        self.assertEqual(md, "> Quoted prose.")
        self.assertEqual(stored, "  Quoted prose.")
        self.assertFalse([w for w in warnings if INDENT_WARNING in w])

    def test_nested_canonical_depth_round_trips_and_keeps_depth(self):
        """Depth 1 then depth 2, across a blank line -- both expressible.

        The stack carries across the blank line, which is why the second run
        measures as depth 2 rather than starting over at 1.
        """
        src = "  outer\n\n    inner"
        stored, md, _ = _round_trip(src)
        self.assertEqual(md, "> outer\n\n> > inner")
        self.assertEqual(stored, src)

    def test_a_depth_change_inside_one_run_takes_the_fallback(self):
        """Measured: the write leg cannot reproduce it, so it is not canonical.

        ``> a`` / ``> > b`` on consecutive lines comes back as
        ``"  a\\n  \\n    b"`` -- mistune reads the depth change as a nested
        blockquote whose sibling paragraph contributes a blank line, and the
        blank line is quoted too.  The fallback keeps the bytes exact instead.
        """
        src = "  outer\n    inner"
        stored, _, warnings = _round_trip(src)
        self.assertEqual(stored, src)
        self.assertTrue([w for w in warnings if INDENT_WARNING in w])


class TestRenderedContentType(unittest.TestCase):
    """Ticket #73 section 5: assert the four-space row on the rendered HTML.

    The bytes understate this row.  What the converter used to store was not a
    quote with the wrong depth -- it was a *code block*, which renders
    monospaced and stops interpreting markup.  Both renders below come from the
    live Trac renderer, so this compares two real content types rather than two
    strings someone believed in.
    """

    SOURCE = "    Quoted prose with *markup*."

    def test_source_renders_as_a_blockquote_not_a_code_block(self):
        """The pinned meaning of the source, from the real renderer."""
        tracwiki, html = _fixture_html("four_space_indent")
        self.assertEqual(tracwiki, self.SOURCE)
        self.assertIn("<blockquote>", html)
        self.assertNotIn("<pre", html)

    def test_the_old_output_rendered_as_a_different_content_type(self):
        """What the defect stored, from the same renderer -- the contrast."""
        _, html = _fixture_html("code_block_corruption")
        self.assertIn("<pre", html)
        self.assertNotIn("<blockquote>", html)

    def test_round_trip_reproduces_the_bytes_that_render_as_a_blockquote(
        self,
    ):
        """Byte-identical to the pinned source, so it renders as that HTML."""
        pinned, _ = _fixture_html("four_space_indent")
        stored, _, _ = _round_trip(self.SOURCE)
        self.assertEqual(stored, pinned)
        corrupt_source, _ = _fixture_html("code_block_corruption")
        self.assertNotEqual(stored, corrupt_source)


class TestListsAreNotQuotes(unittest.TestCase):
    """The recall gate most at risk from this fix.

    Measured on the live renderer: `` * a``, ``  * a``, ``   * a``, `` 1. a``
    and ``  1. a`` all render as a plain ``<ul>``/``<ol>`` with **no**
    blockquote wrapper.  A list line's indent is consumed by the list, so the
    quote scanner must exclude these rather than merely happen to miss them.
    """

    LISTS = [
        ("bullet, one space", " * a"),
        ("bullet, two spaces", "  * a"),
        ("bullet, three spaces", "   * a"),
        ("dash bullet", " - a"),
        ("nested bullet", " * a\n   * b"),
        ("numbered, one space", " 1. a"),
        ("numbered, two spaces", "  1. a"),
        ("lettered", " a. a"),
        ("roman", " iv. a"),
        ("list of several items", " * a\n * b\n * c"),
        ("multi-line item", " * item\n   continuation"),
    ]

    # The rows the ticket names, at the width the write leg emits.  Three
    # separate pre-existing defects keep the others off this list, all of them
    # verified byte-identical to ``master`` and none of them touched by this
    # ticket -- they are indent *width*, not blockquote depth:
    #
    # * ``  * a`` and ``   * a`` normalise to `` * a`` in the write leg.
    #   Render-neutral: Trac renders every width as the same bare <ul>.
    # * `` - a`` normalises to `` * a``, the write leg's one bullet character.
    # * `` a. a`` / `` iv. a`` lost the indent entirely and came back as
    #   paragraphs -- Markdown has no lettered or roman list to carry them.
    #   That one was NOT render-neutral, and it got the ticket this comment
    #   asked for: #74, fixed with ticket #63's verbatim fallback.  Both rows
    #   now round-trip byte-for-byte, which is why they are absent from
    #   NO_FENCE below and asserted in test_converter_lettered_lists.py
    #   instead.
    ROUND_TRIPPING = [
        " * a",
        " 1. a",
        " * a\n   * b",
        " * a\n * b\n * c",
    ]

    # Every row EXCEPT the lettered and roman ones, which ticket #74 carries
    # verbatim on purpose.  Kept as a subset of LISTS rather than as its own
    # literal list, so a row added above cannot silently escape this gate.
    NO_FENCE = [
        row for row in LISTS if row[0] not in ("lettered", "roman")
    ]

    def test_lists_round_trip_unchanged(self):
        for src in self.ROUND_TRIPPING:
            with self.subTest(src):
                stored, _, _ = _round_trip(src)
                self.assertEqual(stored, src)

    def test_lists_do_not_warn_as_indented_blocks(self):
        for label, src in self.LISTS:
            with self.subTest(label):
                _, _, warnings = _round_trip(src)
                self.assertFalse(
                    [w for w in warnings if INDENT_WARNING in w]
                )

    def test_lists_are_not_quoted_in_the_markdown(self):
        """The regression a naive indent-to-quote rule would cause."""
        for label, src in self.LISTS:
            with self.subTest(label):
                _, md, _ = _round_trip(src)
                self.assertNotIn(">", md)

    def test_no_list_is_buried_in_a_verbatim_fence(self):
        """The other way a quote scanner could damage a list.

        Stated separately from the width assertions because it is the one
        that must hold for every list Markdown can actually express.

        It used to run over *every* row, on the reasoning that a fence is
        always damage.  Ticket #74 measured otherwise for the two rows
        Markdown cannot express at all: `` a. a`` and `` iv. a`` reached the
        Markdown as prose, so the fence is what restores them rather than
        what buries them.  Those two rows are therefore excluded here and
        asserted the other way round -- fenced, verbatim, and warning -- in
        test_converter_lettered_lists.py.  The exclusion is deliberately
        narrow: it names the two rows, so a quote scanner that started
        fencing `` * a`` would still fail here.
        """
        for label, src in self.NO_FENCE:
            with self.subTest(label):
                stored, md, _ = _round_trip(src)
                self.assertNotIn("```", md)
                self.assertNotIn("{{{", stored)


class TestOtherIndentConsumingConstructs(unittest.TestCase):
    """Constructs whose indent belongs to them, not to a blockquote.

    All measured on the live renderer while planning this ticket.
    """

    def test_definition_list_is_not_quoted(self):
        """`` term:: def`` renders as a bare <dl>, no blockquote, at any indent."""
        _, md, warnings = _round_trip(" term:: def")
        self.assertNotIn(">", md.replace("**", ""))
        self.assertFalse([w for w in warnings if INDENT_WARNING in w])

    def test_indented_heading_stays_a_heading(self):
        """`` = H =`` renders as <h1>; the indent is consumed, not quoted.

        The indent is dropped rather than preserved, and that is correct here
        rather than a residue of the defect: Trac renders `` = H =`` and
        ``= H =`` identically, so there is no quoting to lose.  What matters
        is that the quote scanner does not claim the line -- a fallback here
        would bury a heading in a verbatim fence.
        """
        stored, _, warnings = _round_trip(" = Indented heading =")
        self.assertEqual(stored, "= Indented heading =")
        self.assertFalse([w for w in warnings if INDENT_WARNING in w])

    def test_indented_rule_is_literal_text_in_a_quote(self):
        """Trac renders `` ----`` as a blockquote containing "----"."""
        stored, _, _ = _round_trip(" ----")
        self.assertEqual(stored, " ----")


class TestIndentedCodeBlockIsUntouched(unittest.TestCase):
    """An indent inside a ``{{{ }}}`` block is content, not a quote.

    The fallback passes run before the code-block stashing, so the quote
    scanner is one of the passes that has to do its own shielding
    (``_verbatim_mask``).  Without it this reopens tickets #45/#46/#51.
    """

    def test_indent_inside_a_code_block_survives(self):
        src = "{{{\n    indented code\n}}}"
        stored, _, _ = _round_trip(src)
        self.assertEqual(stored, src)

    def test_indent_inside_a_processor_block_survives(self):
        src = "{{{#!python\ndef f():\n    return 1\n}}}"
        stored, _, _ = _round_trip(src)
        self.assertEqual(stored, src)


if __name__ == "__main__":
    unittest.main()
