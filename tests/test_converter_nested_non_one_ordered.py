"""A nested ordered list that does not start at 1 stays nested (ticket #180).

CommonMark lets an ordered list interrupt a paragraph only when it starts at
1. A TracWiki list nested under an item's text, with its first number not 1,
was therefore paragraph text of the parent in Markdown: ` * a` / `   4. x` /
`   5. y` became ` - a` / `   4. x` / `   5. y`, which mistune (the write leg's
reader) renders as `<ul><li>a 4. x 5. y</li></ul>`. #179 froze the shape at
`master`'s bytes (review fix 3) and left it to this ticket; ` 1. a` / `   4. x`
(column 3, left of the parent's Markdown text column 4) was read as a sibling
item, so the round trip rewrote it as ` 1. a` / ` 2. x`.

The rule, read leg only:

* `_shift_nested_markers` no longer freezes such a marker: it moves to its
  parent's text column like any nested marker (` 1. a` / `   4. x` becomes
  ` 1. a` / `    4. x`);
* `_separate_list_from_trailing_paragraph` puts a blank line before a nested
  marker that is an ordered number other than 1 and opens a deeper level
  straight after the parent's paragraph text, the nested counterpart of its
  top-level rule (#144). A blank line is the only CommonMark form: the list
  cannot interrupt the paragraph. The Markdown becomes loose (`<li><p>`), the
  accepted cost. The write leg already collapses that blank line, so it is
  not touched.

Each shape is pinned three ways: the exact Markdown, what mistune renders
(an `<ol start="4">` inside the parent's `<li>`), and the round trip.
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

_render = mistune.create_markdown(plugins=_MISTUNE_PLUGINS)


def _read(tracwiki: str) -> str:
    return tracwiki_to_markdown(tracwiki).text


def _round_trip(tracwiki: str) -> str:
    return markdown_to_tracwiki(_read(tracwiki))


# (id, TracWiki, Markdown, mistune HTML, round trip). The round trip is
# byte-identical to the source minus its trailing newline (the write leg
# never ends in one), except where noted.
SHAPES = [
    (
        "bullet-parent",
        " * a\n   4. x\n   5. y\n",
        " - a\n\n   4. x\n   5. y\n",
        '<ul>\n<li><p>a</p>\n<ol start="4">\n<li>x</li>\n<li>y</li>\n'
        "</ol>\n</li>\n</ul>\n",
        " * a\n   4. x\n   5. y",
    ),
    (
        "ordered-parent-at-column-3",
        " 1. a\n   4. x\n",
        " 1. a\n\n    4. x\n",
        '<ol>\n<li><p>a</p>\n<ol start="4">\n<li>x</li>\n</ol>\n</li>\n'
        "</ol>\n",
        " 1. a\n   4. x",
    ),
    (
        "ordered-parent-at-column-4",
        " 1. a\n    4. x\n",
        " 1. a\n\n    4. x\n",
        '<ol>\n<li><p>a</p>\n<ol start="4">\n<li>x</li>\n</ol>\n</li>\n'
        "</ol>\n",
        # Column 3, where #179 puts a nested `1.` too.
        " 1. a\n   4. x",
    ),
    (
        "continuation-line-before",
        " * a\n   more\n   4. x\n",
        " - a\n   more\n\n   4. x\n",
        '<ul>\n<li><p>a\nmore</p>\n<ol start="4">\n<li>x</li>\n</ol>\n'
        "</li>\n</ul>\n",
        " * a\n   more\n   4. x",
    ),
    (
        "sibling-after",
        " * a\n   4. x\n * b\n",
        " - a\n\n   4. x\n - b\n",
        '<ul>\n<li><p>a</p>\n<ol start="4">\n<li>x</li>\n</ol>\n</li>\n'
        "<li><p>b</p>\n</li>\n</ul>\n",
        " * a\n   4. x\n * b",
    ),
    (
        "one-level-deeper",
        " * a\n   * b\n     4. x\n",
        " - a\n   - b\n\n     4. x\n",
        '<ul>\n<li>a<ul>\n<li><p>b</p>\n<ol start="4">\n<li>x</li>\n'
        "</ol>\n</li>\n</ul>\n</li>\n</ul>\n",
        " * a\n   * b\n     4. x",
    ),
    (
        "bullet-under-the-moved-item",
        " 1. a\n   4. x\n     * y\n",
        " 1. a\n\n    4. x\n       - y\n",
        '<ol>\n<li><p>a</p>\n<ol start="4">\n<li>x<ul>\n<li>y</li>\n'
        "</ul>\n</li>\n</ol>\n</li>\n</ol>\n",
        " 1. a\n   4. x\n     * y",
    ),
    (
        "paragraph-before-the-list",
        "para\n * a\n   4. x\n * b\n",
        "para\n - a\n\n   4. x\n - b\n",
        '<p>para</p>\n<ul>\n<li><p>a</p>\n<ol start="4">\n<li>x</li>\n'
        "</ol>\n</li>\n<li><p>b</p>\n</li>\n</ul>\n",
        "para\n\n * a\n   4. x\n * b",
    ),
    (
        "ordered-10-11-under-ordered",
        " 1. a\n   10. b\n   11. c\n 2. d\n",
        " 1. a\n\n    10. b\n    11. c\n 2. d\n",
        '<ol>\n<li><p>a</p>\n<ol start="10">\n<li>b</li>\n<li>c</li>\n'
        "</ol>\n</li>\n<li><p>d</p>\n</li>\n</ol>\n",
        " 1. a\n   10. b\n   11. c\n 2. d",
    ),
    (
        "ordered-10-with-a-bullet-under-it",
        " 1. a\n   10. b\n      * c\n 2. d\n",
        " 1. a\n\n    10. b\n        - c\n 2. d\n",
        '<ol>\n<li><p>a</p>\n<ol start="10">\n<li>b<ul>\n<li>c</li>\n'
        "</ul>\n</li>\n</ol>\n</li>\n<li><p>d</p>\n</li>\n</ol>\n",
        # The write leg puts a nested list at depth * 2 + 1.
        " 1. a\n   10. b\n     * c\n 2. d",
    ),
    (
        "bullet-parent-marker-at-m-plus-1",
        " * a\n  3. b\n",
        " - a\n\n   3. b\n",
        '<ul>\n<li><p>a</p>\n<ol start="3">\n<li>b</li>\n</ol>\n</li>\n'
        "</ul>\n",
        # Column 3, where the write leg puts a nested list.
        " * a\n   3. b",
    ),
    (
        "two-lists-each-with-a-nested-one",
        " * a\n   4. x\n\n * b\n   5. y\n",
        " - a\n\n   4. x\n\n * b\n\n   5. y\n",
        '<ul>\n<li><p>a</p>\n<ol start="4">\n<li>x</li>\n</ol>\n</li>\n'
        '</ul>\n<ul>\n<li><p>b</p>\n<ol start="5">\n<li>y</li>\n</ol>\n'
        "</li>\n</ul>\n",
        " * a\n   4. x\n\n * b\n   5. y",
    ),
]

_IDS = [shape[0] for shape in SHAPES]


@pytest.mark.parametrize(
    ("tracwiki", "markdown"),
    [(s[1], s[2]) for s in SHAPES],
    ids=_IDS,
)
def test_the_marker_moves_to_the_text_column_after_a_blank_line(
    tracwiki, markdown
):
    assert _read(tracwiki) == markdown


@pytest.mark.parametrize(
    ("markdown", "html"),
    [(s[2], s[3]) for s in SHAPES],
    ids=_IDS,
)
def test_mistune_nests_the_ordered_list_in_the_parent_item(
    markdown, html
):
    """The defect: with no blank line the nested list was the parent's
    paragraph text (`<li>a\\n4. x\\n5. y</li>`)."""
    assert _render(markdown) == html
    assert '<ol start="' in html


@pytest.mark.parametrize(
    ("tracwiki", "html"),
    [(s[1], s[3]) for s in SHAPES],
    ids=_IDS,
)
def test_the_converted_text_renders_nested(tracwiki, html):
    """The same assertion on the converter's own output, so a drift between
    the pinned Markdown and the converter shows up here too."""
    assert _render(_read(tracwiki)) == html


@pytest.mark.parametrize(
    ("tracwiki", "round_trip"),
    [(s[1], s[4]) for s in SHAPES],
    ids=_IDS,
)
def test_round_trip(tracwiki, round_trip):
    assert _round_trip(tracwiki) == round_trip


def test_the_column_3_shape_no_longer_becomes_a_sibling_item():
    """On `master` ` 1. a` / `   4. x` read as a sibling item (the marker is
    left of the text column 4) and the round trip wrote ` 1. a` / ` 2. x`."""
    assert _round_trip(" 1. a\n   4. x\n") != " 1. a\n 2. x"


# -- the shapes that keep their bytes ---------------------------------------


@pytest.mark.parametrize(
    ("tracwiki", "markdown"),
    [
        # A nested `1.` and a nested bullet interrupt a paragraph already.
        (" * a\n   1. x\n", " - a\n   1. x\n"),
        (" * a\n   * x\n", " - a\n   - x\n"),
        # An ordered marker that opens a list after a deeper item: mistune
        # already nests `<ol start="4">` in `a`.
        (" * a\n   * b\n   4. x\n", " - a\n   - b\n   4. x\n"),
        # After a blank line the marker is a root of a new list (#177), not
        # a nested one.
        (" 1. a\n\n   10. b\n", " 1. a\n\n   10) b\n"),
    ],
    ids=[
        "nested-1",
        "nested-bullet",
        "sibling-list-after-a-deeper-item",
        "after-a-blank-line",
    ],
)
def test_shapes_that_need_no_blank_line_keep_their_bytes(
    tracwiki, markdown
):
    assert _read(tracwiki) == markdown


def test_a_sibling_list_after_a_deeper_item_already_nests():
    markdown = _read(" * a\n   * b\n   4. x\n")
    assert _render(markdown) == (
        '<ul>\n<li>a<ul>\n<li>b</li>\n</ul>\n<ol start="4">\n'
        "<li>x</li>\n</ol>\n</li>\n</ul>\n"
    )
    assert _round_trip(" * a\n   * b\n   4. x\n") == (
        " * a\n   * b\n   4. x"
    )


def test_a_nested_ordered_marker_1_after_the_parents_text_gets_no_blank():
    assert (
        _read(" 1. a\n   1. b\n 2. d\n") == " 1. a\n    1. b\n 2. d\n"
    )


def test_a_top_level_non_one_marker_after_a_paragraph_is_unchanged():
    """#144's rule, the top-level arm, is untouched."""
    assert _read("para\n 4. x\n") == "para\n\n 4. x\n"


