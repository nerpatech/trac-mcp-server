"""A line at a list item's marker column continues the item (ticket #149).

The read leg's list walks (`_list_stack_step`, the blank-line separator
`_separate_list_from_trailing_paragraph`, `_continues_item_after_block`)
used to pop every open item whose marker column was at or past the line's
column, and to end the list when none was left. So ` 1. a\\n b\\n 1. c` ended
the list at ` b`, and ` * a\\n   * b\\n   text\\n * c` read ` text` as a
lazy continuation of ``b`` only by accident of Markdown's column rules.

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

The cases, and what Trac renders:

* A `" 1. a\\n b at marker column\\n 1. c\\n"`: one list, b in item a.
* B `" * a\\n   * b\\n   text at inner marker col\\n * c\\n"`: the text
  continues the inner item b.
* C `" * a\\n    * b\\n  text col2 between\\n * c\\n"`: b closes, the text
  continues the outer item a (ticket #176, see below).
* D `" * a\\n   * b\\n text at outer col\\n * c\\n"`: the list ends (the text
  is a blockquote, c starts a new list).
* F `"   * a\\n text left of marker but indented\\n   * c\\n"`: the list
  ends.
* G `" * a\\n   col3\\n * c\\n"` and J `" 1. a\\n    col4\\n 1. c\\n"`:
  continue.

D and F, and the other shapes the rule does not touch, are pinned to the
bytes `master` (cf92e87) produced before the change.

**The gate.** The rule applies only to a bullet or digits-only ordered
innermost item, with no tab in either line's indentation, and a line that is
not a list interrupter; the separator also needs no blank line since the
marker. Outside it the sites keep `master`'s pop (GATE_ROWS below).

**Case C was left as on `master` here and is changed by ticket #176.** A
line that continues a non-innermost item, closing deeper ones (C, and a line
at a middle marker's column in a three-level list), was pinned to `master`'s
bytes by #149, because the Markdown for it (the text after the nested list
in a's item) could only be written back below a blank line, ending the Trac
list. #176 changes both legs together: the read leg moves the line into the
outer item below a blank line, and the write leg writes a paragraph after a
nested list one column past the item's marker with no blank line, which Trac
keeps in the outer item. Those rows are in
tests/test_converter_between_column_continuation.py, and C and the middle
row below carry the new bytes. `_continued_item`'s answer for C is
unit-tested here.
"""

import re
from html.parser import HTMLParser

import mistune
import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    _MISTUNE_PLUGINS,
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    _continued_item,
    _list_stack_step,
    tracwiki_to_markdown,
)

A = " 1. a\n b at marker column\n 1. c\n"
B = " * a\n   * b\n   text at inner marker col\n * c\n"
C = " * a\n    * b\n  text col2 between\n * c\n"
D = " * a\n   * b\n text at outer col\n * c\n"
F = "   * a\n text left of marker but indented\n   * c\n"
G = " * a\n   col3\n * c\n"
J = " 1. a\n    col4\n 1. c\n"
MIDDLE = " * a\n   * b\n     * c\n   mid\n * d\n"
BLOCK = " 1. d\n{{{\nx\n}}}\n e at marker column after a block\n 1. f\n"

# (source, expected read-leg Markdown)
READ_ROWS = {
    "A": (A, " 1. a\n    b at marker column\n 1. c\n"),
    "B": (B, " - a\n   - b\n     text at inner marker col\n - c\n"),
    "G": (G, " - a\n   col3\n - c\n"),
    "J": (J, " 1. a\n    col4\n 1. c\n"),
    # #176: closes deeper items to continue an outer one; a blank line and
    # that item's text column.
    "C": (C, " - a\n    - b\n\n   text col2 between\n - c\n"),
    "middle": (
        MIDDLE,
        " - a\n   - b\n     - c\n\n     mid\n - d\n",
    ),
    "block": (
        BLOCK,
        " 1. d\n    ```\n    x\n    ```\n"
        "    e at marker column after a block\n 1. f\n",
    ),
}

# Shapes the rule leaves alone: the bytes master produced.
UNCHANGED_ROWS = {
    "D": (D, " - a\n   - b\n\n text at outer col\n - c\n"),
    "F": (F, "   - a\n\n text left of marker but indented\n   - c\n"),
}


def _md(wiki: str) -> str:
    return tracwiki_to_markdown(wiki).text


