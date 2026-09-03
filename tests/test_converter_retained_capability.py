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
    FALLBACK_FENCE_INFO,
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


class TestUnrepresentableFallback(unittest.TestCase):
    """The ticket #63 fallback contract, replacing the pre-change inventory.

    This class was ``TestUnrepresentableInventory`` in the previous commit,
    where it characterised the *broken* behaviour so the change could be
    diffed against it.  The fallback has now landed and every row moved from
    "fails to round-trip" to "round-trips byte-for-byte", so the rows are
    updated here deliberately, as that class's docstring required.

    Measured before the change: seven of these eight rows did not survive
    ``tw -> md -> tw``, and the eighth -- the comment block -- round-tripped
    byte-for-byte while inverting a semantic, becoming *visible* content
    where ``Reference/trac/WikiEscapeContexts`` records Trac as dropping it
    entirely.  All eight are now lossless.

    The contract, per the operator decision on this ticket:

    * read leg emits the construct verbatim inside a fallback fence and warns;
    * write leg *unwraps* that fence, restoring the original source exactly.

    Unwrapping is a literal unwrap with nothing to infer, which is what
    separates it from the ``[MACRO: ...]`` placeholder deleted earlier on this
    ticket -- that one had to be reconstructed by guessing.
    """

    # (label, tracwiki source)
    CONSTRUCTS = [
        ("table cell spanning", "||=a=||=b=||\n|||| spanned ||"),
        ("multi-line row", "||a||b|| \\\n||c||d||"),
        ("standalone processor cell (td)", "{{{#!td\nbody here\n}}}"),
        ("standalone processor cell (th)", "{{{#!th\nhead\n}}}"),
        (
            "adjacent processor cells, separate lines",
            "{{{#!th\nH\n}}}\n{{{#!td\nd\n}}}",
        ),
        (
            "adjacent processor cells, same line",
            "{{{#!th\nH\n}}}{{{#!td\nd\n}}}",
        ),
        ("comment block", "{{{#!comment\nhidden\n}}}"),
        (
            "table processor block",
            "{{{#!table\n|| {{{#!td\na\n}}} ||\n}}}",
        ),
    ]

    def test_round_trips_byte_for_byte(self):
        """The headline: every one of these was lossy before this commit."""
        for label, src in self.CONSTRUCTS:
            with self.subTest(label):
                md = tracwiki_to_markdown(src).text
                self.assertEqual(markdown_to_tracwiki(md), src)

    def test_source_is_carried_verbatim(self):
        """The fence body is the original source, not a reconstruction."""
        for label, src in self.CONSTRUCTS:
            with self.subTest(label):
                md = tracwiki_to_markdown(src).text
                self.assertIn(FALLBACK_FENCE_INFO, md)
                self.assertIn(src, md)

    def test_each_construct_warns_exactly_once(self):
        """Silent loss is the defect; one warning per construct, no repeats.

        Adjacent blocks coalesce into a single region, so the two adjacent
        rows warn once rather than twice.
        """
        for label, src in self.CONSTRUCTS:
            with self.subTest(label):
                self.assertEqual(
                    len(tracwiki_to_markdown(src).warnings), 1
                )

    def test_fence_pairs_are_well_formed(self):
        """Adjacent fallback regions must not run their fences together.

        Measured while building this: emitting one fence per block put a
        closing fence against the next opening fence on one line
        (``````tracwiki-unconverted``), which is not a fence pair at all.
        """
        for label, src in self.CONSTRUCTS:
            with self.subTest(label):
                md = tracwiki_to_markdown(src).text
                opens = [
                    ln
                    for ln in md.split("\n")
                    if ln.startswith("`") and FALLBACK_FENCE_INFO in ln
                ]
                for ln in opens:
                    self.assertRegex(
                        ln, r"^`+" + FALLBACK_FENCE_INFO + r"$"
                    )

    def test_quoted_token_is_not_a_construct(self):
        """A processor token inside a code span stays a token (ticket #46).

        The recall half of the gate, at the sharpest point: the fallback runs
        before the converter's own code-span shielding, so it has to do its
        own -- see ``_verbatim_mask``.
        """
        for src in (
            "`{{{#!td}}}`",
            "`{{{#!table}}}`",
            "`{{{#!comment}}}`",
        ):
            with self.subTest(src):
                result = tracwiki_to_markdown(src)
                self.assertEqual(result.text, src)
                self.assertNotIn(FALLBACK_FENCE_INFO, result.text)
                self.assertEqual(result.warnings, [])

    def test_nested_block_is_not_a_construct(self):
        """A fallback processor nested in a code block is content (#51)."""
        src = "{{{\n{{{#!comment\nAGENT: do the thing\n}}}\n}}}"
        result = tracwiki_to_markdown(src)
        self.assertNotIn(FALLBACK_FENCE_INFO, result.text)
        self.assertEqual(result.warnings, [])

    def test_write_leg_footnote_loses_its_definition(self):
        """Unrepresentable *Markdown*, still unhandled -- the write-leg half.

        Pinned here as the measured "before" for the write-leg fallback, which
        has not landed yet.  The definition line is deleted outright and the
        reference becomes a wiki link to a page named after the note text.
        """
        stored = markdown_to_tracwiki("Text[^1]\n\n[^1]: note\n")
        self.assertEqual(stored, "Text[wiki:note ^1]")
        self.assertNotIn("[^1]:", stored)


if __name__ == "__main__":
    unittest.main()
