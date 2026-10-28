"""Lettered and roman ordered-list pin for ticket #74.

Trac's ordered-list marker is not only ``1.``.  Measured against Trac's own
renderer -- ``convert_preview`` with ``format="tracwiki"`` on ``/trac_test``,
reading the returned HTML:

=================  ===============================
source             Trac renders
=================  ===============================
`` 1. x``          ``<ol>``
`` a. x``          ``<ol class="loweralpha">``
`` A. x``          ``<ol class="upperalpha">``
`` i. x``          ``<ol class="lowerroman">``
`` I. x``          ``<ol class="upperroman">``
=================  ===============================

**Markdown has none of these.**  CommonMark's ordered-list marker is digits
only, so there is nothing for ``a.`` or ``iv.`` to convert *to*.  Before this
ticket the read leg did not convert them and did not warn either: the line
passed through untouched and the Markdown consumer got a paragraph.  The list
survived the round trip only because neither leg touched it, and the stored
bytes still lost their leading space on the way back.

That "Markdown has none of these" is asserted here against the write leg's own
parser rather than quoted from the spec -- see ``TestMarkdownCannotExpressIt``.

**The damage is on the Markdown leg, which is the exposed one**:
``wiki_file_pull`` hands a caller a file in which a lettered list has become
prose, and any edit-and-push cycle then reasons about prose.  So the
assertions below are on the intermediate Markdown as well as on the stored
bytes, per ticket #74 section 5 -- the bytes alone nearly pass before the fix,
and a bytes-only pin would measure the wrong leg.

The fix is ticket #63's verbatim fallback, the same one ticket #73 took for
indent widths: emit the run unchanged in a ``tracwiki-unconverted`` fence,
which is byte-exact both ways and, the actual point, **warns**.

The alternative -- converting to a numbered Markdown list -- is rejected
explicitly and pinned as rejected by ``test_numbering_is_never_rewritten``: it
would silently renumber ``a./b./c.`` as ``1./2./3.``, changing what the page
says, and the write leg could not tell the difference on the way back.

Scope, decided with the operator on ticket #74 comment 2 and measured rather
than assumed: **column zero is a follow-up**, ticket #89.  ``a. first`` at
column zero is an ``<ol class="loweralpha">`` to Trac as well, so the read leg
loses that list too, but a sweep of every wiki page, ticket description and
ticket comment on both stores (1020 documents carrying a stored source) found
22 marker lines in 4 documents, **all of them indented and none at column
zero** -- and with this fix the indented form stays indented, so the converter
no longer manufactures the column-zero form it used to emit.
"""

import unittest

import mistune

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    _ALPHA_LIST_MARKER_RE,
    _LIST_MARKER_RE,
    FALLBACK_FENCE_INFO,
    tracwiki_to_markdown,
)

FENCE = f"```{FALLBACK_FENCE_INFO}"

# The warning ticket #73 raises for an unrepresentable indent width.  Named
# here because ticket #74's runs must NOT be reported as indent widths: they
# are a construct with no Markdown equivalent, not a quote at an awkward
# width, and TestListsAreNotQuotes in test_converter_blockquote_indent.py
# still asserts that no list ever draws this one.
INDENT_WARNING = "Indented block"


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


class TestLetteredListSeeds(unittest.TestCase):
    """The seeds: ticket #74 section 2's five non-numeric rows.

    All five were watched failing first, per
    ``Rules/testing/SeededDefectFirst``.  Before the fix every row came back
    from a full ``tw -> md -> tw`` cycle stripped of its leading space, with
    the list gone from the intermediate Markdown, and with an **empty**
    warning list -- the silent shape ticket #68 is about.

    The warning is asserted separately from the bytes rather than as a detail
    of one combined check, because the bytes half very nearly passed before
    the fix (only the leading space was lost) while the warning half failed
    outright on all five.  A combined assertion would have hidden which half
    the fix actually repaired.
    """

    SEEDS = [
        ("lower alpha", " a. first\n b. second"),
        ("upper alpha", " A. first\n B. second"),
        ("lower roman", " i. first\n ii. second"),
        ("upper roman", " I. first\n II. second"),
        ("single item", " a. only"),
    ]

    def test_stored_bytes_round_trip_byte_for_byte(self):
        for label, src in self.SEEDS:
            with self.subTest(label):
                stored, _, _ = _round_trip(src)
                self.assertEqual(stored, src)

    def test_the_indent_is_not_stripped(self):
        """Stated on its own because it is the whole of the bytes defect.

        Every row lost exactly its leading space before the fix, which is
        render-neutral -- Trac accepts a list at column zero -- and therefore
        the half of this ticket that a bytes-only pin would have called
        nearly green.
        """
        for label, src in self.SEEDS:
            with self.subTest(label):
                stored, _, _ = _round_trip(src)
                for line in stored.split("\n"):
                    self.assertTrue(
                        line.startswith(" "),
                        f"{label}: {line!r} came back at column zero",
                    )

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

    def test_the_warning_does_not_call_it_an_indent_width(self):
        """It is an unrepresentable construct, not an awkward indent.

        The two take the same fallback, so only the warning text tells a
        caller which one it is holding.
        """
        for label, src in self.SEEDS:
            with self.subTest(label):
                _, _, warnings = _round_trip(src)
                self.assertFalse(
                    [w for w in warnings if INDENT_WARNING in w]
                )

    def test_the_markdown_carries_the_run_verbatim(self):
        """The leg the list is actually lost on, per section 5.

        Asserting the fence is present is not enough on its own -- the body
        has to be the source text unchanged, because that is what makes the
        way back byte-exact and what a caller reads to see the list is there.
        """
        for label, src in self.SEEDS:
            with self.subTest(label):
                _, md, _ = _round_trip(src)
                self.assertIn(FENCE, md)
                self.assertIn(src, md)