# -- _continued_item -----------------------------------------------------------


@pytest.mark.parametrize(
    ("columns", "width", "expected", "ordered"),
    [
        ([1], 1, 0, False),  # A: at the only marker's column
        ([1, 3], 3, 1, False),  # B: at the inner marker's column
        ([1, 4], 2, 0, False),  # C: between; the inner item closes
        ([1, 3], 1, None, False),  # D: at the outer column, deeper open
        ([3], 1, None, False),  # F: left of the only marker
        ([1], 3, 0, False),  # G: deeper than the marker, at the bound
        ([1], 4, 0, True),  # J: an ordered item's bound is m + 3
        ([1], 4, None, False),  # #175 case H: past a bullet's bound
        ([1, 3, 5], 3, 1, False),  # a middle item of three levels
        ([1, 3, 5], 4, 1, False),  # between the middle and the inner
        ([1, 3, 5], 2, 0, False),
        # past the innermost bound (m+2): the list ends (#175)
        ([1, 3, 5], 9, None, False),
        ([1, 3, 5], 7, 2, False),  # exactly at the bound
        ([1, 3], 0, None, False),  # column zero ends the list
        ([1], 0, None, False),
        ([], 3, None, False),
    ],
)
def test_continued_item(columns, width, expected, ordered):
    assert (
        _continued_item(
            columns, width, innermost_ordered=ordered, upper_bound=True
        )
        == expected
    )


# -- Read leg ---------------------------------------------------------------


@pytest.mark.parametrize("name", list(READ_ROWS))
def test_read_leg_rows(name):
    wiki, expected = READ_ROWS[name]
    assert _md(wiki) == expected


@pytest.mark.parametrize("name", list(UNCHANGED_ROWS))
def test_shapes_the_rule_does_not_touch_read_as_on_master(name):
    wiki, expected = UNCHANGED_ROWS[name]
    assert _md(wiki) == expected


@pytest.mark.parametrize(
    ("columns", "line", "kept"),
    [
        ([1], " b", [1]),  # A: the innermost item keeps the line (#149)
        ([1, 3], "   t", [1, 3]),  # B
        ([1, 3], "  t", [1]),  # C: b closes, a stays open (#176)
        # a middle marker's column: the middle item stays open (#176)
        ([1, 3, 5], "   t", [1, 3]),
        ([1, 3], " t", []),  # D: the list ends
    ],
    ids=["A", "B", "C", "middle", "D"],
)
def test_list_stack_step_keeps_the_continued_item_open(
    columns, line, kept
):
    stack = [(c, re.match(r"\s*", "")) for c in columns]
    _list_stack_step(stack, line, upper_bound=False)
    assert [c for c, _ in stack] == kept


def test_a_line_that_closes_deeper_items_after_a_block_continues_the_outer():
    """#176: `_continues_item_after_block` still does not claim it, but the
    separator moves it into a's item below a blank line."""
    wiki = " * a\n   * b\n   {{{\n   x\n   }}}\n  text\n * c\n"
    assert _md(wiki) == (
        " - a\n   - b\n     ```\n     x\n     ```\n\n   text\n - c\n"
    )


def test_a_line_after_a_blank_line_keeps_the_pre_149_reading():
    """Trac ends the list at a blank line, which the measurement did not
    include: a line at the marker column right after one is read as before
    (ticket #144's REDUCED row depends on it). Master's bytes."""
    wiki = " 2. a\n\n b\n 3. c\n"
    assert _md(wiki) == " 2. a\n\n b\n\n 3. c\n"


# -- The gate (code review of the first #149 branch) ---------------------------
#
# The marker-column rule applies only when the innermost item is a bullet or
# a digits-only ordered item, neither line has a tab in its indentation, the
# line is not one of CommonMark's list interrupters, and (separator only) no
# blank line has passed since the item's marker. Everywhere else the bytes
# are master's (cf92e87), on the read leg and on the round trip.

