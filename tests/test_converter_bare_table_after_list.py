"""Bare ``||`` rows under a list item read back as a table outside it (#146).

**Measured on Trac's own renderer** (ticket #142): ``||`` rows indented under
a list item, with no ``{{{#!div}}}`` around them, render OUTSIDE the list --
the list ends at the table. ``tracwiki_to_markdown`` already drops the rows'
indentation to say so, but it put no blank line between the item text and the
table::

    ' - a\\n| x | y |\\n|---|---|\\n| 1 | 2 |\\n - d\\n'

A Markdown reader (GFM; mistune with ``table_in_list``) takes a table that
follows item text with no blank line for part of the item, and
``markdown_to_tracwiki`` then wrote ``{{{#!div ... }}}`` inside the item,
moving the table INTO the list -- a different document.

The read leg now writes a blank line (no indentation) before a bare row group
when the item stack is still open over the lines above it. It does not when
the line above is already blank, when no list item is open (a top-level
table, flush-left text closed the list), and rows that came from a list
item's unwrapped ``{{{#!div}}}`` (#142) are handled as before.

The negative rows' expected values were computed on ``master`` (adc1325)
before the change and hard-coded.
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

TICKET_WIKI = " * a\n   ||=x=||=y=||\n   ||1||2||\n * d\n"
TICKET_MD = " - a\n\n| x | y |\n|---|---|\n| 1 | 2 |\n - d\n"


def _md(wiki):
    return tracwiki_to_markdown(wiki).text


def _parse(md):
    return mistune.create_markdown(
        renderer=None, plugins=_MISTUNE_PLUGINS
    )(md)


# -- Positive: a blank line separates the table from the open item ----------


@pytest.mark.parametrize(
    ("name", "wiki", "expected"),
    [
        ("ticket's input", TICKET_WIKI, TICKET_MD),
        (
            "rows at column 0 straight after the item",
            " * a\n||=x=||=y=||\n||1||2||\n * d\n",
            " - a\n\n| x | y |\n|---|---|\n| 1 | 2 |\n - d\n",
        ),
        (
            "rows after a continuation line",
            " * a\n   more\n   ||=x=||=y=||\n   ||1||2||\n * d\n",
            " - a\n   more\n\n| x | y |\n|---|---|\n| 1 | 2 |\n - d\n",
        ),
        (
            "rows after a nested item",
            " * a\n   * b\n     ||=x=||\n     ||1||\n * d\n",
            " - a\n   - b\n\n| x |\n|---|\n| 1 |\n - d\n",
        ),
        (
            "ordered item",
            " 1. a\n    ||=x=||\n    ||1||\n 2. d\n",
            " 1. a\n\n| x |\n|---|\n| 1 |\n 2. d\n",
        ),
    ],
)
def test_bare_rows_after_an_open_item_get_a_blank_line(
    name, wiki, expected
):
    assert _md(wiki) == expected, name


def test_the_table_is_outside_any_list_item_in_markdown():
    """Structural: the table is a top-level block, not a child of item `a`
    (without the blank line mistune puts it inside the item)."""
    tokens = _parse(_md(TICKET_WIKI))
    assert [t["type"] for t in tokens if t["type"] != "blank_line"] == [
        "list",
        "table",
        "list",
    ]
    first_item = tokens[0]["children"][0]
    assert "table" not in [c["type"] for c in first_item["children"]]


# -- Negatives: byte-for-byte as on master ----------------------------------


@pytest.mark.parametrize(
    ("name", "wiki", "expected"),
    [
        (
            "blank line already present",
            " * a\n\n   ||=x=||=y=||\n   ||1||2||\n * d\n",
            " - a\n\n| x | y |\n|---|---|\n| 1 | 2 |\n - d\n",
        ),
        (
            "top-level table after a paragraph",
            "text\n||=x=||\n||1||\n",
            "text\n| x |\n|---|\n| 1 |\n",
        ),
        (
            "table after a heading",
            "= H =\n||=x=||\n||1||\n",
            "# H\n| x |\n|---|\n| 1 |\n",
        ),
        (
            "table at document start",
            "||=x=||\n||1||\n",
            "| x |\n|---|\n| 1 |\n",
        ),
        (
            "flush-left text closed the list before the table",
            " * a\ntext\n||=x=||\n||1||\n",
            " - a\n\ntext\n| x |\n|---|\n| 1 |\n",
        ),
        (
            "div-wrapped table in an item (#142)",
            " * a\n   {{{#!div\n   ||=x=||=y=||\n   ||1||2||\n   }}}\n * d\n",
            " - a\n   | x | y |\n   |---|---|\n   | 1 | 2 |\n - d\n",
        ),
    ],
)
def test_other_shapes_read_back_exactly_as_on_master(
    name, wiki, expected
):
    assert _md(wiki) == expected, name


@pytest.mark.parametrize(
    ("name", "wiki", "expected"),
    [
        (
            "rows inside a div with an argument",
            " * a\n   {{{#!div class=x\n   ||1||2||\n   }}}\n * d\n",
            " - a\n   {{{#!div class=x\n| 1 | 2 |\n|---|---|\n   }}}\n - d\n",
        ),
        (
            "rows after text inside a div with an argument",
            " * a\n   {{{#!div class=x\n   text\n   ||1||2||\n   }}}\n * d\n",
            " - a\n   {{{#!div class=x\n   text\n| 1 | 2 |\n|---|---|\n"
            "   }}}\n - d\n",
        ),
    ],
)
def test_rows_inside_a_passthrough_block_are_not_bare(
    name, wiki, expected
):
    """Master bytes. Trac renders rows inside a `{{{#!div class=x` block
    within the block, so they are not bare rows: a blank line plus the
    block's column-0 closer would end the list on the write leg (the div
    would stay in the item on master, `d` would start a new list)."""
    assert _md(wiki) == expected, name


@pytest.mark.parametrize(
    ("name", "wiki", "expected"),
    [
        (
            "inline {{{x}}} on the item line is not an opener",
            " * a {{{x}}}\n   ||1||2||\n * d\n",
            " - a {{{x}}}\n\n| 1 | 2 |\n|---|---|\n - d\n",
        ),
        (
            "a closed block before the rows leaves them bare",
            " * a\n   {{{#!div class=x\n   t\n   }}}\n   ||1||2||\n * d\n",
            " - a\n   {{{#!div class=x\n   t\n   }}}\n\n"
            "| 1 | 2 |\n|---|---|\n - d\n",
        ),
    ],
)
def test_a_closed_or_inline_brace_does_not_exempt_the_rows(
    name, wiki, expected
):
    assert _md(wiki) == expected, name


# -- Round trip --------------------------------------------------------------


def test_round_trip_keeps_the_table_out_of_the_list():
    back = markdown_to_tracwiki(_md(TICKET_WIKI))
    assert "{{{#!div" not in back
    rows = [
        ln for ln in back.split("\n") if ln.lstrip().startswith("||")
    ]
    assert rows, back
    assert all(ln.startswith("||") for ln in rows), back


def test_second_read_write_cycle_is_byte_stable():
    wiki1 = markdown_to_tracwiki(_md(TICKET_WIKI))
    md1 = _md(wiki1)
    wiki2 = markdown_to_tracwiki(md1)
    assert wiki2 == wiki1
    assert _md(wiki2) == md1


# -- Rows after a div-wrapped table ------------------------------------------


def test_rows_directly_after_an_unwrapped_div_join_its_group():
    """Contiguous `||` rows are ONE group, so a bare row cannot directly
    follow a div's rows as a group of its own: they stay in the div group
    (indented, no blank line), exactly as on master."""
    wiki = (
        " * a\n   {{{#!div\n   ||=x=||\n   ||1||\n   }}}\n"
        "   ||=y=||\n   ||2||\n * d\n"
    )
    assert _md(wiki) == (
        " - a\n   | x |\n   |---|\n   | 1 |\n   | y |\n   | 2 |\n - d\n"
    )


def test_bare_group_after_div_table_and_item_text_is_unchanged():
    """KNOWN GAP, pinned as on master: the #143 blank line after the div
    table is the last blank, so the lines since it hold item text but not
    the item's marker and the item does not count as open; no blank line
    is added before the bare group."""
    wiki = (
        " * a\n   {{{#!div\n   ||=x=||\n   ||1||\n   }}}\n   t\n"
        "   ||=y=||\n   ||2||\n * d\n"
    )
    assert _md(wiki) == (
        " - a\n   | x |\n   |---|\n   | 1 |\n\n   t\n"
        "| y |\n|---|\n| 2 |\n - d\n"
    )
