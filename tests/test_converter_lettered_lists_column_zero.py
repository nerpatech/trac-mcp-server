"""Lettered and roman ordered-list pin for ticket #89.

#74 fixed the *indented* form of this list (`` a. first``); this is the
identical defect one column to the left, deliberately left out of #74 and
split into its own ticket -- see ``test_converter_lettered_lists.py``'s
module docstring for the exposure measurement that put it there.

**The grammar is different from the indented form, not just narrower.**
Measured against Trac's own renderer, reading the HTML:

=================================  =================================
source                             Trac renders
=================================  =================================
``a. first`` / ``b. second``       ``<ol class="loweralpha">``
``I. am here``                     ``<ol class="upperroman">``
``some prose`` then ``a. first``   ``<p>`` then a separate ``<ol>``
``A.`` / continuation / ``B.``     ``<ol>``, ``<p>``, ``<ol start="2">``
=================================  =================================

So a marker line at column zero opens a list even in the middle of a
paragraph, and the run it takes is the *maximal set of consecutive marker
lines* -- a column-zero line that is not itself a marker ends the list
rather than being absorbed into it, unlike the indented form where a
following indented line joins the item.  That is why the fix is a new
top-level pass (``_fallback_column_zero_lists``) rather than a branch
inside ``_convert_indented_blocks``.

The fix is the same #63 verbatim fallback #74 used: emit the run unchanged
in a ``tracwiki-unconverted`` fence, byte-exact both ways, and **warn**.
Renumbering as ``1./2./3.`` is rejected for the same reason #74 rejected it
-- pinned there, not repeated here.

Unlike #74, there is no bytes-side damage on `master` before this fix: a
column-zero marker line already passed straight through untouched (no
leading space to lose), so a bytes-only round-trip assertion would already
have been green.  The whole defect is that the *intermediate Markdown*
carried a paragraph instead of a list, which is what the assertions below
target, per ``Rules/testing/SeededDefectFirst``.
"""

import unittest

import mistune

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    _ALPHA_LIST_MARKER_COL0_RE,
    _ALPHA_MARKER,
    FALLBACK_FENCE_INFO,
    tracwiki_to_markdown,
)

FENCE = f"```{FALLBACK_FENCE_INFO}"


def _round_trip(source):
    """Return (stored bytes after tw -> md -> tw, intermediate md, warnings)."""
    result = tracwiki_to_markdown(source)
    return (
        markdown_to_tracwiki(result.text),
        result.text,
        result.warnings,
    )


def _block_types(markdown):
    """Return the top-level token types mistune reads out of ``markdown``.

    ``renderer=None`` puts mistune in AST mode, so this reads what the write
    leg's own parser makes of the text rather than what the text looks like.
    """
    ast = mistune.create_markdown(renderer=None)(markdown)
    return [token["type"] for token in ast]  # type: ignore[union-attr,index]


class TestColumnZeroLetteredListSeeds(unittest.TestCase):
    """The seeds: ticket #89 section 5, each at column zero."""

    SEEDS = [
        ("lower alpha", "a. first\nb. second"),
        ("upper alpha", "A. first\nB. second"),
        ("lower roman", "i. first\nii. second"),
        ("upper roman", "I. first\nII. second"),
        ("single item", "a. only"),
    ]

    def test_stored_bytes_round_trip_byte_for_byte(self):
        """A standalone run round-trips exactly -- no adjoining paragraph
        to trigger the pre-existing fence/paragraph blank-line spacing
        that ``markdown_to_tracwiki`` applies around any fenced block."""
        for label, src in self.SEEDS:
            with self.subTest(label):
                stored, _, _ = _round_trip(src)
                self.assertEqual(stored, src)

    def test_every_seed_warns(self):
        for label, src in self.SEEDS:
            with self.subTest(label):
                _, _, warnings = _round_trip(src)
                self.assertTrue(
                    [
                        w
                        for w in warnings
                        if "ordered list" in w.lower()
                    ],
                    f"{label}: converted silently, warnings={warnings}",
                )

    def test_the_markdown_carries_the_run_verbatim(self):
        for label, src in self.SEEDS:
            with self.subTest(label):
                _, md, _ = _round_trip(src)
                self.assertIn(FENCE, md)
                self.assertIn(src, md)

    def test_a_lettered_list_is_not_a_list_in_markdown(self):
        """Why the fallback is needed at all -- the same check
        ``TestMarkdownCannotExpressIt`` runs for the indented form,
        measured against mistune rather than assumed."""
        self.assertEqual(
            _block_types("a. first\nb. second"), ["paragraph"]
        )