# (source, master's read-leg bytes, master's round-trip bytes)
GATE_ROWS = {
    # F1: a lettered list (fallback path, #74/#89): the first branch put a
    # blank line inside the code body.
    "F1 lettered, block, marker line": (
        " a. x\n {{{\n y\n }}}\n a. z\n",
        " a. x\n\n ```\n y\n```\n```tracwiki-unconverted\n a. z\n```\n",
        "a. x\n\n{{{\ny\n}}}\n\n a. z",
    ),
    # F2: a lettered list: the first branch moved the continuation out of
    # the fallback fence into the list.
    "F2 lettered, block, continuation": (
        " a. x\n {{{\n y\n z\n }}}\n b\n",
        " a. x\n\n ```\n y\n z\n```\n```tracwiki-unconverted\n b\n```\n",
        "a. x\n\n{{{\ny\nz\n}}}\n\n b",
    ),
    # F3: a tab-indented list is a declined shape, and the separator
    # measures in characters.
    "F3 tab-indented": (
        "\t* a\n\tb\n\t* c\n",
        "\t* a\n\n\tb\n\t* c\n",
        "{{{\n* a\n\nb\n* c\n}}}",
    ),
    # F5: a blockquote line at the marker column, then a continuation: the
    # first branch moved the continuation below a blank line.
    "F5 interrupter then continuation": (
        " * a\n > q\n b\n * c\n",
        " - a\n\n > q\n b\n - c\n",
        " * a\n\n  q\n  b\n\n * c",
    ),
    # F6: the separator keeps its stack across a blank line (#52/#53), so a
    # line two below a blank one was judged against closed items.
    "F6 line below a blank line": (
        " * a\n   * b\n\n   c\n d\n * e\n",
        " - a\n   - b\n\n   c\n\n d\n - e\n",
        # #176 changed the write leg: `c` follows b's nested list, so it
        # now goes between the markers (the Markdown puts it in a).
        " * a\n   * b\n  c\n\nd\n\n * e",
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


# Inside the gate (computed on be8880b): the improved reading stays.
@pytest.mark.parametrize(
    ("wiki", "read", "written"),
    [
        (
            " 1. x\n {{{\n y\n z\n }}}\n b\n",
            " 1. x\n    ```\n    y\n    z\n    ```\n    b\n",
            " 1. x\n    {{{\n    y\n    z\n    }}}\n    b",
        ),
        (
            " * x\n {{{\n y\n }}}\n b\n * c\n",
            " - x\n   ```\n   y\n   ```\n   b\n - c\n",
            " * x\n   {{{\n   y\n   }}}\n   b\n * c",
        ),
    ],
    ids=["numeric block", "bullet block then marker"],
)
def test_inside_the_gate_the_continuation_stays_in_the_item(
    wiki, read, written
):
    assert _md(wiki) == read
    assert markdown_to_tracwiki(read) == written


# -- Structure in Markdown --------------------------------------------------


def _parse(md: str) -> list[dict]:
    return mistune.create_markdown(
        renderer=None, plugins=_MISTUNE_PLUGINS
    )(md)


def _raw(token: dict) -> str:
    if "raw" in token:
        return token["raw"]
    return " ".join(_raw(c) for c in token.get("children", []))


def _own_text(item: dict) -> str:
    """The item's text outside any nested list."""
    return " ".join(
        _raw(c) for c in item["children"] if c["type"] != "list"
    )


def _nested_items(item: dict) -> list[dict]:
    return [
        i
        for c in item["children"]
        if c["type"] == "list"
        for i in c["children"]
    ]


def test_a_text_sits_in_a_list_item():
    items = _parse(_md(A))[0]["children"]
    assert len(items) == 2
    assert "b at marker column" in _own_text(items[0])
    assert "b at marker column" not in _own_text(items[1])


def test_b_text_sits_in_the_inner_item():
    outer = _parse(_md(B))[0]["children"]
    assert len(outer) == 2
    inner = _nested_items(outer[0])
    assert len(inner) == 1
    assert "text at inner marker col" in _own_text(inner[0])
    assert "text at inner marker col" not in _own_text(outer[0])


# -- Round trip ---------------------------------------------------------------

ROUND_TRIPS = ["A", "B", "G", "J"]


def _continuation_line(wiki: str, needle: str) -> tuple[int, str]:
    lines = wiki.split("\n")
    idx = next(i for i, ln in enumerate(lines) if needle in ln)
    return idx, lines[idx]


def _marker_and_text_columns(wiki: str, idx: int) -> tuple[int, int]:
    """(marker column, text column) of the nearest item marker above the
    line at `idx` that the line sits right of (the item it continues)."""
    lines = wiki.split("\n")
    here = len(lines[idx]) - len(lines[idx].lstrip(" "))
    for line in reversed(lines[:idx]):
        found = re.match(r"^( +)(?:[*-]|\d+\.) +", line)
        if found and len(found.group(1)) < here:
            return len(found.group(1)), len(found.group(0))
    raise AssertionError(wiki)


NEEDLES = {
    "A": "b at marker column",
    "B": "text at inner marker col",
    "G": "col3",
    "J": "col4",
}
SOURCES = {"A": A, "B": B, "G": G, "J": J}


def _check_round_trip(name: str) -> None:
    wiki = markdown_to_tracwiki(_md(SOURCES[name]))
    idx, line = _continuation_line(wiki, NEEDLES[name])
    marker, text_col = _marker_and_text_columns(wiki, idx)
    depth = len(line) - len(line.lstrip(" "))
    # Deeper than the continued item's marker, within its text column:
    # Trac's own band (marker + 1 .. marker + 2 for a bullet, + 3 for an
    # ordered item, whose text column is marker + 3).
    assert marker < depth <= text_col, wiki
    assert "\n\n" not in wiki, wiki  # the list is not split
    # Byte-stable on a second cycle.
    again = markdown_to_tracwiki(_md(wiki))
    assert again == wiki


@pytest.mark.parametrize("name", ROUND_TRIPS)
def test_round_trip_keeps_the_text_in_the_item_and_is_stable(name):
    _check_round_trip(name)


@pytest.mark.parametrize(
    ("wiki", "written"),
    [
        # #176: the text comes back between the outer item's marker and the
        # nested one's, with no blank line, so the list stays whole and the
        # text stays in the outer item.
        (C, " * a\n   * b\n  text col2 between\n * c"),
        (MIDDLE, " * a\n   * b\n     * c\n    mid\n * d"),
    ],
    ids=["C", "middle"],
)
def test_round_trip_of_a_line_closing_deeper_items_keeps_the_outer_item(
    wiki, written
):
    assert markdown_to_tracwiki(_md(wiki)) == written


def test_round_trip_of_the_block_case_keeps_e_in_the_item():
    wiki = markdown_to_tracwiki(_md(BLOCK))
    assert wiki == (
        " 1. d\n    {{{\n    x\n    }}}\n"
        "    e at marker column after a block\n 2. f"
    )
    assert markdown_to_tracwiki(_md(wiki)) == wiki


# -- Live: the real renderer on /trac_test -----------------------------------


class _ListTree(HTMLParser):
    """The nesting of ul/ol/li/blockquote/pre in a render, with the text each
    holds outside any nested one: `[tag, [text, ...], [children]]`."""

    _TAGS = ("ul", "ol", "li", "blockquote", "pre")

    def __init__(self) -> None:
        super().__init__()
        self.root: list = ["root", [], []]
        self._open: list[list] = [self.root]

    def handle_starttag(self, tag, attrs):
        if tag in self._TAGS:
            node: list = [tag, [], []]
            self._open[-1][2].append(node)
            self._open.append(node)

    def handle_endtag(self, tag):
        if tag in self._TAGS and len(self._open) > 1:
            self._open.pop()

    def handle_data(self, data):
        text = " ".join(data.split())
        if text:
            self._open[-1][1].append(text)


def _structure(html: str) -> list:
    parser = _ListTree()
    parser.feed(html)
    return parser.root


def test_structure_helper_tells_item_text_apart():
    """The live row's comparison would be vacuous if it could not."""
    in_a = _structure(
        "<ul><li>a b<ul><li>x</li></ul></li><li>c</li></ul>"
    )
    in_b = _structure(
        "<ul><li>a<ul><li>x b</li></ul></li><li>c</li></ul>"
    )
    split = _structure("<ul><li>a</li></ul><blockquote>b</blockquote>")
    assert in_a == _structure(
        "<ul><li>a b<ul><li>x</li></ul></li><li>c</li></ul>"
    )
    assert in_a != in_b
    assert in_a != split


@pytest.mark.live
@pytest.mark.parametrize(
    "name",
    [
        "A",
        "B",
        "G",
        "J",
    ],
)
def test_live_source_and_round_trip_render_the_same_list_structure(
    name,
):
    """Ticket #149 acceptance, against Trac's own renderer on /trac_test: the
    TracWiki source and `markdown_to_tracwiki(tracwiki_to_markdown(source))`
    render the same `<li>` nesting, and the continuation text sits in the same
    `<li>`. Read-only (wiki.wikiToHtml writes nothing)."""
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