class TestMarkdownCannotExpressIt(unittest.TestCase):
    """Why the fallback, rather than a numbered Markdown list.

    Measured against ``mistune`` -- the parser the write leg itself uses --
    rather than argued from the CommonMark spec, so the claim is checked
    against the thing that will actually read the output.
    """

    def test_a_lettered_list_is_not_a_list_in_markdown(self):
        self.assertEqual(
            _block_types(" a. first\n b. second"), ["paragraph"]
        )

    def test_the_same_input_numbered_is_a_list(self):
        """The control that makes the row above mean something."""
        self.assertEqual(
            _block_types(" 1. first\n 2. second"), ["list"]
        )

    def test_numbering_is_never_rewritten(self):
        """The rejected alternative, pinned as rejected.

        Renumbering ``a./b./c.`` to ``1./2./3.`` would change what the page
        says, and the write leg could not tell the rewritten list from one
        that was numbered all along -- so the damage would be permanent after
        one edit-and-push cycle.
        """
        stored, md, _ = _round_trip(" a. first\n b. second\n c. third")
        for marker in ("a.", "b.", "c."):
            self.assertIn(marker, md)
            self.assertIn(marker, stored)
        self.assertNotIn("1.", md)
        self.assertNotIn("1.", stored)


class TestConvertibleListsAreUntouched(unittest.TestCase):
    """The recall gate: what must keep converting, from section 5.

    A fallback that swallowed these would trade a silent loss for a loud one
    and still hand the caller prose-in-a-fence where a real Markdown list was
    both possible and already working.
    """

    CONVERTIBLE = [
        ("numbered", " 1. first\n 2. second"),
        ("bullet", " * x"),
        ("dash bullet", " - x"),
        ("nested bullet", " * a\n   * b"),
        ("several items", " * a\n * b\n * c"),
    ]

    def test_no_fence_and_no_warning(self):
        for label, src in self.CONVERTIBLE:
            with self.subTest(label):
                _, md, warnings = _round_trip(src)
                self.assertNotIn("```", md)
                self.assertEqual(warnings, [])

    def test_the_markdown_is_a_real_list(self):
        """Asserted through mistune, not by looking for a marker character.

        ``- x`` appearing in the output does not prove a list; being parsed
        as one does.
        """
        for label, src in self.CONVERTIBLE:
            with self.subTest(label):
                _, md, _ = _round_trip(src)
                self.assertEqual(_block_types(md), ["list"])

    def test_they_still_round_trip(self):
        """Indent width and bullet character are tickets #76 and #75.

        Only the rows whose bytes already survived on ``master`` are asserted
        here, so this file measures its own ticket rather than inheriting a
        neighbouring defect.
        """
        for src in (
            " 1. first\n 2. second",
            " * x",
            " * a\n   * b",
            " * a\n * b\n * c",
        ):
            with self.subTest(src):
                stored, _, _ = _round_trip(src)
                self.assertEqual(stored, src)


