"""Definition-list detector pin for ticket #71.

The detector in ``_convert_other_elements`` matches
``^(\\s*)(.+?)::\\s*(.+)$``, which claims *any* line containing a double colon
with a non-empty remainder.  Ordinary prose is therefore rewritten as a bold
term plus a colon, and a full read-edit-write cycle stores that damage
permanently.

**This file characterises the defect before it is fixed.**  Every ``expected``
below is the *current, wrong* output, pinned so the fix can be diffed against a
recorded baseline rather than against memory -- the same shape ticket #63's
inventory class used, and like that class these rows are meant to be updated
deliberately by the commit that fixes them.

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

DEFINITION_LIST_WARNING = "Definition lists detected"


def _round_trip(source):
    """Return (stored bytes after tw -> md -> tw, definition-list warned?)."""
    result = tracwiki_to_markdown(source)
    warned = any(DEFINITION_LIST_WARNING in w for w in result.warnings)
    return markdown_to_tracwiki(result.text), warned


class TestProseWithDoubleColonCharacterised(unittest.TestCase):
    """The false positives: none of these is a definition list to Trac.

    Every row is corrupted today -- prose becomes bold markup in the stored
    bytes, permanently -- and the warning fires as well, spending the suite's
    credibility on content containing no definition list at all.  Tickets #57
    and #59 are this project's evidence that an over-firing check gets muted
    and takes its true positives with it.

    ``expected`` records that damage rather than the correct output.  The
    commit that tightens the detector must update every row here to the
    source text unchanged, and flip ``warned`` to False.
    """

    # (label, source, stored bytes today, does the warning fire today?)
    NOT_A_DEFINITION_LIST = [
        (
            "C++ scope, column zero",
            "Use std::vector here.",
            "'''Use std''': vector here.",
            True,
        ),
        (
            "realm-shaped token, column zero",
            "See Reference::Page for detail.",
            "'''See Reference''': Page for detail.",
            True,
        ),
        (
            "ratio, column zero",
            "A ratio of 3::1 was used.",
            "'''A ratio of 3''': 1 was used.",
            True,
        ),
        # Term-shaped, but at column zero -- prose to Trac.
        (
            "term-shaped at column zero",
            "term:: definition at column zero",
            "'''term''': definition at column zero",
            True,
        ),
        (
            "C++ scope, indented",
            " Use std::vector here indented.",
            "'''Use std''': vector here indented.",
            True,
        ),
        (
            "ratio, indented",
            " A ratio of 3::1 was used, indented.",
            "'''A ratio of 3''': 1 was used, indented.",
            True,
        ),
        (
            "no whitespace after the colons",
            " term::definition with no space",
            "'''term''': definition with no space",
            True,
        ),
        # Trac stops at the FIRST colon pair, finds "v", and declines the line.
        (
            "first colon pair is not followed by whitespace",
            " std::vector:: a C++ type as the term",
            "'''std''': vector:: a C++ type as the term",
            True,
        ),
    ]

    def test_prose_is_corrupted_today(self):
        """The defect proper: prose is rewritten as bold in the stored bytes."""
        for label, src, expected, _ in self.NOT_A_DEFINITION_LIST:
            with self.subTest(label):
                stored, _ = _round_trip(src)
                self.assertEqual(stored, expected)

    def test_warning_fires_on_prose_today(self):
        """The aggravating half: the false positive is not even silent."""
        for label, src, _, warned_today in self.NOT_A_DEFINITION_LIST:
            with self.subTest(label):
                _, warned = _round_trip(src)
                self.assertEqual(warned, warned_today)


class TestGenuineDefinitionListCharacterised(unittest.TestCase):
    """The recall gate: real definition lists, and what they do today.

    The danger on this ticket is the inverse of the usual one -- the check
    fires too often, so tightening it risks a false negative.  These four rows
    are definition lists to Trac (measured, see the module docstring) and must
    stay detected once the detector is exact.

    Two of them are already wrong today, in opposite directions, and both are
    fixed by the same tightening:

    * *empty definition* is **missed entirely** -- the current pattern requires
      a non-empty remainder, so a real ``<dl>`` draws no warning and no
      conversion.  A false negative that was hiding underneath the false
      positives.
    * *definition on the next line* picks up a stray ``> `` from the blockquote
      pass, which runs first and claims any line indented exactly two spaces.
      Trac reads that line as part of the ``<dd>``.
    """

    # (label, source, stored bytes today, does the warning fire today?)
    DEFINITION_LISTS = [
        (
            "single-word term",
            " term:: definition indented one space",
            "'''term''': definition indented one space",
            True,
        ),
        (
            "multi-word term",
            " multi word term:: a definition",
            "'''multi word term''': a definition",
            True,
        ),
        # MISSED today: no warning, no conversion.
        (
            "empty definition",
            " trailing colons only::",
            "trailing colons only::",
            False,
        ),
        # The stray "> " comes from the blockquote pass.
        (
            "definition on the next line",
            " term::\n  the definition",
            "'''term''': > the definition",
            True,
        ),
    ]

    def test_conversion_is_characterised(self):
        for label, src, expected, _ in self.DEFINITION_LISTS:
            with self.subTest(label):
                stored, _ = _round_trip(src)
                self.assertEqual(stored, expected)

    def test_detection_is_characterised(self):
        for label, src, _, warned_today in self.DEFINITION_LISTS:
            with self.subTest(label):
                _, warned = _round_trip(src)
                self.assertEqual(warned, warned_today)


class TestControl(unittest.TestCase):
    """Prose with no double colon: silent and unchanged, before and after.

    This row must not move.  It is the only assertion in the file that is
    already correct today and must still be correct after the fix, which is
    what makes it the control rather than a characterisation.
    """

    def test_plain_prose_round_trips_silently(self):
        src = "Plain prose with no colons at all."
        stored, warned = _round_trip(src)
        self.assertEqual(stored, src)
        self.assertFalse(warned)


class TestSingleSpaceIndentStrippedPreexisting(unittest.TestCase):
    """A separate pre-existing defect, pinned so this ticket does not hide it.

    The write leg strips a single-space indent from any line, with or without
    a double colon and with no warning.  Trac renders a single-space-indented
    line as a ``<blockquote>``, so this silently demotes a quote to a
    paragraph.  Measured on ``master`` at ``6ecbdc1``, independent of the
    definition-list pass -- it reproduces on a line containing no colons at
    all, which is what puts it outside ticket #71.

    Pinned rather than fixed because this ticket is the ``::`` detector.  It
    is recorded here so that when the indented rows above stop being bolded,
    the leading space they still lose is traceable to a known cause rather
    than looking like an incomplete fix.
    """

    def test_single_space_indent_is_stripped_without_a_double_colon(
        self,
    ):
        stored, warned = _round_trip(" Indented prose with no colons.")
        self.assertEqual(stored, "Indented prose with no colons.")
        self.assertFalse(warned)

    def test_two_space_indent_survives(self):
        """The contrast: a two-space indent round-trips via the blockquote pass."""
        stored, _ = _round_trip("  Two-space indented prose.")
        self.assertEqual(stored, "  Two-space indented prose.")


if __name__ == "__main__":
    unittest.main()
