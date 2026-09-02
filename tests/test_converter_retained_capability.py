"""Retained-capability pin for the ticket #63 accommodation-layer deletion.

Ticket #63 is a *deletion* pass, which inverts the usual measure: "bugs went
away" and "tests still pass" are both satisfied by deleting the code under
test.  So the gate is not the test count -- it is this list, pinned *before*
any deletion and asserted unchanged after it.

Two independent halves:

* ``TestMacroRoundTripRetained`` states the capability in terms of the
  *observable contract* -- a TracWiki macro survives a tw->md->tw round trip
  -- and deliberately says nothing about the intermediate Markdown.  That is
  what lets it pass both before the deletion (where the macro travels as a
  ``[MACRO: ...]`` placeholder restored by ``_MACRO_PLACEHOLDER_RE``) and
  after it (where ``unknown_macros="preserve"`` leaves ``[[Name]]`` literal
  and ``_BRACKET_SYNTAX_RE`` carries it through).  A pin written against the
  placeholder spelling would only have restated the implementation it was
  meant to guard.

* ``TestStoreCorpusBaseline`` diffs four real ``auto_pm`` store pages against
  golden output generated from the pre-deletion code, per
  ``Rules/testing/PreserveTheBaseline``.  Real content rather than invented
  fixtures, because the point is to catch damage to the shapes actually in
  use.  The four were chosen as the ones already carrying ``HOST`` /
  ``PROJECT`` / ``PATH`` placeholders in the store itself -- this repo has a
  public remote, and the other candidate pages embed the internal Trac
  address.

Measured while pinning this (2026-09-02), and the reason the deletion is a
no-op for the store rather than merely a small change: across those pages
plus ``Index``, ``Reference/trac/InterTrac`` and
``Rules/trac/PreferInterTracLinks`` -- 46 KB of real content --
``unknown_macros="bracket"`` and ``"preserve"`` produce byte-identical
Markdown *and* byte-identical round trips, and **zero** ``[MACRO: ...]``
placeholders are emitted at all.  Real store pages use ``[[BR]]`` (excluded
from the macro handler) and slash-path wiki links (which its ``(\\w+)`` name
group cannot match), so nothing in the store reaches the deleted path.
"""

import glob
import os
import unittest

from trac_mcp_server.converters import markdown_to_tracwiki
from trac_mcp_server.converters.tracwiki_to_markdown import (
    tracwiki_to_markdown,
)

CORPUS_DIR = os.path.join(
    os.path.dirname(__file__), "fixtures", "store_corpus"
)


class TestMacroRoundTripRetained(unittest.TestCase):
    """A TracWiki macro must survive tw -> md -> tw unchanged."""

    # (label, source) -- every form that reaches the macro branch at all.
    MACROS = [
        ("known macro, no args", "[[PageOutline]]"),
        ("known macro, args", "[[TOC(depth=2)]]"),
        ("known macro, empty args", "[[TicketQuery()]]"),
        ("unknown name, args", "[[SomeMacro(arg)]]"),
    ]

    def test_macro_survives_round_trip(self):
        for label, src in self.MACROS:
            with self.subTest(label):
                md = tracwiki_to_markdown(src).text
                self.assertEqual(markdown_to_tracwiki(md).strip(), src)

    def test_macro_survives_round_trip_in_prose(self):
        """Same, but embedded in surrounding text rather than standing alone."""
        for label, macro in self.MACROS:
            with self.subTest(label):
                src = f"Intro text.\n\n{macro}\n\nTrailing text."
                md = tracwiki_to_markdown(src).text
                back = markdown_to_tracwiki(md)
                self.assertIn(macro, back)

    def test_br_and_image_are_not_macro_placeholders(self):
        """[[BR]] and [[Image]] are handled before the macro branch."""
        for src in ("Line one[[BR]]line two", "[[Image(foo.png)]]"):
            with self.subTest(src):
                md = tracwiki_to_markdown(src).text
                self.assertNotIn("[MACRO:", md)

    def test_slash_path_wiki_link_round_trips(self):
        """The store's dominant [[...]] form -- must not regress into a macro."""
        src = "See [[Rules/trac/RenderVerify]] for the procedure."
        md = tracwiki_to_markdown(src).text
        self.assertNotIn("[MACRO:", md)
        self.assertIn(
            "[[Rules/trac/RenderVerify]]", markdown_to_tracwiki(md)
        )

    def test_no_nul_sentinel_leaks(self):
        """No conversion may leak an internal \\x00 sentinel into output."""
        for _, src in self.MACROS:
            with self.subTest(src):
                md = tracwiki_to_markdown(src).text
                self.assertNotIn("\x00", md)
                self.assertNotIn("\x00", markdown_to_tracwiki(md))


class TestStoreCorpusBaseline(unittest.TestCase):
    """Real store pages must convert byte-identically to the pinned golden."""

    def _corpus(self):
        found = sorted(
            glob.glob(os.path.join(CORPUS_DIR, "*.tracwiki"))
        )
        # Exclude the generated round-trip goldens, which share the suffix.
        return [
            f
            for f in found
            if not f.endswith(".golden.roundtrip.tracwiki")
        ]

    def test_corpus_is_present(self):
        """Guard against the corpus silently vanishing and the pin passing."""
        self.assertEqual(len(self._corpus()), 4)

    def test_tracwiki_to_markdown_matches_golden(self):
        for path in self._corpus():
            with self.subTest(os.path.basename(path)):
                golden = path[: -len(".tracwiki")] + ".golden.md"
                with open(path) as fh:
                    src = fh.read()
                with open(golden) as fh:
                    expected = fh.read()
                self.assertEqual(
                    tracwiki_to_markdown(src).text, expected
                )

    def test_round_trip_matches_golden(self):
        for path in self._corpus():
            with self.subTest(os.path.basename(path)):
                golden = (
                    path[: -len(".tracwiki")]
                    + ".golden.roundtrip.tracwiki"
                )
                with open(path) as fh:
                    src = fh.read()
                with open(golden) as fh:
                    expected = fh.read()
                md = tracwiki_to_markdown(src).text
                self.assertEqual(markdown_to_tracwiki(md), expected)


if __name__ == "__main__":
    unittest.main()
