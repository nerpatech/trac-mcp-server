"""A line indented past a list item's bound ends the list (ticket #175).

Trac 1.6's `Formatter._indent_formatter` continues the innermost list item
only while an indented, non-marker line's width `d` is at most `m + 2` for a
bullet item or `m + 3` for a digits-only ordered one (`m` is the marker's
column). Past that the list ENDS and the line is a blockquote, one
`<blockquote>` per tabstop below `d`, plus one (`_set_quote_depth`). The
read leg (`_continued_item`, #149) applied only the lower side of the rule,
so such a line stayed in the item, and the write leg put it back at the
item's text column, inside the list.

**Measured on /trac_test on 2026-11-07** (`convert_preview`,
`format=tracwiki`):

* H1 `" * a" / "    col4" / " * c"`: `<ul>` a, 2 blockquotes, a new `<ul>` c
* N1 `" * a" / "   * b" / "      col6" / " * c"`: 3 blockquotes
* N2 `" * a" / "   * b" / " * c" / "    col4 after sibling"`: 3 blockquotes
* O1 `" 1. a" / "     col5" / "     col5 again" / "   col3 after quote" /
  " 1. c"`: `<ol>` a, then a quote; all three lines are in the quote; a new
  `<ol>` c
* ORD `" 1. a" / "     col5" / " 1. c"`: the list ends at col5
* T1 `" * a" / "    col4" / "    col4b" / " * c"` (without the blank-line
  tail, which is ticket #182): one quote of 2 lines
* B1 `" 1. d" / "{{{" / "x" / "}}}" / "      deep after block" / " 1. f"`:
  `<ol>` d with its `<pre>`, 2 blockquotes, a new `<ol>` f

and, at the bound, G `" * a" / "   col3" / " * c"`, J `" 1. a" / "    col4" /
" 1. c"` and B2 (J's shape after a block, `"    col4 after block"`) still
continue the item.

**The fix is to carry the bytes**: every list walk now ends the list at such a
line (`_continued_item`'s upper bound, `_list_stack_step`,
`_separate_list_from_trailing_paragraph`, `_continues_item_after_block`), and
`_convert_indented_blocks` splits the run there. The quote part (from the
line up to the next list marker) goes out through the `tracwiki-unconverted`
fallback, the rest as before. The write leg writes a fallback fence after a
list as blank line / original bytes / blank line, which closes the list and
the quote and keeps Trac's tabstops, so each case above renders as its
source. No depth model is needed. The Markdown view shows a fenced block
instead of a quote.

The unchanged rows below are the bytes `master` (8218d53) produced before
the change.
"""

import re

import mistune
import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    _MISTUNE_PLUGINS,
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    TracWikiParser,
    _continued_item,
    _list_stack_step,
    tracwiki_to_markdown,
)

# name -> (source, the fenced body, whether a list follows the fence,
#          the first list's top-level item count)
CASES = {
    "H1": (" * a\n    col4\n * c", "    col4", True, 1),
    "N1": (" * a\n   * b\n      col6\n * c", "      col6", True, 1),
    "N2": (
        " * a\n   * b\n * c\n    col4 after sibling",
        "    col4 after sibling",
        False,
        2,
    ),
    "O1": (
        " 1. a\n     col5\n     col5 again\n   col3 after quote\n 1. c",
        "     col5\n     col5 again\n   col3 after quote",
        True,
        1,
    ),
    "ORD": (" 1. a\n     col5\n 1. c", "     col5", True, 1),
    "T1": (
        " * a\n    col4\n    col4b\n * c",
        "    col4\n    col4b",
        True,
        1,
    ),
    "B1": (
        " 1. d\n{{{\nx\n}}}\n      deep after block\n 1. f",
        "      deep after block",
        True,
        1,
    ),
}

# Further shapes of the same rule (not measured on Trac, the same code path).
EXTRA_CASES = {
    # The run starts straight after an INDENTED block's closing line.
    "indented block": (
        " * a\n   {{{\n   x\n   }}}\n      deep\n * c",
        "      deep",
        True,
        1,
    ),
    # Two past-bound runs in one run of indented lines: the loop splits twice.
    "twice": (" * a\n    q1\n * b\n    q2\n * c", None, True, None),
}

# name -> the bytes the round trip writes (observed; H1 is the ticket's).
ROUND_TRIPS = {
    "H1": " * a\n\n    col4\n\n * c",
    "N1": " * a\n   * b\n\n      col6\n\n * c",
    "N2": " * a\n   * b\n * c\n\n    col4 after sibling",
    "O1": (
        " 1. a\n\n     col5\n     col5 again\n   col3 after quote\n\n 1. c"
    ),
    "ORD": " 1. a\n\n     col5\n\n 1. c",
    "T1": " * a\n\n    col4\n    col4b\n\n * c",
    "B1": " 1. d\n    {{{\n    x\n    }}}\n\n      deep after block\n\n 1. f",
}

