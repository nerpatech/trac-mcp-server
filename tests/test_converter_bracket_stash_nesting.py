"""Nested-placeholder restore pin for ticket #91.

Found while sweeping the cached `auto_pm` + `trac_mcp_server` corpora
(`~/.cache/trac-sweep`) to measure #88's blast radius. Unrelated to #88;
surfaced only because the stock `WikiFormatting` page, present unmodified
on both stores, happens to also contain the shape this ticket is about.

`_stash_bracket_syntax` in `markdown_to_tracwiki.py` stashes backtick code
spans first, then `[[...]]` bracket syntax, then single-bracket TracLinks
-- each pass runs over the text the previous one already produced. If a
`[[...]]` span (or single-bracket link) itself quotes a code span, the
code span is already a `\\x00WK<n>\\x00` sentinel by the time the outer
span is stashed, and that sentinel becomes part of the *stored value* for
the outer span's own placeholder.

`_restore_bracket_syntax` used to substitute sentinels in one
non-recursive `re.sub` pass. Restoring the outer placeholder pastes its
stored text back in -- inner sentinel included -- and `re.sub` never
rescans replacement text for further matches, so that inner sentinel's
raw NUL byte survived all the way to the final converter output, tripping
the guard added on #51 ("unrestored placeholder sentinel (NUL byte)
survived to converter output").

**The seed was watched failing first.** Measured on `master`: the stock
`WikiFormatting` page (both stores) raised on this exact shape, isolated
to a 181-line prefix of the real page (`|| [[html(<code>`{{{-}}}` triple
curly brackets</code>)]] ||`, a stock example of escaping a literal
`{{{`). The bare line alone does not crash -- it mis-converts into a
garbled link, itself a pre-existing, separate defect (`[[html(...)]]` has
no bracket-macro handling to begin with) -- so the fix is verified below
against the minimal shape that *does* trigger the nested-placeholder
leak, not against the WikiFormatting page itself (too large to pin as a
unit-test fixture; the corpus sweep is the real-content check).

**Fix:** loop `_restore_bracket_syntax`'s substitution until no sentinel
remains, instead of a single pass. A stashed span can only ever contain a
placeholder from an *earlier* stash pass (never itself or a later one),
so this always terminates.
"""

import unittest

from trac_mcp_server.converters import markdown_to_tracwiki


class TestNestedPlaceholderRestore(unittest.TestCase):
    """A code span quoted inside `[[...]]` or a single-bracket link must
    not leak its stashed sentinel into the final output."""

    def test_code_span_inside_a_bracket_macro(self):
        """The exact shape measured on the stock WikiFormatting page."""
        src = "[[html(<code>`{{{-}}}` triple curly brackets</code>)]]"
        out = markdown_to_tracwiki(src)
        self.assertNotIn("\x00", out)
        self.assertEqual(out, src)

    def test_code_span_inside_a_bracket_macro_mid_prose(self):
        src = "prose [[span(`x`)]] more prose"
        out = markdown_to_tracwiki(src)
        self.assertNotIn("\x00", out)
        self.assertEqual(out, src)

    def test_code_span_inside_a_single_bracket_link(self):
        """`_SINGLE_BRACKET_LINK_RE` is the other stash pass that runs
        over already-code-span-stashed text and can hit the same leak."""
        src = "[wiki:SomePage `label with code`]"
        out = markdown_to_tracwiki(src)
        self.assertNotIn("\x00", out)

    def test_code_span_inside_a_heading_with_a_bracket_macro(self):
        """`heading()` calls `_restore_bracket_syntax` mid-render, before
        the one global restore pass runs over the rest of the document
        (ticket #45 regression guard) -- must not reintroduce the leak
        there either."""
        out = markdown_to_tracwiki(
            "# heading [[html(`x`)]] text", heading_anchors=True
        )
        self.assertNotIn("\x00", out)


class TestRecallGate(unittest.TestCase):
    """Constructs pinned by earlier tickets in the same stash mechanism."""

    def test_plain_bracket_macro_is_unaffected(self):
        self.assertEqual(
            markdown_to_tracwiki("[[PageOutline]]"), "[[PageOutline]]"
        )

    def test_plain_code_span_is_unaffected(self):
        self.assertEqual(markdown_to_tracwiki("`code`"), "`code`")

    def test_bracket_macro_with_no_code_span_is_unaffected(self):
        self.assertEqual(
            markdown_to_tracwiki("[[BR]] and [[TOC]]"),
            "[[BR]] and [[TOC]]",
        )
