"""A nested ordered list that does not start at 1 stays nested (ticket #180).

CommonMark lets an ordered list interrupt a paragraph only when it starts at
1. A list nested under an item's text with its first number not 1 is
therefore paragraph text of the parent in Markdown unless a blank line
separates them: ` - a` / `   4. x` / `   5. y` renders as
`<ul><li>a 4. x 5. y</li></ul>` in mistune (the write leg's reader), while
` - a` / blank / `   4. x` / `   5. y` renders an `<ol start="4">` inside the
parent's `<li>`. The rows below pin that rendering for the Markdown forms, and
that the write leg collapses the blank line (it writes the same TracWiki
whether or not the Markdown is loose).
"""

import mistune
import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    _MISTUNE_PLUGINS,
    markdown_to_tracwiki,
)

_render = mistune.create_markdown(plugins=_MISTUNE_PLUGINS)


# (id, TracWiki source, Markdown, mistune HTML, what the write leg stores).
# The TracWiki source is where the Markdown came from.
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


# -- the write leg ----------------------------------------------------------


def test_the_write_leg_collapses_the_blank_line():
    assert markdown_to_tracwiki(" - a\n\n   4. x\n   5. y\n - b\n") == (
        " * a\n   4. x\n   5. y\n * b"
    )


def test_the_write_leg_reads_the_two_forms_alike():
    tight = " - a\n   4. x\n   5. y\n"
    loose = " - a\n\n   4. x\n   5. y\n"
    assert markdown_to_tracwiki(loose) == " * a\n   4. x\n   5. y"
    assert markdown_to_tracwiki(tight) == markdown_to_tracwiki(loose)
