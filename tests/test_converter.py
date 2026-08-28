"""
Tests for Markdown to TracWiki converter and TracWiki to Markdown converter.
"""

import unittest
from unittest.mock import MagicMock, patch

from trac_mcp_server.converters import (
    ConversionResult,
    convert_with_warnings,
    markdown_to_tracwiki,
    tracwiki_to_markdown,
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
        -- exactly what ``tracwiki_to_markdown`` emits for a native
        ``[wiki:BnodeHardware Hardware]`` link -- was left as literal
        Markdown by the ``":" in url and "/" not in url`` guard, and the
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

    def test_traclink_round_trips_through_both_converters(self):
        """wiki_get -> wiki_update round-trip leaves existing links intact.

        The end-to-end shape of ticket #17: pull TracWiki through
        ``tracwiki_to_markdown``, push the untouched result back through
        ``markdown_to_tracwiki``, and the link must come out identical.
        """
        for tracwiki in [
            "[wiki:BnodeHardware Hardware]",
            "[wiki:b-node/blog Blog]",
            "[ticket:42 Bug]",
            "[source:trunk/foo.py file]",
            "[milestone:1.0 M]",
            "[http://example.com ext]",
            "[#anchor jump]",
        ]:
            with self.subTest(tracwiki=tracwiki):
                markdown = tracwiki_to_markdown(tracwiki).text
                self.assertEqual(
                    markdown_to_tracwiki(markdown).strip(), tracwiki
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


class TestTracWikiToMarkdownConverter(unittest.TestCase):
    """Test TracWiki to Markdown conversion."""

    def test_heading_level_1(self):
        """Test H1 heading conversion."""
        result = tracwiki_to_markdown("= Heading 1 =")
        self.assertEqual(result.text, "# Heading 1")

    def test_heading_level_2(self):
        """Test H2 heading conversion."""
        result = tracwiki_to_markdown("== Heading 2 ==")
        self.assertEqual(result.text, "## Heading 2")

    def test_heading_level_3(self):
        """Test H3 heading conversion."""
        result = tracwiki_to_markdown("=== Heading 3 ===")
        self.assertEqual(result.text, "### Heading 3")

    def test_heading_level_4(self):
        """Test H4 heading conversion."""
        result = tracwiki_to_markdown("==== Heading 4 ====")
        self.assertEqual(result.text, "#### Heading 4")

    def test_heading_level_5(self):
        """Test H5 heading conversion."""
        result = tracwiki_to_markdown("===== Heading 5 =====")
        self.assertEqual(result.text, "##### Heading 5")

    def test_heading_level_6(self):
        """Test H6 heading conversion."""
        result = tracwiki_to_markdown("====== Heading 6 ======")
        self.assertEqual(result.text, "###### Heading 6")

    def test_heading_without_trailing_equals(self):
        """Trailing = is optional in TracWiki."""
        result = tracwiki_to_markdown("= Heading 1")
        self.assertEqual(result.text, "# Heading 1")

    def test_heading_h2_without_trailing_equals(self):
        """Trailing == is optional in TracWiki."""
        result = tracwiki_to_markdown("== Heading 2")
        self.assertEqual(result.text, "## Heading 2")

    def test_heading_h3_without_trailing_equals(self):
        """Trailing === is optional in TracWiki."""
        result = tracwiki_to_markdown("=== Heading 3")
        self.assertEqual(result.text, "### Heading 3")

    def test_bold_text(self):
        """Test bold text conversion."""
        result = tracwiki_to_markdown("'''bold text'''")
        self.assertEqual(result.text, "**bold text**")

    def test_italic_text(self):
        """Test italic text conversion."""
        result = tracwiki_to_markdown("''italic text''")
        self.assertEqual(result.text, "*italic text*")

    def test_bold_italic_text(self):
        """Test bold italic text conversion."""
        result = tracwiki_to_markdown("'''''bold italic'''''")
        self.assertEqual(result.text, "***bold italic***")

    def test_link_with_text(self):
        """Test link with text conversion."""
        result = tracwiki_to_markdown("[https://example.com link text]")
        self.assertEqual(
            result.text, "[link text](https://example.com)"
        )

    def test_link_without_text(self):
        """Test link without text conversion."""
        result = tracwiki_to_markdown("[https://example.com]")
        self.assertEqual(result.text, "<https://example.com>")

    def test_code_block_with_language(self):
        """Test code block with language conversion."""
        tracwiki = """{{{#!python
def hello():
    print("world")
}}}"""
        expected = """```python
def hello():
    print("world")
```"""
        result = tracwiki_to_markdown(tracwiki)
        self.assertEqual(result.text, expected)

    def test_code_block_without_language(self):
        """Test code block without language conversion."""
        tracwiki = """{{{
plain code
}}}"""
        expected = """```
plain code
```"""
        result = tracwiki_to_markdown(tracwiki)
        self.assertEqual(result.text, expected)

    def test_unordered_list(self):
        """Test unordered list conversion."""
        tracwiki = """ * item 1
 * item 2
 * item 3"""
        expected = """ - item 1
 - item 2
 - item 3"""
        result = tracwiki_to_markdown(tracwiki)
        self.assertEqual(result.text, expected)

    def test_ordered_list(self):
        """Test ordered list conversion."""
        tracwiki = """ 1. first
 2. second
 3. third"""
        # Ordered lists are already compatible
        result = tracwiki_to_markdown(tracwiki)
        self.assertEqual(result.text, tracwiki)

    def test_horizontal_rule(self):
        """Test horizontal rule conversion."""
        result = tracwiki_to_markdown("----")
        self.assertEqual(result.text, "---")

    def test_line_break(self):
        """[[BR]] converts to a CommonMark hard break, not a soft break."""
        result = tracwiki_to_markdown("Line one[[BR]]Line two")
        self.assertEqual(result.text, "Line one  \nLine two")

    def test_line_break_case_insensitive(self):
        """Test line break is case insensitive."""
        result = tracwiki_to_markdown("Line one[[br]]Line two")
        self.assertEqual(result.text, "Line one  \nLine two")

    def test_image(self):
        """Test image conversion."""
        result = tracwiki_to_markdown("[[Image(screenshot.png)]]")
        self.assertEqual(result.text, "![](screenshot.png)")

    def test_image_case_insensitive(self):
        """Test image macro is case insensitive."""
        result = tracwiki_to_markdown("[[image(screenshot.png)]]")
        self.assertEqual(result.text, "![](screenshot.png)")

    def test_unknown_macro_passthrough(self):
        """Test unknown macros are preserved with [MACRO: ...] notation."""
        result = tracwiki_to_markdown("[[TOC]]")
        self.assertEqual(result.text, "[MACRO: TOC]")

    def test_blockquote(self):
        """Test blockquote conversion."""
        tracwiki = """  This is quoted
  Another quoted line"""
        expected = """> This is quoted
> Another quoted line"""
        result = tracwiki_to_markdown(tracwiki)
        self.assertEqual(result.text, expected)

    def test_mixed_content(self):
        """Test real-world mixed content."""
        tracwiki = """= Bug Report =

This is a '''critical''' bug in the ''login'' system.

Reproduction steps:
 1. Navigate to [https://example.com login page]
 2. Enter invalid credentials
 3. Observe error

Code that fails:
{{{#!python
def login(user, password):
    return False
}}}

See screenshot: [[Image(error.png)]]"""

        result = tracwiki_to_markdown(tracwiki)

        # Check key conversions
        self.assertIn("# Bug Report", result.text)
        self.assertIn("**critical**", result.text)
        self.assertIn("*login*", result.text)
        self.assertIn(
            "1. Navigate to [login page](https://example.com)",
            result.text,
        )
        self.assertIn("```python", result.text)
        self.assertIn("![](error.png)", result.text)

    def test_inline_code_passthrough(self):
        """Test inline code passes through unchanged."""
        result = tracwiki_to_markdown("`inline code`")
        self.assertEqual(result.text, "`inline code`")

    def test_nested_lists(self):
        """Test nested list conversion with proper indentation format."""
        tracwiki = """ * item 1
   * nested 1
   * nested 2
 * item 2"""
        expected = """ - item 1
   - nested 1
   - nested 2
 - item 2"""
        result = tracwiki_to_markdown(tracwiki)
        self.assertEqual(result.text, expected)

    def test_multiple_headings(self):
        """Test multiple headings in document."""
        tracwiki = """= Title =
== Section ==
=== Subsection ==="""
        expected = """# Title
## Section
### Subsection"""
        result = tracwiki_to_markdown(tracwiki)
        self.assertEqual(result.text, expected)

    def test_mixed_formatting_inline(self):
        """Test mixed formatting in single line."""
        result = tracwiki_to_markdown(
            "This is '''bold''' and ''italic'' text"
        )
        self.assertEqual(
            result.text, "This is **bold** and *italic* text"
        )

    def test_longer_horizontal_rule(self):
        """Test longer horizontal rule converts."""
        result = tracwiki_to_markdown("--------")
        self.assertEqual(result.text, "---")


class TestUnknownMacrosOption(unittest.TestCase):
    """Tests for the unknown_macros kwarg added in Phase 16.

    Verifies that the default "bracket" preserves existing behavior and that
    "preserve" / "drop" produce the expected output without disturbing known
    macros (Image, BR) or the lossy-elements warning.
    """

    def test_default_bracket(self):
        """unknown_macros="bracket" (default) emits [MACRO: Name]."""
        result = tracwiki_to_markdown("[[PageOutline]]")
        self.assertIn("[MACRO: PageOutline]", result.text)

    def test_bracket_explicit(self):
        """Explicit unknown_macros="bracket" matches the default."""
        default = tracwiki_to_markdown("[[PageOutline]]")
        explicit = tracwiki_to_markdown(
            "[[PageOutline]]", unknown_macros="bracket"
        )
        self.assertEqual(default.text, explicit.text)

    def test_preserve_keeps_literal(self):
        """unknown_macros="preserve" leaves [[MacroName]] verbatim."""
        result = tracwiki_to_markdown(
            "[[PageOutline]]", unknown_macros="preserve"
        )
        self.assertIn("[[PageOutline]]", result.text)
        self.assertNotIn("[MACRO:", result.text)

    def test_drop_removes_macro(self):
        """unknown_macros="drop" silently omits the macro."""
        result = tracwiki_to_markdown(
            "before [[PageOutline]] after", unknown_macros="drop"
        )
        self.assertNotIn("PageOutline", result.text)
        self.assertIn("before", result.text)
        self.assertIn("after", result.text)

    def test_known_macros_unaffected_bracket(self):
        """Image macro is converted to Markdown img regardless of mode."""
        result = tracwiki_to_markdown(
            "[[Image(foo.png)]]", unknown_macros="bracket"
        )
        self.assertIn("![](foo.png)", result.text)

    def test_known_macros_unaffected_preserve(self):
        """Image macro converts correctly under preserve mode too."""
        result = tracwiki_to_markdown(
            "[[Image(foo.png)]]", unknown_macros="preserve"
        )
        self.assertIn("![](foo.png)", result.text)

    def test_known_macros_unaffected_drop(self):
        """Image macro converts correctly under drop mode too."""
        result = tracwiki_to_markdown(
            "[[Image(foo.png)]]", unknown_macros="drop"
        )
        self.assertIn("![](foo.png)", result.text)

    def test_macro_with_args_preserve(self):
        """[[TOC(depth=2)]] under preserve stays literal."""
        result = tracwiki_to_markdown(
            "[[TOC(depth=2)]]", unknown_macros="preserve"
        )
        self.assertIn("[[TOC(depth=2)]]", result.text)

    def test_warning_fires_under_all_modes(self):
        """_detect_lossy_elements warning fires regardless of rendering mode."""
        for mode in ("bracket", "preserve", "drop"):
            with self.subTest(mode=mode):
                result = tracwiki_to_markdown(
                    "[[PageOutline]]",
                    unknown_macros=mode,  # type: ignore[arg-type]
                )
                self.assertTrue(
                    any("Unknown macros" in w for w in result.warnings),
                    f"Expected 'Unknown macros' warning in mode={mode!r}",
                )


class TestTracWikiEnhancements(unittest.TestCase):
    """Test TracWiki to Markdown enhancements (macros, TracLinks, tables, etc)."""

    def test_tracwiki_unknown_macros(self):
        """Test unknown macros are preserved with [MACRO: ...] notation."""
        result = tracwiki_to_markdown("[[PageOutline]]")
        self.assertIn("[MACRO: PageOutline]", result.text)
        self.assertTrue(
            any("macro" in w.lower() for w in result.warnings)
        )

    def test_tracwiki_traclinks_preserved(self):
        """Test TracLinks are preserved and warnings issued."""
        result = tracwiki_to_markdown("See #123 and ticket:456")
        # TracLinks should be preserved in text
        self.assertIn("#123", result.text)
        self.assertIn("ticket:456", result.text)
        # Should issue warning about TracLinks
        self.assertTrue(
            any("traclink" in w.lower() for w in result.warnings)
        )

    def test_tracwiki_definition_lists(self):
        """Test definition lists conversion with warnings."""
        result = tracwiki_to_markdown("term:: definition")
        # Should convert to bold with colon
        self.assertIn("**term**:", result.text)
        # Should warn about lossy conversion
        self.assertTrue(
            any("definition" in w.lower() for w in result.warnings)
        )

    def test_tracwiki_tables(self):
        """Test basic table conversion from TracWiki to Markdown."""
        result = tracwiki_to_markdown("||cell1||cell2||")
        # Should convert to Markdown table
        self.assertIn("| cell1 | cell2 |", result.text)
        self.assertIn("|---|---|", result.text)

    def test_tracwiki_table_headers(self):
        """Test TracWiki header row (||= ... =||) converts to Markdown header."""
        result = tracwiki_to_markdown("||= H1 =||= H2 =||\n||a||b||")
        lines = result.text.split("\n")
        self.assertEqual(lines[0], "| H1 | H2 |")
        # Header cells with = markers are centered by default
        self.assertIn(":---:", lines[1])
        self.assertEqual(lines[2], "| a | b |")

    def test_tracwiki_table_alignment(self):
        """Test TracWiki table alignment converts to Markdown separator."""
        # TracWiki: space on right = left, space on left = right, both = center
        result = tracwiki_to_markdown(
            "||=Left =||= Center =||= Right=||\n||left || center || right||"
        )
        lines = result.text.split("\n")
        # Check separator row has correct alignment markers
        self.assertIn(":---", lines[1])  # left
        self.assertIn(":---:", lines[1])  # center
        self.assertIn("---:", lines[1])  # right

    def test_tracwiki_table_spanning(self):
        """Test TracWiki cell spanning with warning."""
        result = tracwiki_to_markdown(
            "||= A =||= B =||= C =||\n|||| Span 2 || 3 ||"
        )
        # Should have span indicator
        self.assertIn("[span:", result.text)
        # Should warn about spanning
        self.assertTrue(
            any("span" in w.lower() for w in result.warnings)
        )

    def test_tracwiki_table_multiline(self):
        """Test TracWiki multi-line rows (backslash continuation)."""
        result = tracwiki_to_markdown(
            "||= H1 =||= H2 =||\n|| column 1 \\\n|| column 2 ||"
        )
        # Should join into single row
        self.assertIn("| column 1 | column 2 |", result.text)
        # Should warn about multi-line
        self.assertTrue(
            any("multi-line" in w.lower() for w in result.warnings)
        )


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


class TestConversionResultMetadata(unittest.TestCase):
    """Test ConversionResult metadata and backward compatibility."""

    def test_conversion_result_metadata(self):
        """Test ConversionResult contains correct metadata."""
        result = tracwiki_to_markdown("= Test =")
        self.assertEqual(result.source_format, "tracwiki")
        self.assertEqual(result.target_format, "markdown")
        self.assertEqual(result.converted, True)
        self.assertIn("# Test", result.text)

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

    # TracWiki to Markdown tests

    def test_tw_to_md_basic_table(self):
        """Test basic TracWiki table converts to Markdown."""
        tw = "||A||B||\n||1||2||"
        result = tracwiki_to_markdown(tw)
        self.assertIn("| A | B |", result.text)
        self.assertIn("|---|---|", result.text)
        self.assertIn("| 1 | 2 |", result.text)

    def test_tw_to_md_header_row(self):
        """Test TracWiki header row converts to Markdown header."""
        tw = "||= H1 =||= H2 =||\n||a||b||"
        result = tracwiki_to_markdown(tw)
        lines = result.text.split("\n")
        self.assertEqual(lines[0], "| H1 | H2 |")
        self.assertEqual(lines[2], "| a | b |")

    def test_tw_to_md_alignment(self):
        """Test TracWiki alignment converts to Markdown separator."""
        tw = "||=Left =||= Center =||= Right=||\n||left || center || right||"
        result = tracwiki_to_markdown(tw)
        # Check separator has alignment markers
        sep_line = result.text.split("\n")[1]
        self.assertIn(":---", sep_line)
        self.assertIn(":---:", sep_line)
        self.assertIn("---:", sep_line)

    def test_tw_to_md_spanning_warning(self):
        """Test TracWiki cell spanning produces warning."""
        tw = "||= A =||= B =||\n|||| Span ||"
        result = tracwiki_to_markdown(tw)
        self.assertTrue(
            any("span" in w.lower() for w in result.warnings)
        )

    def test_tw_to_md_multiline_warning(self):
        """Test TracWiki multi-line rows produce warning."""
        tw = "||= H1 =||= H2 =||\n|| col1 \\\n|| col2 ||"
        result = tracwiki_to_markdown(tw)
        self.assertTrue(
            any("multi-line" in w.lower() for w in result.warnings)
        )

    # Round-trip tests

    def test_table_roundtrip_md_to_tw_to_md(self):
        """Test Markdown -> TracWiki -> Markdown preserves structure."""
        original_md = """| A | B |
| --- | --- |
| 1 | 2 |"""
        to_tw = markdown_to_tracwiki(original_md)
        back_to_md = tracwiki_to_markdown(to_tw)
        # Should have table structure
        self.assertIn("| A | B |", back_to_md.text)
        self.assertIn("| 1 | 2 |", back_to_md.text)

    def test_table_roundtrip_tw_to_md_to_tw(self):
        """Test TracWiki -> Markdown -> TracWiki preserves structure."""
        original_tw = "||= H1 =||= H2 =||\n||a||b||"
        to_md = tracwiki_to_markdown(original_tw)
        back_to_tw = markdown_to_tracwiki(to_md.text)
        # Should have TracWiki table structure
        self.assertIn("||", back_to_tw)
        # Header content preserved (may have alignment spaces)
        self.assertIn("H1", back_to_tw)
        self.assertIn("H2", back_to_tw)

    def test_table_roundtrip_alignment_preserved(self):
        """Test alignment is preserved in round-trip."""
        original_md = """| Left | Center | Right |
| :--- | :---: | ---: |
| L | C | R |"""
        to_tw = markdown_to_tracwiki(original_md)
        back_to_md = tracwiki_to_markdown(to_tw)
        sep_line = back_to_md.text.split("\n")[1]
        # Alignment should be preserved
        self.assertIn(":---", sep_line)
        self.assertIn(":---:", sep_line)
        self.assertIn("---:", sep_line)


class TestRoundTripConversion(unittest.TestCase):
    """Test round-trip conversion behavior (lossy and compatible elements)."""

    def test_roundtrip_lossy_macros(self):
        """Test macros survive round-trip but become [MACRO: ...] notation."""
        original = "Text [[PageOutline]] more"
        to_md = tracwiki_to_markdown(original)
        # Macro becomes [MACRO: ...] - not identical but preserved
        self.assertIn("[MACRO: PageOutline]", to_md.text)
        self.assertGreater(len(to_md.warnings), 0)

    def test_roundtrip_compatible_elements(self):
        """Test elements that survive round-trip semantically."""
        # Elements that convert cleanly both ways
        original_tw = "= H1 =\n\n'''bold''' and ''italic''\n\n * list"
        to_md = tracwiki_to_markdown(original_tw)
        to_tw = markdown_to_tracwiki(to_md.text)
        # Should be semantically equivalent
        self.assertIn("= H1 =", to_tw)
        self.assertIn("'''bold'''", to_tw)
        self.assertIn("''italic''", to_tw)

    def test_roundtrip_tracwiki_to_markdown_to_tracwiki(self):
        """Test TracWiki -> Markdown -> TracWiki preserves basic formatting."""
        original = "== Section ==\n\nSome '''bold''' text."
        to_md = tracwiki_to_markdown(original)
        to_tw = markdown_to_tracwiki(to_md.text)
        # Should preserve headings and bold
        self.assertIn("== Section ==", to_tw)
        self.assertIn("'''bold'''", to_tw)


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

    def test_tw_to_md_code_block_sh(self):
        """Test TracWiki sh code block converts to Markdown bash."""
        tw = """{{{#!sh
echo "hello"
}}}"""
        result = tracwiki_to_markdown(tw)
        self.assertIn("```bash", result.text)
        self.assertIn('echo "hello"', result.text)

    def test_tw_to_md_code_block_python_unchanged(self):
        """Test TracWiki python code block stays python in Markdown."""
        tw = """{{{#!python
print("hello")
}}}"""
        result = tracwiki_to_markdown(tw)
        self.assertIn("```python", result.text)

    # ==========================================================================
    # Round-trip tests: verify asymmetric mappings work correctly
    # ==========================================================================

    def test_roundtrip_bash_sh_bash(self):
        """Test bash -> sh -> bash round-trip."""
        original_md = """```bash
echo "test"
```"""
        to_tw = markdown_to_tracwiki(original_md)
        # Should be sh in TracWiki
        self.assertIn("{{{#!sh", to_tw)

        back_to_md = tracwiki_to_markdown(to_tw)
        # Should be bash in Markdown (canonical form)
        self.assertIn("```bash", back_to_md.text)

    def test_roundtrip_shell_sh_bash(self):
        """Test shell -> sh -> bash (normalizes to canonical bash)."""
        original_md = """```shell
ls -la
```"""
        to_tw = markdown_to_tracwiki(original_md)
        self.assertIn("{{{#!sh", to_tw)

        back_to_md = tracwiki_to_markdown(to_tw)
        # Returns canonical form 'bash', not original 'shell'
        self.assertIn("```bash", back_to_md.text)

    def test_roundtrip_js_javascript_js(self):
        """Test js -> javascript -> javascript (one-way normalization)."""
        original_md = """```js
console.log("x");
```"""
        to_tw = markdown_to_tracwiki(original_md)
        self.assertIn("{{{#!javascript", to_tw)

        back_to_md = tracwiki_to_markdown(to_tw)
        # javascript is identity, stays as javascript
        self.assertIn("```javascript", back_to_md.text)

    def test_roundtrip_python_unchanged(self):
        """Test python stays python through round-trip."""
        original_md = """```python
x = 1
```"""
        to_tw = markdown_to_tracwiki(original_md)
        self.assertIn("{{{#!python", to_tw)

        back_to_md = tracwiki_to_markdown(to_tw)
        self.assertIn("```python", back_to_md.text)


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
    def test_explicit_markdown_target(self, mock_caps):
        """target_format='markdown' with tracwiki input converts to markdown."""
        from trac_mcp_server.converters.common import auto_convert

        mock_config = MagicMock()
        result = self._run(
            auto_convert(
                "= Heading =\n\nParagraph",
                mock_config,
                target_format="markdown",
            )
        )

        self.assertTrue(result.converted)
        self.assertEqual(result.target_format, "markdown")
        self.assertIn("# Heading", result.text)
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

        # TracWiki input should be converted to markdown
        result = self._run(
            auto_convert(
                "= Heading =\n\nText", mock_config, target_format=None
            )
        )

        self.assertEqual(result.target_format, "markdown")
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

    def test_heading_roundtrip_strips_anchor(self):
        """``== Heading == #anchor`` round-trips back to ``## Heading``
        (the explicit anchor is implicit on the Markdown side).
        """
        from trac_mcp_server.converters.tracwiki_to_markdown import (
            tracwiki_to_markdown,
        )

        markdown_source = "## Wiki task index page schema"
        tracwiki_form = markdown_to_tracwiki(
            markdown_source, heading_anchors=True
        )
        self.assertEqual(
            tracwiki_form,
            "== Wiki task index page schema == #wiki-task-index-page-schema",
        )
        roundtripped = tracwiki_to_markdown(tracwiki_form).text.strip()
        self.assertEqual(roundtripped, "## Wiki task index page schema")

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

    def test_ticket_19_macro_placeholder_restored(self):
        """[MACRO: Name(args)] round-trips back to [[Name(args)]].

        `tracwiki_to_markdown`'s "bracket" mode placeholder for an
        unresolved macro used to pass through markdown_to_tracwiki as
        literal text, permanently flattening the macro the first time a
        page carrying one was edited via the Markdown path.
        """
        self.assertEqual(
            markdown_to_tracwiki("[MACRO: PageOutline]"),
            "[[PageOutline]]",
        )
        self.assertEqual(
            markdown_to_tracwiki("[MACRO: TOC(depth=2)]"),
            "[[TOC(depth=2)]]",
        )

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

    def test_ticket_40_server_relative_link_round_trips(self):
        """The target survives a markdown -> tracwiki -> markdown round
        trip byte-for-byte, mirroring the existing TracLink round-trip
        guard (`test_traclink_round_trips_through_both_converters`).
        """
        markdown = (
            "[trac_mcp_server ticket 13](//trac_mcp_server/ticket/13)"
        )
        tracwiki = markdown_to_tracwiki(markdown)
        self.assertEqual(tracwiki_to_markdown(tracwiki).text, markdown)

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

    def test_ticket_45_table_round_trip_byte_identical(self):
        """The exact fixture from the ticket round-trips byte-identical
        through markdown -> tracwiki -> markdown.
        """
        md = (
            "| Context | Effect |\n"
            "|---|---|\n"
            "| plain prose | consumed |\n"
            "| `{{{#!div}}}`, `||||`, `{{{#!table}}}` | consumed |\n"
            "| table cell | consumed |"
        )
        tracwiki = markdown_to_tracwiki(md)
        self.assertEqual(tracwiki_to_markdown(tracwiki).text, md)

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


class TestReadPathConverterTicketRegressions(unittest.TestCase):
    """Regression tests for tickets against the tracwiki_to_markdown
    (read-path) converter, kept together since they were found and
    fixed in the same sweep.
    """

    def test_ticket_30_br_terminated_line_no_blank_line(self):
        """A [[BR]]-terminated line followed by the source's own "\\n"
        must become a single hard-break line, not a blank-line paragraph
        break -- the exact `auto_pm` #10 symptom this ticket reintroduced
        via the read path.
        """
        tracwiki = (
            "**Scope:** `substrate:trac`[[BR]]\n"
            "**Type:** hard-rule[[BR]]\n"
            "**Status:** active"
        )
        result = tracwiki_to_markdown(tracwiki)
        self.assertEqual(
            result.text,
            "**Scope:** `substrate:trac`  \n"
            "**Type:** hard-rule  \n"
            "**Status:** active",
        )
        # No blank line anywhere -- that would be a paragraph break.
        self.assertNotIn("\n\n", result.text)

    def test_ticket_30_br_without_trailing_newline(self):
        """[[BR]] with no following source newline still produces a
        hard break, not a bare (soft-break) "\\n".
        """
        result = tracwiki_to_markdown("Line one[[BR]]Line two")
        self.assertEqual(result.text, "Line one  \nLine two")

    def test_ticket_31_code_block_body_byte_identical(self):
        """A {{{ }}} code block body that merely resembles TracWiki
        markup must survive conversion byte-identical -- the fixture
        from the ticket's own "Test coverage to add" section.
        """
        body = (
            "<Target>\n"
            "[Label](Target)\n"
            " * not a bullet\n"
            "||a||b||\n"
            "''italics'' and '''bold'''\n"
            "= heading =\n"
            "trailing backslash \\"
        )
        tracwiki = "{{{\n" + body + "\n}}}"
        result = tracwiki_to_markdown(tracwiki)
        self.assertEqual(result.text, f"```\n{body}\n```")

    def test_ticket_31_code_block_body_uwsgi_log(self):
        """The exact uWSGI log excerpt from the ticket: bracket-shaped
        log text must not be reparsed as a TracWiki [url text] link.
        """
        body = (
            "<uWSGI> getting INI configuration from /tmp/trac-fg.ini\n"
            "*** Starting uWSGI on [Aug 17 08:18:13 2026](Mon) ***\n"
            'open("--master"): Permission denied [line 288](core/logging.c)'
        )
        tracwiki = "{{{\n" + body + "\n}}}"
        result = tracwiki_to_markdown(tracwiki)
        self.assertEqual(result.text, f"```\n{body}\n```")

    def test_ticket_31_code_block_with_language_body_byte_identical(
        self,
    ):
        """Same shielding applies to {{{#!lang ... }}} blocks."""
        body = "* not a bullet\n''not italic''"
        tracwiki = "{{{#!text\n" + body + "\n}}}"
        result = tracwiki_to_markdown(tracwiki)
        self.assertEqual(result.text, f"```text\n{body}\n```")

    def test_ticket_31_inline_code_span_shielded(self):
        """Inline `code` spans get the same shielding as fenced blocks."""
        result = tracwiki_to_markdown(
            "Config uses `[[BR]]` and `''never italic''` here."
        )
        self.assertIn("`[[BR]]`", result.text)
        self.assertIn("`''never italic''`", result.text)

    def test_ticket_43_br_in_code_span_survives_unbalanced_backtick(
        self,
    ):
        """A well-formed `[[BR]]` code span must survive even when an
        unrelated, unpaired backtick earlier in the text (e.g. a stray
        apostrophe-like backtick) throws off the nearest-neighbor pairing
        that _convert_code_blocks uses to find code spans. Without a
        backtick-adjacency backstop, that mis-pairing steals one of
        `[[BR]]`'s own delimiters, leaving the macro's literal backticks
        in place but the macro itself unshielded and corrupted into a
        hard break -- the exact `auto_pm` Meta/PageConventions symptom
        from ticket #43. Minimal case, matching the ticket's own repro.
        """
        result = tracwiki_to_markdown(
            "Note: it`s odd. Then `[[BR]]` appears."
        )
        self.assertEqual(
            result.text, "Note: it`s odd. Then `[[BR]]` appears."
        )

    def test_ticket_43_br_minimal_case(self):
        """The ticket's literal minimal-case fixture round-trips."""
        result = tracwiki_to_markdown("a `[[BR]]` b")
        self.assertEqual(result.text, "a `[[BR]]` b")

    def test_ticket_43_unknown_macro_in_code_span_survives_unbalanced_backtick(
        self,
    ):
        """The same backstop applies to any `[[...]]` macro, not just
        `[[BR]]` -- the ticket's remediation section calls this out
        explicitly.
        """
        result = tracwiki_to_markdown(
            "Note: it`s odd. Then `[[TOC]]` appears."
        )
        self.assertEqual(
            result.text, "Note: it`s odd. Then `[[TOC]]` appears."
        )

    def test_ticket_43_br_adjacent_to_unrelated_closing_backtick_still_converts(
        self,
    ):
        """The backtick-adjacency backstop must require backticks on
        *both* sides -- `[[BR]] legitimately follows the closing backtick
        of a *different*, preceding code span with no space in between
        (ticket #30's own fixture), and that must still convert to a hard
        break rather than being mistaken for being wrapped itself.
        """
        result = tracwiki_to_markdown("`substrate:trac`[[BR]]Next")
        self.assertEqual(result.text, "`substrate:trac`  \nNext")


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
    ``markdown_to_tracwiki``, but the rewrite actually happened in
    ``tracwiki_to_markdown``'s ``[target label]`` matcher.
    """

    SENTINEL = "[auto-pm: state NEEDS_EDIT]"

    def test_sentinel_preserved_tracwiki_to_markdown(self):
        """The direction that actually carried the bug."""
        result = tracwiki_to_markdown(self.SENTINEL)
        self.assertEqual(result.text, self.SENTINEL)

    def test_sentinel_preserved_markdown_to_tracwiki(self):
        """The direction the ticket named; already correct, pinned here."""
        self.assertEqual(
            markdown_to_tracwiki(self.SENTINEL).strip(), self.SENTINEL
        )

    def test_sentinel_survives_full_round_trip(self):
        """md -> tw -> md leaves the marker byte-identical."""
        once = markdown_to_tracwiki(self.SENTINEL)
        twice = tracwiki_to_markdown(once)
        self.assertEqual(twice.text.strip(), self.SENTINEL)

    def test_generic_colon_prose_preserved(self):
        """Any '[word: word]' construct, not just auto-pm markers."""
        for text in ("[note: see below]", "[TODO: fix this]"):
            with self.subTest(text=text):
                self.assertEqual(tracwiki_to_markdown(text).text, text)

    def test_real_links_still_convert(self):
        """Regression guard: valid targets are unaffected."""
        self.assertEqual(
            tracwiki_to_markdown(
                "[https://example.com Link Text]"
            ).text,
            "[Link Text](https://example.com)",
        )
        self.assertEqual(
            tracwiki_to_markdown("[wiki:WikiPage Wiki Link]").text,
            "[Wiki Link](wiki:WikiPage)",
        )


class TestOrphanBracketDoesNotSpanLines(unittest.TestCase):
    """Ticket #14: '[text]' must not consume a '(url)' on a later line.

    The old matcher used ``\\S+`` for the target (which matches ``]`` and
    ``(``) and ``\\s+`` for the separator (which matches newlines), so a
    complete link on one line plus an orphan bracket on the next collapsed
    into a single mangled construct. As with #13, the ticket named
    ``markdown_to_tracwiki`` but the defect was in ``tracwiki_to_markdown``.
    """

    BLOCK = (
        "[file1.diff](attachment:file1.diff)\n"
        "[attachment:file2.diff]\n"
        "(claude-code:sonnet)\n"
    )

    def test_each_line_renders_independently(self):
        """No construct spans a line break."""
        result = tracwiki_to_markdown(self.BLOCK).text
        lines = result.strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(
            lines[0], "[file1.diff](attachment:file1.diff)"
        )
        self.assertEqual(lines[1], "<attachment:file2.diff>")
        self.assertEqual(lines[2], "(claude-code:sonnet)")

    def test_no_nested_mush(self):
        """The reported mangled output must not reappear."""
        result = tracwiki_to_markdown(self.BLOCK).text
        self.assertNotIn("[[", result)
        self.assertNotIn("](file1.diff]", result)

    def test_orphan_bracket_then_parenthetical(self):
        """Minimal case: orphan '[foo]' then '(bar)' on the next line."""
        result = tracwiki_to_markdown("[foo]\n(bar)\n").text
        lines = result.strip().splitlines()
        self.assertEqual(lines[0], "<foo>")
        self.assertEqual(lines[1], "(bar)")

    def test_target_cannot_swallow_closing_bracket(self):
        """A target may not contain ']' — that was the swallow vector."""
        result = tracwiki_to_markdown("[wiki:A a] [wiki:B b]").text
        self.assertEqual(result, "[a](wiki:A) [b](wiki:B)")

    def test_single_line_link_still_converts(self):
        """Regression guard: the ordinary form is untouched."""
        self.assertEqual(
            tracwiki_to_markdown("[wiki:Page label]").text,
            "[label](wiki:Page)",
        )

    def test_attachment_block_round_trips(self):
        """tw -> md -> tw returns the orphan bracket unchanged."""
        as_md = tracwiki_to_markdown(self.BLOCK).text
        back = markdown_to_tracwiki(as_md)
        self.assertIn("[attachment:file2.diff]", back)
        self.assertIn("(claude-code:sonnet)", back)


if __name__ == "__main__":
    unittest.main()
