"""Text after a nested list, at a column between the outer item's marker and
the nested one's, belongs to the OUTER item (ticket #176).

Measured on /trac_test on 2026-11-07 (ticket #176 comment 1, cases C1-C10,
Q1, D1, N1) and read off Trac 1.6's `Formatter._indent_formatter`: in

    ` * a` / `   * b` / `  text col2 between` / ` * c`

the text line closes `b` and continues `a`; it renders inside `a`'s `<li>`,
after the nested list, and `c` stays in the same list. It is the only
TracWiki form for "more of the outer item after its nested list". Once such
a line has closed the nested list the outer item is innermost again, so a
code block, a citation or a `{{{#!div}}}` table that follows joins `a` too
(C10, Q1, D1).

The write leg writes a paragraph run directly after a nested list with no
blank line, its first line at the item's marker column + 1 and the later
lines at the continuation indent (`_item_block_separator`'s `"between"`),
and `find_list_item_splits` no longer reports it.

C2, C3, C7 and C8 (an ordered outer item, whose nested list sits left of
the Markdown text column; a wide gap) are tickets #179 and #178, and are
not asserted here beyond the ordered-outer rows below.
"""

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    _item_block_separator,
    find_list_item_splits,
    markdown_to_tracwiki,
)

# -- Ordered outer item ------------------------------------------------------


def test_an_ordered_outer_items_text_after_its_nested_list_is_written():
    """` 1. a` / `   * b` / `  l1` is what the write leg writes for an
    ordered outer item: the first line at marker column + 1, no blank
    line."""
    wiki = markdown_to_tracwiki("1. a\n   - b\n\n   l1\n2. c\n")
    assert wiki == " 1. a\n   * b\n  l1\n 2. c"
    assert "{{{" not in wiki


def test_an_ordered_outer_item_with_its_nested_list_left_of_the_text_column():
    """Ticket #179: a nested list at column 4 under ` 1. a` (its text
    column) and `l1` after it, in `a`'s own paragraph, are written as the
    nested list left of the text column and `l1` between the markers."""
    md = " 1. a\n    - b\n\n    l1\n 2. c\n"
    assert markdown_to_tracwiki(md) == " 1. a\n   * b\n  l1\n 2. c"


def test_an_ordered_outer_item_with_its_nested_list_at_the_text_column_moves():
    """#105's shape: ` 1. x` has its text at column 4 and the nested list at
    column 4, so Markdown keeps the line in the item (text column 4 is not
    past marker 4)."""
    md = " 1. x\n    - y\n\n    between\n 2. z\n"
    assert markdown_to_tracwiki(md) == " 1. x\n   * y\n  between\n 2. z"


@pytest.mark.parametrize(
    "inline",
    [
        "`* x` c",
        "**1. x** c",
        "*- x* c",
        "[* x](http://e.com) c",
    ],
)
def test_finder_decides_on_the_rendered_text_as_the_writer_does(inline):
    """Inline markup hides the marker-like first characters in the rendering
    (a stashed span, quotes): the writer uses the between form, so the
    finder has nothing to report."""
    md = f" - a\n   - b\n\n   {inline}\n - c\n"
    assert "\n\n" not in markdown_to_tracwiki(md)
    assert find_list_item_splits(md) == []


# -- Write leg --------------------------------------------------------------

WRITE_ROWS = {
    "new form": (
        " - a\n   - b\n\n   text\n - c\n",
        " * a\n   * b\n  text\n * c",
    ),
    "two paragraphs": (
        " - a\n   - b\n\n   t1\n\n   t2\n - c\n",
        " * a\n   * b\n  t1 [[BR]][[BR]]\n   t2\n * c",
    ),
    # A nested item (depth 1) whose own nested list is followed by a
    # paragraph: column 3 + 1.
    "depth 1": (
        " - a\n   - b\n     - c\n\n     t\n   - d\n",
        " * a\n   * b\n     * c\n    t\n   * d",
    ),
    "soft break": (
        " - a\n   - b\n\n   t1\n   t2\n - c\n",
        " * a\n   * b\n  t1\n   t2\n * c",
    ),
    # An ordered outer item: first line at column 2, the rest at its
    # continuation indent (4).
    "ordered outer, two lines": (
        "1. a\n   - b\n\n   l1\n   l2\n2. c\n",
        " 1. a\n   * b\n  l1\n    l2\n 2. c",
    ),
    # After the run, the outer item is innermost again: each block takes
    # the form it takes in an item with no nested list.
    "code after": (
        " - a\n   - b\n\n   text\n   ```\n   x\n   ```\n - c\n",
        " * a\n   * b\n  text\n   {{{\n   x\n   }}}\n * c",
    ),
    "paragraph after code": (
        " - a\n   - b\n\n   text\n   ```\n   x\n   ```\n\n   t2\n - c\n",
        " * a\n   * b\n  text\n   {{{\n   x\n   }}}\n   t2\n * c",
    ),
    "quote after": (
        " - a\n   - b\n\n   text\n\n   > q\n - c\n",
        " * a\n   * b\n  text\n   > q\n * c",
    ),
    "table after": (
        " - a\n   - b\n\n   text\n\n   | x | y |\n   | - | - |\n"
        "   | 1 | 2 |\n - c\n",
        " * a\n   * b\n  text\n   {{{#!div\n   ||=x=||=y=||\n"
        "   ||1||2||\n   }}}\n * c",
    ),
    # N1 with a bullet outer item: the second nested list opens after the
    # first between line, and a second between line follows it.
    "second nested list": (
        " - a\n   - b\n\n   t1\n\n   - b2\n\n   t2\n - c\n",
        " * a\n   * b\n  t1\n   * b2\n  t2\n * c",
    ),
}