# name -> (source, master's read-leg bytes): at the bound, or outside the
# gate (#149): unchanged.
UNCHANGED_ROWS = {
    "G": (" * a\n   col3\n * c\n", " - a\n   col3\n - c\n"),
    "J": (" 1. a\n    col4\n 1. c\n", " 1. a\n    col4\n 1. c\n"),
    "B2": (
        " 1. d\n{{{\nx\n}}}\n    col4 after block\n 1. f\n",
        " 1. d\n    ```\n    x\n    ```\n    col4 after block\n 1. f\n",
    ),
    "lettered": (
        " a. x\n      deep\n b. y\n",
        "```tracwiki-unconverted\n a. x\n      deep\n b. y\n```\n",
    ),
    "tab": (" * a\n\tdeep\n * c\n", " - a\n\n\tdeep\n - c\n"),
    "149 A": (
        " 1. a\n b at marker column\n 1. c\n",
        " 1. a\n    b at marker column\n 1. c\n",
    ),
    "149 B": (
        " * a\n   * b\n   text at inner marker col\n * c\n",
        " - a\n   - b\n     text at inner marker col\n - c\n",
    ),
}


def _md(wiki: str) -> str:
    return tracwiki_to_markdown(wiki).text


def _parse(md: str) -> list[dict]:
    return [
        t
        for t in mistune.create_markdown(
            renderer=None, plugins=_MISTUNE_PLUGINS
        )(md)
        if t["type"] != "blank_line"
    ]


# -- _continued_item ------------------------------------------------------------


@pytest.mark.parametrize(
    ("columns", "width", "ordered", "expected"),
    [
        # The upper bound: marker column + 2 (bullet), + 3 (digits-only).
        ([1], 3, False, 0),
        ([1], 4, False, None),
        ([1], 4, True, 0),
        ([1], 5, True, None),
        ([1], 9, False, None),
        ([1, 3], 5, False, 1),
        ([1, 3], 6, False, None),
        ([1, 3], 6, True, 1),
        ([1, 3], 7, True, None),
        ([1, 3, 5], 9, False, None),
        # The lower side is unchanged, whatever the item's type.
        ([1], 1, False, 0),
        ([1], 1, True, 0),
        ([1, 3], 3, False, 1),
        ([1, 3], 3, True, 1),
        ([1, 4], 2, False, 0),
        ([1, 4], 2, True, 0),
        ([1, 3], 1, False, None),
        ([1, 3], 1, True, None),
        ([3], 1, False, None),
        ([1, 3, 5], 4, True, 1),
        ([1, 3, 5], 2, True, 0),
        ([1], 0, True, None),
        ([], 3, True, None),
    ],
)
def test_continued_item_upper_bound(columns, width, ordered, expected):
    assert (
        _continued_item(columns, width, innermost_ordered=ordered)
        == expected
    )


def _stack(columns: list[int], marker_line: str):
    match = re.match(r"\s*(?:\*|\d+\.)", marker_line)
    return [(c, match) for c in columns]


def test_list_stack_step_ends_the_list_past_the_bound():
    stack = _stack([1], " * a")
    _list_stack_step(stack, "    col4")
    assert stack == []

    stack = _stack([1], " 1. a")
    _list_stack_step(stack, "    col4")  # at the bound: kept
    assert [c for c, _ in stack] == [1]
    _list_stack_step(stack, "     col5")  # past it
    assert stack == []


# -- Read leg -------------------------------------------------------------------


@pytest.mark.parametrize("name", [*CASES, *EXTRA_CASES])
def test_past_bound_lines_go_through_one_fallback_fence(name):
    source, body, follows, count = CASES.get(name) or EXTRA_CASES[name]
    markdown = _md(source)
    tokens = _parse(markdown)

    kinds = [t["type"] for t in tokens]
    if name == "twice":
        assert kinds == [
            "list",
            "block_code",
            "list",
            "block_code",
            "list",
        ], markdown
        fences = [t for t in tokens if t["type"] == "block_code"]
        assert [f["raw"] for f in fences] == ["    q1\n", "    q2\n"]
        return
    assert kinds == [
        "list",
        "block_code",
        *(["list"] if follows else []),
    ], markdown
    assert tokens[1]["attrs"]["info"] == "tracwiki-unconverted"
    assert tokens[1]["raw"] == body + "\n"
    assert len(tokens[0]["children"]) == count
    if follows:
        assert len(tokens[2]["children"]) == 1


