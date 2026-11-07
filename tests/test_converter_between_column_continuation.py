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

* Read leg: such a line gets a blank line before it and is moved to the
  outer item's Markdown text column, so CommonMark keeps it in that item
  (`_separate_list_from_trailing_paragraph`); `_list_stack_step` keeps the
  middle item open for a line at its marker column (C9).
* Write leg: a paragraph run directly after a nested list is written with
  no blank line, its first line at the item's marker column + 1 and the
  later lines at the continuation indent (`_item_block_separator`'s
  `"between"`), and `find_list_item_splits` no longer reports it.

C2, C3, C7 and C8 (an ordered outer item, whose nested list sits left of
the Markdown text column; a wide gap) are tickets #179 and #178, and are
not asserted here.
"""

import re

import pytest

from tests.test_converter_list_marker_column import (
    _nested_items,
    _own_text,
    _parse,
    _structure,
)
from trac_mcp_server.converters.markdown_to_tracwiki import (
    _item_block_separator,
    find_list_item_splits,
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    TracWikiParser,
    _list_stack_step,
    tracwiki_to_markdown,
)

C1 = " * a\n   * b\n  text col2 between\n * c\n"
C4 = " * a\n   * b\n     * c\n    text col4\n   * d\n"
C5 = " * a\n   * b\n  t1\n  t2\n * c\n"
C6 = " * a\n   * b\n  t1 [[BR]][[BR]]\n   t2\n * c\n"
C9 = " * a\n   * b\n     * c\n   text col3\n * d\n"
C10 = " * a\n   * b\n  text col2\n   {{{\n   x\n   }}}\n * c\n"
Q1 = " * a\n   * b\n  text col2\n   > q\n  t2\n * c\n"
D1 = " * a\n   * b\n  text col2\n   {{{#!div\n   ||x||y||\n   }}}\n * c\n"

SOURCES = {
    "C1": C1,
    "C4": C4,
    "C5": C5,
    "C6": C6,
    "C9": C9,
    "C10": C10,
    "Q1": Q1,
    "D1": D1,
}


def _md(wiki: str) -> str:
    return tracwiki_to_markdown(wiki).text


# -- Read leg ---------------------------------------------------------------

# (source, read-leg Markdown): the line gets a blank line before it and the
# outer item's Markdown text column.
READ_ROWS = {
    "C1": (C1, " - a\n   - b\n\n   text col2 between\n - c\n"),
    "C4": (
        C4,
        " - a\n   - b\n     - c\n\n     text col4\n   - d\n",
    ),
    # A following line of the same paragraph stays as it is: CommonMark
    # reads it as a lazy continuation, which is the same paragraph.
    "C5": (C5, " - a\n   - b\n\n   t1\n  t2\n - c\n"),
    "C6": (C6, " - a\n   - b\n\n   t1   \n  \n   t2\n - c\n"),
    "C9": (
        C9,
        " - a\n   - b\n     - c\n\n     text col3\n - d\n",
    ),
    "C10": (
        C10,
        " - a\n   - b\n\n   text col2\n   ```\n   x\n   ```\n - c\n",
    ),
}


@pytest.mark.parametrize("name", list(READ_ROWS))
def test_read_leg_rows(name):
    wiki, expected = READ_ROWS[name]
    assert _md(wiki) == expected


def test_the_blank_line_is_not_doubled_after_a_quote_line():
    """#145 may have put a blank line there already."""
    wiki = " * a\n   * b\n   > q\n  text col2\n * c\n"
    assert "\n\n\n" not in _md(wiki)


# Gate refusals: master's bytes.
GATE_ROWS = {
    # A blank line since the marker: Trac ended the list at the blank.
    "blank since marker": (
        " * a\n   * b\n\n  text\n * c\n",
        " - a\n   - b\n\n  text\n - c\n",
        " * a\n   * b\n\ntext\n\n * c",
    ),
    # A tab in the line's indentation: a declined shape.
    "tab in the line": (
        " * a\n   * b\n \ttext\n * c\n",
        " - a\n   - b\n \ttext\n - c\n",
        " * a\n   * b\n     text\n * c",
    ),
    # A list interrupter keeps #90/#145's reading.
    "interrupter": (
        " * a\n   * b\n  > q\n * c\n",
        " - a\n   - b\n  > q\n - c\n",
        " * a\n   * b\n\n  q\n\n * c",
    ),
}


