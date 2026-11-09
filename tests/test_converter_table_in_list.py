"""A GFM table inside a Markdown list item or blockquote (ticket #142).

mistune parses a table that is indented under a list item (or
after a ``>`` marker) as lazy paragraph text unless its ``table_in_list`` /
``table_in_quote`` plugins are loaded, so the converter used to glue the
rows onto the item text (tight item) or emit raw ``| x | y |`` text (loose
item) -- never a table.

**Measured on Trac's own renderer (/trac_test) before writing the fix:**

* ``||`` rows indented under a list item end the list in every shape (blank
  line before or not, after a nested list or not).
* A ``{{{#!div}}}`` block wrapping ordinary ``||`` rows, directly under the
  item text with NO blank line before it, renders the table INSIDE the
  ``<li>`` and the list continues with the next item.
* The same block after a blank line ends the list (Trac ends a list at any
  blank line).
* Placed after a nested list it attaches to the nested item -- the #125
  ``list_item_split`` shape, which the advisory already covers.
* In a blockquote, ``||`` rows indented two spaces render as a blockquoted
  table, so the quote case needs only the plugin.

The negative rows (shapes that must NOT change) were computed on ``master``
(f85cdc1) before the change and hard-coded.
"""

import re

import pytest

from trac_mcp_server.converters import find_list_item_splits
from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

TICKET_INPUT = "- a\n\n  | x | y |\n  |---|---|\n  | 1 | 2 |\n- d\n"
TICKET_EXPECTED = (
    " * a\n   {{{#!div\n   ||=x=||=y=||\n   ||1||2||\n   }}}\n * d"
)


def test_loose_item_is_the_tickets_input():
    """Measured: the div block directly under the item text keeps the table
    in the <li>. The item is loose in Markdown, but a blank line before the
    block would end the Trac list, so none is emitted."""
    assert markdown_to_tracwiki(TICKET_INPUT) == TICKET_EXPECTED


def test_tight_item():
    """Master glued the rows onto the item text (' * a||=x=||=y=||')."""
    md = "- a\n  | x | y |\n  |---|---|\n  | 1 | 2 |\n- d\n"
    assert markdown_to_tracwiki(md) == TICKET_EXPECTED


def test_nested_item_uses_its_own_deeper_indent():
    md = (
        "- a\n  - b\n    | 1 | 2 |\n    |---|---|\n    | 3 | 4 |\n- d\n"
    )
    assert markdown_to_tracwiki(md) == (
        " * a\n   * b\n     {{{#!div\n     ||=1=||=2=||\n"
        "     ||3||4||\n     }}}\n * d"
    )


def test_ordered_item():
    md = "1. a\n   | x |\n   |---|\n   | 1 |\n2. d\n"
    assert markdown_to_tracwiki(md) == (
        " 1. a\n    {{{#!div\n    ||=x=||\n    ||1||\n    }}}\n 2. d"
    )


def test_table_after_nested_list_output_and_advisory():
    """Placed after a nested list the block attaches to the nested item in
    Trac (measured) -- the #125 split. The output is pinned as written, and
    the existing advisory reports block 'table' (the detector sees a table
    token only because it loads the same plugins as the converter)."""
    md = "- a\n  - b\n\n  | x | y |\n  |---|---|\n  | 1 | 2 |\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   * b\n   {{{#!div\n   ||=x=||=y=||\n   ||1||2||\n"
        "   }}}\n * d"
    )
    assert find_list_item_splits(md) == [
        {"item": "a", "block": "table", "depth": 0}
    ]


def test_alignment_row_matches_the_top_level_path():
    """Alignment is carried by cell padding; the in-list table must emit
    exactly what the top-level table emits, indented."""
    top = markdown_to_tracwiki("| x | y |\n|:-|-:|\n| 1 | 2 |\n")
    assert top == "||=x =||= y=||\n||1 || 2||"
    md = "- a\n  | x | y |\n  |:-|-:|\n  | 1 | 2 |\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!div\n   ||=x =||= y=||\n   ||1 || 2||\n   }}}\n * d"
    )


def test_backticked_pipe_stays_in_its_cell():
    """Ticket #45's stash still shields `p|q` from the row splitter."""
    md = "- a\n  | x | y |\n  |---|---|\n  | `p|q` | 2 |\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!div\n   ||=x=||=y=||\n   ||`p|q`||2||\n   }}}\n * d"
    )


