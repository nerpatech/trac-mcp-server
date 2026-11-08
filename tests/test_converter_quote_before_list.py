"""Indented quote lines directly before a list marker (ticket #182).

**The defect.** ``_convert_indented_blocks`` (#73) classifies each maximal
run of consecutive indented lines, and a run that holds a list marker was
left untouched ("not a quote", step 4). When indented quote lines came
directly before the run's first marker, they stayed raw indented text in the
Markdown, and the round trip turned them into a paragraph or a code block::

    P / blank / "  q" / " * c"   ->  ... back as the paragraph "q"
    P / blank / "    q4" / " * c"  ->  ... back as a ``{{{`` block, the list lost

**Measured on /trac_test** (``convert_preview``, ``format=tracwiki``): the
indented lines before the marker are always a ``<blockquote>``, nested only
by relative indent, and the list after them is always a top-level list after
the blockquote. This held for the head at column 1, 2, 4 and 6, for two head
lines, for ``"  q" / "    deeper"`` (a nested blockquote), for a marker deeper
than the quote (``"  q" / "   * c"``), for an ordered marker (``"  q" /
"  1. c"``) and with no paragraph before. It is exactly what Trac renders
with a blank line before the marker, a shape the read leg already got right.

**The fix (read leg only).** The run is cut at its first marker line when no
list can be open before it, so the head is classified on its own (canonical
indent: ``> `` lines; anything else: the ``tracwiki-unconverted`` fallback)
and the rest is the next run. Exactly one blank line then separates the
head from the list, so CommonMark ends the quote and the list is not a lazy
continuation of it. The write leg does not change: the round trip gains that
one blank line, which renders the same in Trac.

Unchanged, with the bytes ``master`` (11e5512) produced: a quote run after a
list (#181/#183), a run whose first line is a marker, a head that is a table
row, and a run that starts straight after a block inside an open item.
"""

import mistune
import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    _MISTUNE_PLUGINS,
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    tracwiki_to_markdown,
)

_FENCE = "```tracwiki-unconverted\n"
_PRE = '<pre><code class="language-tracwiki-unconverted">'