class TestMarkerGrammarDoesNotDrift(unittest.TestCase):
    """One definition of the marker set, read from two places.

    Ticket #73's quote scanner has to know every list marker so it does not
    claim a list line's indent as a blockquote; this ticket needs the
    lettered and roman subset of exactly that set.  Both patterns are built
    from the same fragments, so the subset relation below holds by
    construction -- this class is what fails if someone widens one of them in
    place instead.

    The marker set is exact rather than generous, and that is load-bearing in
    both directions: measured on the live renderer, `` Hello. World`` and
    `` xyz. not roman`` are **blockquotes** to Trac, not lettered lists, so
    "any word followed by a period" would have swallowed real quotes here and
    skipped them there.
    """

    ALPHA = [" a. x", " A. x", " i. x", " I. x", " iv. x", " XIV. x"]
    OTHER_MARKERS = [" * x", " - x", " 1. x", " 42. x"]
    NOT_MARKERS = [
        " Hello. World",
        " xyz. not roman",
        " 1) x",
        " a.x",
        "a. x",
    ]

    def test_every_alpha_marker_is_also_a_list_marker(self):
        for line in self.ALPHA:
            with self.subTest(line):
                self.assertTrue(_ALPHA_LIST_MARKER_RE.match(line))
                self.assertTrue(_LIST_MARKER_RE.match(line))

    def test_bullets_and_numbers_are_list_markers_but_not_alpha(self):
        for line in self.OTHER_MARKERS:
            with self.subTest(line):
                self.assertTrue(_LIST_MARKER_RE.match(line))
                self.assertFalse(_ALPHA_LIST_MARKER_RE.match(line))

    def test_neither_pattern_claims_a_non_marker(self):
        for line in self.NOT_MARKERS:
            with self.subTest(line):
                self.assertFalse(_LIST_MARKER_RE.match(line))
                self.assertFalse(_ALPHA_LIST_MARKER_RE.match(line))

    def test_prose_that_looks_like_a_marker_is_still_quoted(self):
        """The recall cost of a generous grammar, measured end to end.

        `` Hello. World`` is a blockquote at one space, so it takes ticket
        #73's indent fallback -- not this ticket's list fallback, and not
        nothing.
        """
        _, _, warnings = _round_trip(" Hello. World")
        self.assertTrue([w for w in warnings if INDENT_WARNING in w])
        self.assertFalse(
            [w for w in warnings if "ordered list" in w.lower()]
        )


class TestQuotedLetteredListIsContent(unittest.TestCase):
    """A lettered list someone *quoted* is source text, not a construct.

    The same seeded defect ``TestWarningIsShielded`` pins for definition
    lists, and it fails the same way: a check that scans raw text announces a
    construct inside quoted source.  It holds here because the fallback runs
    against ``_verbatim_mask`` (tickets #45, #46, #51) rather than because
    anything was added for it -- which is exactly why it is asserted.
    """

    QUOTED = [
        ("plain code block", "{{{\n a. not a list here\n}}}"),
        (
            "processor block",
            "{{{#!python\n a. not a list here\n}}}",
        ),
        ("code span", "A ` a. quoted` span."),
    ]

    def test_quoted_lettered_list_does_not_warn(self):
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

    def test_quoted_lettered_list_round_trips(self):
        for label, src in self.QUOTED:
            with self.subTest(label):
                stored, _, _ = _round_trip(src)
                self.assertEqual(stored, src)


class TestRunContainingBothKinds(unittest.TestCase):
    """A numbered list with a lettered item nested inside it.

    This is Trac's own ``WikiFormatting`` page, and 8 of the 22 marker lines
    the store sweep found are this shape.  Trac renders it as an ``<ol>``
    containing a nested ``<ol class="loweralpha">``.

    The whole run falls back, which is the operator decision recorded on
    ticket #63 for a run holding two constructs, applied unchanged: the run
    is the unit, splitting it at the construct boundary needs a real block
    parser, and skipping it would put the lettered items back in prose.

    The cost is recorded rather than hidden -- a numbered list Markdown could
    have expressed is carried verbatim too.  It still beats the behaviour it
    replaces, where the nested item lost its indent silently and so came back
    a sibling of the outer list rather than a child of it.
    """

    SOURCE = " 1. Item 1\n   a. Item 1.a\n 2. Item 2"

    def test_the_whole_run_round_trips(self):
        stored, _, _ = _round_trip(self.SOURCE)
        self.assertEqual(stored, self.SOURCE)

    def test_the_nesting_survives(self):
        """The assertion that it is still the same list afterwards.

        Not merely that some bytes came back: the lettered item has to return
        indented **deeper** than the numbered items around it, because that
        relative indent is the whole of what makes it a nested list to Trac.
        """
        stored, _, _ = _round_trip(self.SOURCE)
        lines = stored.split("\n")
        self.assertGreater(
            len(lines[1]) - len(lines[1].lstrip()),
            len(lines[0]) - len(lines[0].lstrip()),
        )

    def test_it_warns_rather_than_degrading_silently(self):
        _, _, warnings = _round_trip(self.SOURCE)
        self.assertTrue(
            [w for w in warnings if "ordered list" in w.lower()]
        )


if __name__ == "__main__":
    unittest.main()
