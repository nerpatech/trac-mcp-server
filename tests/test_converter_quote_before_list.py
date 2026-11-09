"""A quote head directly before a list marker (ticket #182).

**Measured on /trac_test** (``convert_preview``, ``format=tracwiki``): the
indented lines before a list marker are always a ``<blockquote>``, nested only
by relative indent, and the list after them is always a top-level list after
the blockquote. This held for the head at column 1, 2, 4 and 6, for two head
lines, for ``"  q" / "    deeper"`` (a nested blockquote), for a marker deeper
than the quote (``"  q" / "   * c"``), for an ordered marker (``"  q" /
"  1. c"``) and with no paragraph before. It is exactly what Trac renders
with a blank line before the marker.

The write leg writes a Markdown quote followed by a list as the TracWiki
quote, one blank line, then the list, so the list is not a lazy continuation
of the quote. The rows below pin that, and the shapes that keep the bytes
``master`` (11e5512) produced: a quote run straight after a list line (no
blank line between), a run whose first line is a marker, a head that is a
table row, and a run that starts straight after a block inside an open item.
A quote run after a BLANK line that ended a list is #183's (``CHANGED_BY_183``
pins the rows that moved).
"""

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

# name -> (source, the Markdown, what mistune renders, the round trip)
CASES = {
    "col2": (
        "P\n\n  q\n * c\n",
        "P\n\n> q\n\n - c\n",
        "<p>P</p>\n<blockquote>\n<p>q</p>\n</blockquote>\n"
        "<ul>\n<li>c</li>\n</ul>\n",
        "P\n\n  q\n\n * c",
    ),
    "two head lines": (
        "P\n\n  q\n  r\n * c\n",
        "P\n\n> q\n> r\n\n - c\n",
        "<p>P</p>\n<blockquote>\n<p>q\nr</p>\n</blockquote>\n"
        "<ul>\n<li>c</li>\n</ul>\n",
        "P\n\n  q\n  r\n\n * c",
    ),
    "marker deeper than the quote": (
        "P\n\n  q\n   * c\n",
        "P\n\n> q\n\n   - c\n",
        "<p>P</p>\n<blockquote>\n<p>q</p>\n</blockquote>\n"
        "<ul>\n<li>c</li>\n</ul>\n",
        "P\n\n  q\n\n * c",
    ),
    "ordered marker": (
        "P\n\n  q\n  1. c\n",
        "P\n\n> q\n\n  1. c\n",
        "<p>P</p>\n<blockquote>\n<p>q</p>\n</blockquote>\n"
        "<ol>\n<li>c</li>\n</ol>\n",
        "P\n\n  q\n\n 1. c",
    ),
    "ordered not from 1": (
        "P\n\n  q\n  4. c\n",
        "P\n\n> q\n\n  4. c\n",
        "<p>P</p>\n<blockquote>\n<p>q</p>\n</blockquote>\n"
        '<ol start="4">\n<li>c</li>\n</ol>\n',
        "P\n\n  q\n\n 4. c",
    ),
    "start of document": (
        "  q\n * c\n",
        "> q\n\n - c\n",
        "<blockquote>\n<p>q</p>\n</blockquote>\n<ul>\n<li>c</li>\n</ul>\n",
        "  q\n\n * c",
    ),
    "a longer list after": (
        "P\n\n  q\n * c\n * d\n   cont\n",
        "P\n\n> q\n\n - c\n - d\n   cont\n",
        "<p>P</p>\n<blockquote>\n<p>q</p>\n</blockquote>\n"
        "<ul>\n<li>c</li>\n<li>d\ncont</li>\n</ul>\n",
        "P\n\n  q\n\n * c\n * d\n   cont",
    ),
    "an earlier quote run": (
        "P\n\n  q0\n\n  q\n * c\n",
        "P\n\n> q0\n\n> q\n\n - c\n",
        "<p>P</p>\n<blockquote>\n<p>q0</p>\n</blockquote>\n"
        "<blockquote>\n<p>q</p>\n</blockquote>\n<ul>\n<li>c</li>\n</ul>\n",
        "P\n\n  q0\n\n  q\n\n * c",
    ),
    # A `- a` line inside a code block is content, not a list item.
    "a list-looking line in a code block does not keep a list open": (
        "{{{\n- a\n}}}\n\n  q\n * c\n",
        "```\n- a\n```\n\n> q\n\n - c\n",
        "<pre><code>- a\n</code></pre>\n<blockquote>\n<p>q</p>\n"
        "</blockquote>\n<ul>\n<li>c</li>\n</ul>\n",
        "{{{\n- a\n}}}\n\n  q\n\n * c",
    ),
    # The second run follows a list, but a blank line ended it: Trac reads
    # `r` as a quote too, so it is cut (#183). On master (#182) only the
    # first head was a quote and `r` stayed in the list's run.
    "a later quote run after a blank line is a quote too": (
        "P\n\n  q\n * c\n\n  r\n * d\n",
        "P\n\n> q\n\n - c\n\n> r\n\n - d\n",
        "<p>P</p>\n<blockquote>\n<p>q</p>\n</blockquote>\n"
        "<ul>\n<li>c</li>\n</ul>\n<blockquote>\n<p>r</p>\n</blockquote>\n"
        "<ul>\n<li>d</li>\n</ul>\n",
        "P\n\n  q\n\n * c\n\n  r\n\n * d",
    ),
}