class TestConvertibleListsAreUntouchedAtColumnZero(unittest.TestCase):
    """The recall gate, section 5: what must keep converting.

    ``1. x`` and ``* x`` at column zero are already valid Markdown as
    written -- CommonMark's numbered marker is digits, and ``*`` is a valid
    Markdown bullet too -- so the read leg needs no new handling for them,
    and this pass must not start claiming them.
    """

    CONVERTIBLE = [
        ("numbered", "1. first\n2. second"),
        ("bullet", "* x"),
        ("dash bullet", "- x"),
    ]

    def test_no_fence_and_no_warning(self):
        for label, src in self.CONVERTIBLE:
            with self.subTest(label):
                _, md, warnings = _round_trip(src)
                self.assertNotIn(FENCE, md)
                self.assertEqual(warnings, [])

    def test_the_markdown_is_a_real_list(self):
        for label, src in self.CONVERTIBLE:
            with self.subTest(label):
                _, md, _ = _round_trip(src)
                self.assertEqual(_block_types(md), ["list"])


class TestProseIsUntouched(unittest.TestCase):
    """A paragraph merely containing a marker-shaped word is not a list.

    Measured against the live renderer (see the module docstrings on both
    lettered-list test files): at column zero these ARE prose to Trac, not
    just to a generous human reading -- the marker grammar excludes them the
    same way ``_LIST_MARKER_RE`` excludes their indented equivalents.
    """

    NOT_MARKERS = [
        "Hello. World",
        "xyz. not roman",
        "a.x",
        "A sentence with a. mid-line is untouched.",
    ]

    def test_no_fence_and_no_warning(self):
        for src in self.NOT_MARKERS:
            with self.subTest(src):
                _, md, warnings = _round_trip(src)
                self.assertNotIn(FENCE, md)
                self.assertFalse(
                    [w for w in warnings if "ordered list" in w.lower()]
                )

    def test_stored_bytes_are_unchanged(self):
        for src in self.NOT_MARKERS:
            with self.subTest(src):
                stored, _, _ = _round_trip(src)
                self.assertEqual(stored, src)

    def test_1_paren_x_is_not_claimed_by_this_pass(self):
        """``1) x`` is excluded by the marker grammar (not digits-with-dot,
        see ``TestMarkerGrammarDoesNotDriftAtColumnZero``), so the read leg
        never fences or warns on it.  Its bytes do NOT round-trip, but for
        a reason unrelated to this ticket: mistune reads ``)`` as a valid
        Markdown ordered-list delimiter too, and ``markdown_to_tracwiki``
        re-renders any column-zero numbered list indented -- the same
        pre-existing quirk ``1. x`` has (see
        ``TestConvertibleListsAreUntouchedAtColumnZero``), not a fence or a
        warning this pass introduced.
        """
        _, md, warnings = _round_trip("1) x")
        self.assertNotIn(FENCE, md)
        self.assertFalse(
            [w for w in warnings if "ordered list" in w.lower()]
        )


class TestAColumnZeroContinuationLineEndsTheList(unittest.TestCase):
    """Grammar-table row 4: the defining difference from the indented form.

    ``A. first`` / a continuation line / ``B. second`` is TWO separate
    lists to Trac (``<ol>``, ``<p>``, ``<ol start="2">``), not one list
    whose second item absorbed a continuation line the way an indented
    item would.  So the fallback must fire twice, with the continuation
    line passed through untouched in between -- fencing the whole three
    lines as one run would silently change what the page says on the way
    back (the continuation line would come back INSIDE the fenced verbatim
    body rather than as its own paragraph).
    """

    SOURCE = "A. first\ncontinuation line\nB. second"

    def test_two_separate_fenced_runs(self):
        _, md, _ = _round_trip(self.SOURCE)
        # One FENCE (opening delimiter + info string) per run.
        self.assertEqual(md.count(FENCE), 2)
        self.assertIn("A. first", md)
        self.assertIn("B. second", md)

    def test_the_continuation_line_sits_between_the_fences_unfenced(
        self,
    ):
        """Not absorbed into either fenced body -- its own bare paragraph,
        bounded by the first run's closing fence and the second run's
        opening fence."""
        _, md, _ = _round_trip(self.SOURCE)
        self.assertIn("```\ncontinuation line\n" + FENCE, md)

    def test_each_run_warns(self):
        _, _, warnings = _round_trip(self.SOURCE)
        matches = [w for w in warnings if "ordered list" in w.lower()]
        self.assertEqual(len(matches), 2)