def test_a_code_block_before_the_marker_gets_no_extra_blank_line():
    """The previous line is a fence, not paragraph text: CommonMark already
    starts the list after it."""
    markdown = _read(" * a\n   {{{\n   code\n   }}}\n   4. x\n")
    assert markdown == " - a\n   ```\n   code\n   ```\n   4. x\n"
    assert '<ol start="4">' in _render(markdown)


def test_a_blank_line_that_is_already_there_is_not_doubled():
    assert "\n\n\n" not in _read(" * a\n   4. x\n   5. y\n")
    assert "\n\n\n" not in _read(" * a\n   more\n   4. x\n * b\n")


def test_a_tab_gap_parent_keeps_its_bytes():
    """The children of an item whose marker-to-text gap holds a tab are
    frozen (#179 review fix 2): the blank line would also make
    `_separate_adjacent_lists` switch the marker's delimiter."""
    assert _read(" 1.\ta\n   4. x\n") == " 1.\ta\n   4. x\n"


def test_a_tab_indented_marker_keeps_its_bytes():
    assert _read(" * a\n\t4. x\n") == " - a\n\t4. x\n"


def test_a_table_row_before_the_marker_gets_no_extra_blank_line():
    """The previous line is a table row, not paragraph text."""
    assert (
        _read(" * a\n   ||t||\n   4. x\n")
        == " - a\n\n| t |\n|---|\n   4. x\n"
    )


