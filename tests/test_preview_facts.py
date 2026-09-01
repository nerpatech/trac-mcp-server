"""Tests for ``preview.facts``'s ticket #55 extension: ``code_blocks``
and ``prose_text``.

Anchors/code_spans/plain_text (ticket #56) are already covered end-to-end
through ``test_preview_checks.py``'s fixture suite; this file is scoped
to what #55 added. HTML snippets here are hand-written but match shapes
confirmed live against the daemon while building this feature
(2026-09-01) -- see the comments on each shape.
"""

import unittest

from trac_mcp_server.preview.facts import CodeBlock, extract_facts


class TestCodeBlocks(unittest.TestCase):
    def test_highlighted_block_detected(self):
        """Live shape for `{{{#!python ... }}}`: nested `<div
        class="wiki-code"><div class="code"><pre>`."""
        html = (
            '<div class="wiki-code"><div class="code">'
            '<pre><span class="k">def</span></pre>'
            "</div></div>"
        )
        facts = extract_facts(html)
        self.assertEqual(
            facts.code_blocks,
            (CodeBlock(highlighted=True, text="def"),),
        )

    def test_plain_block_detected(self):
        """Live shape for a bare `{{{ }}}`: `<pre class="wiki">`, no
        highlighter wrapper."""
        html = '<pre class="wiki">plain text</pre>'
        facts = extract_facts(html)
        self.assertEqual(
            facts.code_blocks,
            (CodeBlock(highlighted=False, text="plain text"),),
        )

    def test_multiple_blocks_in_document_order(self):
        html = (
            '<div class="wiki-code"><div class="code"><pre>a</pre>'
            "</div></div>"
            '<pre class="wiki">b</pre>'
        )
        facts = extract_facts(html)
        self.assertEqual(
            facts.code_blocks,
            (
                CodeBlock(highlighted=True, text="a"),
                CodeBlock(highlighted=False, text="b"),
            ),
        )

    def test_no_code_blocks_is_empty_tuple(self):
        facts = extract_facts("<p>just prose</p>")
        self.assertEqual(facts.code_blocks, ())


class TestProseText(unittest.TestCase):
    def test_excludes_pre_block_content(self):
        html = "<p>before</p><pre>secret</pre><p>after</p>"
        facts = extract_facts(html)
        self.assertNotIn("secret", facts.prose_text)
        self.assertIn("before", facts.prose_text)
        self.assertIn("after", facts.prose_text)

    def test_excludes_inline_code_span_content(self):
        html = "<p>see <code>|=h=|</code> there</p>"
        facts = extract_facts(html)
        self.assertNotIn("|=h=|", facts.prose_text)
        self.assertIn("see", facts.prose_text)
        self.assertIn("there", facts.prose_text)

    def test_plain_text_still_includes_code_content(self):
        """`plain_text` is the pre-#55 field -- must stay unchanged so
        `convert_preview`'s existing rules keep working."""
        html = "<p>see <code>|=h=|</code> there</p>"
        facts = extract_facts(html)
        self.assertIn("|=h=|", facts.plain_text)

    def test_tail_text_after_removed_element_is_preserved(self):
        """Found live while building this feature: `<code></code>`'s
        TAIL text (the prose right after the closing tag) was silently
        eaten when the element was removed via a plain `parent.remove()`
        -- fixed via `strip_elements(..., with_tail=False)`. This is the
        regression guard."""
        html = (
            "<p>before <code>dropped</code> and this text survives "
            "after</p>"
        )
        facts = extract_facts(html)
        self.assertIn("before", facts.prose_text)
        self.assertIn("and this text survives after", facts.prose_text)
        self.assertNotIn("dropped", facts.prose_text)

    def test_root_level_pre_is_fully_excluded(self):
        """Found live while building this feature: when the ENTIRE
        rendered section is a single `<pre>` (a description/comment
        that's nothing but a `{{{ }}}` block), that `<pre>` parses as
        the document's own root, and `strip_elements` can't remove a
        tree's own root -- fixed by wrapping in `<div>` before parsing
        for `prose_text`. This is the regression guard."""
        html = '<pre class="wiki">some prose |=h=| more prose</pre>'
        facts = extract_facts(html)
        self.assertEqual(facts.prose_text, "")

    def test_empty_input_has_empty_prose_text(self):
        facts = extract_facts("")
        self.assertEqual(facts.prose_text, "")


if __name__ == "__main__":
    unittest.main()