class TestAMarkerLineOpensAListMidParagraph(unittest.TestCase):
    """Grammar-table row 3: prose above and below a run is untouched.

    The run itself round-trips byte-exact and warns; the pre-existing
    fence/paragraph spacing ``markdown_to_tracwiki`` applies around any
    fenced block (also present for #74's indented form -- not a defect
    this ticket introduces or is asked to fix) means the SURROUNDING prose
    is not asserted byte-exact here.
    """

    SOURCE = "some prose\na. first\nb. second\nmore prose"

    def test_the_list_run_is_fenced_and_warns(self):
        _, md, warnings = _round_trip(self.SOURCE)
        self.assertIn(FENCE, md)
        self.assertIn("a. first\nb. second", md)
        self.assertTrue(
            [w for w in warnings if "ordered list" in w.lower()]
        )

    def test_the_prose_survives_untouched(self):
        _, md, _ = _round_trip(self.SOURCE)
        self.assertIn("some prose", md)
        self.assertIn("more prose", md)


class TestQuotedColumnZeroListIsContent(unittest.TestCase):
    """A column-zero lettered list someone *quoted* is source text.

    Mirrors ``TestQuotedLetteredListIsContent`` in the indented-list test
    file -- holds here because the fallback runs against
    ``_verbatim_mask`` (tickets #45, #46, #51) rather than because
    anything new was added for it.
    """

    QUOTED = [
        ("plain code block", "{{{\na. not a list here\n}}}"),
        (
            "processor block",
            "{{{#!python\na. not a list here\n}}}",
        ),
        ("code span", "A ` a. quoted` span."),
    ]

    def test_quoted_list_does_not_warn(self):
        for label, src in self.QUOTED:
            with self.subTest(label):
                warnings = tracwiki_to_markdown(src).warnings
                self.assertFalse(
                    [
                        w
                        for w in warnings
                        if "ordered list" in w.lower()
                    ],
                    f"{label}: warned about quoted source: {warnings}",
                )

    def test_quoted_list_round_trips(self):
        for label, src in self.QUOTED:
            with self.subTest(label):
                stored, _, _ = _round_trip(src)
                self.assertEqual(stored, src)


class TestMarkerGrammarDoesNotDriftAtColumnZero(unittest.TestCase):
    """One definition of the alpha/roman marker, read from a third place.

    ``test_converter_lettered_lists.py`` already pins that
    ``_ALPHA_LIST_MARKER_RE`` (indented) is built from ``_ALPHA_MARKER``.
    This is the column-zero sibling of that same no-drift assertion.
    """

    def test_col0_pattern_is_built_from_the_shared_fragment(self):
        self.assertEqual(
            _ALPHA_LIST_MARKER_COL0_RE.pattern,
            rf"^{_ALPHA_MARKER}(?=[ \t])",
        )

    def test_every_alpha_marker_matches_at_column_zero(self):
        for line in ["a. x", "A. x", "i. x", "I. x", "iv. x", "XIV. x"]:
            with self.subTest(line):
                self.assertTrue(_ALPHA_LIST_MARKER_COL0_RE.match(line))

    def test_numeric_and_bullet_markers_do_not_match(self):
        for line in ["1. x", "42. x", "* x", "- x"]:
            with self.subTest(line):
                self.assertFalse(_ALPHA_LIST_MARKER_COL0_RE.match(line))

    def test_prose_that_looks_like_a_marker_does_not_match(self):
        for line in ["Hello. World", "xyz. not roman", "1) x", "a.x"]:
            with self.subTest(line):
                self.assertFalse(_ALPHA_LIST_MARKER_COL0_RE.match(line))


if __name__ == "__main__":
    unittest.main()