@pytest.mark.parametrize("name", list(GATE_ROWS))
def test_outside_the_gate_the_read_leg_is_masters(name):
    wiki, read, _ = GATE_ROWS[name]
    assert _md(wiki) == read


@pytest.mark.parametrize("name", list(GATE_ROWS))
def test_outside_the_gate_the_round_trip_is_masters(name):
    wiki, _, written = GATE_ROWS[name]
    assert markdown_to_tracwiki(_md(wiki)) == written


def test_a_lettered_outer_item_keeps_masters_pop():
    """A whole lettered list takes the fallback fence before the separator
    runs, so ask the separator itself: the target item's marker line is
    lettered, and the line keeps master's bytes (no blank line, in place)."""
    text = " a. x\n   * b\n  text\n a. z\n"
    out = TracWikiParser()._separate_list_from_trailing_paragraph(text)
    assert out == text


def test_a_bullet_outer_item_is_moved_by_the_separator_itself():
    """The contrast to the lettered row above, on the same method."""
    text = " * x\n   * b\n  text\n * z\n"
    out = TracWikiParser()._separate_list_from_trailing_paragraph(text)
    assert out == " * x\n   * b\n\n   text\n * z\n"


@pytest.mark.parametrize(
    ("columns", "line", "kept"),
    [
        ([1], " b", [1]),  # A: the innermost item keeps the line (#149)
        ([1, 3], "   t", [1, 3]),  # B
        ([1, 3], "  t", [1]),  # C1: the outer item stays open, b closes
        # C9: a line at the middle marker's column keeps the middle item
        # open (master popped it too).
        ([1, 3, 5], "   t", [1, 3]),
        (
            [1, 3, 5],
            "    t",
            [1, 3],
        ),  # strictly between middle and inner
        ([1, 3, 5], "  t", [1]),  # strictly between outer and middle
        ([1, 3], " t", []),  # D: the list ends
    ],
    ids=["A", "B", "C1", "C9", "C4", "outer-middle", "D"],
)
def test_list_stack_step_truncates_to_the_continued_item(
    columns, line, kept
):
    stack = [(c, re.match(r"\s*", "")) for c in columns]
    _list_stack_step(stack, line, upper_bound=False)
    assert [c for c, _ in stack] == kept


def test_list_stack_step_outside_the_gate_pops_as_master_did():
    """A list interrupter at a middle item's marker column: the rule does
    not apply, so the stack is popped at or past the line's column as
    before (the middle item closes too, unlike the C9 row above)."""
    stack = [(c, re.match(r"\s*", "")) for c in [1, 3, 5]]
    _list_stack_step(stack, "   > t", upper_bound=False)
    assert [c for c, _ in stack] == [1]


def test_c1_text_sits_in_the_outer_item_in_markdown():
    outer = _parse(_md(C1))[0]["children"]
    assert len(outer) == 2
    assert "text col2 between" in _own_text(outer[0])
    inner = _nested_items(outer[0])
    assert len(inner) == 1
    assert "text col2 between" not in _own_text(inner[0])


def test_c9_text_sits_in_the_middle_item_in_markdown():
    outer = _parse(_md(C9))[0]["children"]
    assert len(outer) == 2
    middle = _nested_items(outer[0])
    assert len(middle) == 1
    assert "text col3" in _own_text(middle[0])
    inner = _nested_items(middle[0])
    assert "text col3" not in _own_text(inner[0])


# -- Round trip -------------------------------------------------------------

# tw -> md -> tw: the first between line comes back at the outer marker column + 1
# with no blank line; C10's block comes back in the outer item.
ROUND_TRIP_ROWS = {
    "C1": (C1, " * a\n   * b\n  text col2 between\n * c"),
    "C4": (C4, " * a\n   * b\n     * c\n    text col4\n   * d"),
    # Only the first line of the run goes at marker + 1; the rest is at the
    # item's continuation indent, as every continuation line is (#75).
    "C5": (C5, " * a\n   * b\n  t1\n   t2\n * c"),
    "C6": (C6, " * a\n   * b\n  t1 [[BR]][[BR]]\n   t2\n * c"),
    # The middle item's column: the text comes back one past it, still
    # between the middle marker and the inner one.
    "C9": (C9, " * a\n   * b\n     * c\n    text col3\n * d"),
    "C10": (
        C10,
        " * a\n   * b\n  text col2\n   {{{\n   x\n   }}}\n * c",
    ),
    "Q1": (Q1, " * a\n   * b\n  text col2\n   > q\n   t2\n * c"),
    "D1": (
        D1,
        " * a\n   * b\n  text col2\n   {{{#!div\n   ||=x=||=y=||\n   }}}\n * c",
    ),
}


