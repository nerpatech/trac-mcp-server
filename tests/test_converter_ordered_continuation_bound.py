"""An ordered item numbered 10 or more continues at marker + 3, not at its
Markdown text column (ticket #178).

Trac continues an ordered item only up to the marker column `m` plus 3
(`Formatter._indent_formatter`, measured in #149 and #175). The write leg put
a continuation line at the Markdown text column, `len(prefix) + 1`, which is
`m + 4` for ` 10. ` (`m + 5` from ` 100. `): one column past the bound, so
Trac ended the list there. Measured on /trac_test on 2026-11-08 (`convert_
preview format=tracwiki`, render only; ticket #178 comment 1):

* depth 0: ` 10. j` / text at column 5 ends the list, at column 4 continues;
* depth 1 (`m = 3`): `   10. j` / text at column 7 ends the list, at
  column 6 continues;
* a `>` citation line at column 5 under ` 10. j` stays INSIDE the item, so
  quotes are not held to the bound; a `{{{` block joins at any indent
  (#144), and so does the `{{{#!div}}}` that carries a table.

The write leg puts, for an ordered marker, a TEXT continuation (a soft-break
or loose-paragraph line, a plain text run, the `rest` lines of a #176
"between" paragraph) at `min(len(prefix) + 1, bound)`, `bound` being
`trac_continuation_bound`, and only while no EMPTY line has ended the list (a
run after one keeps the text column). A quote child, a code child and a table
child keep the text column, and bullets are unchanged (`len(prefix) + 1` is
already `m + 2`, their bound).
"""

from html.parser import HTMLParser

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

# -- builders -------------------------------------------------------------