@pytest.mark.parametrize("name", [*CASES, "twice"])
def test_the_fence_is_set_off_by_blank_lines(name):
    lines = _md(CASES.get(name, EXTRA_CASES.get(name))[0]).split("\n")
    opens = [
        i
        for i, line in enumerate(lines)
        if line.startswith("```tracwiki-unconverted")
    ]
    assert opens
    for i in opens:
        assert lines[i - 1] == ""
        close = lines.index("```", i + 1)
        if close + 1 < len(lines):
            assert lines[close + 1] == ""


@pytest.mark.parametrize("name", list(UNCHANGED_ROWS))
def test_at_the_bound_or_outside_the_gate_the_bytes_are_masters(name):
    wiki, expected = UNCHANGED_ROWS[name]
    assert _md(wiki) == expected


def test_the_warning_names_the_fallback():
    assert any(
        "past a list item's bound" in w
        for w in tracwiki_to_markdown(CASES["H1"][0]).warnings
    )


# -- Round trip -----------------------------------------------------------------


def test_h1_round_trips_as_blank_line_quote_blank_line():
    assert (
        markdown_to_tracwiki(_md(CASES["H1"][0]))
        == " * a\n\n    col4\n\n * c"
    )


@pytest.mark.parametrize("name", list(ROUND_TRIPS))
def test_round_trip_bytes(name):
    assert (
        markdown_to_tracwiki(_md(CASES[name][0])) == ROUND_TRIPS[name]
    )


# -- The other two walks --------------------------------------------------------


def test_the_separator_ends_the_list_past_the_bound_like_a_flush_left_line():
    parser = TracWikiParser()
    assert parser._separate_list_from_trailing_paragraph(
        " - a\n    col4\n - c"
    ) == (" - a\n\n    col4\n - c")
    # Digits-only ordered: + 3, so col4 continues and col5 ends.
    assert parser._separate_list_from_trailing_paragraph(
        " 1. a\n    col4\n 1. c"
    ) == (" 1. a\n    col4\n 1. c")
    assert parser._separate_list_from_trailing_paragraph(
        " 1. a\n     col5\n 1. c"
    ) == (" 1. a\n\n     col5\n 1. c")
    # At the bound for a bullet: unchanged.
    assert parser._separate_list_from_trailing_paragraph(
        " - a\n   col3\n - c"
    ) == (" - a\n   col3\n - c")


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (
            ["   * x", "   {{{", "   y", "   }}}", "     at the bound"],
            True,
        ),
        (
            ["   * x", "   {{{", "   y", "   }}}", "      past it"],
            False,
        ),
        (
            [
                "   1. x",
                "   {{{",
                "   y",
                "   }}}",
                "      at the bound",
            ],
            True,
        ),
        (
            ["   1. x", "   {{{", "   y", "   }}}", "       past it"],
            False,
        ),
    ],
)
def test_continues_item_after_block_stops_at_the_bound(lines, expected):
    parser = TracWikiParser()
    inside = parser._lines_inside_blocks(lines)
    assert (
        parser._continues_item_after_block(
            lines, inside, len(lines) - 1
        )
        is expected
    )


# -- Against Trac's renderer -----------------------------------------------------


def _tag_shape(html: str) -> list[str]:
    """Every list, blockquote and `<pre>` open and close tag and every
    `<li>`, in document order, whitespace collapsed."""
    return [
        " ".join(m.group(0).split())
        for m in re.finditer(
            r"<(?:ol|ul|blockquote|pre)\b[^>]*>"
            r"|</(?:ol|ul|blockquote|pre)>"
            r"|<li\b[^>]*>",
            html,
        )
    ]


def test_tag_shape_tells_a_quote_from_a_list_item():
    assert _tag_shape(
        "<ul><li>a</li></ul><blockquote><blockquote><p>q</p>"
        "</blockquote></blockquote><ul><li>c</li></ul>"
    ) != _tag_shape("<ul><li>a<br/>q</li><li>c</li></ul>")


LIVE_SOURCES = {name: CASES[name][0] for name in CASES}


@pytest.mark.live
@pytest.mark.parametrize("name", sorted(LIVE_SOURCES))
def test_live_source_and_round_trip_render_the_same_structure(name):
    """Ticket #175 acceptance, against Trac's own renderer on /trac_test:
    the TracWiki source and its `tw -> md -> tw` round trip render the same
    sequence of list, blockquote and `<pre>` tags. Read-only
    (wiki.wikiToHtml writes nothing)."""
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.instances import InstanceRegistry

    config, _ = bootstrap_config()
    client = TracClient(
        InstanceRegistry(config, {}).resolve("/trac_test")
    )

    source = LIVE_SOURCES[name]
    before = client.wiki_to_html(source)
    after = client.wiki_to_html(markdown_to_tracwiki(_md(source)))

    assert "<blockquote" in before, before
    assert _tag_shape(before) == _tag_shape(after), (before, after)
