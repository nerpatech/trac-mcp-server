"""A GFM table inside a Markdown list item or blockquote (ticket #142).

**Write leg.** mistune parses a table that is indented under a list item (or
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

**Read leg.** ``_convert_code_blocks`` fenced a bare ``{{{#!div`` as a code
block with info ``div`` and ``_convert_tables`` dropped a row group's
indentation, so the div-wrapped table read back as a broken fence (or as an
unindented table that ends the list). The read leg unwraps a bare
``{{{#!div}}}`` that holds only ``||`` rows and sits in a list item, and keeps
the rows' indentation there; every other div, and every table outside a list
item, reads back exactly as on ``master``.

The negative rows (shapes that must NOT change) were computed on ``master``
(f85cdc1) before the change and hard-coded.
"""

import re

import mistune
import pytest

from trac_mcp_server.converters import find_list_item_splits
from trac_mcp_server.converters.markdown_to_tracwiki import (
    _MISTUNE_PLUGINS,
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    tracwiki_to_markdown,
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


# -- Read leg ---------------------------------------------------------------

DIV_IN_LIST = (
    " * a\n   {{{#!div\n   ||=x=||=y=||\n   ||1||2||\n   }}}\n * d"
)
DIV_IN_LIST_MD = " - a\n   | x | y |\n   |---|---|\n   | 1 | 2 |\n - d"


def _md(wiki):
    return tracwiki_to_markdown(wiki).text


def test_div_wrapped_table_in_a_list_item_unwraps_and_keeps_indent():
    """Master read this as a fence: ' - a\\n   ```div\\n\\n   ||=x=||...'."""
    assert _md(DIV_IN_LIST) == DIV_IN_LIST_MD


def test_div_wrapped_table_in_a_nested_item_keeps_the_deeper_indent():
    wiki = (
        " * a\n   * b\n     {{{#!div\n     ||=1=||=2=||\n"
        "     ||3||4||\n     }}}\n * d"
    )
    assert _md(wiki) == (
        " - a\n   - b\n     | 1 | 2 |\n     |---|---|\n"
        "     | 3 | 4 |\n - d"
    )


def test_headerless_div_table_unwraps_too():
    """The brief's variant ` * b` / `||1||2||` unwraps; Markdown needs a
    header, so the first row becomes it (same as a top-level table)."""
    wiki = " * a\n   * b\n     {{{#!div\n     ||1||2||\n     }}}\n * d"
    assert _md(wiki) == (
        " - a\n   - b\n     | 1 | 2 |\n     |---|---|\n - d"
    )


# Negatives. Expected values were computed on master (f85cdc1) BEFORE the
# change and hard-coded; each must stay byte-for-byte.
@pytest.mark.parametrize(
    ("name", "wiki", "expected"),
    [
        (
            "top-level bare div",
            "{{{#!div\n||=x=||=y=||\n||1||2||\n}}}",
            "```div\n||=x=||=y=||\n||1||2||\n```",
        ),
        (
            "div with an argument in a list item",
            " * a\n   {{{#!div class=x\n   ||1||2||\n   }}}\n * d",
            " - a\n   {{{#!div class=x\n| 1 | 2 |\n|---|---|\n   }}}\n - d",
        ),
        (
            "div holding a paragraph in a list item",
            " * a\n   {{{#!div\n   hello\n   }}}\n * d",
            " - a\n   ```div\n\n   hello\n```\n - d",
        ),
        (
            "div after a blank line (outside the list)",
            " * a\n\n   {{{#!div\n   ||1||2||\n   }}}\n * d",
            " - a\n\n   ```div\n\n   ||1||2||\n```\n - d",
        ),
        (
            "div in a blockquote",
            "  {{{#!div\n  ||1||2||\n  }}}\n",
            "  ```div\n  ||1||2||\n```\n",
        ),
        (
            "blockquoted || rows",
            "  ||=x=||=y=||\n  ||1||2||\n",
            "| x | y |\n|---|---|\n| 1 | 2 |\n",
        ),
        (
            # Review F4: Trac renders bare indented `||` rows OUTSIDE the
            # list (measured), so master's dropped indent is the faithful
            # reading; only rows that came from an unwrapped div keep it.
            "bare indented rows in a list item",
            " * a\n   ||=x=||=y=||\n   ||1||2||\n * d\n",
            " - a\n| x | y |\n|---|---|\n| 1 | 2 |\n - d\n",
        ),
        (
            "bare indented rows after a nested item",
            " * a\n   * b\n     ||=x=||\n     ||1||\n * d\n",
            " - a\n   - b\n| x |\n|---|\n| 1 |\n - d\n",
        ),
        (
            "top-level rows",
            "||=x=||=y=||\n||1||2||",
            "| x | y |\n|---|---|\n| 1 | 2 |",
        ),
    ],
)
def test_other_shapes_read_back_exactly_as_on_master(
    name, wiki, expected
):
    assert _md(wiki) == expected, name


# -- Round trips (the acceptance criterion) ---------------------------------


def test_round_trip_div_in_list():
    assert markdown_to_tracwiki(_md(DIV_IN_LIST)) == DIV_IN_LIST


def test_round_trip_div_in_nested_item():
    """The brief's variant used a headerless `||1||2||` row, which cannot
    round trip anywhere (Markdown needs a header, so the write leg emits
    `||=1=||=2=||`; a top-level `||1||2||` behaves the same on master).
    The header row makes the same shape lossless."""
    wiki = (
        " * a\n   * b\n     {{{#!div\n     ||=1=||=2=||\n"
        "     ||3||4||\n     }}}\n * d"
    )
    assert markdown_to_tracwiki(_md(wiki)) == wiki


def test_headerless_rows_gain_a_header_as_at_top_level():
    headerless = (
        " * a\n   * b\n     {{{#!div\n     ||1||2||\n     }}}\n * d"
    )
    assert markdown_to_tracwiki(_md(headerless)) == (
        " * a\n   * b\n     {{{#!div\n     ||=1=||=2=||\n     }}}\n * d"
    )
    assert markdown_to_tracwiki(_md("||1||2||")) == "||=1=||=2=||"


def test_write_then_read_gives_markdown_that_is_a_table_in_the_item():
    md = "- a\n  | x | y |\n  |---|---|\n  | 1 | 2 |\n- d\n"
    back = _md(markdown_to_tracwiki(md))
    assert back == DIV_IN_LIST_MD
    parse = mistune.create_markdown(
        renderer=None, plugins=_MISTUNE_PLUGINS
    )
    first_item = parse(back)[0]["children"][0]
    assert [c["type"] for c in first_item["children"]] == [
        "block_text",
        "table",
    ]


# -- Live: the real renderer on /trac_test ----------------------------------


@pytest.mark.live
def test_live_table_renders_inside_the_list_item():
    """Ticket #142 acceptance, against Trac's own renderer on /trac_test:
    the ticket's input, converted, renders ONE <ul> whose first <li> holds
    the <table>. Read-only (wiki.wikiToHtml writes nothing)."""
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.instances import InstanceRegistry

    config, _ = bootstrap_config()
    client = TracClient(
        InstanceRegistry(config, {}).resolve("/trac_test")
    )

    html = client.wiki_to_html(markdown_to_tracwiki(TICKET_INPUT))

    assert len(re.findall(r"<ul[\s>]", html)) == 1, html
    first_li = re.search(r"<li[\s>].*?</li>", html, re.DOTALL)
    assert first_li is not None, html
    assert "<table" in first_li.group(0), html
    assert html.count("<li") == 2, html


# -- Review fixes -------------------------------------------------------------


def test_inline_braces_on_the_item_line_do_not_hide_the_item():
    """F1: ` * use {{{foo}}} here` is still the list-marker line. An inline
    `{{{x}}}` is not a block opener or closer and must not end the walk."""
    wiki = (
        " * use {{{foo}}} here\n   {{{#!div\n   ||=x=||\n   ||1||\n"
        "   }}}\n * b"
    )
    md = _md(wiki)
    assert (
        md
        == " - use {{{foo}}} here\n   | x |\n   |---|\n   | 1 |\n - b"
    )
    assert markdown_to_tracwiki(md) == wiki


def test_backticked_closer_in_a_cell_unwraps_and_round_trips():
    """F2: measured on Trac, a cell holding `}}}` in a backtick span renders
    correctly inside the div and the list continues; braces inside a row are
    inline content, not the block's closer."""
    wiki = " * a\n   {{{#!div\n   ||=x=||=y=||\n   ||`}}}`||2||\n   }}}\n * d"
    md = _md(wiki)
    assert (
        md == " - a\n   | x | y |\n   |---|---|\n   | `}}}` | 2 |\n - d"
    )
    assert markdown_to_tracwiki(md) == wiki


def test_inline_monospace_in_a_cell_unwraps_and_round_trips():
    """F2: inline `{{{z}}}` in a cell is cell content; the block unwraps and
    the cell comes back byte-for-byte."""
    wiki = " * a\n   {{{#!div\n   ||=x=||=y=||\n   ||{{{z}}}||2||\n   }}}\n * d"
    md = _md(wiki)
    assert md == (
        " - a\n   | x | y |\n   |---|---|\n   | {{{z}}} | 2 |\n - d"
    )
    assert markdown_to_tracwiki(md) == wiki


TWO_TABLES_MD = (
    "- a\n\n  | x |\n  |---|\n  | 1 |\n\n  | y |\n  |---|\n  | 2 |\n"
)
TWO_TABLES_WIKI = (
    " * a\n   {{{#!div\n   ||=x=||\n   ||1||\n   }}}\n"
    "   {{{#!div\n   ||=y=||\n   ||2||\n   }}}"
)


def test_adjacent_div_tables_stay_two_tables_on_read_back():
    """F3: two adjacent divs unwrap to adjacent row groups; without a blank
    line between them Markdown reads ONE table and demotes `y` to a data row.
    The blank line carries no indentation."""
    assert markdown_to_tracwiki(TWO_TABLES_MD) == TWO_TABLES_WIKI
    md = _md(TWO_TABLES_WIKI)
    assert md == (
        " - a\n   | x |\n   |---|\n   | 1 |\n\n   | y |\n   |---|\n   | 2 |"
    )
    parse = mistune.create_markdown(
        renderer=None, plugins=_MISTUNE_PLUGINS
    )
    kinds = [c["type"] for c in parse(md)[0]["children"][0]["children"]]
    assert kinds.count("table") == 2


def test_two_tables_round_trip_is_stable_both_ways():
    wiki = markdown_to_tracwiki(TWO_TABLES_MD)
    assert markdown_to_tracwiki(_md(wiki)) == wiki
    md = _md(wiki)
    assert _md(markdown_to_tracwiki(md)) == md


def test_tab_indented_div_in_a_list_item_unwraps():
    """F7: Trac's tab is 8 columns (_TAB_WIDTH), so a tab-indented block is
    deeper than a one-space marker. Counting a tab as 1 column made it
    equal, and the block was fenced instead."""
    wiki = " * a\n\t{{{#!div\n\t||=x=||\n\t||1||\n\t}}}\n * b"
    md = _md(wiki)
    # Rewritten to the item's content column (3 spaces): a tab is 8
    # columns, which Markdown would read as an indented code block.
    assert md == " - a\n   | x |\n   |---|\n   | 1 |\n - b"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!div\n   ||=x=||\n   ||1||\n   }}}\n * b"
    )


def test_crlf_rows_are_rows_for_both_passes():
    """F8: the unwrap and `_convert_tables` share one row pattern. A row
    ending in `\\r` is a row for `_convert_tables` (`\\s*$`); the unwrap
    used `[ \\t]*$` and fenced the block instead."""
    wiki = " * a\n   {{{#!div\n   ||=x=||\r\n   ||1||\r\n   }}}\n * b"
    md = _md(wiki)
    assert md == " - a\n   | x |\n   |---|\n   | 1 |\n - b"
