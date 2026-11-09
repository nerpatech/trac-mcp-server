"""A line at a list item's marker column continues the item (ticket #149).

**Measured on /trac_test on 2026-11-07, and read off Trac 1.6's
`Formatter._indent_formatter`.** For an indented, non-blank, non-marker
line `d` columns wide, with open marker columns `m_0 < ... < m_k`:

* `d >= m_k`: it continues the innermost item, up to the upper bound
  (marker + 2 for a bullet, + 3 for an ordered item; past it the list
  ends, ticket #175);
* `0 < d < m_k` and `d > m_0`: the items whose marker is past `d` close and
  it continues the deepest one left;
* otherwise (`d <= m_0` while a deeper item is open, or `d == 0`): the
  list ends.

The write leg writes a paragraph after a nested list one column past the
item's marker with no blank line, which Trac keeps in the outer item (#176;
the rows are in tests/test_converter_between_column_continuation.py). The
rows here pin what it writes for a line at an item's marker column after a
block, and for the shapes outside that rule, which keep their bytes.
"""

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

# -- The gate (code review of the first #149 branch) ---------------------------
#
# The marker-column rule applies only when the innermost item is a bullet or
# a digits-only ordered item, neither line has a tab in its indentation, the
# line is not one of CommonMark's list interrupters, and (separator only) no
# blank line has passed since the item's marker. Everywhere else the bytes
# are master's (cf92e87).

# name -> (the Markdown, what the write leg stores)
GATE_ROWS = {
    # F3: a tab-indented list is a declined shape.
    "F3 tab-indented": (
        "\t* a\n\n\tb\n\t* c\n",
        "{{{\n* a\n\nb\n* c\n}}}",
    ),
    # F5: a blockquote line at the marker column, then a continuation.
    # Since #187 the quote stays in item `a`, at its text column, and #145's
    # separator puts the blank line and the re-indent after it.
    "F5 interrupter then continuation": (
        " - a\n   > q\n\n   b\n - c\n",
        " * a\n   > q\n   b\n * c",
    ),
}


@pytest.mark.parametrize("name", list(GATE_ROWS))
def test_outside_the_gate_the_write_leg_is_masters(name):
    markdown, written = GATE_ROWS[name]
    assert markdown_to_tracwiki(markdown) == written


# Inside the gate (computed on be8880b): the continuation stays in the item.
@pytest.mark.parametrize(
    ("read", "written"),
    [
        (
            " 1. x\n    ```\n    y\n    z\n    ```\n    b\n",
            " 1. x\n    {{{\n    y\n    z\n    }}}\n    b",
        ),
        (
            " - x\n   ```\n   y\n   ```\n   b\n - c\n",
            " * x\n   {{{\n   y\n   }}}\n   b\n * c",
        ),
    ],
    ids=["numeric block", "bullet block then marker"],
)
def test_inside_the_gate_the_continuation_stays_in_the_item(
    read, written
):
    assert markdown_to_tracwiki(read) == written


@pytest.mark.parametrize(
    ("markdown", "written"),
    [
        # #176: the text is written between the outer item's marker and the
        # nested one's, with no blank line, so the list stays whole and the
        # text stays in the outer item.
        (
            " - a\n    - b\n\n   text col2 between\n - c\n",
            " * a\n   * b\n  text col2 between\n * c",
        ),
        (
            " - a\n   - b\n     - c\n\n     mid\n - d\n",
            " * a\n   * b\n     * c\n    mid\n * d",
        ),
    ],
    ids=["C", "middle"],
)
def test_a_line_closing_deeper_items_is_written_in_the_outer_item(
    markdown, written
):
    assert markdown_to_tracwiki(markdown) == written


def test_the_block_case_keeps_e_in_the_item():
    markdown = (
        " 1. d\n    ```\n    x\n    ```\n"
        "    e at marker column after a block\n 1. f\n"
    )
    assert markdown_to_tracwiki(markdown) == (
        " 1. d\n    {{{\n    x\n    }}}\n"
        "    e at marker column after a block\n 2. f"
    )
