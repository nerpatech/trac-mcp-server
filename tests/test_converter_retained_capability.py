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
                self.assertEqual(markdown_to_tracwiki(md), src)

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


class TestRepresentableRoundTripRetained(unittest.TestCase):
    """Constructs that survive ``tw -> md -> tw`` unchanged today.

    This is the retained-capability list proper, extended for the ticket #63
    fallback contract.  Every entry here maps cleanly between the formats, so
    the fallback must never fire on any of them -- they are the *recall* gate.
    Per ``Rules/testing/SeededDefectFirst`` a marker that always fires and one
    that never fires look identical from a green suite, and the store corpus
    cannot tell them apart because it contains none of the broken constructs
    (measured on ticket #63: the only warning the four pinned pages produce is
    the content-free "TracLinks detected").  So this class carries the "stays
    silent" half and ``TestUnrepresentableInventory`` carries the "fires" half.

    These assertions must hold before *and* after every commit on this ticket.
    """

    IDENTITY = [
        ("plain table", "||=a=||=b=||\n||x||y||"),
        (
            "code block, indented body",
            "{{{#!python\ndef f(x):\n    return 1\n}}}",
        ),
        ("plain code block", "{{{\nliteral\n}}}"),
        ("div processor", "{{{#!div class=important\nhello\n}}}"),
        ("macro, no args", "[[PageOutline]]"),
        ("macro, args", "[[TOC(depth=2)]]"),
        (
            "slash-path wiki link",
            "See [[Rules/trac/RenderVerify]] here.",
        ),
        ("bold and italic", "'''bold''' and ''italic''"),
        ("bullet list", " * a\n * b"),
        ("heading", "== Head =="),
        (
            "markdown container",
            "{{{#!markdown\n| a | b |\n|---|---|\n}}}",
        ),
    ]

    def test_round_trips_unchanged(self):
        for label, src in self.IDENTITY:
            with self.subTest(label):
                md = tracwiki_to_markdown(src).text
                self.assertEqual(markdown_to_tracwiki(md), src)

    def test_fallback_never_fires_on_representable_input(self):
        """The recall gate: no fallback wrapper on anything that maps.

        Asserted on the *stored* bytes rather than on the warning list, per
        ``Rules/trac/DeclareSourceFormat`` -- an empty warning list is exactly
        the signal that stayed clean while content was being destroyed.
        """
        for label, src in self.IDENTITY:
            with self.subTest(label):
                md = tracwiki_to_markdown(src).text
                # The {{{#!markdown}}} container is itself an IDENTITY row,
                # so only flag it when it was not in the source to begin with.
                if "#!markdown" not in src:
                    self.assertNotIn("#!markdown", md)
                    self.assertNotIn(
                        "#!markdown", markdown_to_tracwiki(md)
                    )


class TestNormalisedRoundTrip(unittest.TestCase):
    """Constructs that survive in substance but not byte-for-byte.

    Measured on ticket #63: three of the four pinned store pages do *not*
    round-trip byte-identically, and every diff is normalisation of this kind
    rather than accommodation damage.  Pinned separately from ``IDENTITY`` so
    the distinction stays visible -- these are representable, so the fallback
    must not fire on them either, but the exact bytes are expected to shift
    and asserting identity would be a false alarm.
    """

    NORMALISED = [
        # A numbered list is renumbered rather than echoed.
        ("numbered list", " 1. a\n 1. b", " 1. a\n 2. b"),
        # [[BR]] gains a leading space and splits the line.
        ("BR", "one[[BR]]two", "one [[BR]]\ntwo"),
    ]

    def test_normalisation_is_stable(self):
        for label, src, expected in self.NORMALISED:
            with self.subTest(label):
                md = tracwiki_to_markdown(src).text
                self.assertEqual(markdown_to_tracwiki(md), expected)

    def test_normalisation_is_idempotent(self):
        """Re-converting the normalised form must not drift further."""
        for label, _, expected in self.NORMALISED:
            with self.subTest(label):
                md = tracwiki_to_markdown(expected).text
                self.assertEqual(markdown_to_tracwiki(md), expected)


