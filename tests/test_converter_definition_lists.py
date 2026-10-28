"""Definition-list detector pin for ticket #71.

The detector in ``_convert_other_elements`` matched
``^(\\s*)(.+?)::\\s*(.+)$``, which claimed *any* line containing a double colon
with a non-empty remainder.  Ordinary prose was therefore rewritten as a bold
term plus a colon, and a full read-edit-write cycle stored that damage
permanently.

The previous commit pinned that damage as a characterisation baseline; this
file now asserts the fixed behaviour, the rows updated deliberately as that
commit's docstring required.  The baseline is what the fix was diffed against,
rather than against memory -- the shape ticket #63's inventory class
established.

**The grammar was measured against Trac's own renderer**, not derived from the
source or from the ticket's remediation sketch -- ``convert_preview`` with
``format="tracwiki"`` on ``/trac_test``, reading the returned HTML for a
``<dl class="wiki">``.  That measurement corrected two of the three constraints
ticket #71 section 4 proposed, so it is recorded here rather than left in a
comment thread:

===========================================  ==========================
source (leading space significant)           Trac renders
===========================================  ==========================
``Use std::vector here.``                    paragraph
``term:: definition``                        paragraph -- *not* a ``<dl>``
`` term:: definition``                       ``<dl>``
`` multi word term:: a definition``          ``<dl>`` -- multi-word terms
                                             are legal
`` Use std::vector here.``                   blockquote
`` A ratio of 3::1 was used.``               blockquote
`` term::definition``                        blockquote -- no space after
`` std::vector:: a C++ type``                blockquote -- Trac stops at
                                             the *first* colon pair
`` trailing colons only::``                  ``<dl>``, empty definition
`` term::`` + 2-space continuation           ``<dl>``
``term::`` + 2-space continuation            paragraph + blockquote
===========================================  ==========================

So Trac's rule is three parts:

1. **Leading whitespace is required.**  At column zero a ``term:: definition``
   line is prose.  Anchoring the term to the start of the line -- section 4's
   first suggestion -- would keep matching exactly the prose Trac treats as
   prose.
2. **The colons must be followed by whitespace or end-of-line.**  This is what
   excludes ``std::vector`` and ``3::1``.
3. **The term runs to the first colon pair.**  `` std::vector:: a C++ type``
   is a blockquote, not a definition of *vector*.

Section 4's other suggestion -- forbid whitespace inside the term -- is wrong:
`` multi word term:: a definition`` is a genuine definition list, and
implementing it would have traded a false-positive class for a false-negative
one.

Note what this pin does **not** cover.  A genuine definition list converts to
bold and so does not round-trip even once the detector is exact; that is ticket
#63's verbatim-fallback remainder, deliberately left there (operator decision)
so the two seeded-defect gates stay separable -- this one guards a check that
fires *too often*, #63's guards one that fires *too rarely*.

Per ticket #71 section 5 every assertion here is on the **stored bytes after a
full tw -> md -> tw cycle**, never on the warning list alone -- the warning is
itself part of the defect, so a pin written against it would be measuring the
thing under test with itself.
"""

import unittest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    tracwiki_to_markdown,
)

# The fallback's warning, raised by ``_stash_fallback`` at the point it fires
# (ticket #63).  Deliberately matched on the new wording rather than on a
# prefix both spellings share: the old ``"Definition lists detected"`` warning
# came from ``_detect_lossy_elements``, which scanned the **raw** text with no
# shielding and so announced a definition list inside quoted source.  A
# constant matching both would have let that false positive pass as a hit.
DEFINITION_LIST_WARNING = "Definition list cannot be represented"


def _round_trip(source):
    """Return (stored bytes after tw -> md -> tw, definition-list warned?)."""
    result = tracwiki_to_markdown(source)
    warned = any(DEFINITION_LIST_WARNING in w for w in result.warnings)
    return markdown_to_tracwiki(result.text), warned


