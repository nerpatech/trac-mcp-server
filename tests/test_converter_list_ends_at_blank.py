"""A list ends at every blank line: what follows it is not the item's (#183).

**Measured on /trac_test** (2026-11-08, ``convert_preview``, ``format=
tracwiki``; ticket #183 comment 2 has the table). Trac ends a list at EVERY
blank line, including one after a ``{{{ }}}`` block inside an item. After
it:

* an indented line that is not a list marker is a blockquote, at any column
  (1, 2, 3 and 4 measured);
* an indented list marker opens a NEW top-level ``<ul>``/``<ol>``, at any
  column, keeping its own nesting below it.

The write leg is unchanged by #183. It writes Markdown-authored loose items
with #143's ``[[BR]][[BR]]`` join and no blank line, so they stay one item in
Trac; the other shapes below keep the bytes the write leg produced before
#183.
"""

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

# name -> (the Markdown, what the write leg stores): the shapes this ticket
# leaves alone (b5f7557).
UNCHANGED = {
    # #177's sibling case: a marker left of the item's text column.
    "C1 sibling marker": (
        " - a\n\n * b\n",
        " * a\n\n * b",
    ),
    "marker left of the text column": (
        " - a\n\n  * c\n",
        " * a\n\n * c",
    ),
    "marker left of the outermost text column, past a fenced run": (
        " - a\n   - b\n\n  * c\n",
        " * a\n   * b\n\n * c",
    ),
    # No list above the blank line: the marker is the first item.
    "a marker after a paragraph": (
        "P\n\n   - c\n",
        "P\n\n * c",
    ),
    "a nested marker with a list above, no blank line": (
        " - a\n   - c\n - d\n",
        " * a\n   * c\n * d",
    ),
    # A marker indented 4+ columns, or by a tab, after the head: the cut is
    # declined (its added blank line would make it indented code).
    "a marker 4 columns after the head": (
        " - a\n\n  q\n    - c\n",
        " * a\n\nq\n- c",
    ),
    "a marker 5 columns after the head": (
        " - a\n\n  q\n     - c\n",
        " * a\n\nq\n- c",
    ),
    "a tab-indented marker after the head": (
        " - a\n\n  q\n\t* c\n",
        " * a\n\nq\n* c",
    ),
    # The marker is moved to the list's own column by #179 when the list
    # above had a nested marker moved; that is a new list in Markdown too.
    "a list whose nested marker #179 moved": (
        " - a\n   - b\n\n * c\n",
        " * a\n   * b\n\n * c",
    ),
    # Only a line of spaces, tabs or `\r` is a measured blank line (#178):
    # another whitespace character (here the unit separator) keeps master's
    # bytes.
    "a line holding another whitespace character is not a blank line": (
        " - a\n\x1f\n   c\n - d\n",
        " * a\n   c\n * d",
    ),
    # A head that is not a quote (a heading) gets the blank line after it
    # only when the next marker is an ordered one not starting at 1, which
    # cannot interrupt a paragraph. A bullet or `1.` interrupts, so
    # #182's bytes for it stay (see CASES for the `2.` row).
    "a heading head before a bullet": (
        " - a\n\n  = h =\n - d\n",
        " * a\n\n= h =\n\n * d",
    ),
    "a heading head before a 1.": (
        " 1. a\n\n  = h =\n 1. d\n",
        " 1. a\n\n= h =\n\n 1. d",
    ),
    # `[[BR]]` is text at this pass, not a blank line: a line that holds
    # only `[[BR]]` continues the item, and so does what follows it.
    "a [[BR]] line is not a blank line": (
        " - a\n     \n   c\n - d\n",
        " * a [[BR]][[BR]]\n   c\n * d",
    ),
    "the write leg's loose item join": (
        " - a   \n  \n   c\n - d\n",
        " * a [[BR]][[BR]]\n   c\n * d",
    ),
}

# Markdown a person wrote -> what the write leg stores. The stored form is
# #143's `[[BR]][[BR]]` join with no blank line, which Trac reads as one item.
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


@pytest.mark.parametrize("name", UNCHANGED)
def test_out_of_scope_shapes_keep_masters_bytes(name):
    markdown, trip = UNCHANGED[name]
    assert markdown_to_tracwiki(markdown) == trip


@pytest.mark.parametrize("name", LOOSE)
def test_markdown_authored_loose_items_stay_one_item(name):
    markdown, stored = LOOSE[name]
    assert markdown_to_tracwiki(markdown) == stored