def _tail(text: str, marker: str) -> str:
    """`text` from the line that opens item `j` (the filler items of a
    depth-1 list, which carry no continuation, are cut off)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{marker} j"):
            return "\n".join(lines[i:])
    raise AssertionError(f"no {marker!r} item j in {text!r}")


def _md_list(
    marker: str, number: int, depth: int, body: list[str]
) -> str:
    """Markdown for an item `j` with `body` lines under it, followed by item
    `k`. `marker` is "ordered" (the item is numbered `number`) or "bullet".
    A body line is written relative to the item's text column; "" is a blank
    line, and a line starting with "^" is written at column 0 (a lazy line).
    At depth 1 the list is nested under `- a`, with `number - 1`
    filler items before `j` (a non-1 ordered list cannot interrupt a
    paragraph, so the nested list must start at 1)."""
    lines: list[str] = []
    pad = " " * (3 * depth)
    if depth:
        lines.append("- a")
        if marker == "ordered":
            lines += [f"   {i}. x" for i in range(1, number)]
    token = f"{number}." if marker == "ordered" else "-"
    next_token = f"{number + 1}." if marker == "ordered" else "-"
    text_col = " " * (len(pad) + len(token) + 1)
    lines.append(f"{pad}{token} j")
    for line in body:
        if line.startswith("^"):
            lines.append(line[1:])
        else:
            lines.append(text_col + line if line else "")
    lines.append(f"{pad}{next_token} k")
    return "\n".join(lines) + "\n"


ORDERED = [(10, 0), (10, 1), (100, 0), (100, 1)]
IDS = ["10-d0", "10-d1", "100-d0", "100-d1"]


def _cols(number: int, depth: int) -> tuple[str, int, int, str]:
    """(marker indent, Trac's bound m + 3, Markdown text column, marker)."""
    m = 2 * depth + 1
    marker = f"{number}."
    return " " * m, m + 3, m + len(marker) + 1, marker


# -- write leg ------------------------------------------------------------


@pytest.mark.parametrize(("number", "depth"), ORDERED, ids=IDS)
class TestWriteLegCapsTextAtMarkerPlus3:
    def _write(self, number, depth, body):
        out = markdown_to_tracwiki(
            _md_list("ordered", number, depth, body)
        )
        ind, bound, _, marker = _cols(number, depth)
        return _tail(out, marker), ind, " " * bound, marker

    def test_lazy_line(self, number, depth):
        # A lazy line at column 0 is a soft break in Markdown.
        out, ind, cap, marker = self._write(number, depth, ["^lazy"])
        assert out == (
            f"{ind}{marker} j\n{cap}lazy\n{ind}{number + 1}. k"
        )

    def test_soft_break(self, number, depth):
        out, ind, cap, marker = self._write(
            number, depth, ["soft1", "soft2"]
        )
        assert out == (
            f"{ind}{marker} j\n{cap}soft1\n{cap}soft2\n"
            f"{ind}{number + 1}. k"
        )

    def test_loose_second_paragraph(self, number, depth):
        out, ind, cap, marker = self._write(
            number, depth, ["", "second para"]
        )
        assert out == (
            f"{ind}{marker} j [[BR]][[BR]]\n{cap}second para\n"
            f"{ind}{number + 1}. k"
        )

    def test_between_paragraph_after_a_nested_list(self, number, depth):
        # #176: the FIRST line goes at marker + 1 (it closes the nested
        # list); every later line is a continuation line, capped.
        out, ind, cap, marker = self._write(
            number, depth, ["- nested", "", "between", "more"]
        )
        nested = " " * (2 * depth + 3)
        first = " " * (2 * depth + 2)
        assert out == (
            f"{ind}{marker} j\n{nested}* nested\n{first}between\n"
            f"{cap}more\n{ind}{number + 1}. k"
        )

    def test_between_loose_second_paragraph(self, number, depth):
        out, ind, cap, marker = self._write(
            number, depth, ["- nested", "", "between1", "", "between2"]
        )
        nested = " " * (2 * depth + 3)
        first = " " * (2 * depth + 2)
        assert out == (
            f"{ind}{marker} j\n{nested}* nested\n"
            f"{first}between1 [[BR]][[BR]]\n{cap}between2\n"
            f"{ind}{number + 1}. k"
        )

    def test_quote_child_keeps_the_text_column(self, number, depth):
        out, ind, _, marker = self._write(number, depth, ["", "> q"])
        text = " " * _cols(number, depth)[2]
        assert (
            out == f"{ind}{marker} j\n{text}> q\n{ind}{number + 1}. k"
        )

    def test_quote_after_a_loose_paragraph_keeps_the_text_column(
        self, number, depth
    ):
        # The paragraph is capped; the quote stays at the text column.
        out, ind, cap, marker = self._write(
            number, depth, ["", "second", "", "> q"]
        )
        text = " " * _cols(number, depth)[2]
        assert out == (
            f"{ind}{marker} j [[BR]][[BR]]\n{cap}second\n{text}> q\n"
            f"{ind}{number + 1}. k"
        )

    def test_quote_gap_line_keeps_the_text_column(self, number, depth):
        # The lone `>` between two sibling quotes (#145) is a quote line.
        out, ind, _, marker = self._write(
            number, depth, ["", "> q1", "", "> q2"]
        )
        text = " " * _cols(number, depth)[2]
        assert out == (
            f"{ind}{marker} j\n{text}> q1\n{text}>\n{text}> q2\n"
            f"{ind}{number + 1}. k"
        )

    def test_code_child_keeps_the_text_column(self, number, depth):
        out, ind, _, marker = self._write(
            number, depth, ["", "```", "code", "```"]
        )
        text = " " * _cols(number, depth)[2]
        assert out == (
            f"{ind}{marker} j\n{text}{{{{{{\n{text}code\n{text}}}}}}}\n"
            f"{ind}{number + 1}. k"
        )

    def test_table_child_keeps_the_text_column(self, number, depth):
        out, ind, _, marker = self._write(
            number, depth, ["", "| a | b |", "|---|---|", "| 1 | 2 |"]
        )
        text = " " * _cols(number, depth)[2]
        assert out == (
            f"{ind}{marker} j\n{text}{{{{{{#!div\n{text}||=a=||=b=||\n"
            f"{text}||1||2||\n{text}}}}}}}\n{ind}{number + 1}. k"
        )


@pytest.mark.parametrize(("number", "depth"), ORDERED, ids=IDS)
class TestWriteLegNoCapAfterABlankSeparator:
    """Review fix 1 (#178): the marker + 3 cap holds only while no EMPTY line
    separates the run from the item. After one Trac has already ended the
    list, so the run keeps `master`'s text-column indent (`continuation_
    indent`); capping it would move it into column 4, a line of the NEXT
    list's body."""

    def _write(self, number, depth, body):
        out = markdown_to_tracwiki(
            _md_list("ordered", number, depth, body)
        )
        ind, _, text, marker = _cols(number, depth)
        return _tail(out, marker), ind, " " * text, marker

    def test_heading_child_after_a_blank(self, number, depth):
        out, ind, text, marker = self._write(
            number, depth, ["", "# h", "", "after"]
        )
        assert out == (
            f"{ind}{marker} j\n\n{text}= h =\n{text}after\n"
            f"{ind}{number + 1}. k"
        )

    def test_heading_child_alone(self, number, depth):
        out, ind, text, marker = self._write(number, depth, ["", "# h"])
        assert out == (
            f"{ind}{marker} j\n\n{text}= h =\n{ind}{number + 1}. k"
        )

    def test_hr_child_after_a_blank(self, number, depth):
        out, ind, text, marker = self._write(
            number, depth, ["", "---", "", "after"]
        )
        assert out == (
            f"{ind}{marker} j\n\n{text}----\n\n{text}after\n"
            f"{ind}{number + 1}. k"
        )

    def test_hr_child_alone(self, number, depth):
        out, ind, text, marker = self._write(number, depth, ["", "---"])
        assert out == (
            f"{ind}{marker} j\n\n{text}----\n{ind}{number + 1}. k"
        )

    def test_two_paragraphs_after_a_nested_list_each_after_a_blank(
        self, number, depth
    ):
        # `||x||` is a passthrough line, so #176's "between" is refused and
        # both paragraphs sit after a blank line (the second inside the run).
        out, ind, text, marker = self._write(
            number, depth, ["- n", "", "||x||", "", "p2"]
        )
        nested = " " * (2 * depth + 3)
        assert out == (
            f"{ind}{marker} j\n{nested}* n\n\n{text}||x||\n\n"
            f"{text}p2\n{ind}{number + 1}. k"
        )

    def test_a_blank_inside_the_item_text_uncaps_the_lines_after_it(
        self, number, depth
    ):
        # `p1` is before the blank (the list is still open: capped); the
        # lines after the blank line keep master's text column.
        out, ind, text, marker = self._write(
            number, depth, ["p1", "", "||x||", "", "p3"]
        )
        bound = " " * _cols(number, depth)[1]
        assert out == (
            f"{ind}{marker} j\n{bound}p1\n\n{text}||x||\n\n{text}p3\n"
            f"{ind}{number + 1}. k"
        )

    def test_a_blank_inside_a_run_uncaps_the_lines_after_it(
        self, number, depth
    ):
        # The run is adjacent to the code child (capped up to its own blank
        # line); the line after that blank keeps the text column.
        out, ind, text, marker = self._write(
            number,
            depth,
            ["", "```", "c", "```", "p1", "||x||", "", "p3"],
        )
        bound = " " * _cols(number, depth)[1]
        assert out == (
            f"{ind}{marker} j\n{text}{{{{{{\n{text}c\n{text}}}}}}}\n"
            f"{bound}p1\n{bound}||x||\n\n{text}p3\n{ind}{number + 1}. k"
        )

    def test_a_run_after_a_code_child_and_a_blank_keeps_the_text_column(
        self, number, depth
    ):
        out, ind, text, marker = self._write(
            number, depth, ["", "```", "c", "```", "||x||", "", "p3"]
        )
        assert out == (
            f"{ind}{marker} j\n{text}{{{{{{\n{text}c\n{text}}}}}}}\n\n"
            f"{text}||x||\n\n{text}p3\n{ind}{number + 1}. k"
        )

    def test_a_paragraph_adjacent_to_a_code_child_is_still_capped(
        self, number, depth
    ):
        # No empty line before it (#143 "adjacent"): the cap applies.
        out, ind, text, marker = self._write(
            number, depth, ["", "```", "c", "```", "after"]
        )
        bound = " " * _cols(number, depth)[1]
        assert out == (
            f"{ind}{marker} j\n{text}{{{{{{\n{text}c\n{text}}}}}}}\n"
            f"{bound}after\n{ind}{number + 1}. k"
        )


def test_the_blank_separator_repro_is_written_with_the_heading_in_the_item():
    """The review's repro: a heading after a blank line under item 10 is
    written at the item's continuation indent, the next item right after."""
    md = "10. j\n\n    # h\n\n    after\n11. k\n"
    assert markdown_to_tracwiki(md) == (
        " 10. j\n\n     = h =\n     after\n 11. k"
    )


def test_the_documented_example_literally():
    """The ticket's example, spelled out: the paragraph lands at column 4."""
    md = "10. j\n\n    second para\n\n11. k\n"
    assert markdown_to_tracwiki(md) == (
        " 10. j [[BR]][[BR]]\n    second para\n 11. k"
    )


# Bytes `master` (052306b) wrote for shapes whose text column is within the
# bound: every bullet and an item 1-9. Pinned literally.
WRITE_PINS = {
    ("bullet", 0, "loose"): " * j [[BR]][[BR]]\n   second para\n * k",
    ("bullet", 0, "soft"): " * j\n   soft1\n   soft2\n * k",
    ("bullet", 0, "between"): (
        " * j\n   * nested\n  between\n   more\n * k"
    ),
    ("bullet", 0, "quote"): " * j\n   > q\n * k",
    ("bullet", 1, "loose"): (
        " * a\n   * j [[BR]][[BR]]\n     second para\n   * k"
    ),
    ("bullet", 1, "soft"): (
        " * a\n   * j\n     soft1\n     soft2\n   * k"
    ),
    ("bullet", 1, "between"): (
        " * a\n   * j\n     * nested\n    between\n     more\n   * k"
    ),
    ("bullet", 1, "quote"): " * a\n   * j\n     > q\n   * k",
    ("ordered9", 0, "loose"): (
        " 9. j [[BR]][[BR]]\n    second para\n 10. k"
    ),
    ("ordered9", 0, "soft"): " 9. j\n    soft1\n    soft2\n 10. k",
    ("ordered9", 0, "between"): (
        " 9. j\n   * nested\n  between\n    more\n 10. k"
    ),
    ("ordered9", 0, "quote"): " 9. j\n    > q\n 10. k",
    # Depth 1: from item 9 on (the filler items before it are cut off).
    ("ordered9", 1, "loose"): (
        "   9. j [[BR]][[BR]]\n      second para\n   10. k"
    ),
    ("ordered9", 1, "soft"): (
        "   9. j\n      soft1\n      soft2\n   10. k"
    ),
    ("ordered9", 1, "between"): (
        "   9. j\n     * nested\n    between\n      more\n   10. k"
    ),
    ("ordered9", 1, "quote"): "   9. j\n      > q\n   10. k",
}
WRITE_BODIES = {
    "loose": ["", "second para"],
    "soft": ["soft1", "soft2"],
    "between": ["- nested", "", "between", "more"],
    "quote": ["", "> q"],
}


@pytest.mark.parametrize(
    ("kind", "depth", "shape"),
    list(WRITE_PINS),
    ids=lambda v: str(v),
)
def test_write_leg_bullets_and_items_1_to_9_keep_masters_bytes(
    kind, depth, shape
):
    if kind == "bullet":
        md = _md_list("bullet", 0, depth, WRITE_BODIES[shape])
        out = markdown_to_tracwiki(md)
    else:
        md = _md_list("ordered", 9, depth, WRITE_BODIES[shape])
        out = _tail(markdown_to_tracwiki(md), "9.")
    assert out == WRITE_PINS[(kind, depth, shape)]


# -- the bound helper -----------------------------------------------------


def test_trac_continuation_bound_helper():
    from trac_mcp_server.converters.common import (
        trac_continuation_bound,
    )

    assert trac_continuation_bound(1, ordered=True) == 4
    assert trac_continuation_bound(1, ordered=False) == 3
    assert trac_continuation_bound(3, ordered=True) == 6
    assert trac_continuation_bound(3, ordered=False) == 5


# -- live -----------------------------------------------------------------


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


@pytest.mark.live
def test_live_loose_ten_item_list_renders_as_one_list_with_item_10_whole():
    """Ticket #178 acceptance, against Trac's own renderer on /trac_test:
    the write leg's output for a ten-item list whose 10th item has a second
    paragraph renders as ONE `<ol>`, the paragraph inside item 10. Read-only
    (`wiki_to_html`, the render path `convert_preview` uses, writes
    nothing)."""
    from tests.live_helpers import TEST_INSTANCE, trac_test_registry
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient

    config, _ = bootstrap_config()
    client = TracClient(
        trac_test_registry(config).resolve(TEST_INSTANCE)
    )

    md = "".join(f"{i}. x\n" for i in range(1, 10)) + (
        "10. j\n\n    second para\n"
    )
    tracwiki = markdown_to_tracwiki(md)
    assert " 10. j [[BR]][[BR]]\n    second para" in tracwiki

    root = _structure(client.wiki_to_html(tracwiki))
    lists = root[2]
    assert [node[0] for node in lists] == ["ol"], lists
    items = lists[0][2]
    assert [node[0] for node in items] == ["li"] * 10, items
    tenth = " ".join(items[9][1])
    assert "j" in tenth and "second para" in tenth, items[9]
    assert not items[9][2], items[9]  # nothing nested: no blockquote