class TestProseWithDoubleColonNotConverted(unittest.TestCase):
    """The false positives: none of these is a definition list to Trac.

    Before this ticket every row was corrupted -- prose became bold markup in
    the stored bytes, permanently -- and the warning fired as well, spending
    the suite's credibility on content containing no definition list at all.
    Tickets #57 and #59 are this project's evidence that an over-firing check
    gets muted and takes its true positives with it.

    These rows were characterised as broken in the previous commit and are
    updated here deliberately, as that commit's docstring required.  Each
    ``expected`` is now the source text unchanged, and no row may warn.

    The indented rows lost their single leading space to a separate defect,
    independent of the double colon, which was pinned in
    ``TestSingleSpaceIndentNoLongerStripped`` rather than papered over with a
    lenient comparison here.  Ticket #73 fixed it, so those rows are now
    tightened to the source unchanged like the rest -- which is the payoff of
    having pinned it instead of loosening the comparison.
    """

    # (label, source, expected stored bytes after the round trip)
    NOT_A_DEFINITION_LIST = [
        (
            "C++ scope, column zero",
            "Use std::vector here.",
            "Use std::vector here.",
        ),
        (
            "realm-shaped token, column zero",
            "See Reference::Page for detail.",
            "See Reference::Page for detail.",
        ),
        (
            "ratio, column zero",
            "A ratio of 3::1 was used.",
            "A ratio of 3::1 was used.",
        ),
        # Term-shaped, but at column zero -- prose to Trac.
        (
            "term-shaped at column zero",
            "term:: definition at column zero",
            "term:: definition at column zero",
        ),
        # Indented rows.  Every one of these is a BLOCKQUOTE to Trac, and each
        # kept its `expected` shortened by one leading space until ticket #73
        # fixed the indent handling.  Tightened here deliberately, as this
        # ticket's section 6 said they would be once that landed: the indent is
        # now preserved, so `expected` is the source unchanged like every other
        # row in the table.
        (
            "C++ scope, indented",
            " Use std::vector here indented.",
            " Use std::vector here indented.",
        ),
        (
            "ratio, indented",
            " A ratio of 3::1 was used, indented.",
            " A ratio of 3::1 was used, indented.",
        ),
        (
            "no whitespace after the colons",
            " term::definition with no space",
            " term::definition with no space",
        ),
        # Trac stops at the FIRST colon pair, finds "v", and declines the line.
        # A plain non-greedy term would backtrack past it to the second pair,
        # which is why the pattern uses a tempered dot.
        (
            "first colon pair is not followed by whitespace",
            " std::vector:: a C++ type as the term",
            " std::vector:: a C++ type as the term",
        ),
    ]

    def test_prose_survives_the_round_trip(self):
        """The defect proper: prose must not be rewritten as bold."""
        for label, src, expected in self.NOT_A_DEFINITION_LIST:
            with self.subTest(label):
                stored, _ = _round_trip(src)
                self.assertEqual(stored, expected)

    def test_no_bold_markup_is_introduced(self):
        """Stated a second way, because it is the damage a reader would see."""
        for label, src, _ in self.NOT_A_DEFINITION_LIST:
            with self.subTest(label):
                stored, _ = _round_trip(src)
                self.assertNotIn("'''", stored)

    def test_no_definition_list_warning(self):
        """The aggravating half: the warning fired on these rows too."""
        for label, src, _ in self.NOT_A_DEFINITION_LIST:
            with self.subTest(label):
                _, warned = _round_trip(src)
                self.assertFalse(warned)


