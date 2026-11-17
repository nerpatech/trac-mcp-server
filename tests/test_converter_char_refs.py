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
        # decimal
        ("issue &#35;12", "issue #12"),
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
    ],
)
def test_non_reference_stays_literal(source):
    assert markdown_to_tracwiki(source) == source


def test_code_span_keeps_reference():
    assert markdown_to_tracwiki("`&amp;` and &amp;") == "`&amp;` and &"


def test_code_block_keeps_reference():
    source = "```\nx &amp; y &#x20;\n```"
    assert "x &amp; y &#x20;" in markdown_to_tracwiki(source)


def test_backslash_escaped_ampersand_stays_literal():
    assert markdown_to_tracwiki(r"\&amp; x") == "&amp; x"