@pytest.mark.parametrize("name", list(ROUND_TRIP_ROWS))
def test_round_trip_keeps_trac_structure(name):
    wiki, expected = ROUND_TRIP_ROWS[name]
    written = markdown_to_tracwiki(_md(wiki))
    assert written == expected
    assert "\n\n" not in written  # the list is not split
    assert find_list_item_splits(_md(wiki)) == []


# tw -> md -> tw -> md -> tw: the second cycle changes nothing. C6 is the row
# that needs the later lines of a between run at the continuation indent: the
# read leg turns `[[BR]][[BR]]` into a blank line, and a line at marker + 1
# after it would leave the item.
@pytest.mark.parametrize("name", list(ROUND_TRIP_ROWS))
def test_round_trip_is_byte_stable_on_a_second_cycle(name):
    wiki, _ = ROUND_TRIP_ROWS[name]
    once = markdown_to_tracwiki(_md(wiki))
    assert markdown_to_tracwiki(_md(once)) == once


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
    # A non-paragraph child in the run holds a blank line of its own.
    "blank line inside the run": (
        " - a\n   - b\n\n   t1\n\n   ```tracwiki-unconverted\n   plain\n"
        "   ```\n - c\n",
        " * a\n   * b\n\n   t1\n\n   plain\n * c",
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


@pytest.mark.parametrize(
    ("md", "block", "depth"),
    [
        # Any other block directly after a nested list is still reported.
        (
            " - a\n   - b\n\n   ```\n   x\n   ```\n - c\n",
            "code block",
            0,
        ),
        (" - a\n   - b\n\n   > q\n - c\n", "blockquote", 0),
        # A refused run is still reported.
        (" - a\n   - b\n\n   see\n   = x\n - c\n", "paragraph", 0),
        (" - a\n   - b\n\n   1\\. x\n - c\n", "paragraph", 0),
        (" - a\n   - b\n\n   \\> x\n - c\n", "paragraph", 0),
        (
            " - a\n   - b\n\n   t1\n\n   ```tracwiki-unconverted\n"
            "   plain\n   ```\n - c\n",
            "paragraph",
            0,
        ),
        # Depth 1, refused.
        (
            " - a\n   - b\n     - c\n\n     \\- x\n   - d\n",
            "paragraph",
            1,
        ),
    ],
    ids=[
        "code",
        "quote",
        "passthrough",
        "ordered marker",
        "citation marker",
        "blank inside the run",
        "depth 1 refused",
    ],
)
def test_finder_still_reports_what_the_writer_cannot_keep(
    md, block, depth
):
    found = find_list_item_splits(md)
    assert [(f["block"], f["depth"]) for f in found] == [(block, depth)]


def test_finder_reports_a_block_after_a_between_run_only_once_a_list_returns():
    """The between run resets the walk; a nested list after it starts it
    again, and a code block directly after THAT list is reported."""
    md = " - a\n   - b\n\n   t1\n\n   - b2\n\n   ```\n   x\n   ```\n - c\n"
    found = find_list_item_splits(md)
    assert [f["block"] for f in found] == ["code block"]


# -- Live: the real renderer on /trac_test -----------------------------------


@pytest.mark.live
@pytest.mark.parametrize("name", ["C1", "C4", "C9", "C10"])
def test_live_source_and_round_trip_render_the_same_list_structure(
    name,
):
    """Ticket #176 acceptance, against Trac's own renderer on /trac_test: the
    TracWiki source and `markdown_to_tracwiki(tracwiki_to_markdown(source))`
    render the same `<li>` nesting, with the between text (and C10's block)
    in the same `<li>`. Read-only (wiki.wikiToHtml writes nothing)."""
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.instances import InstanceRegistry

    config, _ = bootstrap_config()
    client = TracClient(
        InstanceRegistry(config, {}).resolve("/trac_test")
    )

    source = SOURCES[name]
    round_tripped = markdown_to_tracwiki(_md(source))

    before = client.wiki_to_html(source)
    after = client.wiki_to_html(round_tripped)

    assert _structure(before) == _structure(after), (before, after)
