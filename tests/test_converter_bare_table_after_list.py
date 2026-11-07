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
when a list item is still open over the INPUT lines above it (one forward
pass, a list-item stack per ``{{{`` container depth). It does not when the
line above is already blank, when no list item is open (a top-level
table, flush-left text closed the list), and rows that came from a list
item's unwrapped ``{{{#!div}}}`` (#142) are handled as before. Review
findings F1-F5 and #171 are pinned below.

The negative rows' expected values were computed on ``master`` (adc1325)
before the change and hard-coded.
"""

import importlib

import mistune
import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    _MISTUNE_PLUGINS,
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    tracwiki_to_markdown,
)

# The package re-exports the function under the module's name, so import the
# module itself to patch its `_list_stack_step`.
t2m = importlib.import_module(
    "trac_mcp_server.converters.tracwiki_to_markdown"
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
            # Finding 8: this already passes on master -- the rows at
            # column 0 get their blank line from the converter's other
            # passes -- so it is not evidence for the #146 change.
            "rows at column 0 straight after the item (already separated "
            "on master)",
            " * a\n||=x=||=y=||\n||1||2||\n * d\n",
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


# -- Review findings and #171 -------------------------------------------------

F2_WIKI = "{{{#!div class=x\n * a\n   ||1||\n * d\n}}}\n"
F3_WIKI = (
    " * a\n   {{{#!div class=x\n   t\n\n   u\n   }}}\n   ||1||\n * d\n"
)
F4_WIKI = " * a\n   {{{ some text\n   ||1||\n * d\n"


def _rows_in_a_new_div(back):
    """True when a bare `{{{#!div` (no argument) wraps table rows, which
    is how the write leg moves a table INTO a list item."""
    return "{{{#!div\n" in back


def test_linear_in_the_length_of_a_blank_free_run(monkeypatch):
    """F1: every group used to re-walk the whole blank-free run above it
    (about 16x the steps for 4x the input). The forward pass steps the
    list stack at most once per input line, so the count is linear.
    Counted, not timed: a wall-clock ratio is noisy and says nothing about
    which loop did the work."""
    real = t2m._list_stack_step
    calls = 0

    def counting(stack, line):
        nonlocal calls
        calls += 1
        return real(stack, line)

    monkeypatch.setattr(t2m, "_list_stack_step", counting)

    def count(n):
        nonlocal calls
        calls = 0
        wiki = "".join(f"text {i}\n||{i}||x||\n" for i in range(n))
        tracwiki_to_markdown(wiki)
        return calls

    small, large = count(1000), count(4000)
    assert small > 0
    assert large <= 4 * small + 16, (small, large)
    # Never more steps than input lines (4000 text/row pairs: 8000 lines).
    assert large <= 2 * 4000, large


def test_f2_a_container_wrapping_the_whole_list_does_not_exempt():
    """The `{{{#!div class=x` is open over the item but does not wrap the
    rows' own level: the rows are bare in the list."""
    md = _md(F2_WIKI)
    assert md == (
        "{{{#!div class=x\n - a\n\n| 1 |\n|---|\n - d\n\n}}}\n"
    )
    assert not _rows_in_a_new_div(markdown_to_tracwiki(md))


def test_f3_a_blank_line_inside_a_closed_block_does_not_hide_the_item():
    md = _md(F3_WIKI)
    assert md == (
        " - a\n   {{{#!div class=x\n   t\n\n   u\n   }}}\n\n"
        "| 1 |\n|---|\n - d\n"
    )
    assert not _rows_in_a_new_div(markdown_to_tracwiki(md))


def test_f4_an_opener_with_text_after_the_braces_is_not_a_block():
    md = _md(F4_WIKI)
    assert md == (" - a\n   {{{ some text\n\n| 1 |\n|---|\n - d\n")
    assert not _rows_in_a_new_div(markdown_to_tracwiki(md))


def test_171_the_blank_line_after_a_div_table_does_not_end_the_list():
    """The blank line `_convert_tables` inserts after a div group (#143)
    is output, not input: it must not count as the list's end."""
    wiki = (
        " * a\n   {{{#!div\n   ||=x=||\n   ||1||\n   }}}\n   t\n"
        "   ||=y=||\n   ||2||\n * d\n"
    )
    md = _md(wiki)
    assert md == (
        " - a\n   | x |\n   |---|\n   | 1 |\n\n   t\n"
        "\n| y |\n|---|\n| 2 |\n - d\n"
    )
    back = markdown_to_tracwiki(md)
    # The y table is outside the list: unindented rows, no div around them.
    assert "\n||=y=||\n||2||" in back, back
    assert "   {{{#!div\n   ||=y=||" not in back, back


def test_f5_a_bare_group_directly_before_a_div_group_stays_two_tables():
    wiki = " * a\n   ||1||\n   {{{#!div\n   ||2||\n   }}}\n * d\n"
    md = _md(wiki)
    assert md == (" - a\n\n| 1 |\n|---|\n\n   | 2 |\n   |---|\n - d\n")
    assert "||---||" not in markdown_to_tracwiki(md)


# -- Second review: R1, R2, R5 -------------------------------------------------

R1_WIKI = " * a\n   {{{ #!div class=x\n   ||1||\n   }}}\n * d\n"
R2_WIKI = " * a\n   {{{#!div class=x\n   ||1||\n * d\n"
R5_WIKI = " * a\n   ||1||\n   more\n * d\n"


def test_r1_space_between_the_braces_and_the_processor_is_an_opener():
    """Measured on Trac's renderer (/trac_test, 2026-11-07): `{{{ #!div
    class=x` renders the div with its table INSIDE the item, and the list
    goes on with `d`. Master bytes (no blank line added)."""
    md = _md(R1_WIKI)
    assert (
        md == " - a\n   {{{ #!div class=x\n| 1 |\n|---|\n   }}}\n - d\n"
    )
    back = markdown_to_tracwiki(md)
    lines = back.split("\n")
    assert any(
        ln.startswith(" ") and ln.strip() == "{{{ #!div class=x"
        for ln in lines
    ), back
    assert not any(ln.startswith("}}}") for ln in lines), back


def test_r2_an_opener_with_no_closer_runs_to_the_end_of_the_document():
    """Trac closes open blocks at the end of the document: the div swallows
    `d`, so its rows are inside it, not bare. Master bytes (no blank)."""
    md = _md(R2_WIKI)
    assert md == " - a\n   {{{#!div class=x\n| 1 |\n|---|\n - d\n"
    back = markdown_to_tracwiki(md)
    assert not any(ln.startswith("||") for ln in back.split("\n")), back


def test_r2_a_stray_closer_with_no_opener_is_an_ordinary_line():
    md = _md(" * a\n   }}}\n   ||1||\n * d\n")
    assert md == " - a\n   }}}\n\n| 1 |\n|---|\n - d\n"


def test_r5_text_right_after_a_bare_group_gets_a_blank_line():
    """GFM reads `   more` straight after a table as one more row (spec
    example 202), as it does after a div group (#143)."""
    md = _md(R5_WIKI)
    assert md == " - a\n\n| 1 |\n|---|\n\n   more\n - d\n"
    back = markdown_to_tracwiki(md)
    # Where `more` lands is not measured: only that it is not a table row.
    assert not any(
        "more" in ln for ln in back.split("\n") if "||" in ln
    ), back