# name -> (source, the Markdown, what mistune renders, the round trip)
CASES = {
    "col2": (
        "P\n\n  q\n * c\n",
        "P\n\n> q\n\n - c\n",
        "<p>P</p>\n<blockquote>\n<p>q</p>\n</blockquote>\n"
        "<ul>\n<li>c</li>\n</ul>\n",
        "P\n\n  q\n\n * c",
    ),
    "col4 (fallback)": (
        "P\n\n    q4\n * c\n",
        f"P\n\n{_FENCE}    q4\n```\n\n - c\n",
        f"<p>P</p>\n{_PRE}    q4\n</code></pre>\n<ul>\n<li>c</li>\n</ul>\n",
        "P\n\n    q4\n\n * c",
    ),
    "col1 (fallback)": (
        "P\n\n q1\n * c\n",
        f"P\n\n{_FENCE} q1\n```\n\n - c\n",
        f"<p>P</p>\n{_PRE} q1\n</code></pre>\n<ul>\n<li>c</li>\n</ul>\n",
        "P\n\n q1\n\n * c",
    ),
    "col6 (fallback)": (
        "P\n\n      q6\n * c\n",
        f"P\n\n{_FENCE}      q6\n```\n\n - c\n",
        f"<p>P</p>\n{_PRE}      q6\n</code></pre>\n<ul>\n<li>c</li>\n</ul>\n",
        "P\n\n      q6\n\n * c",
    ),
    "two head lines": (
        "P\n\n  q\n  r\n * c\n",
        "P\n\n> q\n> r\n\n - c\n",
        "<p>P</p>\n<blockquote>\n<p>q\nr</p>\n</blockquote>\n"
        "<ul>\n<li>c</li>\n</ul>\n",
        "P\n\n  q\n  r\n\n * c",
    ),
    # 4 spaces under a 2-space head is not a canonical nesting, so the
    # head goes out through the fallback, as it does with a blank line.
    "nested quote (fallback)": (
        "P\n\n  q\n    deeper\n * c\n",
        f"P\n\n{_FENCE}  q\n    deeper\n```\n\n - c\n",
        f"<p>P</p>\n{_PRE}  q\n    deeper\n</code></pre>\n"
        "<ul>\n<li>c</li>\n</ul>\n",
        "P\n\n  q\n    deeper\n\n * c",
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
    # The second run follows a list, so it is out of scope (#181/#183) and
    # keeps master's bytes; only the first head is a quote.
    "a later quote run after the list is left alone": (
        "P\n\n  q\n * c\n\n  r\n * d\n",
        "P\n\n> q\n\n - c\n\n  r\n - d\n",
        "<p>P</p>\n<blockquote>\n<p>q</p>\n</blockquote>\n"
        "<ul>\n<li>c</li>\n</ul>\n<p>r</p>\n<ul>\n<li>d</li>\n</ul>\n",
        "P\n\n  q\n\n * c\n\nr\n\n * d",
    ),
    "the list's run needs a past-bound split": (
        "P\n\n  q\n * c\n      past\n",
        f"P\n\n> q\n\n - c\n\n{_FENCE}      past\n```\n",
        "<p>P</p>\n<blockquote>\n<p>q</p>\n</blockquote>\n"
        f"<ul>\n<li>c</li>\n</ul>\n{_PRE}      past\n</code></pre>\n",
        "P\n\n  q\n\n * c\n\n      past",
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
    "a quote run after a list, blank line between": (
        "P\n\n * a\n\n  q\n * c\n",
        "P\n\n - a\n\n  q\n - c\n",
        "P\n\n * a\n\nq\n\n * c",
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
    # The block's lines are not prose: they leave the open-list flag alone,
    # so a quote run further on is still after a list.
    "after text that follows a block in an open item": (
        " * a\n   {{{\n   c\n   }}}\n   tail\n\n   more\n * b\n",
        " - a\n   ```\n   c\n   ```\n   tail\n\n   more\n - b\n",
        " * a\n   {{{\n   c\n   }}}\n   tail [[BR]][[BR]]\n   more\n * b",
    ),
    "a quote run two lines after a block in an open item": (
        " * a\n {{{\n c\n }}}\n tail\n\n  more\n * b\n",
        " - a\n   ```\n   c\n   ```\n   tail\n\n  more\n - b\n",
        " * a\n   {{{\n   c\n   }}}\n   tail\n\nmore\n\n * b",
    ),
    "a head that is a heading": (
        "P\n\n  = H =\n * c\n",
        "P\n\n  = H =\n - c\n",
        "P\n\n= H =\n\n * c",
    ),
    "a definition term in the run: the whole run falls back": (
        "P\n\n  q\n  term:: x\n * c\n",
        "P\n\n```tracwiki-unconverted\n  q\n  term:: x\n * c\n```\n",
        "P\n\n  q\n  term:: x\n * c",
    ),
    "straight after a block inside an open item": (
        " * a\n {{{\n x\n }}}\n  q\n * c\n",
        " - a\n   ```\n   x\n   ```\n  q\n - c\n",
        " * a\n   {{{\n   x\n   }}}\n   q\n * c",
    ),
}


def _md(wiki: str) -> str:
    return tracwiki_to_markdown(wiki).text


def _html(markdown: str) -> str:
    return mistune.create_markdown(plugins=_MISTUNE_PLUGINS)(markdown)


@pytest.mark.parametrize("name", CASES)
def test_markdown_puts_a_blank_line_between_the_head_and_the_list(name):
    source, markdown, _html_expected, _trip = CASES[name]
    assert _md(source) == markdown


@pytest.mark.parametrize("name", CASES)
def test_mistune_renders_the_head_then_a_list_outside_it(name):
    """A blockquote (or the fallback ``<pre>``), then the list: the list is
    a sibling, not inside the blockquote and not a lazy continuation."""
    source, _markdown, html, _trip = CASES[name]
    assert _html(_md(source)) == html


@pytest.mark.parametrize("name", CASES)
def test_round_trip_gains_one_blank_line(name):
    source, _markdown, _html_expected, trip = CASES[name]
    assert markdown_to_tracwiki(_md(source)) == trip


def test_the_round_trip_is_what_trac_renders_with_a_blank_line_before():
    """The blank line is the only change: the source with the blank line in
    already converts to the same Markdown (the shape the read leg always
    got right)."""
    assert _md("P\n\n  q\n\n * c\n") == _md("P\n\n  q\n * c\n")


def test_the_head_is_a_quote_not_a_paragraph_or_a_code_block():
    """The two reported failures, by name."""
    paragraph = markdown_to_tracwiki(_md("P\n\n  q\n * c\n"))
    assert paragraph == "P\n\n  q\n\n * c"
    code_block = markdown_to_tracwiki(_md("P\n\n    q4\n * c\n"))
    assert "{{{" not in code_block
    assert code_block.endswith(" * c")


@pytest.mark.parametrize("name", UNCHANGED)
def test_out_of_scope_shapes_keep_masters_bytes(name):
    source, markdown, trip = UNCHANGED[name]
    assert _md(source) == markdown
    assert markdown_to_tracwiki(markdown) == trip
