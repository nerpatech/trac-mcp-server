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

* `d >= m_k`: it continues the innermost item (the upper bound, marker + 2
  for a bullet and + 3 for an ordered item, is a separate ticket and not
  applied here);
* `0 < d < m_k` and `d > m_0`: the items whose marker is past `d` close and
  it continues the deepest one left;
* otherwise (`d <= m_0` while a deeper item is open, or `d == 0`): the
  list ends.

The cases, and what Trac renders:

* A `" 1. a\\n b at marker column\\n 1. c\\n"`: one list, b in item a.
* B `" * a\\n   * b\\n   text at inner marker col\\n * c\\n"`: the text
  continues the inner item b.
* C `" * a\\n    * b\\n  text col2 between\\n * c\\n"`: b closes, the text
  continues the outer item a.
* D `" * a\\n   * b\\n text at outer col\\n * c\\n"`: the list ends (the text
  is a blockquote, c starts a new list).
* F `"   * a\\n text left of marker but indented\\n   * c\\n"`: the list
  ends.
* G `" * a\\n   col3\\n * c\\n"` and J `" 1. a\\n    col4\\n 1. c\\n"`:
  continue.

D and F, and the other shapes the rule does not touch, are pinned to the
bytes `master` (cf92e87) produced before the change.

**Round trip.** For C the write leg cannot give the text back inside ``a``:
Markdown has the text after the nested list in a's item (a loose item), which
TracWiki can only express by ending the list at a blank line (the #125
list-item split, `find_list_item_splits`). The C round-trip rows are strict
xfails recording that, so they turn loud if the write leg ever learns it.
"""

import re
from html.parser import HTMLParser

import mistune
import pytest

from trac_mcp_server.converters import find_list_item_splits
from trac_mcp_server.converters.markdown_to_tracwiki import (
    _MISTUNE_PLUGINS,
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    _continued_item,
    tracwiki_to_markdown,
)

A = " 1. a\n b at marker column\n 1. c\n"
B = " * a\n   * b\n   text at inner marker col\n * c\n"
C = " * a\n    * b\n  text col2 between\n * c\n"
D = " * a\n   * b\n text at outer col\n * c\n"
F = "   * a\n text left of marker but indented\n   * c\n"
G = " * a\n   col3\n * c\n"
J = " 1. a\n    col4\n 1. c\n"
BLOCK = " 1. d\n{{{\nx\n}}}\n e at marker column after a block\n 1. f\n"

# (source, expected read-leg Markdown)
READ_ROWS = {
    "A": (A, " 1. a\n    b at marker column\n 1. c\n"),
    "B": (B, " - a\n   - b\n     text at inner marker col\n - c\n"),
    "C": (C, " - a\n    - b\n\n   text col2 between\n - c\n"),
    "G": (G, " - a\n   col3\n - c\n"),
    "J": (J, " 1. a\n    col4\n 1. c\n"),
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
    ("columns", "width", "expected"),
    [
        ([1], 1, 0),  # A: at the only marker's column
        ([1, 3], 3, 1),  # B: at the inner marker's column
        ([1, 4], 2, 0),  # C: between; the inner item closes
        ([1, 3], 1, None),  # D: at the outer column, deeper item open
        ([3], 1, None),  # F: left of the only marker
        ([1], 3, 0),  # G: deeper than the marker
        ([1], 4, 0),  # J
        ([1, 3, 5], 3, 1),  # a middle item of three levels
        ([1, 3, 5], 4, 1),  # between the middle and the inner
        ([1, 3, 5], 2, 0),
        ([1, 3, 5], 9, 2),  # deeper than the innermost: unchanged
        ([1, 3], 0, None),  # column zero ends the list
        ([1], 0, None),
        ([], 3, None),
    ],
)
def test_continued_item(columns, width, expected):
    assert _continued_item(columns, width) == expected


# -- Read leg ---------------------------------------------------------------


@pytest.mark.parametrize("name", list(READ_ROWS))
def test_read_leg_rows(name):
    wiki, expected = READ_ROWS[name]
    assert _md(wiki) == expected


@pytest.mark.parametrize("name", list(UNCHANGED_ROWS))
def test_shapes_the_rule_does_not_touch_read_as_on_master(name):
    wiki, expected = UNCHANGED_ROWS[name]
    assert _md(wiki) == expected


def test_three_level_middle_item_closes_the_inner_one():
    """A line at the middle marker's column closes the innermost item and
    continues the middle one: a blank line, then the middle's text column."""
    wiki = " * a\n   * b\n     * c\n   mid\n * d\n"
    assert _md(wiki) == (" - a\n   - b\n     - c\n\n     mid\n - d\n")


def test_a_line_after_a_blank_line_keeps_the_pre_149_reading():
    """Trac ends the list at a blank line, which the measurement did not
    include: a line at the marker column right after one is read as before
    (ticket #144's REDUCED row depends on it). Master's bytes."""
    wiki = " 2. a\n\n b\n 3. c\n"
    assert _md(wiki) == " 2. a\n\n b\n\n 3. c\n"


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


def test_c_text_sits_in_the_outer_item_not_the_inner_one():
    outer = _parse(_md(C))[0]["children"]
    assert len(outer) == 2
    assert "text col2 between" in _own_text(outer[0])
    inner = _nested_items(outer[0])
    assert len(inner) == 1
    assert "text col2 between" not in _own_text(inner[0])


# -- Round trip ---------------------------------------------------------------

ROUND_TRIPS = ["A", "B", "G", "J"]
C_SPLIT = pytest.mark.xfail(
    strict=True,
    reason="C: text after a nested list in the outer item; TracWiki can only "
    "write it after a blank line, which ends the list (#125 split)",
)


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
    "C": "text col2 between",
    "G": "col3",
    "J": "col4",
}
SOURCES = {"A": A, "B": B, "C": C, "G": G, "J": J}


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


@C_SPLIT
def test_round_trip_c_keeps_the_text_in_the_outer_item_unsplit():
    _check_round_trip("C")


def test_round_trip_c_is_the_list_item_split_the_advisory_reports():
    """The reason for the C xfail above, measured: the Markdown has the text
    after the nested list in a's item, and `find_list_item_splits` says so."""
    md = _md(C)
    assert [s["item"] for s in find_list_item_splits(md)] == ["a"]
    assert markdown_to_tracwiki(md) == (
        " * a\n   * b\n\n   text col2 between\n * c"
    )


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
        pytest.param(
            "C",
            marks=pytest.mark.xfail(
                strict=False,
                reason="C round-trips through the #125 split (see module "
                "docstring); the live render says whether Trac agrees",
            ),
        ),
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