@pytest.mark.parametrize("name", list(WRITE_ROWS))
def test_write_leg_rows(name):
    md, expected = WRITE_ROWS[name]
    assert markdown_to_tracwiki(md) == expected


# Refusals: the run keeps today's blank line and column.
REFUSED_ROWS = {
    "passthrough line": (
        " - a\n   - b\n\n   see\n   = x\n - c\n",
        " * a\n   * b\n\n   see\n   = x\n * c",
    ),
    "ordered marker first": (
        " - a\n   - b\n\n   1\\. x\n - c\n",
        " * a\n   * b\n\n   1. x\n * c",
    ),
    "bullet marker first": (
        " - a\n   - b\n\n   \\- x\n - c\n",
        " * a\n   * b\n\n   - x\n * c",
    ),
    "citation marker first": (
        " - a\n   - b\n\n   \\> x\n - c\n",
        " * a\n   * b\n\n   > x\n * c",
    ),
}


@pytest.mark.parametrize("name", list(REFUSED_ROWS))
def test_refused_runs_keep_the_blank_line_form(name):
    md, expected = REFUSED_ROWS[name]
    assert markdown_to_tracwiki(md) == expected


@pytest.mark.parametrize(
    ("prev", "nxt", "list_seen", "prev_text", "next_text", "expected"),
    [
        ("list", "para", True, "", "text", "between"),
        ("list", "para", True, "", "t1 [[BR]][[BR]]\nt2", "between"),
        ("list", "para", True, "", "see\n= x", "blank"),  # passthrough
        ("list", "para", True, "", "{{{\nx", "blank"),
        ("list", "para", True, "", "t1\n\nt2", "blank"),  # blank inside
        ("list", "para", True, "", "1. x", "blank"),  # opens an item
        ("list", "para", True, "", "- x", "blank"),
        ("list", "para", True, "", "> x", "blank"),
        ("list", "text", True, "", "text", "blank"),  # not a paragraph
        ("list", "code", True, "", "x", "blank"),
        ("code", "para", False, "", "text", "adjacent"),  # unchanged
        ("code", "para", True, "", "text", "blank"),  # unchanged
        ("para", "para", False, "a", "b", "br"),  # unchanged
    ],
)
def test_item_block_separator_between(
    prev, nxt, list_seen, prev_text, next_text, expected
):
    assert (
        _item_block_separator(
            prev, nxt, list_seen, prev_text, next_text
        )
        == expected
    )


def test_item_runs_helper_groups_children_as_the_writer_does():
    """The one grouping the renderer and the finder share."""
    import mistune

    from trac_mcp_server.converters.markdown_to_tracwiki import (
        _MISTUNE_PLUGINS,
        _item_runs,
    )

    md = (
        " - a\n   - b\n\n   t1\n\n   t2\n   ```\n   x\n   ```\n\n"
        "   > q\n\n   | x | y |\n   | - | - |\n   | 1 | 2 |\n"
    )
    tokens = mistune.create_markdown(
        renderer=None, plugins=_MISTUNE_PLUGINS
    )(md)
    item = tokens[0]["children"][0]
    runs = [
        (kind, run)
        for kind, run in _item_runs(item["children"])
        if any(c["type"] != "blank_line" for c in run)
    ]
    assert [kind for kind, _ in runs] == [
        "text",
        "list",
        "text",
        "code",
        "quote",
        "table",
    ]
    # Adjacent plain children share a run: t1 and t2 (and the blank line
    # between them) are one.
    assert [
        c["type"] for c in runs[2][1] if c["type"] != "blank_line"
    ] == [
        "paragraph",
        "paragraph",
    ]


# -- find_list_item_splits --------------------------------------------------


@pytest.mark.parametrize(
    "md",
    [
        " - a\n   - b\n\n   text\n - c\n",
        " - a\n   - b\n\n   t1\n\n   t2\n - c\n",
        " - a\n   - b\n     - c\n\n     t\n   - d\n",
        " - a\n   - b\n\n   text\n   ```\n   x\n   ```\n - c\n",
        " - a\n   - b\n\n   text\n\n   > q\n - c\n",
        " - a\n   - b\n\n   text\n\n   | x | y |\n   | - | - |\n"
        "   | 1 | 2 |\n - c\n",
        " - a\n   - b\n\n   t1\n\n   - b2\n\n   t2\n - c\n",
    ],
    ids=[
        "paragraph",
        "two paragraphs",
        "depth 1",
        "code after",
        "quote after",
        "table after",
        "second nested list",
    ],
)
def test_finder_does_not_report_a_between_paragraph(md):
    assert find_list_item_splits(md) == []


def test_finder_reports_a_block_after_a_between_run_only_once_a_list_returns():
    """The between run resets the walk; a nested list after it starts it
    again, and a code block directly after THAT list is reported."""
    md = " - a\n   - b\n\n   t1\n\n   - b2\n\n   ```\n   x\n   ```\n - c\n"
    found = find_list_item_splits(md)
    assert [f["block"] for f in found] == ["code block"]