def test_a_quote_line_before_the_marker_gets_the_blank_line():
    """A quote line is not a fence: `4. x` after it was a lazy continuation
    of the quote, and is a list in the item with the blank line."""
    markdown = _read(" * a\n   > q\n   4. x\n")
    assert markdown == " - a\n   > q\n\n   4. x\n"
    assert '<ol start="4">' in _render(markdown)


def test_a_tab_gap_parent_keeps_its_bytes_at_a_deeper_column_too():
    assert _read(" 1.\ta\n    4. x\n") == " 1.\ta\n    4. x\n"


def test_a_root_at_column_4_keeps_its_bytes():
    """Indented code to CommonMark already: frozen, `master`'s bytes."""
    assert _read("    * a\n       4. x\n") == "    - a\n       4. x\n"


def test_a_marker_far_past_the_text_column_is_clamped_and_separated():
    assert _read(" * a\n         4. x\n") == " - a\n\n      4. x\n"


# -- the write leg is untouched ---------------------------------------------


def test_the_write_leg_collapses_the_blank_line():
    assert markdown_to_tracwiki(" - a\n\n   4. x\n   5. y\n - b\n") == (
        " * a\n   4. x\n   5. y\n * b"
    )


def test_the_write_leg_reads_the_two_forms_alike():
    tight = " - a\n   4. x\n   5. y\n"
    loose = " - a\n\n   4. x\n   5. y\n"
    assert markdown_to_tracwiki(loose) == " * a\n   4. x\n   5. y"
    assert markdown_to_tracwiki(tight) == markdown_to_tracwiki(loose)


# -- the write leg's output reads back to the same Markdown -----------------


@pytest.mark.parametrize("tracwiki", [s[1] for s in SHAPES], ids=_IDS)
def test_the_written_tracwiki_reads_back_to_the_same_markdown(tracwiki):
    """A second trip is stable: the TracWiki the write leg produces reads
    to the Markdown the first read produced (the write leg ends no text
    with a newline, and `para` gets its blank line from the write leg)."""
    written = _round_trip(tracwiki)
    assert markdown_to_tracwiki(_read(written)) == written