def test_two_tables_in_one_item():
    md = (
        "- a\n  | x | y |\n  |---|---|\n  | 1 | 2 |\n\n"
        "  | z |\n  |---|\n  | 3 |\n- d\n"
    )
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!div\n   ||=x=||=y=||\n   ||1||2||\n   }}}\n"
        "   {{{#!div\n   ||=z=||\n   ||3||\n   }}}\n * d"
    )


def test_item_whose_first_child_is_a_table_keeps_its_content():
    """Out of scope (no leading text). Pinned so it neither crashes nor
    loses the table: the empty item text leaves ' * ' and the div follows."""
    md = "- | x | y |\n  |---|---|\n  | 1 | 2 |\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * \n   {{{#!div\n   ||=x=||=y=||\n   ||1||2||\n   }}}\n * d"
    )


def test_blockquote_table_becomes_quoted_rows():
    """Measured: two-space-indented `||` rows render as a blockquoted table.
    `table_in_quote` makes the quoted rows a table token; the existing
    block_quote path indents them. (Master: raw '| x | y |' text.)"""
    md = "> q\n>\n> | x | y |\n> |---|---|\n> | 1 | 2 |\n"
    assert markdown_to_tracwiki(md) == (
        "  q\n  \n  ||=x=||=y=||\n  ||1||2||"
    )


def test_top_level_table_is_unchanged():
    md = "| x | y |\n|---|---|\n| 1 | 2 |\n"
    assert markdown_to_tracwiki(md) == "||=x=||=y=||\n||1||2||"


# -- Header-only table -------------------------------------------------------


def test_a_header_only_table_gets_a_header_as_at_top_level():
    md = " - a\n   - b\n     | 1 | 2 |\n     |---|---|\n - d"
    assert markdown_to_tracwiki(md) == (
        " * a\n   * b\n     {{{#!div\n     ||=1=||=2=||\n     }}}\n * d"
    )
    assert (
        markdown_to_tracwiki("| 1 | 2 |\n|---|---|") == "||=1=||=2=||"
    )


# -- Live: the real renderer on /trac_test ----------------------------------


@pytest.mark.live
def test_live_table_renders_inside_the_list_item():
    """Ticket #142 acceptance, against Trac's own renderer on /trac_test:
    the ticket's input, converted, renders ONE <ul> whose first <li> holds
    the <table>. Read-only (wiki.wikiToHtml writes nothing)."""
    from tests.live_helpers import trac_test_client

    client = trac_test_client()

    html = client.wiki_to_html(markdown_to_tracwiki(TICKET_INPUT))

    assert len(re.findall(r"<ul[\s>]", html)) == 1, html
    first_li = re.search(r"<li[\s>].*?</li>", html, re.DOTALL)
    assert first_li is not None, html
    assert "<table" in first_li.group(0), html
    assert html.count("<li") == 2, html


# -- Review fixes -------------------------------------------------------------


def test_inline_braces_on_the_item_line_are_item_text():
    """F1: ` * use {{{foo}}} here` is still the list-marker line. An inline
    `{{{x}}}` is item text, not a block opener or closer."""
    md = " - use {{{foo}}} here\n   | x |\n   |---|\n   | 1 |\n - b"
    assert markdown_to_tracwiki(md) == (
        " * use {{{foo}}} here\n   {{{#!div\n   ||=x=||\n   ||1||\n"
        "   }}}\n * b"
    )


def test_backticked_closer_in_a_cell_is_cell_content():
    """F2: measured on Trac, a cell holding `}}}` in a backtick span renders
    correctly inside the div and the list continues; braces inside a row are
    inline content, not the block's closer."""
    md = " - a\n   | x | y |\n   |---|---|\n   | `}}}` | 2 |\n - d"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!div\n   ||=x=||=y=||\n   ||`}}}`||2||\n   }}}\n * d"
    )


def test_inline_monospace_in_a_cell_is_cell_content():
    """F2: inline `{{{z}}}` in a cell is cell content; the cell is written
    byte-for-byte."""
    md = " - a\n   | x | y |\n   |---|---|\n   | {{{z}}} | 2 |\n - d"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!div\n   ||=x=||=y=||\n   ||{{{z}}}||2||\n   }}}\n * d"
    )
