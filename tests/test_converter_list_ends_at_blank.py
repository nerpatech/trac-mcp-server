"""A list ends at every blank line: what follows it is not the item's (#183).

**The defect.** CommonMark keeps an indented line after a blank line inside
the open list item, and ``_convert_indented_blocks`` left such a run alone
whenever a list could be open above it (#182's ``list_may_be_open``). Trac
does not: it ends a list at EVERY blank line. The Markdown the read leg
produced therefore described a different document::

    " * a" / blank / "   c" / " * d"        ``c`` kept in item ``a``
    " * a" / blank / "   * c" / " * d"      ``c`` nested in item ``a``

and the round trip rewrote the page (``c`` moved into the list, the indent
of a paragraph lost, ``c`` nested, the blank line dropped, an ordered list
renumbered).

**Measured on /trac_test** (2026-11-08, ``convert_preview``, ``format=
tracwiki``; ticket #183 comment 2 has the table). Trac ends the list at the
blank line, including one after a ``{{{ }}}`` block inside an item. After
it:

* an indented line that is not a list marker is a blockquote, at any column
  (1, 2, 3 and 4 measured);
* an indented list marker opens a NEW top-level ``<ul>``/``<ol>``, at any
  column, keeping its own nesting below it.

**The fix (read leg only).** Both changes are in ``_convert_indented_blocks``.

1. *A paragraph after the blank line* (C2, C4, C5, C6, C7, C9, C12): #182's
   head cut is also made when the line before the run is a blank line, not
   only when no list can be open. The head (the lines before the run's first
   marker) is a quote to Trac, so it goes through the same step 5 on its own
   (canonical indent: ``> `` lines; anything else: the ``tracwiki-
   unconverted`` fallback), then one blank line, then the marker starts the
   next run. The operator accepted the added blank line (the round trip gains
   one between the quote and the next list; the render is identical), as on
   #182.
2. *A marker after the blank line* (C3, C8, C11): a run that starts after
   one or more blank lines with a list open above them, and whose first line
   is a list marker at or past the OUTERMOST open item's text column, is
   carried verbatim through the fallback, up to the first marker line left
   of that column or the end of the run. CommonMark would nest the marker
   inside the open item; Trac opens a new top-level list. A marker indented
   4 or more columns, or by a tab, is fenced whatever the item (CommonMark
   reads it as indented code or nested even left of the text column, and
   after a lettered item as indented code); the fence then runs to the
   first later plain marker left of the text column (a lettered item or a
   tab-indented one has no known column: the first later plain marker).
   The fence adds no blank line of its own, but the write leg puts one
   between a code block and a list that follows it, so the round trip is
   NOT byte-exact: it gains that one blank line before the next list (pinned
   below), and is byte-exact only when nothing follows the fence. The fence
   is the only way to keep Trac's stored indent: dedenting the run to the
   top level was rejected because it rewrites the indent and merges C3's
   ``c`` and ``d``, two lists in Trac, into one.
3. *A head that is not a quote* (a heading or a table row, cut by the
   blank line): carried unchanged, and one blank line follows it when the
   next marker is an ordered one that does not start at 1 (it cannot
   interrupt the head's paragraph and would read as its text). A bullet or
   ``1.`` interrupts, and #182's bytes for it stay.

Unchanged: a marker LEFT of the text column (#177's sibling case, C1), the
no-marker shapes (already fenced), a marker indented 4+ columns after the head
(the cut is declined: the added blank line would make it indented code), a
marker after a paragraph with no list (``P`` / blank / ``   * c``), Markdown-
authored loose items (the write leg joins them with #143's ``[[BR]][[BR]]``,
so no blank line reaches this pass), the write leg, and #181's quote depth.
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
_UL_D = "<ul>\n<li>d</li>\n</ul>\n"

# name -> (source, the Markdown, what mistune renders, the round trip)
CASES = {
    # --- change 1: a paragraph after the blank line is a quote -------------
    "C2 paragraph at the text column": (
        " * a\n\n   c\n * d\n",
        f" - a\n\n{_FENCE}   c\n```\n\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}   c\n</code></pre>\n{_UL_D}",
        " * a\n\n   c\n\n * d",
    ),
    "C4 nested item above": (
        " * a\n   * b\n\n   c\n * e\n",
        f" - a\n   - b\n\n{_FENCE}   c\n```\n\n - e\n",
        "<ul>\n<li>a<ul>\n<li>b</li>\n</ul>\n</li>\n</ul>\n"
        f"{_PRE}   c\n</code></pre>\n<ul>\n<li>e</li>\n</ul>\n",
        " * a\n   * b\n\n   c\n\n * e",
    ),
    "C5 the next marker is nested": (
        " * a\n\n   c\n   * d\n",
        f" - a\n\n{_FENCE}   c\n```\n\n   - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}   c\n</code></pre>\n"
        "<ul>\n<li>d</li>\n</ul>\n",
        " * a\n\n   c\n\n * d",
    ),
    "C6 blank line after a block in the item": (
        " * a\n   {{{\n   x\n   }}}\n\n   c\n * d\n",
        f" - a\n   ```\n   x\n   ```\n\n{_FENCE}   c\n```\n\n - d\n",
        "<ul>\n<li>a<pre><code>x\n</code></pre>\n</li>\n</ul>\n"
        f"{_PRE}   c\n</code></pre>\n{_UL_D}",
        " * a\n   {{{\n   x\n   }}}\n\n   c\n\n * d",
    ),
    "C7 one space (fallback)": (
        " * a\n\n c\n * d\n",
        f" - a\n\n{_FENCE} c\n```\n\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE} c\n</code></pre>\n{_UL_D}",
        " * a\n\n c\n\n * d",
    ),
    "C9 ordered list": (
        " 1. a\n\n    c\n 1. d\n",
        f" 1. a\n\n{_FENCE}    c\n```\n\n 1. d\n",
        f"<ol>\n<li>a</li>\n</ol>\n{_PRE}    c\n</code></pre>\n"
        "<ol>\n<li>d</li>\n</ol>\n",
        " 1. a\n\n    c\n\n 1. d",
    ),
    "C12 canonical indent is a quote": (
        " * a\n\n  q\n * d\n",
        " - a\n\n> q\n\n - d\n",
        "<ul>\n<li>a</li>\n</ul>\n<blockquote>\n<p>q</p>\n</blockquote>\n"
        f"{_UL_D}",
        " * a\n\n  q\n\n * d",
    ),
    # A raw whitespace-only line is a blank line to Trac (#178, measured):
    # it ends the list like an empty one.
    "C2 with a whitespace-only blank line": (
        " * a\n \n   c\n * d\n",
        f" - a\n \n{_FENCE}   c\n```\n\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}   c\n</code></pre>\n{_UL_D}",
        " * a\n\n   c\n\n * d",
    ),
    # --- change 2: a marker after the blank line opens a new list ----------
    "C3 marker at the text column": (
        " * a\n\n   * c\n * d\n",
        f" - a\n\n{_FENCE}   * c\n```\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}   * c\n</code></pre>\n{_UL_D}",
        " * a\n\n   * c\n\n * d",
    ),
    "C8 marker past the text column": (
        " * a\n\n    * c\n * d\n",
        f" - a\n\n{_FENCE}    * c\n```\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}    * c\n</code></pre>\n{_UL_D}",
        " * a\n\n    * c\n\n * d",
    ),
    "C11 the new list keeps its own nesting": (
        " * a\n\n   * c\n     * c2\n * d\n",
        f" - a\n\n{_FENCE}   * c\n     * c2\n```\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}   * c\n     * c2\n</code></pre>\n"
        f"{_UL_D}",
        " * a\n\n   * c\n     * c2\n\n * d",
    ),
    # A non-marker line in the run goes with the fence (it is part of the
    # new list's item to Trac).
    "C3 with a continuation line": (
        " * a\n\n   * c\n   more\n * d\n",
        f" - a\n\n{_FENCE}   * c\n   more\n```\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}   * c\n   more\n</code></pre>\n"
        f"{_UL_D}",
        " * a\n\n   * c\n   more\n\n * d",
    ),
    # The fence stops at the first marker left of the text column; the rest
    # of the run is master's path.
    "the fence stops at a marker left of the column": (
        " * a\n\n   * c\n  * e\n * d\n",
        f" - a\n\n{_FENCE}   * c\n```\n  - e\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}   * c\n</code></pre>\n"
        "<ul>\n<li>e</li>\n<li>d</li>\n</ul>\n",
        " * a\n\n   * c\n\n * e\n * d",
    ),
    "C3 at the end of the document": (
        " * a\n\n   * c\n",
        f" - a\n\n{_FENCE}   * c\n```\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}   * c\n</code></pre>\n",
        " * a\n\n   * c",
    ),
    # The text column is that of the OUTERMOST open item: `   * c` is left
    # of `b`'s text column (5) but at `a`'s (3), so it is fenced.
    "marker at the outermost item's column, inside a nested item": (
        " * a\n   * b\n\n   * c\n * d\n",
        f" - a\n   - b\n\n{_FENCE}   * c\n```\n - d\n",
        "<ul>\n<li>a<ul>\n<li>b</li>\n</ul>\n</li>\n</ul>\n"
        f"{_PRE}   * c\n</code></pre>\n{_UL_D}",
        " * a\n   * b\n\n   * c\n\n * d",
    ),
    "ordered outer item": (
        " 1. a\n\n    * c\n 1. d\n",
        f" 1. a\n\n{_FENCE}    * c\n```\n 1. d\n",
        f"<ol>\n<li>a</li>\n</ol>\n{_PRE}    * c\n</code></pre>\n"
        "<ol>\n<li>d</li>\n</ol>\n",
        " 1. a\n\n    * c\n\n 1. d",
    ),
    # --- review fixes (#183 review) ---------------------------------------
    # Finding 1: Trac ends the list at the first blank line, so any number of
    # blank lines (a whitespace-only one too) before the marker leaves no
    # list open for Trac. Measured: three `<ul>`.
    "several blank lines before a marker": (
        " * a\n\n\n   * c\n * d\n",
        f" - a\n\n\n{_FENCE}   * c\n```\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}   * c\n</code></pre>\n{_UL_D}",
        " * a\n\n   * c\n\n * d",
    ),
    "several blank lines, some of spaces or a tab, before a marker": (
        " * a\n\n \n\t\n   * c\n * d\n",
        f" - a\n\n \n\t\n{_FENCE}   * c\n```\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}   * c\n</code></pre>\n{_UL_D}",
        " * a\n\n   * c\n\n * d",
    ),
    "several blank lines before a paragraph": (
        " * a\n\n\n   c\n * d\n",
        f" - a\n\n\n{_FENCE}   c\n```\n\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}   c\n</code></pre>\n{_UL_D}",
        " * a\n\n   c\n\n * d",
    ),
    # Finding 2: after a blank line a marker indented 4+ columns, or by a
    # tab, is indented code or nested to CommonMark even left of the
    # outermost item's text column. Same fence, same extent rule.
    # Measured: `<ol start=10>` a, `<ul>` c, `<ol start=11>` d.
    "marker 4 columns, left of the text column of an ordered item": (
        " 10. a\n\n    * c\n 11. d\n",
        # a later pass puts a blank line before an ordered list that does
        # not start at 1
        f" 10. a\n\n{_FENCE}    * c\n```\n\n 11. d\n",
        '<ol start="10">\n<li>a</li>\n</ol>\n'
        f"{_PRE}    * c\n</code></pre>\n"
        '<ol start="11">\n<li>d</li>\n</ol>\n',
        " 10. a\n\n    * c\n\n 11. d",
    ),
    "marker 4 columns, a second 4-column marker stays in the fence": (
        " 10. a\n\n    * c\n    * e\n 11. d\n",
        f" 10. a\n\n{_FENCE}    * c\n    * e\n```\n\n 11. d\n",
        '<ol start="10">\n<li>a</li>\n</ol>\n'
        f"{_PRE}    * c\n    * e\n</code></pre>\n"
        '<ol start="11">\n<li>d</li>\n</ol>\n',
        " 10. a\n\n    * c\n    * e\n\n 11. d",
    ),
    "the fence stops at a plain marker, after a 4-column marker": (
        " * a\n\n    * c\n  * e\n * d\n",
        f" - a\n\n{_FENCE}    * c\n```\n  - e\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}    * c\n</code></pre>\n"
        "<ul>\n<li>e</li>\n<li>d</li>\n</ul>\n",
        " * a\n\n    * c\n\n * e\n * d",
    ),
    "tab-indented marker after the blank line": (
        " * a\n\n\t* c\n * d\n",
        f" - a\n\n{_FENCE}\t* c\n```\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}\t* c\n</code></pre>\n{_UL_D}",
        " * a\n\n\t* c\n\n * d",
    ),
    "tab-indented marker after several blank lines": (
        " * a\n\n\n\t* c\n * d\n",
        f" - a\n\n\n{_FENCE}\t* c\n```\n - d\n",
        f"<ul>\n<li>a</li>\n</ul>\n{_PRE}\t* c\n</code></pre>\n{_UL_D}",
        " * a\n\n\t* c\n\n * d",
    ),
    # A lettered list is a fenced fallback of its own (#74), so the marker
    # after it was left as `     - c`, indented code to CommonMark. Trac:
    # `<ol class=loweralpha>` a, `<ul>` c.
    "lettered outer item, marker 5 columns after the blank line": (
        " a. a\n\n     * c\n",
        f"{_FENCE} a. a\n```\n\n{_FENCE}     * c\n```\n",
        f"{_PRE} a. a\n</code></pre>\n{_PRE}     * c\n</code></pre>\n",
        " a. a\n\n     * c",
    ),
    # The column of a lettered item is unknown, so the fence stops at the
    # first later plain marker (the render of `d` was not measured: this
    # pins the extent only).
    "lettered outer item, the fence stops at a plain marker": (
        " a. a\n\n     * c\n   * d\n",
        f"{_FENCE} a. a\n```\n\n{_FENCE}     * c\n```\n   - d\n",
        f"{_PRE} a. a\n</code></pre>\n{_PRE}     * c\n</code></pre>\n"
        "<ul>\n<li>d</li>\n</ul>\n",
        " a. a\n\n     * c\n\n * d",
    ),
    # Finding 3: a head that is not a quote (a heading here) is carried
    # unchanged, and the blank line after it keeps `2.` from being paragraph
    # text. Measured: `<ol>` a, `<h1>` h, `<ol start=2>` d.
    "a heading head after the blank line": (
        " 1. a\n\n  = h =\n 2. d\n",
        " 1. a\n\n  = h =\n\n 2. d\n",
        "<ol>\n<li>a</li>\n</ol>\n<p>= h =</p>\n"
        '<ol start="2">\n<li>d</li>\n</ol>\n',
        " 1. a\n\n= h =\n\n 2. d",
    ),
}

# name -> (source, master's read-leg bytes, master's round trip): the
# shapes this ticket leaves alone (b5f7557).
UNCHANGED = {
    # #177's sibling case: a marker left of the item's text column.
    "C1 sibling marker": (
        " * a\n\n * b\n",
        " - a\n\n * b\n",
        " * a\n\n * b",
    ),
    "marker left of the text column": (
        " * a\n\n  * c\n",
        " - a\n\n  * c\n",
        " * a\n\n * c",
    ),
    "marker left of the outermost text column, past a fenced run": (
        " * a\n   * b\n\n  * c\n",
        " - a\n   - b\n\n  * c\n",
        " * a\n   * b\n\n * c",
    ),
    # No marker after the head: already fenced on master.
    "no-marker shape": (
        " * a\n\n   c\n",
        f" - a\n\n{_FENCE}   c\n```\n",
        " * a\n\n   c",
    ),
    # No list above the blank line: the marker is the first item.
    "a marker after a paragraph": (
        "P\n\n   * c\n",
        "P\n\n   - c\n",
        "P\n\n * c",
    ),
    "a nested marker with a list above, no blank line": (
        " * a\n   * c\n * d\n",
        " - a\n   - c\n - d\n",
        " * a\n   * c\n * d",
    ),
    # A marker indented 4+ columns, or by a tab, after the head: the cut is
    # declined (its added blank line would make it indented code).
    "a marker 4 columns after the head": (
        " * a\n\n  q\n    * c\n",
        " - a\n\n  q\n    - c\n",
        " * a\n\nq\n- c",
    ),
    "a marker 5 columns after the head": (
        " * a\n\n  q\n     * c\n",
        " - a\n\n  q\n     - c\n",
        " * a\n\nq\n- c",
    ),
    "a tab-indented marker after the head": (
        " * a\n\n  q\n\t* c\n",
        " - a\n\n  q\n\t* c\n",
        " * a\n\nq\n* c",
    ),
    # The marker is moved to the list's own column by #179 when the list
    # above had a nested marker moved; that is a new list in Markdown too.
    "a list whose nested marker #179 moved": (
        " * a\n  * b\n\n   * c\n",
        " - a\n   - b\n\n * c\n",
        " * a\n   * b\n\n * c",
    ),
    # Only a line of spaces, tabs or `\r` is a measured blank line (#178):
    # another whitespace character (here the unit separator) keeps master's
    # bytes.
    "a line holding another whitespace character is not a blank line": (
        " * a\n\x1f\n   c\n * d\n",
        " - a\n\x1f\n   c\n - d\n",
        " * a\n   c\n * d",
    ),
    # A lettered item is itself carried through the fallback (#74), so no
    # Markdown list is open above a plain marker and it is not fenced. A
    # marker indented 4+ columns is: see CASES.
    "a lettered outer item": (
        " a. a\n\n   * c\n",
        f"{_FENCE} a. a\n```\n\n   - c\n",
        " a. a\n\n * c",
    ),
    # A head that is not a quote (a heading) gets the blank line after it
    # only when the next marker is an ordered one not starting at 1, which
    # cannot interrupt a paragraph. A bullet or `1.` interrupts, so
    # #182's bytes for it stay (see CASES for the `2.` row).
    "a heading head before a bullet": (
        " * a\n\n  = h =\n * d\n",
        " - a\n\n  = h =\n - d\n",
        " * a\n\n= h =\n\n * d",
    ),
    "a heading head before a 1.": (
        " 1. a\n\n  = h =\n 1. d\n",
        " 1. a\n\n  = h =\n 1. d\n",
        " 1. a\n\n= h =\n\n 1. d",
    ),
    # `[[BR]]` is text at this pass, not a blank line: a line that holds
    # only `[[BR]]` continues the item, and so does what follows it.
    "a [[BR]] line is not a blank line": (
        " * a\n   [[BR]]\n   c\n * d\n",
        " - a\n     \n   c\n - d\n",
        " * a [[BR]][[BR]]\n   c\n * d",
    ),
    "the write leg's loose item join": (
        " * a [[BR]][[BR]]\n   c\n * d\n",
        " - a   \n  \n   c\n - d\n",
        " * a [[BR]][[BR]]\n   c\n * d",
    ),
}

# Markdown a person wrote -> what the write leg stores. The stored form is
# #143's `[[BR]][[BR]]` join with no blank line, which reads back as one item:
# nothing here reaches either new rule.
LOOSE = {
    "loose item": (
        "- a\n\n  c\n- d\n",
        " * a [[BR]][[BR]]\n   c\n * d",
    ),
    "loose nested item": (
        "- a\n  - b\n\n    c\n- d\n",
        " * a\n   * b [[BR]][[BR]]\n     c\n * d",
    ),
    "loose ordered item": (
        "1. a\n\n   c\n2. d\n",
        " 1. a [[BR]][[BR]]\n    c\n 2. d",
    ),
}


def _md(wiki: str) -> str:
    return tracwiki_to_markdown(wiki).text


def _html(markdown: str) -> str:
    return mistune.create_markdown(plugins=_MISTUNE_PLUGINS)(markdown)


@pytest.mark.parametrize("name", CASES)
def test_markdown_ends_the_list_at_the_blank_line(name):
    source, markdown, _html_expected, _trip = CASES[name]
    assert _md(source) == markdown


@pytest.mark.parametrize("name", CASES)
def test_mistune_renders_what_trac_renders(name):
    """The list, then a quote or a fenced block OUTSIDE it, then the next
    list as a sibling: the structure Trac renders."""
    source, _markdown, html, _trip = CASES[name]
    assert _html(_md(source)) == html


@pytest.mark.parametrize("name", CASES)
def test_round_trip(name):
    source, _markdown, _html_expected, trip = CASES[name]
    assert markdown_to_tracwiki(_md(source)) == trip


def test_the_marker_case_round_trips_byte_for_byte_at_the_end():
    """The fence adds no blank line of its own: with nothing after it, the
    stored page comes back as it was, indent and blank line included."""
    source = " * a\n\n   * c\n"
    assert markdown_to_tracwiki(_md(source)) == source.rstrip("\n")


@pytest.mark.parametrize(
    "name",
    [
        n
        for n in CASES
        if n.startswith(("C3", "C8", "C11", "marker", "ordered"))
        and "end of the document" not in n
    ],
)
def test_the_marker_case_gains_only_the_write_legs_blank_line(name):
    """Measured: `markdown_to_tracwiki` puts a blank line between a code
    block and a list that follows it (a fence, with or without a blank line
    in the Markdown), so a marker run with a next list after it gains that
    one blank line on the round trip. The stored indent is kept, and the
    result is stable on a second round trip. Not changed here: the write
    leg is out of scope for #183."""
    source = CASES[name][0].rstrip("\n")
    once = markdown_to_tracwiki(_md(source))
    lines = once.split("\n")
    assert lines.count("") == source.split("\n").count("") + 1
    assert [x for x in lines if x] == [
        x for x in source.split("\n") if x
    ]
    assert markdown_to_tracwiki(_md(once)) == once