# name -> (source, master's read-leg bytes, master's round trip): the
# shapes this ticket leaves alone.
UNCHANGED = {
    "a quote run after a list": (
        "P\n\n * a\n  q\n * c\n",
        "P\n\n - a\n  q\n - c\n",
        "P\n\n * a\n   q\n * c",
    ),
    "a head that is a table row": (
        "P\n\n  ||t||\n * c\n",
        "P\n\n| t |\n|---|\n - c\n",
        "P\n\n||=t=||\n\n * c",
    ),
    "a run that starts with a marker": (
        "P\n\n * c\n  more\n",
        "P\n\n - c\n  more\n",
        "P\n\n * c\n   more",
    ),
    # A column-0 line that Trac reads as a list item (`- `, `* `, `1. `,
    # `a. `) keeps its list open: an indented line after it is the item's
    # continuation, and a marker after that is a nested item (measured on
    # /trac_test for the WikiFormatting shape: one `<ul>` with a nested
    # `<ul>`). The cut would turn the line into a citation.
    "a column-0 bullet item": (
        "P\n\n- a\n  q\n  - b\n",
        "P\n\n- a\n  q\n  - b\n",
        "P\n\n * a\n   q\n   * b",
    ),
    "a column-0 bullet item after a list": (
        " * x\n- a\n  q\n  - b\n",
        " - x\n\n- a\n  q\n  - b\n",
        " * x\n * a\n   q\n   * b",
    ),
    # #182 pinned this row at master's bytes (`> q`, the write leg moving
    # `q` out of the item), which were wrong: Trac renders `q` as a
    # continuation of item `a`. #190 fixes it: `q` stays in the item, and
    # the round trip writes it at the item's text column.
    "a column-0 bullet item, no marker after the quote": (
        " * x\n- a\n  q\n",
        " - x\n\n- a\n  q\n",
        " * x\n * a\n   q",
    ),
    "a column-0 star item": (
        "P\n\n* a\n  q\n  - b\n",
        "P\n\n* a\n  q\n  - b\n",
        "P\n\n * a\n   q\n   * b",
    ),
    "a column-0 ordered item": (
        "P\n\n1. a\n  q\n  - b\n",
        "P\n\n1. a\n  q\n  - b\n",
        "P\n\n 1. a\n    q\n\n * b",
    ),
    "a column-0 bullet item straight after a paragraph line": (
        "P\n- a\n  q\n  - b\n",
        "P\n- a\n  q\n  - b\n",
        "P\n\n * a\n   q\n   * b",
    ),
    # A marker indented 4+ columns, or by a tab, is indented code to
    # CommonMark once a blank line precedes it, so the cut would lose the
    # list (HEAD: `    - c` after a blank line, back as a `{{{` block).
    # Master's bytes, which kept the list, come back.
    "a marker indented 4 columns": (
        "P\n  q\n    * c\n",
        "P\n  q\n    - c\n",
        "P\nq\n- c",
    ),
    "a marker indented by a tab": (
        "P\n  q\n\t* c\n",
        "P\n  q\n\t* c\n",
        "P\nq\n* c",
    ),
    "a marker indented 5 columns": (
        "P\n\n  q\n     * c\n",
        "P\n\n  q\n     - c\n",
        "P\n\nq\n- c",
    ),
    "a head that is a heading": (
        "P\n\n  = H =\n * c\n",
        "P\n\n  = H =\n - c\n",
        "P\n\n= H =\n\n * c",
    ),
    "straight after a block inside an open item": (
        " * a\n {{{\n x\n }}}\n  q\n * c\n",
        " - a\n   ```\n   x\n   ```\n  q\n - c\n",
        " * a\n   {{{\n   x\n   }}}\n   q\n * c",
    ),
}


# name -> (source, the read-leg bytes, the round trip): rows that pinned
# master's bytes in this file and are #183's shape (an indented quote run
# after the blank line that ended a list, then a marker). They are quotes to
# Trac, so they are cut like the first head (blank line, then the list); the
# blank line they gain is the one #182 accepted.
CHANGED_BY_183 = {
    "a quote run after a list, blank line between": (
        "P\n\n * a\n\n  q\n * c\n",
        "P\n\n - a\n\n> q\n\n - c\n",
        "P\n\n * a\n\n  q\n\n * c",
    ),
    "a quote run two lines after a block in an open item": (
        " * a\n {{{\n c\n }}}\n tail\n\n  more\n * b\n",
        " - a\n   ```\n   c\n   ```\n   tail\n\n> more\n\n - b\n",
        " * a\n   {{{\n   c\n   }}}\n   tail\n\n  more\n\n * b",
    ),
}


@pytest.mark.parametrize("name", CASES)
def test_a_quote_head_before_a_list_is_written_with_a_blank_line(name):
    _source, markdown, _html_expected, trip = CASES[name]
    assert markdown_to_tracwiki(markdown) == trip


def test_the_head_is_a_quote_not_a_paragraph():
    """The reported failure, by name."""
    paragraph = markdown_to_tracwiki("P\n\n> q\n\n - c\n")
    assert paragraph == "P\n\n  q\n\n * c"


@pytest.mark.parametrize("name", UNCHANGED)
def test_out_of_scope_shapes_keep_masters_bytes(name):
    _source, markdown, trip = UNCHANGED[name]
    assert markdown_to_tracwiki(markdown) == trip


@pytest.mark.parametrize("name", CHANGED_BY_183)
def test_quote_runs_after_a_blank_line_are_cut_like_the_first_head(
    name,
):
    _source, markdown, trip = CHANGED_BY_183[name]
    assert markdown_to_tracwiki(markdown) == trip
