"""Nested-fence round-trip pin for ticket #88.

Found while fixing #72's read leg. Verified identical before and after that
fix, and reproducing with no nesting at all -- pre-existing, not a
regression from #72.

`_restore_nested_fences` was added on #51 to undo the read leg's OWN
output: back then, a nested TracWiki ``{{{ }}}`` block was emitted as a
nested Markdown fence, with the outer fence widened so it never collided
with the inner one. On the way back, mistune handed that inner fence to
`block_code` as inert literal text -- it never became its own `block_code`
call -- so a helper had to recognize the shape and restore it.

Ticket #72 changed what the read leg emits: a nested block is now carried
verbatim inside a single fence, with the inner delimiters as literal text
that Trac renders unmodified. `_restore_nested_fences`'s only remaining
input was therefore a fence the *author* had quoted on purpose -- e.g. a
page documenting Markdown syntax -- and rewriting that into a `{{{ }}}`
block was corruption, not restoration. This ticket's fix: delete the
helper.

**The seeds were watched failing first.** Measured on `master` before this
fix: every row below rewrote its quoted backtick fence into a `{{{ }}}`
block on the way back out, silently converting the example into the thing
it was quoting.

**Blast radius measured before deleting**, per this ticket's own section 4
concern (a Markdown file produced by the *pre*-#72 read leg might still
rely on the helper): swept the full cached corpus of both live stores
(``~/.cache/trac-sweep``, 1020 documents) with `tw -> md -> tw` run once
with the helper present and once with it patched to a no-op. Zero
documents differed. 39 documents contain a genuinely nested `{{{ }}}`
block and none of them needed the helper to round-trip correctly -- #72's
verbatim-carry-through already handles every real nested block in both
stores without it.
"""

import unittest

from trac_mcp_server.converters import (
    markdown_to_tracwiki,
    tracwiki_to_markdown,
)


def _round_trip(source):
    """Return (stored bytes after tw -> md -> tw, intermediate md)."""
    result = tracwiki_to_markdown(source)
    return markdown_to_tracwiki(result.text), result.text


class TestQuotedFenceSurvivesRoundTrip(unittest.TestCase):
    """The seeds: ticket #88 section 2's three damaged rows."""

    def test_lang_tagged_fence_quoted_inside_a_code_block(self):
        src = "{{{\nintro\n```python\ninner\n```\n}}}"
        stored, md = _round_trip(src)
        self.assertEqual(md, "````\nintro\n```python\ninner\n```\n````")
        self.assertEqual(stored, src)

    def test_bare_fence_quoted_inside_a_code_block(self):
        src = "{{{\nintro\n```\ninner\n```\n}}}"
        stored, md = _round_trip(src)
        self.assertEqual(md, "````\nintro\n```\ninner\n```\n````")
        self.assertEqual(stored, src)

    def test_quoted_fence_one_level_deeper(self):
        """Section 2's third row: the same shape, nested one level deeper
        inside a genuine `{{{ }}}`-inside-`{{{ }}}` block."""
        src = "{{{\n{{{\n```\ninner\n```\n}}}\n}}}"
        stored, md = _round_trip(src)
        self.assertEqual(md, "````\n{{{\n```\ninner\n```\n}}}\n````")
        self.assertEqual(stored, src)


class TestRecallGate(unittest.TestCase):
    """A genuine nested block -- no quoted fence anywhere -- must keep
    round-tripping exactly as #72 left it."""

    def test_lang_inside_lang(self):
        src = "{{{#!python\nouter\n{{{#!sh\ninner\n}}}\nafter\n}}}"
        stored, _ = _round_trip(src)
        self.assertEqual(stored, src)

    def test_plain_inside_lang(self):
        src = "{{{#!python\nouter\n{{{\ninner\n}}}\nafter\n}}}"
        stored, _ = _round_trip(src)
        self.assertEqual(stored, src)

    def test_plain_inside_plain(self):
        src = "{{{\nouter\n{{{\ninner\n}}}\nafter\n}}}"
        stored, _ = _round_trip(src)
        self.assertEqual(stored, src)

    def test_three_levels_deep(self):
        src = "{{{\na\n{{{\nb\n{{{\nc\n}}}\nd\n}}}\ne\n}}}"
        stored, _ = _round_trip(src)
        self.assertEqual(stored, src)

    def test_ordinary_single_level_code_block_is_unaffected(self):
        src = "{{{#!python\nx = 1\n}}}"
        stored, _ = _round_trip(src)
        self.assertEqual(stored, src)

    def test_fallback_verbatim_fence_is_unaffected(self):
        """Ticket #63's fallback fence carries unrepresentable TracWiki
        verbatim through a placeholder -- must not be touched by removing
        the nested-fence helper, since its body never reaches mistune's
        own code-block parsing."""
        src = "{{{#!td\nnot a real column\n}}}"
        stored, _ = _round_trip(src)
        self.assertEqual(stored, src)