@pytest.mark.parametrize("name", UNCHANGED)
def test_out_of_scope_shapes_keep_masters_bytes(name):
    source, markdown, trip = UNCHANGED[name]
    assert _md(source) == markdown
    assert markdown_to_tracwiki(markdown) == trip


@pytest.mark.parametrize("name", LOOSE)
def test_markdown_authored_loose_items_stay_one_item(name):
    markdown, stored = LOOSE[name]
    assert markdown_to_tracwiki(markdown) == stored
    assert markdown_to_tracwiki(_md(stored)) == stored


def test_a_tab_indented_outer_item_does_not_fence_a_plain_marker():
    """A tab in the outermost item's indent leaves its text column unknown,
    and CommonMark reads that item line as indented code, so no Markdown
    list is open to nest a plain marker: master's bytes (the item line's own
    round trip is a separate, older defect and is not pinned)."""
    assert _md("\t* a\n\n   * c\n") == "\t* a\n\n   - c\n"


def test_a_tab_indented_outer_item_fences_a_code_indented_marker():
    """A marker indented 4+ columns would join the code block that the
    tab-indented item line already is."""
    assert _md("\t* a\n\n     * c\n") == (
        f"\t* a\n\n{_FENCE}     * c\n```\n"
    )


def test_a_quote_head_after_a_blank_warns_nothing_for_canonical_indent():
    result = tracwiki_to_markdown(" * a\n\n  q\n * d\n")
    assert result.warnings == []


def test_the_marker_fence_warns():
    result = tracwiki_to_markdown(" * a\n\n   * c\n * d\n")
    assert any(
        "List marker after a blank line" in w for w in result.warnings
    )
