"""
Tests for Markdown to TracWiki converter and TracWiki to Markdown converter.
"""

import unittest
from unittest.mock import MagicMock, patch

from trac_mcp_server.converters import (
    ConversionResult,
    convert_with_warnings,
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.common import (
    is_link_target,
    markdown_to_tracwiki_lang,
    tracwiki_to_markdown_lang,
)


class TestTracWikiConverter(unittest.TestCase):
    """Test Markdown to TracWiki conversion."""

    def test_heading_level_1(self):
        """Test H1 heading conversion. Trac auto-generates anchors; no explicit slug emitted."""
        result = markdown_to_tracwiki("# Heading 1")
        self.assertEqual(result, "= Heading 1 =")

    def test_heading_level_2(self):
        """Test H2 heading conversion."""
        result = markdown_to_tracwiki("## Heading 2")
        self.assertEqual(result, "== Heading 2 ==")

    def test_heading_level_3(self):
        """Test H3 heading conversion."""
        result = markdown_to_tracwiki("### Heading 3")
        self.assertEqual(result, "=== Heading 3 ===")

    def test_heading_level_4(self):
        """Test H4 heading conversion."""
        result = markdown_to_tracwiki("#### Heading 4")
        self.assertEqual(result, "==== Heading 4 ====")

    def test_bold_text(self):
        """Test bold text conversion."""
        result = markdown_to_tracwiki("**bold text**")
        self.assertEqual(result, "'''bold text'''")

    def test_italic_text(self):
        """Test italic text conversion."""
        result = markdown_to_tracwiki("*italic text*")
        self.assertEqual(result, "''italic text''")

    def test_bold_italic_text(self):
        """Test bold italic text conversion."""
        result = markdown_to_tracwiki("***bold italic***")
        self.assertEqual(result, "'''''bold italic'''''")

    def test_inline_code(self):
        """Test inline code conversion."""
        result = markdown_to_tracwiki("`inline code`")
        self.assertEqual(result, "`inline code`")

    def test_code_block_with_language(self):
        """Test code block with language conversion."""
        markdown = """```python
def hello():
    print("world")
```"""
        expected = """{{{#!python
def hello():
    print("world")
}}}"""
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(result, expected)

    def test_code_block_without_language(self):
        """Test code block without language conversion."""
        markdown = """```
plain code
```"""
        expected = """{{{
plain code
}}}"""
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(result, expected)

    def test_link_conversion(self):
        """Test external link conversion (https)."""
        result = markdown_to_tracwiki(
            "[link text](https://example.com)"
        )
        self.assertEqual(result, "[https://example.com link text]")

    def test_link_conversion_http(self):
        """Test external link conversion (http)."""
        result = markdown_to_tracwiki("[link text](http://example.com)")
        self.assertEqual(result, "[http://example.com link text]")

    def test_link_conversion_ftp(self):
        """Test external link conversion (ftp)."""
        result = markdown_to_tracwiki(
            "[file](ftp://ftp.example.com/file.txt)"
        )
        self.assertEqual(
            result, "[ftp://ftp.example.com/file.txt file]"
        )

    def test_link_conversion_mailto(self):
        """Test mailto link conversion."""
        result = markdown_to_tracwiki(
            "[email](mailto:test@example.com)"
        )
        self.assertEqual(result, "[mailto:test@example.com email]")

    def test_internal_wiki_link(self):
        """Test internal wiki link gets wiki: prefix."""
        result = markdown_to_tracwiki(
            "[Phase 1](Planning/Phases/Phase01)"
        )
        self.assertEqual(
            result, "[wiki:Planning/Phases/Phase01 Phase 1]"
        )

    def test_internal_wiki_link_simple(self):
        """Test simple wiki page link gets wiki: prefix."""
        result = markdown_to_tracwiki("[Home](HomePage)")
        self.assertEqual(result, "[wiki:HomePage Home]")

    def test_internal_wiki_link_relative_parent(self):
        """Test relative wiki link with ../ gets wiki: prefix."""
        result = markdown_to_tracwiki("[Back](../Overview)")
        self.assertEqual(result, "[wiki:../Overview Back]")

    def test_internal_wiki_link_relative_current(self):
        """Test relative wiki link with ./ gets wiki: prefix."""
        result = markdown_to_tracwiki("[Current](./SubPage)")
        self.assertEqual(result, "[wiki:./SubPage Current]")

    def test_anchor_link(self):
        """Test anchor-only link has no prefix."""
        result = markdown_to_tracwiki("[Section](#section)")
        self.assertEqual(result, "[#section Section]")

    def test_non_url_sentinel_link_refused(self):
        """Non-URL-shaped "links" like [text](auto-pm:) must not become wiki links.

        Regression test for ticket #8: the converter previously wrapped
        sentinels such as ``auto-pm:`` as ``[wiki:auto-pm: text]``, which
        TracWiki then rendered as mangled broken-link output. The url
        portion must either contain ``/`` or start with a known scheme
        (http:, https:, mailto:, ftp:); a bare trailing-colon sentinel
        does not qualify and the original Markdown syntax is preserved.
        """
        result = markdown_to_tracwiki("[state NEEDS_CODE](auto-pm:)")
        self.assertEqual(result, "[state NEEDS_CODE](auto-pm:)")
        # Must NOT emit a wiki link
        self.assertNotIn("wiki:", result)

    def test_state_marker_brackets_round_trip(self):
        """auto-pm state marker [auto-pm: state NEEDS_CODE] survives conversion.

        Regression test for ticket #8: state markers using plain square
        brackets must pass through the converter unchanged (mistune does
        not parse these as links, but the test pins the contract).
        """
        result = markdown_to_tracwiki("[auto-pm: state NEEDS_CODE]")
        self.assertEqual(result, "[auto-pm: state NEEDS_CODE]")

    def test_non_url_sentinel_other_shapes(self):
        """Other non-URL-shaped sentinels are also left alone, not wiki-wrapped."""
        # Arbitrary sentinel with trailing colon
        result = markdown_to_tracwiki("[label](sentinel:)")
        self.assertEqual(result, "[label](sentinel:)")
        self.assertNotIn("wiki:", result)
        # Colon-containing non-scheme url with no slash
        result = markdown_to_tracwiki("[label](foo:bar)")
        self.assertEqual(result, "[label](foo:bar)")
        self.assertNotIn("wiki:", result)

    def test_valid_links_still_convert(self):
        """Existing valid links still convert correctly after the refusal fix.

        Regression guard for ticket #8: URL-shaped links (known schemes,
        paths containing /) must continue to produce the expected TracWiki
        output.
        """
        # http/https external links
        self.assertEqual(
            markdown_to_tracwiki("[docs](http://example.com)"),
            "[http://example.com docs]",
        )
        self.assertEqual(
            markdown_to_tracwiki("[docs](https://example.com)"),
            "[https://example.com docs]",
        )
        # mailto link
        self.assertEqual(
            markdown_to_tracwiki("[mail](mailto:x@y)"),
            "[mailto:x@y mail]",
        )
        # Wiki path with /
        self.assertEqual(
            markdown_to_tracwiki("[Phase 1](Planning/Phases/Phase01)"),
            "[wiki:Planning/Phases/Phase01 Phase 1]",
        )
        # Simple wiki page name (no colon, no slash) still treated as wiki
        self.assertEqual(
            markdown_to_tracwiki("[Home](HomePage)"),
            "[wiki:HomePage Home]",
        )

    def test_traclink_url_emitted_verbatim(self):
        """`wiki:`-prefixed link targets convert to working TracWiki links.

        Regression test for ticket #17: ``[Hardware](wiki:BnodeHardware)``
        -- the Markdown form of a native ``[wiki:BnodeHardware Hardware]``
        link -- was left as literal Markdown by the ``":" in url and "/" not in url`` guard, and the
        multi-segment form ``wiki:b-node/blog`` was double-prefixed into
        ``[wiki:wiki:b-node/blog Blog]``. Both must now emit the target
        verbatim.
        """
        self.assertEqual(
            markdown_to_tracwiki("[Hardware](wiki:BnodeHardware)"),
            "[wiki:BnodeHardware Hardware]",
        )
        self.assertEqual(
            markdown_to_tracwiki("[Blog](wiki:b-node/blog)"),
            "[wiki:b-node/blog Blog]",
        )

    def test_traclink_other_resolvers(self):
        """Non-wiki TracLink resolvers survive too, single- and multi-segment.

        Ticket #17: the same guard corrupted every TracLink resolver, not
        just ``wiki:`` -- single-segment targets (``ticket:42``) fell
        through as literal Markdown, multi-segment ones
        (``source:trunk/foo.py``) got a spurious ``wiki:`` prefix.
        """
        for url, text, expected in [
            ("ticket:42", "Bug", "[ticket:42 Bug]"),
            ("milestone:1.0", "M", "[milestone:1.0 M]"),
            ("htdocs:style.css", "css", "[htdocs:style.css css]"),
            ("attachment:file.txt", "att", "[attachment:file.txt att]"),
            (
                "raw-attachment:f.txt",
                "raw",
                "[raw-attachment:f.txt raw]",
            ),
            (
                "source:trunk/foo.py",
                "file",
                "[source:trunk/foo.py file]",
            ),
            ("changeset:abc123", "rev", "[changeset:abc123 rev]"),
        ]:
            with self.subTest(url=url):
                self.assertEqual(
                    markdown_to_tracwiki(f"[{text}]({url})"), expected
                )

    def test_traclink_autolink_form(self):
        """`<wiki:Page>` autolinks emit `[wiki:Page]`, not a doubled target."""
        self.assertEqual(
            markdown_to_tracwiki("<wiki:SomePage>"), "[wiki:SomePage]"
        )

    def test_unknown_scheme_with_slash_left_literal(self):
        """Unknown `scheme:` targets stay literal even when they contain "/".

        Ticket #17 removed the ``"/" not in url`` half of the sentinel
        guard: a wiki page name never contains ":", so anything with a
        colon that is not a known TracLink resolver is a sentinel, slash
        or no slash. Previously ``foo:bar/baz`` became the broken
        ``[wiki:foo:bar/baz label]``.
        """
        self.assertEqual(
            markdown_to_tracwiki("[label](foo:bar/baz)"),
            "[label](foo:bar/baz)",
        )
        self.assertNotIn(
            "wiki:", markdown_to_tracwiki("[label](foo:bar/baz)")
        )

    def test_image_conversion(self):
        """Test image conversion."""
        result = markdown_to_tracwiki("![alt text](image.png)")
        self.assertEqual(result, "[[Image(image.png)]]")

    def test_unordered_list(self):
        """Test unordered list conversion."""
        markdown = """- item 1
- item 2
- item 3"""
        expected = """ * item 1
 * item 2
 * item 3"""
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(result, expected)

    def test_ordered_list(self):
        """Test ordered list conversion."""
        markdown = """1. first
2. second
3. third"""
        expected = """ 1. first
 2. second
 3. third"""
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(result, expected)

    def test_nested_unordered_list(self):
        """Test nested unordered list conversion.

        TracWiki uses indentation for nesting:
        - Level 0: ' * item' (1 space + marker)
        - Level 1: '   * item' (3 spaces + marker)
        """
        markdown = """- item 1
  - nested 1
  - nested 2
- item 2"""
        expected = """ * item 1
   * nested 1
   * nested 2
 * item 2"""
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(result, expected)

    def test_nested_unordered_list_with_bold(self):
        """Test nested unordered list with formatted content (like README Features).

        This specifically tests the bug fix where nested lists were producing
        double asterisks like ' * * item' instead of proper indentation.
        """
        markdown = """- **Ticket Operations**
  - Search and query tickets
  - Read ticket details"""
        expected = """ * '''Ticket Operations'''
   * Search and query tickets
   * Read ticket details"""
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(result, expected)
        # Verify no double asterisks appear
        self.assertNotIn(" * *", result)

    def test_paragraph_tight_against_list_keeps_blank_line_after(self):
        """A paragraph absorbed as a lazy continuation of the last list
        item (no blank line between the list and it in the Markdown
        source) still gets separated by a blank line from whatever real
        paragraph follows it -- otherwise the two paragraphs render as one
        run-together block (ticket #52)."""
        markdown = (
            "- alpha\n"
            "- beta\n"
            "**Bold lead:** first paragraph.\n"
            "\n"
            "Second paragraph.\n"
        )
        result = markdown_to_tracwiki(markdown)
        self.assertIn(
            "'''Bold lead:''' first paragraph.\n\nSecond paragraph.",
            result,
        )

    def test_code_block_followed_by_paragraph_keeps_blank_line(self):
        """A fenced code block ending right before a following paragraph
        (a proper blank line between them in the Markdown source, unlike
        #52's lazy-continuation case) still gets separated by a blank
        line in the TracWiki output -- `block_code()` used to emit only
        one trailing newline regardless of source spacing, merging it
        with whatever block came next (ticket #53)."""
        markdown = "```\nplain code\n```\n\nTail line."
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(result, "{{{\nplain code\n}}}\n\nTail line.")

    def test_code_block_with_lang_followed_by_paragraph_keeps_blank_line(
        self,
    ):
        """Same as above for a language-tagged fence (ticket #53)."""
        markdown = "```python\nx = 1\n```\n\nTail line."
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(
            result, "{{{#!python\nx = 1\n}}}\n\nTail line."
        )

    def test_blockquote_followed_by_paragraph_keeps_blank_line(self):
        """A blockquote ending right before a following paragraph still
        gets separated by a blank line -- `block_quote()` had the same
        single-trailing-newline gap as `block_code()` (ticket #53)."""
        markdown = "> quoted text\n\nTail line."
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(result, "  quoted text\n\nTail line.")

    def test_horizontal_rule_followed_by_paragraph_keeps_blank_line(
        self,
    ):
        """A horizontal rule ending right before a following paragraph
        still gets separated by a blank line -- `thematic_break()` had
        the same gap as `block_code()` (ticket #53)."""
        markdown = "---\n\nTail line."
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(result, "----\n\nTail line.")

    def test_deeply_nested_unordered_list(self):
        """Test deeply nested (3 levels) unordered list conversion.

        TracWiki indentation pattern: (depth * 2 + 1) leading spaces
        - Level 0: ' * item' (1 space)
        - Level 1: '   * item' (3 spaces)
        - Level 2: '     * item' (5 spaces)
        """
        markdown = """- level 0
  - level 1
    - level 2"""
        expected = """ * level 0
   * level 1
     * level 2"""
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(result, expected)

    def test_nested_ordered_list(self):
        """Test nested ordered list conversion with proper indentation."""
        markdown = """1. first
   1. nested first
   2. nested second
2. second"""
        expected = """ 1. first
   1. nested first
   2. nested second
 2. second"""
        result = markdown_to_tracwiki(markdown)
        self.assertEqual(result, expected)

    def test_blockquote(self):
        """Test blockquote conversion."""
        result = markdown_to_tracwiki("> quoted text")
        self.assertEqual(result, "  quoted text")

    def test_horizontal_rule(self):
        """Test horizontal rule conversion."""
        result = markdown_to_tracwiki("---")
        self.assertEqual(result, "----")

    def test_paragraph_separation(self):
        """Test paragraph separation with blank lines."""
        markdown = """First paragraph.

Second paragraph."""
        result = markdown_to_tracwiki(markdown)
        self.assertIn("\n\n", result)

    def test_convert_with_warnings_no_warnings(self):
        """Test conversion without warnings."""
        result = convert_with_warnings("# Simple heading")
        self.assertIsInstance(result, ConversionResult)
        self.assertEqual(result.tracwiki, "= Simple heading =")
        self.assertEqual(len(result.warnings), 0)

    def test_convert_with_warnings_table_converted(self):
        """Test tables are converted without warning (now supported)."""
        markdown = """| Header 1 | Header 2 |
|----------|----------|
| Cell 1   | Cell 2   |"""
        result = convert_with_warnings(markdown)
        # Tables should now be converted without warnings
        self.assertFalse(
            any("table" in w.lower() for w in result.warnings)
        )
        # Output should be valid TracWiki
        self.assertIn("||=Header 1=||=Header 2=||", result.text)
        self.assertIn("||Cell 1||Cell 2||", result.text)

    def test_convert_with_warnings_html_detected(self):
        """Test warning for HTML tags."""
        markdown = "Some text with <div>HTML</div> tags"
        result = convert_with_warnings(markdown)
        self.assertGreater(len(result.warnings), 0)
        self.assertTrue(
            any("html" in w.lower() for w in result.warnings)
        )

    def test_mixed_formatting(self):
        """Test mixed formatting in single line."""
        result = markdown_to_tracwiki(
            "This is **bold** and *italic* text"
        )
        self.assertIn("'''bold'''", result)
        self.assertIn("''italic''", result)

    def test_multiple_headings(self):
        """Test multiple headings in document."""
        markdown = """# Title
## Section
### Subsection"""
        result = markdown_to_tracwiki(markdown)
        self.assertIn("= Title =", result)
        self.assertIn("== Section ==", result)
        self.assertIn("=== Subsection ===", result)


class TestFenceHoldingTracWikiBlocks(unittest.TestCase):
    """A Markdown fence whose body holds ``{{{ }}}`` delimiters."""

    def test_nested_code_block_in_a_fence_is_written_as_nested_tracwiki(
        self,
    ):
        """A fence whose body holds a nested ``{{{ }}}`` block converts to
        the outer block with the inner delimiters as literal content (ticket
        #51 retest -- a same-length outer/inner fence pair was flattened to
        raw content with the block structure destroyed).

        No trailing paragraph after the block: markdown_to_tracwiki drops
        the blank line between a code block and a following paragraph
        regardless of nesting (a pre-existing, separately-scoped gap --
        reproduces the same way for an unnested block on current master),
        so asserting on that spacing here would conflate two bugs.
        """
        markdown = """Intro line.

```
{{{#!comment
AGENT: do the thing
}}}
```"""
        expected = """Intro line.

{{{
{{{#!comment
AGENT: do the thing
}}}
}}}"""
        restored = markdown_to_tracwiki(markdown)
        self.assertNotIn("\x00", restored)
        self.assertEqual(restored, expected)


class TestFormatDetection(unittest.TestCase):
    """Test format detection heuristics."""

    def test_detect_format_tracwiki_heading(self):
        """Test TracWiki heading with trailing equals is detected."""
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        self.assertEqual(
            detect_format_heuristic("= Heading ="), "tracwiki"
        )

    def test_detect_format_tracwiki_bold(self):
        """Test TracWiki bold syntax is detected."""
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        self.assertEqual(
            detect_format_heuristic("'''bold'''"), "tracwiki"
        )

    def test_detect_format_markdown_heading(self):
        """Test Markdown heading without trailing equals is detected."""
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        self.assertEqual(
            detect_format_heuristic("# Heading"), "markdown"
        )

    def test_detect_format_markdown_bold(self):
        """Test Markdown bold syntax is detected."""
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        self.assertEqual(
            detect_format_heuristic("**bold**"), "markdown"
        )

    def test_detect_format_ambiguous_defaults_tracwiki(self):
        """Test ambiguous text defaults to TracWiki."""
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        self.assertEqual(
            detect_format_heuristic("plain text"), "tracwiki"
        )


class TestBlankCodeFences(unittest.TestCase):
    """`blank_code_fences` (ticket #59) -- the offset-preserving
    counterpart to `_strip_code_fences`.

    Its whole reason to exist is that a caller scans the blanked copy
    and then indexes back into the ORIGINAL, so length preservation is
    the contract, not an implementation detail.
    """

    def _blank(self, text):
        from trac_mcp_server.converters.common import blank_code_fences

        return blank_code_fences(text)

    def test_length_is_preserved(self):
        """The contract: same length in, same length out, so a match
        offset taken on the blanked copy is valid on the original."""
        text = "before\n{{{\nauto_pm:#87\n}}}\nafter\n"
        self.assertEqual(len(self._blank(text)), len(text))

    def test_fenced_content_is_blanked_and_prose_survives(self):
        text = "before\n{{{\nauto_pm:#87\n}}}\nafter"
        blanked = self._blank(text)
        self.assertNotIn("auto_pm:#87", blanked)
        self.assertIn("before", blanked)
        self.assertIn("after", blanked)

    def test_markdown_fence_is_blanked_too(self):
        text = "before\n```\nauto_pm:#87\n```\nafter"
        blanked = self._blank(text)
        self.assertNotIn("auto_pm:#87", blanked)
        self.assertIn("after", blanked)

    def test_same_line_fence_closes(self):
        """The deliberate divergence from `_strip_code_fences`, which
        never closes a fence opened and closed on one line and so treats
        the whole rest of the document as fence interior. Harmless for
        its callers, a false-negative generator for this one: everything
        after such a line would stop being checked at all."""
        text = "{{{ inline }}}\nauto_pm:#87 after the fence"
        blanked = self._blank(text)
        self.assertIn("auto_pm:#87 after the fence", blanked)

    def test_nested_fence_depth_is_tracked(self):
        text = (
            "{{{\nouter\n{{{\ninner\n}}}\nstill inside\n}}}\n"
            "auto_pm:#87 after"
        )
        blanked = self._blank(text)
        self.assertNotIn("still inside", blanked)
        self.assertIn("auto_pm:#87 after", blanked)

    def test_unterminated_fence_blanks_to_end(self):
        """An unclosed fence swallows the rest -- the same choice
        `_strip_code_fences` makes. Recorded as intended, not accidental:
        the alternative (treating an unclosed fence as prose) would put
        this check back inside a block Trac renders verbatim."""
        text = "{{{\nauto_pm:#87 never closed"
        self.assertNotIn("auto_pm:#87", self._blank(text))


class TestBlankInlineCodeSpans(unittest.TestCase):
    """`blank_inline_code_spans` (ticket #65) -- the companion to
    `blank_code_fences` for the other way of marking content literal.

    Same length-preserving contract, and the same reason: markup inside
    backticks is quoted ON PURPOSE and Trac renders it as literal text,
    which is precisely the outcome `tracwiki_markup_in_markdown` exists
    to warn about -- so warning about it is self-defeating.
    """

    def _blank(self, text):
        from trac_mcp_server.converters.common import (
            blank_inline_code_spans,
        )

        return blank_inline_code_spans(text)

    def test_length_is_preserved(self):
        text = "See `{{{#!python}}}` for the processor form."
        self.assertEqual(len(self._blank(text)), len(text))

    def test_span_content_is_blanked_and_prose_survives(self):
        text = "Quoted `'''alpha'''` and a genuine '''beta''' one."
        blanked = self._blank(text)
        self.assertNotIn("alpha", blanked)
        self.assertIn("beta", blanked)
        self.assertIn("Quoted", blanked)

    def test_multi_backtick_span_needs_a_matching_run(self):
        """A double-backtick span closes on a double run, not on the
        single tick it legitimately contains."""
        text = "a ``x ` y`` b"
        blanked = self._blank(text)
        self.assertNotIn("x", blanked)
        self.assertIn("b", blanked)

    def test_stray_backtick_blanks_nothing(self):
        """An unmatched backtick is ordinary in prose. Treating it as an
        opener would blank an arbitrary stretch of the document, and
        over-blanking silences real defects while every test stays
        green -- the dangerous direction, so it is pinned."""
        text = "a ` stray tick and '''bold''' after it"
        self.assertEqual(self._blank(text), text)

    def test_a_span_does_not_run_across_lines(self):
        """Same reason as the stray tick: a backtick on one line and
        another paragraphs later must not blank everything between."""
        text = "open ` here\n\nand '''bold''' ` much later"
        blanked = self._blank(text)
        self.assertIn("'''bold'''", blanked)

    def test_newlines_are_preserved_not_just_length(self):
        text = "a `x` b\nc `y` d\n"
        blanked = self._blank(text)
        self.assertEqual(len(blanked), len(text))
        self.assertEqual(blanked.count("\n"), text.count("\n"))


class TestConversionResultMetadata(unittest.TestCase):
    """Test ConversionResult metadata and backward compatibility."""

    def test_conversion_result_backward_compat(self):
        """Test backward compatibility with old .tracwiki property."""
        from trac_mcp_server.converters import convert_with_warnings

        result = convert_with_warnings("# Test")
        # Old code expects .tracwiki property
        self.assertTrue(hasattr(result, "tracwiki"))
        self.assertEqual(result.text, result.tracwiki)


class TestTableConversion(unittest.TestCase):
    """Test bidirectional table conversion between Markdown and TracWiki."""

    # Markdown to TracWiki tests

    def test_md_to_tw_basic_table(self):
        """Test basic Markdown table converts to TracWiki."""
        md = """| A | B |
| --- | --- |
| 1 | 2 |"""
        result = markdown_to_tracwiki(md)
        self.assertIn("||=A=||=B=||", result)
        self.assertIn("||1||2||", result)

    def test_md_to_tw_table_alignment(self):
        """Test Markdown alignment converts to TracWiki whitespace."""
        md = """| Left | Center | Right |
| :--- | :---: | ---: |
| L | C | R |"""
        result = markdown_to_tracwiki(md)
        # Left aligned header: =text =
        self.assertIn("=Left =", result)
        # Center aligned header: = text =
        self.assertIn("= Center =", result)
        # Right aligned header: = text=
        self.assertIn("= Right=", result)
        # Body cells should have alignment too
        self.assertIn("||L ||", result)  # left
        self.assertIn("|| C ||", result)  # center
        self.assertIn("|| R||", result)  # right

    def test_md_to_tw_empty_cells(self):
        """Test empty cells in Markdown table.

        An empty cell renders as a lone space, not a bare "||" run --
        "||||" is TracWiki's colspan-2 marker, not two empty cells, and
        would merge the empty column into its neighbor and shift every
        following header/cell left by one (ticket #20).
        """
        md = """| A | | C |
| --- | --- | --- |
| 1 | | 3 |"""
        result = markdown_to_tracwiki(md)
        self.assertIn("||=A=|| ||=C=||", result)
        self.assertIn("||1|| ||3||", result)

    def test_md_to_tw_single_column(self):
        """Test single column Markdown table."""
        md = """| Only |
| --- |
| A |
| B |"""
        result = markdown_to_tracwiki(md)
        self.assertIn("||=Only=||", result)
        self.assertIn("||A||", result)
        self.assertIn("||B||", result)

    def test_md_to_tw_formatted_content(self):
        """Test Markdown table with formatted content."""
        md = """| **Bold** | *Italic* |
| --- | --- |
| `code` | plain |"""
        result = markdown_to_tracwiki(md)
        self.assertIn("'''Bold'''", result)
        self.assertIn("''Italic''", result)
        self.assertIn("`code`", result)


class TestMacroInProse(unittest.TestCase):
    """A ``[[Macro]]`` typed in Markdown prose."""

    def test_macro_in_prose_is_left_literal(self):
        """A macro in a paragraph comes out byte-for-byte, with no ``!``
        escape inside the brackets and no ``[MACRO: ...]`` placeholder (#63).
        """
        self.assertEqual(
            markdown_to_tracwiki("Text [[PageOutline]] more"),
            "Text [[PageOutline]] more",
        )


class TestCodeBlockLanguageMapping(unittest.TestCase):
    """Test bidirectional language mapping for code blocks."""

    # ==========================================================================
    # Direct lookup tests: markdown_to_tracwiki_lang
    # ==========================================================================

    def test_md_to_tw_lang_bash_to_sh(self):
        """Test bash maps to sh."""
        self.assertEqual(markdown_to_tracwiki_lang("bash"), "sh")

    def test_md_to_tw_lang_shell_to_sh(self):
        """Test shell maps to sh."""
        self.assertEqual(markdown_to_tracwiki_lang("shell"), "sh")

    def test_md_to_tw_lang_zsh_to_sh(self):
        """Test zsh maps to sh."""
        self.assertEqual(markdown_to_tracwiki_lang("zsh"), "sh")

    def test_md_to_tw_lang_js_to_javascript(self):
        """Test js maps to javascript."""
        self.assertEqual(markdown_to_tracwiki_lang("js"), "javascript")

    def test_md_to_tw_lang_ts_to_typescript(self):
        """Test ts maps to typescript."""
        self.assertEqual(markdown_to_tracwiki_lang("ts"), "typescript")

    def test_md_to_tw_lang_cpp_variants(self):
        """Test c++ maps to cpp."""
        self.assertEqual(markdown_to_tracwiki_lang("c++"), "cpp")

    def test_md_to_tw_lang_plaintext_variants(self):
        """Test plaintext/plain/text all map to text."""
        self.assertEqual(markdown_to_tracwiki_lang("plaintext"), "text")
        self.assertEqual(markdown_to_tracwiki_lang("plain"), "text")
        self.assertEqual(markdown_to_tracwiki_lang("text"), "text")

    def test_md_to_tw_lang_identity_python(self):
        """Test python passes through unchanged."""
        self.assertEqual(markdown_to_tracwiki_lang("python"), "python")

    def test_md_to_tw_lang_identity_languages(self):
        """Test common identity languages pass through unchanged."""
        identity_langs = [
            "java",
            "c",
            "ruby",
            "go",
            "rust",
            "sql",
            "html",
            "css",
            "xml",
            "json",
            "yaml",
            "diff",
        ]
        for lang in identity_langs:
            self.assertEqual(markdown_to_tracwiki_lang(lang), lang)

    def test_md_to_tw_lang_unknown_passthrough(self):
        """Test unknown languages pass through unchanged."""
        self.assertEqual(
            markdown_to_tracwiki_lang("obscurelang"), "obscurelang"
        )
        self.assertEqual(
            markdown_to_tracwiki_lang("myspeciallang"), "myspeciallang"
        )

    def test_md_to_tw_lang_case_insensitive(self):
        """Test mapping is case-insensitive."""
        self.assertEqual(markdown_to_tracwiki_lang("BASH"), "sh")
        self.assertEqual(markdown_to_tracwiki_lang("Bash"), "sh")
        self.assertEqual(markdown_to_tracwiki_lang("JS"), "javascript")

    # ==========================================================================
    # Direct lookup tests: tracwiki_to_markdown_lang
    # ==========================================================================

    def test_tw_to_md_lang_sh_to_bash(self):
        """Test sh maps to bash (canonical form)."""
        self.assertEqual(tracwiki_to_markdown_lang("sh"), "bash")

    def test_tw_to_md_lang_identity_python(self):
        """Test python passes through unchanged."""
        self.assertEqual(tracwiki_to_markdown_lang("python"), "python")

    def test_tw_to_md_lang_identity_languages(self):
        """Test common identity languages pass through unchanged."""
        identity_langs = [
            "java",
            "c",
            "ruby",
            "go",
            "rust",
            "sql",
            "html",
            "css",
            "xml",
            "json",
            "yaml",
            "diff",
            "javascript",
            "typescript",
            "cpp",
        ]
        for lang in identity_langs:
            self.assertEqual(tracwiki_to_markdown_lang(lang), lang)

    def test_tw_to_md_lang_unknown_passthrough(self):
        """Test unknown processors pass through unchanged."""
        self.assertEqual(
            tracwiki_to_markdown_lang("obscurelang"), "obscurelang"
        )
        self.assertEqual(
            tracwiki_to_markdown_lang("custom_proc"), "custom_proc"
        )

    def test_tw_to_md_lang_case_insensitive(self):
        """Test mapping is case-insensitive."""
        self.assertEqual(tracwiki_to_markdown_lang("SH"), "bash")
        self.assertEqual(tracwiki_to_markdown_lang("Sh"), "bash")

    # ==========================================================================
    # Integration tests: full code block conversion with language mapping
    # ==========================================================================

    def test_md_to_tw_code_block_bash(self):
        """Test Markdown bash code block converts to TracWiki sh."""
        md = """```bash
echo "hello"
```"""
        result = markdown_to_tracwiki(md)
        self.assertIn("{{{#!sh", result)
        self.assertIn('echo "hello"', result)

    def test_md_to_tw_code_block_shell(self):
        """Test Markdown shell code block converts to TracWiki sh."""
        md = """```shell
ls -la
```"""
        result = markdown_to_tracwiki(md)
        self.assertIn("{{{#!sh", result)

    def test_md_to_tw_code_block_js(self):
        """Test Markdown js code block converts to TracWiki javascript."""
        md = """```js
console.log("hello");
```"""
        result = markdown_to_tracwiki(md)
        self.assertIn("{{{#!javascript", result)

    def test_md_to_tw_code_block_python_unchanged(self):
        """Test Markdown python code block stays python in TracWiki."""
        md = """```python
print("hello")
```"""
        result = markdown_to_tracwiki(md)
        self.assertIn("{{{#!python", result)

    # ==========================================================================
    # Round-trip tests: verify asymmetric mappings work correctly
    # ==========================================================================


class TestAutoConvert(unittest.TestCase):
    """Tests for auto_convert() — automatic format conversion with capability detection."""

    def _run(self, coro):
        """Helper to run async coroutine in sync test."""
        import asyncio

        return asyncio.run(coro)

    @patch(
        "trac_mcp_server.detection.capabilities.get_server_capabilities"
    )
    def test_explicit_tracwiki_target(self, mock_caps):
        """target_format='tracwiki' with markdown input converts to tracwiki."""
        from trac_mcp_server.converters.common import auto_convert

        mock_config = MagicMock()
        result = self._run(
            auto_convert(
                "# Heading\n\nParagraph",
                mock_config,
                target_format="tracwiki",
            )
        )

        self.assertTrue(result.converted)
        self.assertEqual(result.target_format, "tracwiki")
        self.assertIn("= Heading =", result.text)
        # Capabilities should not be queried when target is explicit
        mock_caps.assert_not_called()

    @patch(
        "trac_mcp_server.detection.capabilities.get_server_capabilities"
    )
    def test_same_format_passthrough_markdown(self, _mock_caps):
        """target_format='markdown' with markdown input — no conversion."""
        from trac_mcp_server.converters.common import auto_convert

        mock_config = MagicMock()
        text = "# Markdown Heading\n\nSome **bold** text"
        result = self._run(
            auto_convert(text, mock_config, target_format="markdown")
        )

        self.assertFalse(result.converted)
        self.assertEqual(result.text, text)

    @patch(
        "trac_mcp_server.detection.capabilities.get_server_capabilities"
    )
    def test_auto_detect_with_markdown_processor(self, mock_caps):
        """target_format=None, server has markdown processor — target becomes 'markdown'."""
        from trac_mcp_server.converters.common import auto_convert

        mock_config = MagicMock()
        mock_caps_result = MagicMock()
        mock_caps_result.markdown_processor = True

        async def fake_caps(_config):
            return mock_caps_result

        mock_caps.side_effect = fake_caps

        # Markdown input already is the target format: passed through.
        result = self._run(
            auto_convert(
                "# Heading\n\nText", mock_config, target_format=None
            )
        )

        self.assertEqual(result.target_format, "markdown")
        self.assertFalse(result.converted)
        mock_caps.assert_called_once_with(mock_config)

    @patch(
        "trac_mcp_server.detection.capabilities.get_server_capabilities"
    )
    def test_auto_detect_without_markdown_processor(self, mock_caps):
        """target_format=None, no markdown processor — target becomes 'tracwiki'."""
        from trac_mcp_server.converters.common import auto_convert

        mock_config = MagicMock()
        mock_caps_result = MagicMock()
        mock_caps_result.markdown_processor = False

        async def fake_caps(_config):
            return mock_caps_result

        mock_caps.side_effect = fake_caps

        # Markdown input should be converted to tracwiki
        result = self._run(
            auto_convert(
                "# Heading\n\nText", mock_config, target_format=None
            )
        )

        self.assertEqual(result.target_format, "tracwiki")

    @patch(
        "trac_mcp_server.detection.capabilities.get_server_capabilities"
    )
    def test_capability_detection_failure_defaults_tracwiki(
        self, mock_caps
    ):
        """target_format=None, capability detection raises — defaults to 'tracwiki'."""
        from trac_mcp_server.converters.common import auto_convert

        mock_config = MagicMock()

        async def raise_error(_config):
            raise ConnectionError("Cannot reach server")

        mock_caps.side_effect = raise_error

        result = self._run(
            auto_convert(
                "# Heading\n\nText", mock_config, target_format=None
            )
        )

        self.assertEqual(result.target_format, "tracwiki")
        self.assertTrue(result.converted)

    @patch(
        "trac_mcp_server.detection.capabilities.get_server_capabilities"
    )
    def test_same_format_passthrough_tracwiki(self, _mock_caps):
        """target_format='tracwiki' with tracwiki input — no conversion."""
        from trac_mcp_server.converters.common import auto_convert

        mock_config = MagicMock()
        text = "= TracWiki Heading =\n\n'''bold'''"
        result = self._run(
            auto_convert(text, mock_config, target_format="tracwiki")
        )

        self.assertFalse(result.converted)
        self.assertEqual(result.text, text)

    @patch(
        "trac_mcp_server.detection.capabilities.get_server_capabilities"
    )
    def test_explicit_source_format_overrides_heuristic(
        self, _mock_caps
    ):
        """source_format='markdown' is honored as the canonical answer.

        Regression test: a Markdown file describing the converter itself
        contained TracWiki examples (``= Heading =``) inside fenced code
        blocks. Even after the heuristic was hardened to redact code-fence
        interiors before scanning, callers that already know the source
        format (e.g. from a ``.md`` extension) MUST be able to bypass
        detection entirely — the explicit signal is more authoritative
        than any heuristic could be.
        """
        from trac_mcp_server.converters.common import auto_convert

        mock_config = MagicMock()
        text = (
            "# Format Conversion\n"
            "\n"
            "Some prose.\n"
            "\n"
            "```\n"
            "TracWiki:  = Heading 1 =\n"
            "TracWiki:  == Heading 2 ==\n"
            "```\n"
        )

        # With source_format='markdown', conversion must run regardless of
        # what the heuristic would have decided.
        result = self._run(
            auto_convert(
                text,
                mock_config,
                target_format="tracwiki",
                source_format="markdown",
            )
        )
        self.assertTrue(result.converted)
        self.assertEqual(result.source_format, "markdown")
        self.assertEqual(result.target_format, "tracwiki")
        # The top-level Markdown heading must be converted to TracWiki form.
        self.assertIn("= Format Conversion =", result.text)

    @patch(
        "trac_mcp_server.detection.capabilities.get_server_capabilities"
    )
    def test_returns_conversion_result(self, _mock_caps):
        """Return type is ConversionResult with all expected fields."""
        from trac_mcp_server.converters.common import auto_convert

        mock_config = MagicMock()
        result = self._run(
            auto_convert(
                "# Test", mock_config, target_format="tracwiki"
            )
        )

        self.assertIsInstance(result, ConversionResult)
        self.assertIsNotNone(result.text)
        self.assertIsNotNone(result.source_format)
        self.assertIsNotNone(result.target_format)
        self.assertIsInstance(result.converted, bool)
        self.assertIsInstance(result.warnings, list)


class TestHeadingExplicitAnchor(unittest.TestCase):
    """Tests for the explicit-anchor emission in heading conversion.

    Regression: Markdown source links using GitHub-style slugs
    (``[Surfaces](#surfaces)``) did not resolve after conversion
    because Trac's auto-generated heading id strips whitespace +
    punctuation but DOES NOT lowercase. The converter now emits
    an explicit Markdown-style slug as the heading's TracWiki
    anchor (``== Surfaces == #surfaces``).
    """

    def test_simple_heading_emits_lowercase_slug(self):
        """Single-word heading emits a lowercase slug anchor (heading_anchors=True)."""
        self.assertEqual(
            markdown_to_tracwiki("## Surfaces", heading_anchors=True),
            "== Surfaces == #surfaces",
        )

    def test_multi_word_heading_uses_dash_separator(self):
        """Whitespace runs collapse to a single dash (heading_anchors=True)."""
        self.assertEqual(
            markdown_to_tracwiki(
                "## Wiki task index page schema", heading_anchors=True
            ),
            "== Wiki task index page schema == #wiki-task-index-page-schema",
        )

    def test_heading_strips_inline_code_from_slug(self):
        r"""Inline code (``\`backticks\``) is stripped from the slug
        but preserved in the visible heading text.

        "EvalRef" is CamelCase-shaped, so the visible heading text carries
        the defensive "!" prefix `text()` adds for any such word (ticket
        #27) -- Trac's heading syntax runs through the same WikiFormatting
        engine as body prose, so it's just as auto-link-prone. The slug
        itself is unaffected: `_heading_slug` strips non-word/dash/space
        characters, "!" included, before slugifying.
        """
        self.assertEqual(
            markdown_to_tracwiki(
                "## EvalRef marker fields (`ticket-comment`)",
                heading_anchors=True,
            ),
            "== !EvalRef marker fields (`ticket-comment`) == "
            "#evalref-marker-fields-ticket-comment",
        )

    def test_heading_drops_em_dash_and_punctuation_from_slug(self):
        """Em-dash, commas, and parens drop out of the slug.

        Mirrors the source pattern in eval-payload-spec.md:
        ``### Example 1 — Initial round, judge picks one model``.
        """
        result = markdown_to_tracwiki(
            "### Example 1 — Initial round, judge picks one model",
            heading_anchors=True,
        )
        # Em-dash + comma drop; whitespace runs (incl. the gap left by the
        # dropped em-dash) collapse to single dashes.
        self.assertIn(
            "#example-1-initial-round-judge-picks-one-model",
            result,
        )

    def test_heading_preserves_underscore_in_slug(self):
        """Underscores in identifiers are part of the slug (heading_anchors=True)."""
        self.assertEqual(
            markdown_to_tracwiki(
                "## cheapest_adequate field", heading_anchors=True
            ),
            "== cheapest_adequate field == #cheapest_adequate-field",
        )

    def test_heading_with_inline_bold_strips_markers_from_slug(self):
        """``## **bold** heading`` slug drops the bold markers (heading_anchors=True)."""
        self.assertEqual(
            markdown_to_tracwiki(
                "## **bold** heading", heading_anchors=True
            ),
            "== '''bold''' heading == #bold-heading",
        )

    def test_anchor_link_resolves_to_emitted_heading_slug(self):
        """End-to-end: a Markdown anchor link's slug matches the slug
        the converter emits for the corresponding heading (heading_anchors=True).
        """
        source = (
            "## Field reference\n"
            "\n"
            "See [Field reference](#field-reference) above.\n"
        )
        result = markdown_to_tracwiki(source, heading_anchors=True)
        self.assertIn("== Field reference == #field-reference", result)
        self.assertIn("[#field-reference Field reference]", result)

    def test_heading_with_no_alphanumerics_omits_anchor(self):
        """Punctuation-only heading text emits NO explicit anchor —
        Trac's default id handling applies.
        """
        # ``# ---`` would lex as a thematic break before reaching the
        # heading rule; use punctuation that survives parsing.
        result = markdown_to_tracwiki("## ...")
        self.assertNotIn("#", result.replace("== ... ==", ""))


class TestHeadingAnchorsOption(unittest.TestCase):
    """Tests for the heading_anchors kwarg added in Phase 16.

    Verifies that the default True preserves existing anchor behavior
    and that False omits slugs without touching any other output.
    """

    def test_default_omits_anchor(self):
        """Default (heading_anchors=False) emits plain heading without #slug."""
        result = markdown_to_tracwiki("# Hello")
        self.assertEqual(result, "= Hello =")

    def test_off_omits_anchor(self):
        """heading_anchors=False emits plain heading with no slug suffix."""
        result = markdown_to_tracwiki("# Hello", heading_anchors=False)
        self.assertEqual(result, "= Hello =")

    def test_off_omits_anchor_h2(self):
        """heading_anchors=False works for H2."""
        result = markdown_to_tracwiki(
            "## Section", heading_anchors=False
        )
        self.assertEqual(result, "== Section ==")

    def test_off_omits_anchor_h3(self):
        """heading_anchors=False works for H3."""
        result = markdown_to_tracwiki("### Sub", heading_anchors=False)
        self.assertEqual(result, "=== Sub ===")

    def test_off_omits_anchor_multi_level(self):
        """heading_anchors=False applies to all heading levels in one doc."""
        source = "# Top\n\n## Middle\n\n### Bottom\n"
        result = markdown_to_tracwiki(source, heading_anchors=False)
        self.assertIn("= Top =", result)
        self.assertIn("== Middle ==", result)
        self.assertIn("=== Bottom ===", result)
        self.assertNotIn("#top", result)
        self.assertNotIn("#middle", result)
        self.assertNotIn("#bottom", result)

    def test_convert_with_warnings_forwards_option(self):
        """convert_with_warnings forwards heading_anchors=False correctly."""
        from trac_mcp_server.converters.markdown_to_tracwiki import (
            convert_with_warnings,
        )

        result = convert_with_warnings("# X", heading_anchors=False)
        self.assertEqual(result.text, "= X =")

    def test_off_matches_default(self):
        """Explicit heading_anchors=False produces identical output to the default."""
        default = markdown_to_tracwiki("## Overview")
        explicit = markdown_to_tracwiki(
            "## Overview", heading_anchors=False
        )
        self.assertEqual(default, explicit)


class TestMarkerPoorMarkdownNeedsAnExplicitDeclaration(
    unittest.TestCase
):
    """Ticket #47's pin, re-anchored by ticket #69.

    #47's defect was that a marker-poor Markdown document -- a bare table
    with no heading, bold, fence or link -- scores as TracWiki and is
    stored unconverted. It was pinned on wiki_create/wiki_update, which
    fixed it by passing ``source_format="markdown"`` explicitly instead
    of letting ``auto_convert`` re-detect.

    Ticket #69 removed the Markdown path from those tools, so there is no
    write handler left to assert it on. The heuristic itself did not go
    anywhere: ``auto_convert`` still calls it whenever ``source_format``
    is None, and ``wiki_file_push`` and the ``trac-convert`` binary still
    reach it. So the row moves here rather than being deleted -- it is
    re-anchored, not relaxed, and the misclassification below is still
    the live behaviour that makes declaring necessary.
    """

    # The exact shape ticket #47 measured.
    MARKER_POOR = "| a | b |\n|---|---|\n| 1 | 2 |"

    def test_marker_poor_markdown_is_still_misclassified(self):
        """The defect, unfixed and unfixable by heuristic. Asserted so
        that a future change to the scoring is a deliberate one.
        """
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        self.assertEqual(
            detect_format_heuristic(self.MARKER_POOR), "tracwiki"
        )

    def test_auto_convert_left_to_detect_stores_it_unconverted(self):
        """No declaration, so the heuristic decides -- and getting it
        wrong means source == target, which auto_convert passes through
        untouched. This is #47's symptom end to end.
        """
        import asyncio

        from trac_mcp_server.config import Config
        from trac_mcp_server.converters.common import auto_convert

        config = Config(
            trac_url="http://test", username="u", password="p"
        )
        result = asyncio.run(
            auto_convert(
                self.MARKER_POOR, config, target_format="tracwiki"
            )
        )
        self.assertFalse(result.converted)
        self.assertEqual(result.text, self.MARKER_POOR)

    def test_declaring_markdown_converts_it(self):
        """The fix #47 landed, on the API that still offers the choice."""
        import asyncio

        from trac_mcp_server.config import Config
        from trac_mcp_server.converters.common import auto_convert

        config = Config(
            trac_url="http://test", username="u", password="p"
        )
        result = asyncio.run(
            auto_convert(
                self.MARKER_POOR,
                config,
                target_format="tracwiki",
                source_format="markdown",
            )
        )
        self.assertTrue(result.converted)
        self.assertNotEqual(result.text, self.MARKER_POOR)
        self.assertIn("||", result.text)


class TestDetectFormatHeuristicFenceAware(unittest.TestCase):
    """Tests for detect_format_heuristic() — fence redaction + line-anchored heading match.

    Regression: a Markdown source that embeds TracWiki examples in fenced
    code blocks must still be classified as Markdown. The previous
    heuristic used an unanchored regex over the full text and matched
    inside fences, inverting the verdict on bait-laden inputs.
    """

    def test_markdown_with_tracwiki_example_in_fence_is_markdown(self):
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        text = (
            "# Markdown Heading\n"
            "\n"
            "Here is a TracWiki example block:\n"
            "\n"
            "```\n"
            "= TracWiki Heading inside fence =\n"
            "```\n"
            "\n"
            "Followed by more **markdown** prose.\n"
        )
        self.assertEqual(detect_format_heuristic(text), "markdown")

    def test_tracwiki_with_markdown_example_in_fence_is_tracwiki(self):
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        text = (
            "= TracWiki Heading =\n"
            "\n"
            "Here is a Markdown example block:\n"
            "\n"
            "{{{\n"
            "# Markdown heading inside fence\n"
            "}}}\n"
            "\n"
            "Followed by more '''tracwiki''' prose.\n"
        )
        self.assertEqual(detect_format_heuristic(text), "tracwiki")

    def test_inline_equals_in_prose_is_not_a_tracwiki_heading(self):
        """``key = value = result`` inline prose must not be mistaken for
        a TracWiki heading. The fixed regex anchors to line start AND end.
        """
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        text = (
            "# Markdown Heading\n"
            "\n"
            "Configuration syntax: ``foo = bar = baz`` and ``a=1, b=2``.\n"
            "\n"
            "More markdown prose.\n"
        )
        self.assertEqual(detect_format_heuristic(text), "markdown")

    def test_clean_tracwiki_still_detected(self):
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        text = "= H1 =\n\n== H2 ==\n\nBody with '''bold'''.\n"
        self.assertEqual(detect_format_heuristic(text), "tracwiki")

    def test_clean_markdown_still_detected(self):
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        text = "# H1\n\n## H2\n\nBody with **bold**.\n"
        self.assertEqual(detect_format_heuristic(text), "markdown")

    def test_eval_payload_spec_real_world_pattern(self):
        """End-to-end: the exact pattern that caused the production
        regression on docs/reference/eval-payload-spec.md — Markdown
        spec containing TracWiki output examples in fenced blocks.
        """
        from trac_mcp_server.converters.common import (
            detect_format_heuristic,
        )

        text = (
            "# Eval Payload Spec\n"
            "\n"
            "This document specifies the wire format.\n"
            "\n"
            "```\n"
            "= Evals: AuditDocs =\n"
            "\n"
            "== Frontier ==\n"
            "\n"
            "|| Model || Score ||\n"
            "```\n"
            "\n"
            "## Field reference\n"
            "\n"
            "Each field maps to a column in evals/schema.py.\n"
        )
        self.assertEqual(detect_format_heuristic(text), "markdown")


class TestConverterTicketRegressions(unittest.TestCase):
    """Regression tests for specific numbered tickets against the
    markdown_to_tracwiki converter, kept together since they were all
    found and fixed in the same sweep.
    """

    def test_ticket_19_macro_is_left_literal(self):
        """A macro typed in Markdown is not flattened or escaped.

        This is ticket #19's actual guarantee, carried since ticket #63 by
        the bracket-stashing pass: the macro comes out as typed. The
        guarantee is what the test pins -- asserting a placeholder spelling
        would only have restated the implementation.
        """
        for macro in ("[[PageOutline]]", "[[TOC(depth=2)]]"):
            with self.subTest(macro):
                self.assertEqual(markdown_to_tracwiki(macro), macro)

    def test_ticket_19_literal_bracket_syntax_survives(self):
        """[[Page]] typed directly in Markdown source passes through
        unchanged rather than being corrupted by the CamelCase escaping
        pass (ticket #27) meant for plain prose.
        """
        self.assertEqual(
            markdown_to_tracwiki("[[SomePage]]"), "[[SomePage]]"
        )

    def test_ticket_20_empty_leading_header_cell(self):
        """A leading empty header cell doesn't collapse into TracWiki's
        "||||" colspan-2 marker, which would misalign every following
        header by one column.
        """
        md = (
            "| | Clip lead | Ground spring |\n"
            "|---|---|---|\n"
            "| 5 V undershoot | -2.40 V | -0.56 V |"
        )
        result = markdown_to_tracwiki(md)
        self.assertIn("|| ||=Clip lead=||=Ground spring=||", result)

    def test_ticket_27_camelcase_prose_escaped(self):
        """Plain-prose CamelCase-shaped words get a defensive "!" prefix
        so Trac's WikiFormatting doesn't auto-link them into broken
        missing-page links.
        """
        result = markdown_to_tracwiki(
            "WiFi credentials live in `.env`. The LoRa wire format is "
            "versioned separately."
        )
        self.assertIn("!WiFi", result)
        self.assertIn("!LoRa", result)
        # Inside a code span, WiFi/LoRa-shaped text must NOT be escaped --
        # Trac's WikiFormatting doesn't touch {{{ }}} / `` `` content.
        result = markdown_to_tracwiki("`WiFiConfig` is a struct.")
        self.assertIn("`WiFiConfig`", result)

    def test_ticket_27_camelcase_not_escaped_in_link_text(self):
        """A link's label is opaque to Trac's WikiFormatting, so a
        CamelCase-shaped word there must not get escaped -- doing so
        would put a stray "!" in the visible link text and could break
        structural comparisons (e.g. autolink detection).
        """
        self.assertEqual(
            markdown_to_tracwiki("[SomePage](wiki:SomePage)"),
            "[wiki:SomePage SomePage]",
        )
        self.assertEqual(
            markdown_to_tracwiki("<wiki:SomePage>"), "[wiki:SomePage]"
        )

    def test_ticket_37_acronym_tailed_words_not_escaped(self):
        """An acronym-tailed word like "PyVISA" or "NASA" must NOT get
        the defensive "!" prefix -- Trac's own WikiFormatting grammar
        requires every hump after the first to carry a real lowercase
        letter, so it never auto-links these in the first place. Only
        genuine multi-hump CamelCase words (WiFi, LoRa, WiFiConfig) are
        still escaped.
        """
        result = markdown_to_tracwiki(
            "PyVISA should show whether Trac auto-links acronym-tailed "
            "words. NASA alone, all caps, for comparison."
        )
        self.assertIn("PyVISA", result)
        self.assertNotIn("!PyVISA", result)
        self.assertIn("NASA", result)
        self.assertNotIn("!NASA", result)
        # Regression guard: genuine multi-hump words are still escaped.
        result = markdown_to_tracwiki(
            "WiFi credentials and the LoRa wire format."
        )
        self.assertIn("!WiFi", result)
        self.assertIn("!LoRa", result)

    def test_ticket_92_acronym_tailed_bang_kept_in_link_label(self):
        """`_CAMELCASE_ESCAPE_RE` (used to undo `text()`'s "!"-escaping
        inside a link label) must match exactly what `_CAMELCASE_RE`
        would have added -- ticket #37 tightened the latter to require a
        real lowercase letter in every hump after the first, but the
        "undo" regex was left loose, so a literal author-typed "!" in a
        link label ahead of an acronym- or digit-tailed word (which
        `_CAMELCASE_RE` never escapes in the first place) was being
        silently stripped instead of left alone.
        """
        self.assertEqual(
            markdown_to_tracwiki("[!LilyGO](wiki:LilyGO)"),
            "[wiki:LilyGO !LilyGO]",
        )
        self.assertEqual(
            markdown_to_tracwiki("[!LoRa32](wiki:LoRa32)"),
            "[wiki:LoRa32 !LoRa32]",
        )
        # Regression guard: a genuine two-lowercase-hump word in a label
        # is still unescaped (ticket #27's original case).
        self.assertEqual(
            markdown_to_tracwiki("[!SomePage](wiki:SomePage)"),
            "[wiki:SomePage SomePage]",
        )

    def test_ticket_29_br_gets_leading_space_after_colon_token(self):
        """A hard line break directly after a colon-valued token (e.g.
        "substrate:trac") gets a leading space before [[BR]] -- without
        it, Trac's wiki-link grammar greedily consumes "[[BR]]" into a
        failed `wikiname:target` TracLink parse instead of recognizing
        it as the line-break macro.
        """
        result = markdown_to_tracwiki("substrate:trac  \nnext line")
        self.assertIn("substrate:trac [[BR]]", result)

    def test_ticket_29_br_also_spaced_after_plain_word(self):
        """The fix is the "preceding char isn't whitespace" rule from the
        ticket's own remediation suggestion, applied unconditionally --
        not conditioned on detecting a colon specifically. A plain
        (non-colon) word before [[BR]] gets the same leading space, which
        is unnecessary (Trac already renders fine there) but harmless.
        """
        result = markdown_to_tracwiki("global  \nnext line")
        self.assertIn("global [[BR]]", result)

    def test_ticket_40_server_relative_link_target_survives(self):
        """A server-relative link target (`//other_instance/ticket/13`)
        must pass through verbatim, not get `wiki:`-prefixed.

        Regression test for ticket #40: this is the sanctioned way to
        cross-link between Trac instances on the same host without
        hardcoding scheme/host/port into the content. The target has no
        ":" and previously fell into the "internal wiki page" branch,
        producing the dead-link `[wiki://trac_mcp_server/ticket/13 ...]`.
        """
        result = markdown_to_tracwiki(
            "[trac_mcp_server ticket 13](//trac_mcp_server/ticket/13)"
        )
        self.assertEqual(
            result,
            "[//trac_mcp_server/ticket/13 trac_mcp_server ticket 13]",
        )
        self.assertNotIn("wiki:", result)

    def test_ticket_44_bare_url_target_not_escaped(self):
        """A CamelCase-shaped path segment inside a bare absolute URL
        (no Markdown link syntax around it) must not be "!"-escaped --
        the escape would land in the URL itself, producing a dead link.
        """
        result = markdown_to_tracwiki(
            "See http://host:8000/bcs/wiki/b-node/bench/BoardIdentity for details."
        )
        self.assertIn(
            "http://host:8000/bcs/wiki/b-node/bench/BoardIdentity",
            result,
        )
        self.assertNotIn("!BoardIdentity", result)

    def test_ticket_44_intertrac_link_target_and_label_not_escaped(
        self,
    ):
        """An unresolved single-bracket InterTrac link typed directly in
        Markdown source (`[prefix:realm:target label]`) is not real
        Markdown link syntax, so mistune renders it as literal text.
        Neither the target nor the label may be "!"-escaped -- an
        escaped target resolves to a nonexistent page, and an escaped
        label shows a stray literal "!" to the reader.
        """
        self.assertEqual(
            markdown_to_tracwiki(
                "[bcs:wiki:b-node/bench/BoardIdentity the budget table]"
            ),
            "[bcs:wiki:b-node/bench/BoardIdentity the budget table]",
        )
        # Label equal to the CamelCase target -- the shape from the
        # ticket's original (narrower) repro.
        self.assertEqual(
            markdown_to_tracwiki(
                "[bcs:wiki:b-node/bench/BoardIdentity BoardIdentity]"
            ),
            "[bcs:wiki:b-node/bench/BoardIdentity BoardIdentity]",
        )

    def test_ticket_44_real_markdown_link_with_scheme_shaped_label_unaffected(
        self,
    ):
        """A genuine Markdown link `[label](url)` must still parse as a
        real link even when its label happens to look scheme-shaped
        (colon-containing) -- the single-bracket-link stash must not
        swallow it just because it's immediately followed by "(url)".
        """
        result = markdown_to_tracwiki(
            "[wiki:Page](http://example.com/x)"
        )
        self.assertEqual(result, "[http://example.com/x wiki:Page]")

    def test_ticket_44_backticked_url_still_unaffected(self):
        """A URL inside a code span was already safe before this fix
        (codespan() never escapes) -- regression guard against the new
        URL-detection logic in text() somehow reaching into code spans.
        """
        result = markdown_to_tracwiki(
            "`http://host:8000/bcs/wiki/b-node/bench/BoardIdentity`"
        )
        self.assertEqual(
            result,
            "`http://host:8000/bcs/wiki/b-node/bench/BoardIdentity`",
        )

    # -- ticket #44, reopened: the bare (unbracketed) InterTrac form --
    #
    # One test per row of the six-form table the ticket was reopened
    # with. Rows A/A' are the defect; B and C are the two forms the
    # first pass fixed, kept here so a later change can't trade one for
    # the other; D, E and F are the over-correction pins -- F in
    # particular must keep its "!", since a bare CamelCase prose word is
    # exactly what the escape exists for (the ticket #37 lesson: this
    # cannot be fixed by loosening the word-shape rule).

    def test_ticket_44_row_a_bare_intertrac_target_not_escaped(self):
        """Row A: a bare `prefix:realm:target` reference in prose, the
        form `Rules/trac/PreferInterTracLinks` makes the preferred way
        to write a cross-instance link. Trac auto-links it with no
        brackets involved, so a "!" in its CamelCase final segment
        becomes part of the page name and the link 302s to a page that
        does not exist.
        """
        self.assertEqual(
            markdown_to_tracwiki(
                "See auto_pm:wiki:Rules/trac/RenderVerify for details."
            ),
            "See auto_pm:wiki:Rules/trac/RenderVerify for details.",
        )
        # Sentence-final and parenthesised placements: the surrounding
        # punctuation must not end the skip span early.
        self.assertEqual(
            markdown_to_tracwiki(
                "(see auto_pm:wiki:Rules/trac/RenderVerify)"
            ),
            "(see auto_pm:wiki:Rules/trac/RenderVerify)",
        )
        self.assertEqual(
            markdown_to_tracwiki(
                "See auto_pm:wiki:Rules/trac/RenderVerify."
            ),
            "See auto_pm:wiki:Rules/trac/RenderVerify.",
        )

    def test_ticket_44_row_a_bare_same_instance_traclink_not_escaped(
        self,
    ):
        """Row A without the InterTrac prefix: a bare `realm:target`
        TracLink (`wiki:SomePage`) auto-links the same way and breaks
        the same way. The prefix segment is optional in the skip rule.
        """
        self.assertEqual(
            markdown_to_tracwiki(
                "See wiki:Rules/trac/RenderVerify for details."
            ),
            "See wiki:Rules/trac/RenderVerify for details.",
        )

    def test_ticket_44_row_d_non_camelcase_target_unaffected(self):
        """Row D: a bare InterTrac reference whose final segment isn't
        CamelCase-shaped was never escaped, and must stay untouched --
        it pins the skip against changing anything it isn't there for.
        """
        self.assertEqual(
            markdown_to_tracwiki("See auto_pm:wiki:Index for details."),
            "See auto_pm:wiki:Index for details.",
        )

    def test_ticket_44_row_e_bare_ticket_shortlink_unaffected(self):
        """Row E: the InterTrac ticket short link `prefix:#N`. Never
        escaped (no CamelCase in it), and must not be rewritten either.
        """
        self.assertEqual(
            markdown_to_tracwiki("See auto_pm:#87 for details."),
            "See auto_pm:#87 for details.",
        )

    def test_ticket_44_row_f_bare_prose_word_still_escaped(self):
        """Row F, the over-correction pin: a CamelCase word standing
        alone in prose is NOT a link and still needs its "!" -- that is
        the whole point of the escape pass (ticket #27). A and F are
        textually identical apart from the `realm:` prefix, so the skip
        has to key on the realm, never on the word shape.
        """
        self.assertEqual(
            markdown_to_tracwiki("The RenderVerify rule applies."),
            "The !RenderVerify rule applies.",
        )
        # A colon that isn't a Trac realm buys nothing: still prose.
        self.assertEqual(
            markdown_to_tracwiki("Note: RenderVerify applies."),
            "Note: !RenderVerify applies.",
        )
        self.assertEqual(
            markdown_to_tracwiki("Also Foo:RenderVerify here."),
            "Also Foo:!RenderVerify here.",
        )
        # Realm names are case-sensitive in Trac; "Wiki:" is prose.
        self.assertEqual(
            markdown_to_tracwiki("Wiki:RenderVerify is prose"),
            "Wiki:!RenderVerify is prose",
        )

    def test_ticket_45_table_cell_code_span_with_pipes_converts(self):
        """A Markdown table cell containing a code span whose body is
        itself pipe-shaped (documenting TracWiki table syntax, e.g.
        `` `||||` ``) must still be recognized as a table -- mistune's
        own table-row splitter doesn't know about code spans and would
        otherwise miscount the cell's columns and reject the whole block.
        """
        md = (
            "| Context | Effect |\n"
            "|---|---|\n"
            "| plain prose | consumed |\n"
            "| `{{{#!div}}}`, `||||`, `{{{#!table}}}` | consumed |\n"
            "| table cell | consumed |"
        )
        result = markdown_to_tracwiki(md)
        self.assertIn(
            "||`{{{#!div}}}`, `||||`, `{{{#!table}}}`||consumed||",
            result,
        )

    def test_ticket_45_table_converts_to_exact_tracwiki(self):
        """The exact fixture from the ticket converts to exactly this
        table: header, body rows and the code-span row, nothing dropped.
        """
        md = (
            "| Context | Effect |\n"
            "|---|---|\n"
            "| plain prose | consumed |\n"
            "| `{{{#!div}}}`, `||||`, `{{{#!table}}}` | consumed |\n"
            "| table cell | consumed |"
        )
        self.assertEqual(
            markdown_to_tracwiki(md),
            "||=Context=||=Effect=||\n"
            "||plain prose||consumed||\n"
            "||`{{{#!div}}}`, `||||`, `{{{#!table}}}`||consumed||\n"
            "||table cell||consumed||",
        )

    def test_ticket_45_heading_slug_unaffected_by_code_span_stash(self):
        """Regression guard: stashing code-span bodies before parsing
        (added for #45) must not break heading-anchor slug computation,
        which runs mid-render on text that may still contain a stash
        sentinel for a code span inside the heading.
        """
        result = markdown_to_tracwiki(
            "## EvalRef marker fields (`ticket-comment`)",
            heading_anchors=True,
        )
        self.assertEqual(
            result,
            "== !EvalRef marker fields (`ticket-comment`) == "
            "#evalref-marker-fields-ticket-comment",
        )


class TestTicket72NestedBlockExtent(unittest.TestCase):
    """A ``{{{ }}}`` block nested inside another (ticket #72).

    A nested block is *content*: Trac renders the outer block verbatim, so
    the inner delimiters are literal text and belong inside the fence
    unchanged.  A Markdown fence holding them converts to the outer block
    with the inner delimiters untouched.
    """

    # (label, expected TracWiki, Markdown source)
    NESTED = [
        (
            "#!lang inside #!lang",
            "{{{#!python\nouter\n{{{#!sh\ninner\n}}}\nafter\n}}}",
            "```python\nouter\n{{{#!sh\ninner\n}}}\nafter\n```",
        ),
        (
            "plain inside #!lang",
            "{{{#!python\nouter\n{{{\ninner\n}}}\nafter\n}}}",
            "```python\nouter\n{{{\ninner\n}}}\nafter\n```",
        ),
        (
            "plain inside plain",
            "{{{\nouter\n{{{\ninner\n}}}\nafter\n}}}",
            "```\nouter\n{{{\ninner\n}}}\nafter\n```",
        ),
        (
            "#!lang inside plain",
            "{{{\n{{{#!comment\nAGENT: do the thing\n}}}\n}}}",
            "```\n{{{#!comment\nAGENT: do the thing\n}}}\n```",
        ),
    ]

    def test_the_fence_converts_to_the_nested_block(self):
        for label, expected, md in self.NESTED:
            with self.subTest(label):
                out = markdown_to_tracwiki(md)
                self.assertNotIn("\x00", out)
                self.assertEqual(out, expected)

    def test_three_levels_deep(self):
        """Depth is counted, not guessed: only the outermost fence converts."""
        src = "{{{\na\n{{{\nb\n{{{\nc\n}}}\nd\n}}}\ne\n}}}"
        md = "```\na\n{{{\nb\n{{{\nc\n}}}\nd\n}}}\ne\n```"
        self.assertEqual(markdown_to_tracwiki(md), src)

    def test_a_quoted_backtick_fence_in_a_nested_body_stays_literal(
        self,
    ):
        """A four-backtick fence whose body holds a quoted three-backtick
        fence inside a nested ``{{{ }}}`` block (ticket #51's fence
        collision).

        `markdown_to_tracwiki` used to rewrite the quoted ```` ``` ```` fence
        into a `{{{ }}}` block, corrupting content the author quoted on
        purpose (ticket #88, fixed by deleting the helper that did the
        rewriting -- see test_converter_nested_fence_restore.py for that
        ticket's own seeds). The quoted fence stays literal.
        """
        src = "{{{\nouter\n{{{\n```\ninner\n```\n}}}\n}}}"
        md = "````\nouter\n{{{\n```\ninner\n```\n}}}\n````"
        self.assertEqual(markdown_to_tracwiki(md), src)


class TestIsLinkTarget(unittest.TestCase):
    """Unit tests for the shared link-target predicate (tickets #13, #14)."""

    def test_bare_page_name_is_a_target(self):
        """No colon at all — a wiki page name or relative path."""
        for candidate in (
            "WikiPage",
            "Planning/Phases/Phase01",
            "../Up",
        ):
            with self.subTest(candidate=candidate):
                self.assertTrue(is_link_target(candidate))

    def test_traclink_resolvers_are_targets(self):
        """Known Trac resolvers with a non-empty target qualify."""
        for candidate in (
            "wiki:WikiPage",
            "ticket:42",
            "source:trunk/file.py",
            "attachment:file.diff",
            "milestone:v2.3.0",
        ):
            with self.subTest(candidate=candidate):
                self.assertTrue(is_link_target(candidate))

    def test_url_schemes_are_targets(self):
        """Transport schemes qualify."""
        for candidate in (
            "https://example.com",
            "http://example.com/a/b",
            "ftp://files.example.com",
            "mailto:someone@example.com",
        ):
            with self.subTest(candidate=candidate):
                self.assertTrue(is_link_target(candidate))

    def test_sentinels_are_not_targets(self):
        """Bracketed prose that merely looks scheme-shaped is refused."""
        for candidate in ("auto-pm:", "foo:bar", "note:", "TODO:"):
            with self.subTest(candidate=candidate):
                self.assertFalse(is_link_target(candidate))

    def test_known_scheme_with_empty_target_is_refused(self):
        """A resolver with nothing after the colon is degenerate."""
        self.assertFalse(is_link_target("wiki:"))


class TestBracketedProseNotRewritten(unittest.TestCase):
    """Ticket #13: '[word: word]' must not become a degenerate link.

    The reported symptom was ``[auto-pm: state NEEDS_EDIT]`` being rewritten
    as ``[state NEEDS_EDIT](auto-pm:)`` — note the empty URL. The ticket named
    ``markdown_to_tracwiki``; the rewrite happened in the reverse converter,
    since removed, so only this direction is pinned here.
    """

    SENTINEL = "[auto-pm: state NEEDS_EDIT]"

    def test_sentinel_preserved_markdown_to_tracwiki(self):
        """The direction the ticket named; already correct, pinned here."""
        self.assertEqual(
            markdown_to_tracwiki(self.SENTINEL).strip(), self.SENTINEL
        )


class TestFootnoteRejection(unittest.TestCase):
    """A Markdown footnote pair is refused, not silently destroyed (#63)."""

    def test_write_leg_rejects_a_destructive_footnote(self):
        """Unrepresentable *Markdown* fails the call (ticket #63, option c).

        Measured as a "before": the definition line was deleted outright and
        the reference became a wiki link to a page named after the note text,
        silently.

        A footnote is not a block: its reference and its definition sit in
        different places, so wrapping either half alone breaks the pairing.
        """
        with self.assertRaises(ValueError) as ctx:
            markdown_to_tracwiki("Text[^1]\n\n[^1]: note\n")
        message = str(ctx.exception)
        self.assertIn("[^1]", message)
        # The escape hatch must be in the message; a hard failure with no way
        # forward is worse than the silent loss it replaces.
        self.assertIn("tracwiki", message)

    def test_write_leg_footnote_rejection_stays_narrow(self):
        """The recall gate: only the destructive PAIR may fail the call.

        Measured on ticket #63 -- a reference alone and a definition alone
        both pass through untouched, so neither is destructive and neither
        may be rejected.  The quoted forms matter most: this check runs
        before the converter's own code-span stashing, so it has to do its
        own shielding or it fires on syntax the author quoted rather than
        wrote.
        """
        harmless = [
            ("reference with no definition", "Text[^1] alone\n"),
            ("definition with no reference", "[^1]: orphan note\n"),
            (
                "both, quoted in code spans",
                "Use `[^1]` and `[^1]:` here\n",
            ),
            (
                "both, inside a fenced block",
                "```\nText[^1]\n\n[^1]: note\n```\n",
            ),
            ("bracketed caret in prose", "An array like a[^b] maybe\n"),
            ("ordinary prose", "Just some text.\n"),
        ]
        for label, src in harmless:
            with self.subTest(label):
                markdown_to_tracwiki(src)  # must not raise


if __name__ == "__main__":
    unittest.main()