class TestGenuineDefinitionListFallsBack(unittest.TestCase):
    """The recall gate: a real definition list must still be recognised.

    **Renamed and re-pointed on ticket #63**, whose verbatim fallback this
    class's own docstring told the next reader to expect.  What "recognised"
    buys changed with it: these rows used to convert to bold and be warned
    about, and they now travel verbatim and round-trip byte-for-byte.  The
    ``expected`` values below are the source unchanged for the first time.

    The danger on this ticket is the inverse of the usual one -- the check
    fired too often, so tightening it risks a false negative.  These four rows
    are definition lists to Trac (measured, see the module docstring) and stay
    detected.

    Two were wrong before this ticket, in opposite directions, and the same
    tightening fixed both:

    * *empty definition* was **missed entirely** -- the old pattern required a
      non-empty remainder, so a real ``<dl>`` drew no warning and no
      conversion.  A false negative hiding underneath the false positives.
    * *definition on the next line* picked up a stray ``> `` from the
      blockquote pass, which ran first and claimed any line indented exactly
      two spaces.  The definition pass now runs first and absorbs its own
      continuation line, which is how Trac reads it -- as part of the ``<dd>``.

    ``expected`` was the lossy conversion to bold, and was explicitly *not*
    an endorsement -- none of these round-tripped, which is what made them
    ticket #63's fallback remainder.  That fallback has now landed, so the
    rows are updated deliberately here, exactly as that ticket's own inventory
    class was updated when the other eight constructs moved.

    Two extra rows join the four, both measured against Trac's own renderer
    and neither expressible before the fallback existed:

    * *two terms* -- consecutive term lines are **one** ``<dl>`` to Trac, so
      the fallback's unit has to be the run, not the line.
    * *absorbed same-indent line* -- a non-term line following a term is part
      of the preceding ``<dd>`` **even at the same indent**.  The deleted
      ``_convert_definition_lists`` absorbed a continuation only when the
      definition was empty *and* the line was indented deeper, so it
      disagreed with Trac on exactly this shape.  Carrying the run verbatim
      sidesteps the disagreement instead of having to fix it.
    """

    # (label, source, expected stored bytes -- now the source, unchanged)
    DEFINITION_LISTS = [
        (
            "single-word term",
            " term:: definition indented one space",
            " term:: definition indented one space",
        ),
        # Multi-word terms are legal: forbidding whitespace inside the term
        # would have turned this row into a false negative.
        (
            "multi-word term",
            " multi word term:: a definition",
            " multi word term:: a definition",
        ),
        (
            "empty definition",
            " trailing colons only::",
            " trailing colons only::",
        ),
        (
            "definition on the next line",
            " term::\n  the definition",
            " term::\n  the definition",
        ),
        (
            "two terms, one <dl>",
            " first:: one\n second:: two",
            " first:: one\n second:: two",
        ),
        (
            "absorbed same-indent line",
            " term:: def\n not a term, just a quote line",
            " term:: def\n not a term, just a quote line",
        ),
    ]

    def test_definition_list_is_detected(self):
        for label, src, _ in self.DEFINITION_LISTS:
            with self.subTest(label):
                _, warned = _round_trip(src)
                self.assertTrue(warned)

    def test_round_trips_byte_for_byte(self):
        """The headline: every one of these was lossy before ticket #63."""
        for label, src, expected in self.DEFINITION_LISTS:
            with self.subTest(label):
                stored, _ = _round_trip(src)
                self.assertEqual(stored, expected)

    def test_no_bold_is_introduced(self):
        """The conversion this fallback replaces, stated as its absence."""
        for label, src, _ in self.DEFINITION_LISTS:
            with self.subTest(label):
                stored, _ = _round_trip(src)
                self.assertNotIn("'''", stored)
                self.assertNotIn("**", tracwiki_to_markdown(src).text)

    def test_no_stray_blockquote_marker(self):
        """The second half of ticket #71, stated on its own.

        Asserted on the **intermediate Markdown** as well as the stored bytes,
        and that is deliberate.  Measured while seeding this gate: restoring
        the old pass order puts ``> the definition`` in the Markdown, but the
        write leg turns that blockquote back into a two-space indent, so the
        stored bytes come back as ``'''term''':\\n\\n  the definition`` -- no
        ``>`` anywhere.  A stored-bytes assertion alone therefore could *not*
        see the regression it exists to catch; it is the byte comparison in
        ``test_conversion_is_characterised`` that fails on that seed.  Both
        halves are kept so the marker is checked where it actually appears.
        """
        source = " term::\n  the definition"
        markdown = tracwiki_to_markdown(source).text
        self.assertNotIn(">", markdown)
        stored, _ = _round_trip(source)
        self.assertNotIn(">", stored)


class TestControl(unittest.TestCase):
    """Prose with no double colon: silent and unchanged, before and after.

    This row must not move.  It was the only assertion in the file already
    correct before the fix and still correct after it, which is what makes it
    the control rather than a characterisation.
    """

    def test_plain_prose_round_trips_silently(self):
        src = "Plain prose with no colons at all."
        stored, warned = _round_trip(src)
        self.assertEqual(stored, src)
        self.assertFalse(warned)