class TestUnrepresentableInventory(unittest.TestCase):
    """Characterisation of the constructs the ticket #63 fallback replaces.

    **These assertions pin behaviour that is expected to CHANGE.**  They are
    not a retained-capability list -- they are the measured "before" that each
    fallback commit is diffed against, so every change is deliberate and
    visible in review rather than discovered afterwards.

    Every row here is a construct TracWiki can express and Markdown cannot.
    Measured on ticket #63 against the pre-fallback converters: five of the six
    fail to round-trip, and the sixth inverts a semantic instead --
    ``Reference/trac/WikiEscapeContexts`` records ``{{{#!comment}}}`` as
    "dropped entirely" by Trac, while the read leg emits a fenced block that
    every Markdown renderer *displays*.

    When a fallback lands for one of these rows, update that row and say so in
    the commit message.  A row that changes without the commit saying so would
    be exactly the silent damage this ticket exists to remove.
    """

    # (label, tracwiki source, markdown today, tracwiki after round trip)
    BROKEN = [
        (
            "table cell spanning",
            "||=a=||=b=||\n|||| spanned ||",
            "| a | b |\n|---|---|\n| [span:2] spanned |",
            "| a | b |\n|---|---|\n| [span:2] spanned |",
        ),
        (
            "multi-line row",
            "||a||b|| \\\n||c||d||",
            "| a | b |  | c | d |\n|---|---|---|---|---|",
            "||=a=||=b=|| ||=c=||=d=||",
        ),
        (
            "standalone processor cell (td)",
            "{{{#!td\nbody here\n}}}",
            "| body here |\n|---|",
            "||=body here=||",
        ),
        (
            "standalone processor cell (th)",
            "{{{#!th\nhead\n}}}",
            "| head |\n|---|",
            "||=head=||",
        ),
        (
            # Measured on ticket #63 while seeding this pin: inverting the
            # td/th branch of _convert_processor_cells produces BYTE-IDENTICAL
            # output for every shape tried, standalone or in a table.  The
            # distinction is erased downstream by _convert_tables, which makes
            # the first row a header regardless.  So that branch's two arms
            # are not observably different -- a deletion candidate for this
            # ticket on its own terms, and the reason no assertion here can
            # guard the distinction: there is nothing left to guard.
            "processor cells, th row then td row",
            "{{{#!th\nH\n}}}\n{{{#!td\nd\n}}}",
            "| H |\n|---|\n| d |",
            "||=H=||\n||d||",
        ),
        (
            # A td following a th on the SAME row picks up a spurious
            # [span:2] marker it never had in the source.
            "processor cells, th and td on one row",
            "{{{#!th\nH\n}}}{{{#!td\nd\n}}}",
            "| H | [span:2] d |\n|---|---|",
            "||=H=||=[span:2] d=||",
        ),
        (
            "definition list",
            "term::\n  definition",
            "**term**: > definition",
            "'''term''': > definition",
        ),
        (
            "comment block",
            "{{{#!comment\nhidden\n}}}",
            "```comment\nhidden\n```",
            "{{{#!comment\nhidden\n}}}",
        ),
    ]

    def test_read_leg_output_is_as_measured(self):
        for label, src, md_today, _ in self.BROKEN:
            with self.subTest(label):
                self.assertEqual(
                    tracwiki_to_markdown(src).text, md_today
                )

    def test_round_trip_result_is_as_measured(self):
        for label, src, _, back_today in self.BROKEN:
            with self.subTest(label):
                md = tracwiki_to_markdown(src).text
                self.assertEqual(markdown_to_tracwiki(md), back_today)

    def test_all_but_the_comment_block_fail_to_round_trip(self):
        """The defect, stated as an assertion rather than as prose.

        The comment block is the exception: it round-trips byte-for-byte and
        is broken in the render instead, which is why no round-trip assertion
        could ever have caught it.
        """
        failures = []
        for label, src, _, _ in self.BROKEN:
            md = tracwiki_to_markdown(src).text
            if markdown_to_tracwiki(md) != src:
                failures.append(label)
        self.assertEqual(len(failures), 7, failures)
        self.assertNotIn("comment block", failures)

    def test_comment_block_becomes_visible(self):
        """The semantic inversion, pinned on its own.

        Trac drops a comment block entirely; the read leg emits a fenced block
        that renders as visible content.
        """
        md = tracwiki_to_markdown("{{{#!comment\nhidden\n}}}").text
        self.assertIn("hidden", md)
        self.assertNotIn("#!comment", md)

    def test_write_leg_footnote_loses_its_definition(self):
        """The one measured unrepresentable *Markdown* construct.

        The definition line is deleted outright and the reference becomes a
        wiki link to a page named after the note text.  Silent: no warning.
        """
        stored = markdown_to_tracwiki("Text[^1]\n\n[^1]: note\n")
        self.assertEqual(stored, "Text[wiki:note ^1]")
        self.assertNotIn("[^1]:", stored)


if __name__ == "__main__":
    unittest.main()
