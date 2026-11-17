"""Entity and numeric character references in Markdown text (ticket #239).

**The defect.** CommonMark decodes ``&amp;``, ``&copy;``, ``&#35;`` and
``&#x20;`` in text, but mistune 3.2 keeps them as raw text and the writer
emitted them unchanged. Trac escapes the ``&``, so the page showed
``&#x20;`` literally (``13 MΩ.&#x20;`` in a pasted ChatGPT sample, #216
comment 9).

**The fix.** ``text()`` decodes the references CommonMark recognises: a
name followed by ``;``, ``&#`` plus 1-7 decimal digits plus ``;``, or
``&#x`` plus 1-6 hex digits plus ``;``. A semicolon-less legacy name
(``&copy 2020``), which ``html.unescape`` would decode, stays literal, as
do unknown names, code spans, code blocks and a backslash-escaped ``\\&``.
"""

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # named
        ("Tom &amp; Jerry &copy; 2026", "Tom & Jerry © 2026"),
        # hex, the shape seen in the ChatGPT sample
        ("13 MΩ.&#x20;Next", "13 MΩ. Next"),
        ("&#X41;", "A"),
        # HTML-looking references decode to text Trac escapes itself
        ("&lt;b&gt; tag", "<b> tag"),
        # inside emphasis, a heading, a link label
        ("*a &amp; b*", "''a & b''"),
        ("# T &amp; U", "= T & U ="),
        (
            "[R&amp;D](https://e.com/rd)",
            "[https://e.com/rd R&D]",
        ),
        # a reference that decodes to CamelCase still gets the #27 escape
        ("&#87;iFi", "!WiFi"),
        # a named reference to a quote or a no-break space
        ("&quot;q&quot;", '"q"'),
        ("&nbsp;x", " x"),
        # CommonMark: code point 0 and a surrogate give U+FFFD
        ("&#0;", "�"),
        ("&#xD800;", "�"),
    ],
)
def test_reference_in_text_is_decoded(source, expected):
    assert markdown_to_tracwiki(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        # CommonMark requires the semicolon; html.unescape does not
        "&copy 2020 AT&T",
        "&amp without semicolon",
        # unknown name, also one with a legacy-name prefix
        "&notanentity; stays",
        "&ampfoo; stays",
        # too many digits for a numeric reference
        "&#12345678; stays",
        "&#x1234567; stays",
        # a bare ampersand
        "a & b",
        # a decoded `#12` would become a Trac ticket link
        "issue &#35;12",
        # a decoded `||` would add a table column
        "x &#124;&#124; y",
        # a decoded `{{{` would open a code block
        "&#123;&#123;&#123;x",
        # a decoded `[[BR]]` would become a macro
        "&#91;&#91;BR&#93;&#93;",
        # a decoded `''` would toggle italics
        "a &#39;&#39;b&#39;&#39;",
        # a decoded backtick would open an inline code span
        "&#96;x&#96;",
        # a decoded line break would split a heading, row or paragraph
        "a&#10;b",
        "a&#13;b",
        "a&#x2028;b",
        # control characters: U+0001 would vanish, U+007F is DEL
        "a&#1;b",
        "a&#127;b",
        # a C1 control; CommonMark does no windows-1252 remapping
        "a&#150;b",
    ],
)
def test_non_reference_stays_literal(source):
    assert markdown_to_tracwiki(source) == source


def test_reference_to_a_bang_stays_literal_and_camelcase_is_still_escaped():
    # The reference stays literal, so it is not consumed as Trac's `!`
    # escape, and the #27 CamelCase escape still guards the word: Trac
    # shows "&#33;WikiPage".
    assert markdown_to_tracwiki("&#33;WikiPage") == "&#33;!WikiPage"


def test_code_span_keeps_reference():
    assert markdown_to_tracwiki("`&amp;` and &amp;") == "`&amp;` and &"


def test_code_block_keeps_reference():
    source = "```\nx &amp; y &#x20;\n```"
    assert markdown_to_tracwiki(source) == "{{{\nx &amp; y &#x20;\n}}}"


def test_table_cell_reference_to_a_pipe_stays_literal():
    source = "| a &#124; b | c |\n|---|---|\n| d | e |"
    assert (
        markdown_to_tracwiki(source)
        == "||=a &#124; b=||=c=||\n||d||e||"
    )


class TestLineStart:
    """Two leading spaces would make Trac render a blockquote."""

    def test_leading_spaces_are_stripped(self):
        assert markdown_to_tracwiki("&#x20;&#x20;x") == "x"

    def test_leading_spaces_after_a_newline_are_stripped(self):
        assert markdown_to_tracwiki("a\n&#x20;&#x20;b") == "a\nb"

    def test_mid_line_spaces_still_decode(self):
        assert markdown_to_tracwiki("*a*&#x20;b") == "''a'' b"


def test_backslash_escaped_ampersand_stays_literal():
    assert markdown_to_tracwiki(r"\&amp; x") == "&amp; x"