class TestSingleSpaceIndentNoLongerStripped(unittest.TestCase):
    """The separate defect this file pinned, now fixed on ticket #73.

    It was pinned here rather than fixed because ticket #71 was the ``::``
    detector: the write leg stripped a single-space indent from any line, with
    or without a double colon and with no warning, silently demoting a
    ``<blockquote>`` to a paragraph.  It was recorded so the leading space the
    indented rows above still lost stayed traceable to a known cause instead
    of looking like an incomplete fix.

    Ticket #73 fixed it, so the assertions are inverted here rather than
    deleted -- this is the seam between the two tickets, and a reader arriving
    at those tightened ``expected`` values above needs it to still say why.
    The full pin lives in ``test_converter_blockquote_indent.py``; these two
    rows stay because they are the ones this file's own table depends on.

    Note what the fix is *not*: the indent is preserved by emitting the line
    verbatim through ticket #63's fallback, not by converting it to a Markdown
    blockquote.  One space is not a width Markdown can carry -- see that
    file's docstring for the measured grammar and why.
    """

    def test_single_space_indent_survives_without_a_double_colon(self):
        stored, warned = _round_trip(" Indented prose with no colons.")
        self.assertEqual(stored, " Indented prose with no colons.")
        self.assertFalse(warned)

    def test_two_space_indent_survives(self):
        """The contrast: two spaces is the width that converts to ``> ``."""
        stored, _ = _round_trip("  Two-space indented prose.")
        self.assertEqual(stored, "  Two-space indented prose.")


class TestWarningIsShielded(unittest.TestCase):
    """The warning must not fire on a definition list someone *quoted*.

    Its own seeded defect, and it genuinely failed before ticket #63: the old
    warning came from ``_detect_lossy_elements``, which scanned the raw text,
    so a ``term::`` line inside a code block announced a definition list in
    quoted source.  That is the over-firing shape this file's ticket (#71)
    was about, surviving in a second place -- and tickets #57 and #59 are
    this project's evidence that an over-firing check gets muted and takes
    its true positives with it.

    The fallback's warning is raised by ``_stash_fallback`` at the point it
    fires, against ``_verbatim_mask``, so the false positive goes away as a
    consequence of the design rather than needing a fix of its own.
    """

    QUOTED = [
        ("plain code block", "{{{\n term:: not a definition\n}}}"),
        (
            "processor block",
            "{{{#!python\n term:: not a definition\n}}}",
        ),
        ("code span", "A `term:: def` span."),
    ]

    def test_quoted_definition_list_does_not_warn(self):
        """Matched on *any* definition warning, not on the fallback's wording.

        Deliberate, and the class would otherwise be untestable: matching the
        new wording alone would have passed before the fix as well, because
        the old warning spelled it differently -- a gate that cannot fail,
        which ``Rules/testing/SeededDefectFirst`` says looks exactly like a
        gate that always passes.  Watched failing on all three rows first.
        """
        for label, src in self.QUOTED:
            with self.subTest(label):
                warnings = tracwiki_to_markdown(src).warnings
                self.assertFalse(
                    [w for w in warnings if "definition" in w.lower()],
                    f"{label}: warned about a definition list in quoted "
                    f"source: {warnings}",
                )

    def test_quoted_definition_list_round_trips(self):
        """The content half: it is source text, and must survive as such."""
        for label, src in self.QUOTED:
            with self.subTest(label):
                stored, _ = _round_trip(src)
                self.assertEqual(stored, src)


class TestRunContainingBothConstructs(unittest.TestCase):
    """A list item and a definition term sharing one indented run.

    Measured: `` * bullet`` followed by `` term:: def`` with no blank line is
    a ``<ul>`` **followed by** a ``<dl>`` -- two constructs in one run of
    indented lines.

    Per the operator decision on ticket #63, the whole run falls back.  The
    two alternatives both lose something the fallback does not: skipping the
    run would let the ``<dl>`` degrade to prose at column zero now that
    nothing converts it, and splitting the run at the construct boundary
    needs a real block parser to know where each construct starts and ends --
    more machinery than the rest of the change put together.

    The cost is recorded rather than hidden: the bullet list is carried
    verbatim too, even though Markdown could have expressed it.
    """

    SOURCE = " * bullet\n term:: def after a list"

    def test_the_whole_run_round_trips(self):
        stored, _ = _round_trip(self.SOURCE)
        self.assertEqual(stored, self.SOURCE)

    def test_it_warns_rather_than_degrading_silently(self):
        _, warned = _round_trip(self.SOURCE)
        self.assertTrue(warned)

    def test_the_definition_does_not_reach_column_zero(self):
        """The failure the other two options would have produced.

        A definition term at column zero is prose to Trac, not a ``<dl>``, so
        this is the assertion that the construct is still a definition list
        after the round trip -- not merely that some bytes came back.
        """
        stored, _ = _round_trip(self.SOURCE)
        self.assertNotIn("\nterm::", stored)


if __name__ == "__main__":
    unittest.main()
