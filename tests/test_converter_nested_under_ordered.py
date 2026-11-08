"""A list nested left of its parent's Markdown text column stays nested
(ticket #179).

Trac nests a list marker indented past its parent's marker at ANY deeper
column. Measured on /trac_test on 2026-11-07 and 2026-11-08 (`convert_preview
format=tracwiki`, render only; ticket #179 comments 1-2): ` 1. outer` /
`   * inner a` / ` 2. outer2` renders the bullets inside `outer`, and so does
the marker at column 2 (m + 1), at 4, 5 and 6; under a bullet, ` * outer` /
`  * inner at m+1` is a nested `<ul>` too. CommonMark nests a marker only at or
past the parent's TEXT column (`_item_text_column`: 4 under ` 1. `, 5 under
` 10. `, 3 under ` * `), and not four columns past it (indented code). The read
leg copied the source column, so a marker nested left of the text column left
its item (bullets became a separate list and the outer list restarted;
ordered ones joined the outer list).

The rule, read leg only (`TracWikiParser._shift_nested_markers`): a nested
marker is moved to its parent's OUTPUT text column when it is left of it, and
clamped to that column + 3 when it is past that, so Markdown never reads
indented code. Its shift is inherited by its children and by every non-marker
line Trac continues with an item (`_continued_item`): relative columns inside
an item are kept, and a `{{{ }}}` block, a quote line or a table row moves
with its item. A line Trac puts outside the list is not touched. Shift 0 is
byte-identical to `master`: every nesting already at or past the parent's text
column (a bullet under a bullet at column 3, column 5 under ` 1. `) keeps its
bytes (PINS). The write leg is unchanged (it writes a nested list at
`depth * 2 + 1`, which Trac nests under any parent).
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

# -- builders -------------------------------------------------------------


def _read(tracwiki: str) -> str:
    return tracwiki_to_markdown(tracwiki).text


def _round_trip(tracwiki: str) -> str:
    return markdown_to_tracwiki(_read(tracwiki))


def _text(inline: list) -> str:
    out = ""
    for node in inline:
        if node["type"] == "softbreak":
            out += "\n"
        elif "children" in node:
            out += _text(node["children"])
        else:
            out += node.get("raw", node.get("text", ""))
    return out


def _shape(markdown: str) -> list:
    """The list structure CommonMark (mistune, the write leg's reader) reads
    out of `markdown`: a list is `("ul"|"ol", [item, ...])` and an item is the
    list of its blocks, a text block being its text, a code block
    `("code", body)`, a quote `("quote", text)`, a table `("table",)`."""

    def blocks(tokens: list) -> list:
        out = []
        for tok in tokens:
            kind = tok["type"]
            if kind in ("paragraph", "block_text"):
                out.append(_text(tok["children"]))
            elif kind == "list":
                ordered = tok["attrs"]["ordered"]
                out.append(
                    (
                        "ol" if ordered else "ul",
                        [
                            blocks(item["children"])
                            for item in tok["children"]
                        ],
                    )
                )
            elif kind == "block_code":
                out.append(("code", tok["raw"].strip("\n")))
            elif kind == "block_quote":
                out.append(
                    (
                        "quote",
                        " ".join(
                            _text(c["children"])
                            for c in tok["children"]
                            if c["type"] == "paragraph"
                        ),
                    )
                )
            elif kind == "table":
                out.append(("table",))
            elif kind != "blank_line":
                out.append((kind,))
        return out

    return blocks(
        mistune.create_markdown(
            renderer=None, plugins=_MISTUNE_PLUGINS
        )(markdown)
    )


# -- the shapes of the ticket, with the Markdown each reads as ---------------

# (id, TracWiki, Markdown). The Markdown has the nested marker at the
# parent's text column.
SHAPES = [
    (
        "bullets-under-ordered",
        " 1. outer\n   * inner a\n   * inner b\n 2. outer2\n",
        " 1. outer\n    - inner a\n    - inner b\n 2. outer2\n",
    ),
    (
        "ordered-under-ordered",
        " 1. outer\n   1. inner a\n   2. inner b\n 2. outer2\n",
        " 1. outer\n    1. inner a\n    2. inner b\n 2. outer2\n",
    ),
    (
        "bullet-under-10",
        " 10. outer\n   * inner\n 11. outer2\n",
        " 10. outer\n     - inner\n 11. outer2\n",
    ),
    (
        "bullet-under-100",
        " 100. outer\n   * inner\n 101. outer2\n",
        " 100. outer\n      - inner\n 101. outer2\n",
    ),
    (
        "ordered-under-10",
        " 10. outer\n   1. inner a\n   2. inner b\n 11. outer2\n",
        " 10. outer\n     1. inner a\n     2. inner b\n 11. outer2\n",
    ),
    (
        "three-levels",
        " 1. outer\n   1. mid\n     * deep\n     deep cont\n"
        "   2. mid2\n  back in outer\n 2. outer2\n",
        " 1. outer\n    1. mid\n       - deep\n         deep cont\n"
        "    2. mid2\n\n    back in outer\n 2. outer2\n",
    ),
    (
        "continuation-line",
        " 1. outer\n   * inner a\n     inner cont\n 2. outer2\n",
        " 1. outer\n    - inner a\n      inner cont\n 2. outer2\n",
    ),
    (
        "between-line",
        " 1. outer\n   * inner a\n  back in outer\n 2. outer2\n",
        " 1. outer\n    - inner a\n\n    back in outer\n 2. outer2\n",
    ),
    (
        "bullet-at-m-plus-1-under-bullet",
        " * outer\n  * inner at m+1\n * outer2\n",
        " - outer\n   - inner at m+1\n - outer2\n",
    ),
    (
        "ordered-at-m-plus-1",
        " 1. outer\n  1. inner at m+1\n 2. outer2\n",
        " 1. outer\n    1. inner at m+1\n 2. outer2\n",
    ),
    (
        "ordered-at-m-plus-1-under-10",
        " 10. outer\n  * inner at m+1\n 11. outer2\n",
        " 10. outer\n     - inner at m+1\n 11. outer2\n",
    ),
]


@pytest.mark.parametrize(
    ("tracwiki", "markdown"),
    [(t, m) for _, t, m in SHAPES],
    ids=[i for i, _, _ in SHAPES],
)
def test_nested_marker_sits_at_the_parents_text_column(
    tracwiki, markdown
):
    assert _read(tracwiki) == markdown


@pytest.mark.parametrize(
    ("tracwiki", "markdown"),
    [(t, m) for _, t, m in SHAPES],
    ids=[i for i, _, _ in SHAPES],
)
def test_commonmark_reads_the_item_nested_and_the_outer_list_whole(
    tracwiki, markdown
):
    """The Markdown is ONE outer list whose first item holds the nested
    list: the defect left the nested bullets as a second top-level list (or
    folded nested ordered items into the outer one)."""
    shape = _shape(_read(tracwiki))
    assert len(shape) == 1, shape
    kind, items = shape[0]
    assert len(items) == 2, shape
    assert items[1][0].startswith("outer2"), shape
    assert any(isinstance(b, tuple) for b in items[0]), shape


@pytest.mark.parametrize(
    ("tracwiki", "markdown"),
    [(t, m) for _, t, m in SHAPES],
    ids=[i for i, _, _ in SHAPES],
)
def test_round_trip_is_stable_and_keeps_the_nesting(tracwiki, markdown):
    """TracWiki -> Markdown -> TracWiki -> Markdown gives the same Markdown
    and the same TracWiki again: the write leg's `depth * 2 + 1` nesting
    reads back at the same shape (Trac nests under any parent)."""
    once = _round_trip(tracwiki)
    # The write leg drops the trailing newline.
    assert _read(once) == markdown.rstrip("\n")
    assert markdown_to_tracwiki(_read(once)) == once


def test_round_trip_of_column_three_nesting_is_the_source_itself():
    """The write leg writes nested lists at column 3: a source already
    written that way comes back as it went in."""
    source = " 1. outer\n   * inner a\n   * inner b\n 2. outer2"
    assert _round_trip(source) == source
    source = " 1. outer\n   1. inner a\n   2. inner b\n 2. outer2"
    assert _round_trip(source) == source


def test_round_trip_of_the_between_line_keeps_it_in_the_outer_item():
    """#176's `between` shape, refused by its own #179 gate before this: the
    line stays in `outer`, after the nested list, as Trac renders it."""
    written = _round_trip(
        " 1. outer\n   * inner a\n  back in outer\n 2. outer2"
    )
    assert written == (
        " 1. outer\n   * inner a\n  back in outer\n 2. outer2"
    )
    shape = _shape(_read(written))
    assert shape == [
        (
            "ol",
            [
                ["outer", ("ul", [["inner a"]]), "back in outer"],
                ["outer2"],
            ],
        )
    ]


# -- structure, read as CommonMark ----------------------------------------


def test_three_levels_keep_every_line_in_its_item():
    shape = _shape(_read(SHAPES[5][1]))
    assert shape == [
        (
            "ol",
            [
                [
                    "outer",
                    (
                        "ol",
                        [
                            ["mid", ("ul", [["deep\ndeep cont"]])],
                            ["mid2"],
                        ],
                    ),
                    "back in outer",
                ],
                ["outer2"],
            ],
        )
    ]


# -- a block, a quote and a table inside a nested item ----------------------

# (id, TracWiki with the nested marker at column 3, the same shape written
# with the marker at the text column, Markdown both read as)
BLOCKS = [
    (
        "fence",
        " 1. outer\n   * inner a\n   {{{\n   code\n     more\n   }}}\n"
        " 2. outer2\n",
        " 1. outer\n    * inner a\n    {{{\n    code\n      more\n    }}}\n"
        " 2. outer2\n",
        " 1. outer\n    - inner a\n      ```\n      code\n        more\n"
        "      ```\n 2. outer2\n",
    ),
    (
        "quote",
        " 1. outer\n   * inner a\n     > cited\n     after\n 2. outer2\n",
        " 1. outer\n    * inner a\n      > cited\n      after\n 2. outer2\n",
        " 1. outer\n    - inner a\n      > cited\n\n      after\n"
        " 2. outer2\n",
    ),
    (
        "table",
        " 1. outer\n   * inner a\n   {{{#!div\n   || a || b ||\n   }}}\n"
        " 2. outer2\n",
        " 1. outer\n    * inner a\n    {{{#!div\n    || a || b ||\n    }}}\n"
        " 2. outer2\n",
        " 1. outer\n    - inner a\n      | a | b |\n      |:---:|:---:|\n"
        " 2. outer2\n",
    ),
    (
        "fence-three-levels",
        " 1. a\n   1. b\n     * c\n     {{{\n     x\n     }}}\n 2. d\n",
        " 1. a\n    1. b\n       * c\n       {{{\n       x\n       }}}\n"
        " 2. d\n",
        " 1. a\n    1. b\n       - c\n         ```\n         x\n"
        "         ```\n 2. d\n",
    ),
]


@pytest.mark.parametrize(
    ("tracwiki", "canonical", "markdown"),
    [(t, c, m) for _, t, c, m in BLOCKS],
    ids=[i for i, _, _, _ in BLOCKS],
)
def test_a_block_quote_or_table_moves_with_its_item(
    tracwiki, canonical, markdown
):
    """The fence (its opener, body and closer: the body sits in the code
    stash), the quote line and the table row move with the item they are in.
    The Markdown is the one the same shape gets with the nested marker
    already at the text column, which `master` reads right."""
    assert _read(tracwiki) == markdown
    assert _read(canonical) == markdown


def test_fenced_body_stays_inside_the_nested_item_for_commonmark():
    shape = _shape(_read(BLOCKS[0][1]))
    assert shape == [
        (
            "ol",
            [
                [
                    "outer",
                    ("ul", [["inner a", ("code", "code\n  more")]]),
                ],
                ["outer2"],
            ],
        )
    ]


def test_quote_and_text_after_it_stay_inside_the_nested_item():
    shape = _shape(_read(BLOCKS[1][1]))
    assert shape == [
        (
            "ol",
            [
                [
                    "outer",
                    ("ul", [["inner a", ("quote", "cited"), "after"]]),
                ],
                ["outer2"],
            ],
        )
    ]


def test_table_stays_inside_the_nested_item_for_commonmark():
    shape = _shape(_read(BLOCKS[2][1]))
    assert shape == [
        (
            "ol",
            [["outer", ("ul", [["inner a", ("table",)]])], ["outer2"]],
        )
    ]


@pytest.mark.parametrize(
    "tracwiki",
    [t for _, t, _, _ in BLOCKS],
    ids=[i for i, _, _, _ in BLOCKS],
)
def test_round_trip_of_a_block_in_a_nested_item_is_stable(tracwiki):
    once = _round_trip(tracwiki)
    assert markdown_to_tracwiki(_read(once)) == once
    assert _shape(_read(once)) == _shape(_read(tracwiki))


# -- what moves and what does not --------------------------------------------


def test_marker_far_past_the_text_column_is_clamped_to_it_plus_three():
    """Column 8 under ` 1. ` (text column 4) is indented code to CommonMark;
    Trac nests it all the same. It goes to column 7, the deepest column that
    still is a list item."""
    assert _read(" 1. outer\n        * deep\n 2. outer2\n") == (
        " 1. outer\n       - deep\n 2. outer2\n"
    )
    # Columns 4 to 7 are already in range.
    for column in (4, 5, 6, 7):
        bullet = " " * column + "* deep"
        assert _read(f" 1. outer\n{bullet}\n 2. outer2\n") == (
            f" 1. outer\n{' ' * column}- deep\n 2. outer2\n"
        )


def test_children_inherit_the_parents_shift():
    """` 1. a` / `   1. b` / `     1. c`: b moves 1 column, c (5 + 1 = 6) is
    left of b's text column 7 and goes there."""
    assert _read(" 1. a\n   1. b\n     1. c\n 2. d\n") == (
        " 1. a\n    1. b\n       1. c\n 2. d\n"
    )


def test_a_sibling_after_the_nested_list_pops_back_to_the_parent():
    assert _read(" 1. a\n   * b\n     * c\n   * d\n 2. e\n") == (
        " 1. a\n    - b\n      - c\n    - d\n 2. e\n"
    )


def test_a_blank_line_ends_the_list_so_the_next_marker_is_a_root():
    """After a blank line Trac opens a NEW list at any column: the marker is
    a root and is not moved (the one before it is, being nested)."""
    assert _read(
        " 1. outer\n   * inner a\n\n   * other\n 2. outer2\n"
    ) == (" 1. outer\n    - inner a\n\n   - other\n 2. outer2\n")


def test_a_line_outside_every_item_is_not_moved():
    assert (
        _read(" 1. a\n   * b\nplain\n") == " 1. a\n    - b\n\nplain\n"
    )


def test_a_line_of_the_outer_item_before_the_nested_list_is_not_moved():
    """`  cont` (column 2) continues `outer`, whose shift is 0."""
    assert _read(" 1. outer\n  cont\n   * inner\n 2. x\n") == (
        " 1. outer\n  cont\n    - inner\n 2. x\n"
    )


# -- pins: master's bytes where nothing is nested left of the text column ----

# (id, TracWiki, Markdown), the Markdown as `master` (c295d45) wrote it.
PINS = [
    (
        "bullet-under-bullet-col-3",
        " * a\n   * b\n * c\n",
        " - a\n   - b\n - c\n",
    ),
    (
        "bullet-under-bullet-col-4",
        " * a\n    * b\n * c\n",
        " - a\n    - b\n - c\n",
    ),
    (
        "bullet-under-bullet-col-6",
        " * a\n      * b\n * c\n",
        " - a\n      - b\n - c\n",
    ),
    (
        "bullet-under-ordered-col-4",
        " 1. a\n    * b\n 2. c\n",
        " 1. a\n    - b\n 2. c\n",
    ),
    (
        "bullet-under-ordered-col-5",
        " 1. a\n     * b\n 2. c\n",
        " 1. a\n     - b\n 2. c\n",
    ),
    (
        "bullet-under-10-col-5",
        " 10. a\n     * b\n 11. c\n",
        " 10. a\n     - b\n 11. c\n",
    ),
    (
        "bullet-under-10-col-8",
        " 10. a\n        * b\n 11. c\n",
        " 10. a\n        - b\n 11. c\n",
    ),
    (
        "three-levels-at-text-columns",
        " 1. a\n    1. b\n       * c\n    2. b2\n 2. d\n",
        " 1. a\n    1. b\n       - c\n    2. b2\n 2. d\n",
    ),
    (
        "bullet-three-levels",
        " * a\n   * b\n     * c\n   * d\n * e\n",
        " - a\n   - b\n     - c\n   - d\n - e\n",
    ),
    (
        "plain-lists",
        " * a\n * b\n\n 1. a\n 2. b\n",
        " - a\n - b\n\n 1. a\n 2. b\n",
    ),
    (
        "plain-lists-with-continuations",
        " * a\n   more\n * b\n\n 1. a\n    more\n 2. b\n",
        " - a\n   more\n - b\n\n 1. a\n    more\n 2. b\n",
    ),
    (
        "root-marker-after-a-blank-line",
        " 1. a\n\n   * b\n 2. c\n",
        " 1. a\n\n   - b\n 2. c\n",
    ),
    (
        "fence-in-bullet",
        " * a\n   {{{\n   x\n   }}}\n * b\n",
        " - a\n   ```\n   x\n   ```\n - b\n",
    ),
    ("tab-indented-lists", "\t* a\n\t\t* b\n", "\t* a\n\t\t* b\n"),
    (
        "root-marker-at-column-4-and-more",
        "    * a\n  * b\n",
        "    - a\n  - b\n",
    ),
    ("no-list", "just text\n\n  quoted\n", "just text\n\n> quoted\n"),
]


@pytest.mark.parametrize(
    ("tracwiki", "markdown"),
    [(t, m) for _, t, m in PINS],
    ids=[i for i, _, _ in PINS],
)
def test_nothing_nested_left_of_the_text_column_keeps_masters_bytes(
    tracwiki, markdown
):
    assert _read(tracwiki) == markdown


# -- review fix 1: a list after a blank line stays a separate list ------------

# (id, TracWiki, Markdown, the structure CommonMark must read out of it).
# Trac ends a list at every blank line, so a marker after one opens a NEW
# top-level list at any column. Once the previous list's nested markers have
# been moved to its text column, a root marker at or past that column would
# be read as nested in the previous list's item: it is moved to the previous
# list's marker column, where #177's `_separate_adjacent_lists` (a marker left
# of the open list's text column, after a blank line) starts a new list.
AFTER_BLANK = [
    (
        "bullets",
        " * a\n  * b\n\n   * c\n",
        " - a\n   - b\n\n * c\n",
        [("ul", [["a", ("ul", [["b"]])]]), ("ul", [["c"]])],
    ),
    (
        "bullet-after-an-ordered-list",
        " 1. a\n   * b\n\n     * c\n",
        " 1. a\n    - b\n\n - c\n",
        [("ol", [["a", ("ul", [["b"]])]]), ("ul", [["c"]])],
    ),
    (
        "ordered-switches-its-delimiter",
        " 1. a\n   1. b\n\n    1. c\n",
        " 1. a\n    1. b\n\n 1) c\n",
        [("ol", [["a", ("ol", [["b"]])]]), ("ol", [["c"]])],
    ),
    (
        "two-blank-lines",
        " * a\n  * b\n\n\n   * c\n",
        " - a\n   - b\n\n\n * c\n",
        [("ul", [["a", ("ul", [["b"]])]]), ("ul", [["c"]])],
    ),
    (
        "later-items-of-the-new-list",
        " * a\n  * b\n\n   * c\n   * d\n",
        " - a\n   - b\n\n * c\n * d\n",
        [("ul", [["a", ("ul", [["b"]])]]), ("ul", [["c"], ["d"]])],
    ),
    (
        "its-own-nested-list-and-continuation",
        " * a\n  * b\n\n   * c\n     more\n     * d\n",
        " - a\n   - b\n\n * c\n   more\n   - d\n",
        [
            ("ul", [["a", ("ul", [["b"]])]]),
            ("ul", [["c\nmore", ("ul", [["d"]])]]),
        ],
    ),
    (
        "a-third-list",
        " * a\n  * b\n\n   * c\n\n   * e\n",
        " - a\n   - b\n\n * c\n\n - e\n",
        [
            ("ul", [["a", ("ul", [["b"]])]]),
            ("ul", [["c"]]),
            ("ul", [["e"]]),
        ],
    ),
]


@pytest.mark.parametrize(
    ("tracwiki", "markdown", "shape"),
    [(t, m, s) for _, t, m, s in AFTER_BLANK],
    ids=[i for i, _, _, _ in AFTER_BLANK],
)
def test_a_root_list_after_a_blank_line_stays_a_separate_list(
    tracwiki, markdown, shape
):
    """Review fix 1 (high). ` * a` / `  * b` / blank / `   * c` is TWO lists
    in Trac. The moved `b` put the text column of `a` at 3, so `c` at column 3
    was read by CommonMark as nested in `a`, next to `b`, and the round trip
    lost the blank line and re-nested it."""
    assert _read(tracwiki) == markdown
    assert _shape(markdown) == shape


@pytest.mark.parametrize(
    ("tracwiki", "markdown", "shape"),
    [(t, m, s) for _, t, m, s in AFTER_BLANK],
    ids=[i for i, _, _, _ in AFTER_BLANK],
)
def test_the_round_trip_of_a_list_after_a_blank_line_keeps_two_lists(
    tracwiki, markdown, shape
):
    once = _round_trip(tracwiki)
    assert "\n\n" in once, once
    assert _shape(_read(once)) == shape
    assert markdown_to_tracwiki(_read(once)) == once


def test_round_trip_of_the_ticket_repro_is_exact():
    assert _round_trip(" * a\n  * b\n\n   * c\n") == (
        " * a\n   * b\n\n * c"
    )


def test_a_list_after_a_blank_line_after_an_unshifted_list_is_masters():
    """P4 of #177 (a marker after the blank line at or past the previous
    item's text column, no nesting moved) is #183's and keeps `master`'s
    bytes: only a list whose own nesting #179 moved is separated."""
    assert (
        _read(" * a\n   * b\n\n   * c\n") == " - a\n   - b\n\n   - c\n"
    )
    assert _read(" * a\n\n   * c\n") == " - a\n\n   - c\n"


def test_a_marker_left_of_the_previous_text_column_is_not_moved():
    """Column 3 under ` 1. ` (text column 4) is already a separate list to
    CommonMark; #177 switches its delimiter as before."""
    assert _read(" 1. a\n   1. b\n\n   1. c\n") == (
        " 1. a\n    1. b\n\n   1) c\n"
    )


def test_a_list_after_a_paragraph_is_not_moved():
    """A paragraph between the lists means the next list cannot attach to
    the previous one."""
    assert _read(" * a\n  * b\nplain\n\n   * c\n") == (
        " - a\n   - b\n\nplain\n\n   - c\n"
    )


# -- review fix 2: a tab after the parent's marker (frozen) -----------------


def test_a_tab_gap_after_the_parents_marker_freezes_its_children():
    """Review fix 2. `_item_text_column` counts a tab as ONE column, so the
    parent's text column is unknown and the nested bullet was folded into
    the item's text ("a - b"). FROZEN: a parent whose marker-to-text gap holds
    a tab gives its children shift 0, `master`'s bytes for the subtree."""
    source = " 1.\ta\n   * b\n 2. d\n"
    assert _read(source) == " 1.\ta\n   - b\n 2. d\n"
    # `master`'s round trip, unchanged.
    assert _round_trip(source) == " 1. a\n\n * b\n\n 2. d"


def test_a_tab_gap_freezes_the_whole_subtree():
    source = " 1.\ta\n   * b\n     * c\n 2. d\n"
    assert _read(source) == " 1.\ta\n   - b\n     - c\n 2. d\n"


def test_a_tab_gap_in_a_bullet_parent_freezes_its_children_too():
    source = " *\ta\n  * b\n * c\n"
    assert _read(source) == " *\ta\n  - b\n - c\n"


# -- review fix 3 (ticket #180): a nested ordered list not starting at 1 -----


@pytest.mark.parametrize(
    ("tracwiki", "markdown"),
    [
        (
            " 1. a\n   10. b\n   11. c\n 2. d\n",
            " 1. a\n\n    10. b\n    11. c\n 2. d\n",
        ),
        (" * a\n  3. b\n", " - a\n\n   3. b\n"),
        (
            " 1. a\n   10. b\n      * c\n 2. d\n",
            " 1. a\n\n    10. b\n        - c\n 2. d\n",
        ),
    ],
    ids=["ordered-10-11", "bullet-parent-3", "its-subtree"],
)
def test_a_nested_ordered_marker_not_1_moves_and_gets_a_blank_line(
    tracwiki, markdown
):
    """Review fix 3 froze these shapes at `master`'s bytes (shift 0), because
    CommonMark does not let an ordered list that starts at a number other
    than 1 interrupt the parent's paragraph: moved to the text column with no
    blank line, `10. b` was paragraph text. Ticket #180 lifted the freeze:
    the marker moves to the parent's text column like any nested marker, and
    `_separate_list_from_trailing_paragraph` puts a blank line before it, so
    it is a real nested list (see
    `tests/test_converter_nested_non_one_ordered.py`)."""
    assert _read(tracwiki) == markdown


def test_a_nested_ordered_marker_1_after_the_parents_text_still_moves():
    assert (
        _read(" 1. a\n   1. b\n 2. d\n") == " 1. a\n    1. b\n 2. d\n"
    )


def test_a_nested_ordered_marker_not_1_after_a_blank_line_is_a_root():
    """After a blank line (not a direct follower of the parent's text) the
    marker is a root and is not frozen by this rule: master's bytes anyway."""
    assert _read(" 1. a\n\n   10. b\n") == " 1. a\n\n   10) b\n"


# -- review fix 4: a dropped macro's whitespace-only line is item content ----


def test_dropped_macro_line_keeps_the_item_open():
    """Review fix 4. With `unknown_macros="drop"` the macro leaves a
    whitespace-only line, which is item content to Trac (the macro was on
    it). The pass took it for a blank line, emptied the stack, and `more of
    b` fell into item `a`. The macro-drop path marks its whitespace-only
    remains with `_BR_LINE_MARK` and the list walks treat the marked line as
    non-blank."""
    source = " 1. a\n   * b\n     [[RecentChanges(x)]]\n     more of b\n 2. c\n"
    out = tracwiki_to_markdown(source, unknown_macros="drop").text
    assert out == " 1. a\n    - b\n     \n      more of b\n 2. c\n"
    assert "\x1f" not in out
    assert _shape(out) == [
        (
            "ol",
            [["a", ("ul", [["b", "more of b"]])], ["c"]],
        )
    ]
    again = markdown_to_tracwiki(out)
    assert _shape(_read(again)) == _shape(out)


def test_dropped_macro_line_before_a_sibling_keeps_both_nested():
    source = (
        " 1. a\n   * b\n     [[RecentChanges(x)]]\n     more of b\n"
        "   * c\n 2. d\n"
    )
    out = tracwiki_to_markdown(source, unknown_macros="drop").text
    assert out == (
        " 1. a\n    - b\n     \n      more of b\n    - c\n 2. d\n"
    )
    assert _shape(out) == [
        (
            "ol",
            [["a", ("ul", [["b", "more of b"], ["c"]])], ["d"]],
        )
    ]


def test_a_raw_whitespace_only_line_still_ends_the_list():
    """Only the dropped macro's remains are marked: a raw whitespace-only
    source line is a blank line to Trac (measured, #178) and empties the
    stack, so `more of b` is outside `b` as before."""
    source = " 1. a\n   * b\n     \n     more of b\n 2. c\n"
    assert (
        _read(source)
        == " 1. a\n    - b\n     \n     more of b\n 2. c\n"
    )


def test_a_dropped_macro_alone_at_column_zero_stays_a_blank_line():
    """No indentation left, so an empty line, not a marked one: it ends the
    list (the bytes are the same as before the fix)."""
    source = " 1. a\n   * b\n[[RecentChanges(x)]]\n   * c\n"
    out = tracwiki_to_markdown(source, unknown_macros="drop").text
    assert out == " 1. a\n    - b\n\n   - c\n"


def test_dropped_macro_in_preserve_mode_is_untouched():
    source = " 1. a\n   * b\n     [[RecentChanges(x)]]\n 2. c\n"
    assert _read(source) == (
        " 1. a\n    - b\n      [[RecentChanges(x)]]\n 2. c\n"
    )


# -- review fix 5: a processor block inside an item --------------------------


def _closer_aligned(tracwiki: str) -> None:
    lines = tracwiki.split("\n")
    opener = next(ln for ln in lines if ln.lstrip().startswith("{{{"))
    closer = next(ln for ln in reversed(lines) if ln.strip() == "}}}")
    assert len(opener) - len(opener.lstrip()) == len(closer) - len(
        closer.lstrip()
    ), tracwiki


def test_a_blank_line_in_an_item_div_keeps_the_block_in_the_item():
    """Review fix 5. A blank line inside `{{{#!div ...}}}` emptied the
    stack partway: the opener and `x` moved, `y`, the closer and the next
    sibling did not. Every line from the opener to its matching closer
    belongs to the owning item: no stack change, the same shift."""
    source = (
        " 1. a\n   * b\n     {{{#!div class=x\n     x\n\n     y\n"
        "     }}}\n   * c\n 2. d\n"
    )
    assert _read(source) == (
        " 1. a\n    - b\n      {{{#!div class=x\n      x\n\n      y\n"
        "      }}}\n    - c\n 2. d\n"
    )
    once = _round_trip(source)
    _closer_aligned(once)
    assert _shape(_read(once)) == _shape(_read(source))
    assert markdown_to_tracwiki(_read(once)) == once


def test_nested_blocks_and_blank_lines_in_an_item_div_move_together():
    source = (
        " 1. a\n   * b\n     {{{#!div class=x\n     {{{#!div class=y\n"
        "     p\n\n     q\n     }}}\n\n     r\n     }}}\n   * c\n 2. d\n"
    )
    assert _read(source) == (
        " 1. a\n    - b\n      {{{#!div class=x\n      {{{#!div class=y\n"
        "      p\n\n      q\n      }}}\n\n      r\n      }}}\n"
        "    - c\n 2. d\n"
    )
    _closer_aligned(_round_trip(source))


def test_a_list_marker_inside_an_item_div_is_not_an_item_of_the_outer_list():
    """Lines inside the block are the block's content: a marker-shaped line
    there neither pops nor pushes."""
    source = (
        " 1. a\n   * b\n     {{{#!div class=x\n * not\n     }}}\n   * c\n"
        " 2. d\n"
    )
    assert _read(source) == (
        " 1. a\n    - b\n      {{{#!div class=x\n  - not\n      }}}\n"
        "    - c\n 2. d\n"
    )


def test_an_unterminated_item_div_keeps_masters_reading():
    """No closer: the block is not a container this pass skips over."""
    source = " 1. a\n   * b\n     {{{#!div class=x\n     x\n 2. d\n"
    assert _read(source) == (
        " 1. a\n    - b\n      {{{#!div class=x\n      x\n 2. d\n"
    )


# -- review fix 6: an interrupter line (a known gap, #187) ---------------------


def test_an_interrupter_line_after_a_nested_item_is_a_known_gap():
    """KNOWN GAP, ticket #187 (already wrong on `master`, not fixed here).
    ` * a` / `  * b` / `  > q`: Trac puts `q` in `b`. The `>` line moves with
    its item (one column, like the item), but CommonMark does not let a
    quote continue a paragraph: it ends item `b`, and `q` lands in `a`. This
    pins the bytes as they are so a fix for #187 shows up as a change here."""
    source = " * a\n  * b\n  > q\n"
    assert _read(source) == " - a\n   - b\n   > q\n"
    assert _shape(_read(source)) == [
        ("ul", [["a", ("ul", [["b"]]), ("quote", "q")]])
    ]


# -- live: the real renderer on /trac_test -------------------------------------


@pytest.mark.live
def test_live_three_level_round_trip_renders_the_nested_structure():
    """Ticket #179 acceptance, against Trac's own renderer on /trac_test:
    the TracWiki -> Markdown -> TracWiki output of the three-level shape
    renders one `<ol>` whose item `outer` holds the nested `<ol>` (items
    `mid`, with `deep` in a `<ul>`, and `mid2`) and the line `back in outer`
    after it. Read-only (`wiki_to_html`, the render path `convert_preview`
    uses, writes nothing)."""
    from tests.test_converter_list_marker_column import _structure
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.instances import InstanceRegistry

    config, _ = bootstrap_config()
    client = TracClient(
        InstanceRegistry(config, {}).resolve("/trac_test")
    )

    source = SHAPES[5][1]
    round_tripped = _round_trip(source)

    root = _structure(client.wiki_to_html(round_tripped))
    lists = root[2]
    assert [node[0] for node in lists] == ["ol"], lists
    items = lists[0][2]
    assert [node[0] for node in items] == ["li", "li"], items
    outer = items[0]
    assert "outer" in " ".join(outer[1]), outer
    assert "back in outer" in " ".join(outer[1]), outer
    nested = outer[2]
    assert [node[0] for node in nested] == ["ol"], nested
    mids = nested[0][2]
    assert [node[0] for node in mids] == ["li", "li"], mids
    assert "mid" in " ".join(mids[0][1]), mids
    deep = mids[0][2]
    assert [node[0] for node in deep] == ["ul"], deep
    assert "deep cont" in " ".join(deep[0][2][0][1]), deep
    assert "outer2" in " ".join(items[1][1]), items
