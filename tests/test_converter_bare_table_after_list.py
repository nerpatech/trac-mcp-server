"""A table after a list item, set off by a blank line, stays outside it (#146).

**Measured on Trac's own renderer** (ticket #142): ``||`` rows indented under
a list item, with no ``{{{#!div}}}`` around them, render OUTSIDE the list --
the list ends at the table. A Markdown table that follows item text after a
blank line is the same document: ``markdown_to_tracwiki`` writes its rows at
column 0, not inside a ``{{{#!div}}}`` in the item. Review findings F5, R5
and #171 are pinned below.
"""

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

TICKET_MD = " - a\n\n| x | y |\n|---|---|\n| 1 | 2 |\n - d\n"


def test_a_table_after_a_blank_line_stays_out_of_the_list():
    back = markdown_to_tracwiki(TICKET_MD)
    assert "{{{#!div" not in back
    rows = [
        ln for ln in back.split("\n") if ln.lstrip().startswith("||")
    ]
    assert rows, back
    assert all(ln.startswith("||") for ln in rows), back


def test_171_a_table_after_an_item_table_and_text_is_outside_the_list():
    """A table inside the item, then item text, then a table after a blank
    line: the second table is outside the list."""
    md = (
        " - a\n   | x |\n   |---|\n   | 1 |\n\n   t\n"
        "\n| y |\n|---|\n| 2 |\n - d\n"
    )
    back = markdown_to_tracwiki(md)
    # The y table is outside the list: unindented rows, no div around them.
    assert "\n||=y=||\n||2||" in back, back
    assert "   {{{#!div\n   ||=y=||" not in back, back


def test_f5_a_table_outside_the_item_and_one_inside_stay_two_tables():
    md = " - a\n\n| 1 |\n|---|\n\n   | 2 |\n   |---|\n - d\n"
    assert "||---||" not in markdown_to_tracwiki(md)


def test_r5_text_right_after_a_table_outside_the_list_is_not_a_row():
    """GFM reads `   more` straight after a table as one more row (spec
    example 202); a blank line keeps it out."""
    md = " - a\n\n| 1 |\n|---|\n\n   more\n - d\n"
    back = markdown_to_tracwiki(md)
    # Where `more` lands is not measured: only that it is not a table row.
    assert not any(
        "more" in ln for ln in back.split("\n") if "||" in ln
    ), back
