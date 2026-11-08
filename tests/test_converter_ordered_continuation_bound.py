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

The change, in two legs:

* Write leg: for an ordered marker, a TEXT continuation (a soft-break or
  loose-paragraph line, a plain text run, the `rest` lines of a #176
  "between" paragraph) goes at `min(len(prefix) + 1, len(indent) + 3)`. A
  quote child, a code child and a table child keep the text column, and
  bullets are unchanged (`len(prefix) + 1` is already `m + 2`, their bound).
* Read leg: a line that continues the INNERMOST item, deeper than its marker
  and no deeper than Trac's bound for it, is re-indented to the item's
  Markdown text column when that column is past the bound (ordered items 10
  or more, or a wider gap after the marker), unless an EMPTY line has passed
  since the marker. Without it ` 10. j` / `    a [[BR]][[BR]]` / `    b` read
  back with `b` at column 4: after the blank line `[[BR]][[BR]]` becomes,
  CommonMark leaves the item and `b` is an indented code block.

The read-leg rule is narrow on purpose (the operator's decision, ticket #178
comment 2): a bullet continued at column 2, or an item 1-9 continued at column
2 or 3, after a `[[BR]][[BR]]` has the same problem, but the converter never
writes those shapes, so they are pinned to `master`'s bytes here (WIDER).
Nested list MARKER lines are #179's and are not asserted.
"""

import pytest

from tests.test_converter_list_marker_column import _structure
from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    tracwiki_to_markdown,
)

# -- builders -------------------------------------------------------------


def _read(tracwiki: str) -> str:
    return tracwiki_to_markdown(tracwiki).text


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


def _tw_list(
    number: int, depth: int, column: int, body: list[str]
) -> str:
    """TracWiki for an ordered item `j` numbered `number`, `body` lines at
    `column` under it (a line starting with a space keeps it), then `k`.
    At depth 1 the list is nested under ` * a` with filler items."""
    lines: list[str] = []
    ind = " " * (2 * depth + 1)
    if depth:
        lines.append(" * a")
        lines += [f"   {i}. x" for i in range(1, number)]
    lines.append(f"{ind}{number}. j")
    lines += [" " * column + line if line else "" for line in body]
    lines.append(f"{ind}{number + 1}. k")
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


# -- read leg -------------------------------------------------------------


@pytest.mark.parametrize(("number", "depth"), ORDERED, ids=IDS)
class TestReadLegMovesTheLineToTheTextColumn:
    def _text(self, number, depth):
        ind, bound, text, marker = _cols(number, depth)
        return ind, bound, " " * text, marker

    @pytest.mark.parametrize(
        "offset", [1, 2, 3], ids=["m+1", "m+2", "m+3"]
    )
    def test_lazy_line_at_every_column_inside_the_bound(
        self, number, depth, offset
    ):
        ind, _, text, marker = self._text(number, depth)
        column = len(ind) + offset
        md = _tail(
            _read(_tw_list(number, depth, column, ["lazy"])), marker
        )
        assert (
            md == f"{ind}{marker} j\n{text}lazy\n{ind}{number + 1}. k\n"
        )

    def test_line_at_the_marker_column_goes_there_too(
        self, number, depth
    ):
        # #149's rule, unchanged: the column is the marker's own.
        ind, _, text, marker = self._text(number, depth)
        md = _tail(
            _read(_tw_list(number, depth, len(ind), ["lazy"])), marker
        )
        assert (
            md == f"{ind}{marker} j\n{text}lazy\n{ind}{number + 1}. k\n"
        )

    def test_second_paragraph_after_br_br(self, number, depth):
        # The motivating case. `[[BR]][[BR]]` becomes a whitespace-only
        # line (item content, not a blank line to Trac); the next line must
        # land at the text column to stay in the item.
        ind, bound, text, marker = self._text(number, depth)
        tw = _tw_list(
            number,
            depth,
            bound,
            ["lazy cont [[BR]][[BR]]", "second para"],
        )
        md = _tail(_read(tw), marker)
        assert md == (
            f"{ind}{marker} j\n{text}lazy cont   \n  \n"
            f"{text}second para\n{ind}{number + 1}. k\n"
        )

    def test_soft_break_lines(self, number, depth):
        ind, bound, text, marker = self._text(number, depth)
        tw = _tw_list(number, depth, bound, ["soft1", "soft2"])
        md = _tail(_read(tw), marker)
        assert md == (
            f"{ind}{marker} j\n{text}soft1\n{text}soft2\n"
            f"{ind}{number + 1}. k\n"
        )

    def test_line_after_a_nested_list_that_closes_it(
        self, number, depth
    ):
        # The write leg's "between" form. The nested marker line and the
        # first between line are #179's and keep master's bytes (the nested
        # list sits left of the text column); a LATER line continues the
        # outer item, which is innermost by then, and is moved.
        ind, bound, text, marker = self._text(number, depth)
        nested = " " * (2 * depth + 3)
        first = " " * (2 * depth + 2)
        head = _tw_list(number, depth, 0, []).split("\n")[:-2]
        tail = [
            f"{nested}* nested",
            f"{first}between1 [[BR]][[BR]]",
            f"{' ' * bound}between2",
            f"{ind}{number + 1}. k",
        ]
        md = _tail(_read("\n".join(head + tail) + "\n"), marker)
        assert md == (
            f"{ind}{marker} j\n{nested}- nested\n{first}between1   \n  \n"
            f"{text}between2\n{ind}{number + 1}. k\n"
        )

    def test_line_past_the_bound_keeps_masters_bytes(
        self, number, depth
    ):
        # m + 4 ends the list in Trac: fenced as unconverted, as on master.
        ind, bound, _, marker = self._text(number, depth)
        md = _tail(
            _read(_tw_list(number, depth, bound + 1, ["past"])), marker
        )
        assert md == (
            f"{ind}{marker} j\n\n```tracwiki-unconverted\n"
            f"{' ' * (bound + 1)}past\n```\n\n{ind}{number + 1}. k\n"
        )

    def test_line_after_a_real_blank_line_keeps_masters_bytes(
        self, number, depth
    ):
        ind, bound, _, marker = self._text(number, depth)
        md = _tail(
            _read(_tw_list(number, depth, bound, ["", "after"])), marker
        )
        assert md == (
            f"{ind}{marker} j\n\n{' ' * bound}after\n{ind}{number + 1}. k\n"
        )

    def test_line_after_br_br_then_a_real_blank_line_is_not_moved(
        self, number, depth
    ):
        ind, bound, _, marker = self._text(number, depth)
        tw = _tw_list(
            number, depth, bound, ["a [[BR]][[BR]]", "", "after"]
        )
        md = _tail(_read(tw), marker)
        assert f"\n\n{' ' * bound}after\n" in md

    def test_a_line_that_would_become_a_nested_item_is_not_moved(
        self, number, depth
    ):
        ind, bound, _, marker = self._text(number, depth)
        md = _tail(
            _read(_tw_list(number, depth, bound, ["+ x"])), marker
        )
        assert f"\n{' ' * bound}+ x\n" in md


def test_read_leg_wide_gap_item_moves_to_the_text_column():
    """` 1.   j` has its text at column 6, past the bound (4)."""
    tw = " 1.   j\n    lazy [[BR]][[BR]]\n    second\n 2.   k\n"
    assert _read(tw) == (
        " 1.   j\n      lazy   \n  \n      second\n 2.   k\n"
    )


# What `master` read for shapes whose text column is within Trac's bound, and
# for the WIDER shape the operator chose not to fix. Pinned literally.
READ_PINS = [
    pytest.param(
        " * j\n   lazy [[BR]][[BR]]\n   second\n * k\n",
        " - j\n   lazy   \n  \n   second\n - k\n",
        id="bullet-m+2",
    ),
    pytest.param(
        " * j\n  lazy\n * k\n",
        " - j\n  lazy\n - k\n",
        id="bullet-m+1-tight",
    ),
    pytest.param(
        " 9. j\n    lazy [[BR]][[BR]]\n    second\n 10. k\n",
        " 9. j\n    lazy   \n  \n    second\n 10. k\n",
        id="item9-m+3",
    ),
    pytest.param(
        " 9. j\n   lazy\n 10. k\n",
        " 9. j\n   lazy\n 10. k\n",
        id="item9-m+2-tight",
    ),
    pytest.param(
        "   * a\n     * j\n       lazy [[BR]][[BR]]\n       second\n   * k\n",
        "   - a\n     - j\n       lazy   \n  \n       second\n   - k\n",
        id="bullet-d1",
    ),
    pytest.param(
        " * j\n  lazy [[BR]][[BR]]\n  second\n * k\n",
        " - j\n  lazy   \n  \n  second\n - k\n",
        id="WIDER-bullet-m+1-after-br-br",
    ),
    pytest.param(
        " 9. j\n   lazy [[BR]][[BR]]\n   second\n 10. k\n",
        " 9. j\n   lazy   \n  \n   second\n 10. k\n",
        id="WIDER-item9-m+2-after-br-br",
    ),
    pytest.param(
        " 9. j\n  lazy [[BR]][[BR]]\n  second\n 10. k\n",
        " 9. j\n  lazy   \n  \n  second\n 10. k\n",
        id="WIDER-item9-m+1-after-br-br",
    ),
]


@pytest.mark.parametrize(("tracwiki", "markdown"), READ_PINS)
def test_read_leg_other_shapes_keep_masters_bytes(tracwiki, markdown):
    assert _read(tracwiki) == markdown


# -- round trip -----------------------------------------------------------

SHAPES = {
    "lazy": ["lazy"],
    "soft": ["soft1", "soft2"],
    "loose": ["", "second para"],
    "loose3": ["", "second para", "", "third para"],
}


@pytest.mark.parametrize(("number", "depth"), ORDERED, ids=IDS)
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_markdown_to_tracwiki_to_markdown_to_tracwiki_is_stable(
    number, depth, shape
):
    """MD -> TW -> MD -> TW gives the same TracWiki the second time, and the
    continuation is in item `j` (a Markdown reading of the second leg keeps
    it in the item, so the second write puts it at marker + 3 again)."""
    md = _md_list("ordered", number, depth, SHAPES[shape])
    first = markdown_to_tracwiki(md)
    second = markdown_to_tracwiki(_read(first))
    assert second == first
    # And the text did not leave the item: no fence, no column-0 line.
    assert "tracwiki-unconverted" not in _read(first)
    assert "{{{" not in first


# -- live -----------------------------------------------------------------


@pytest.mark.live
def test_live_loose_ten_item_list_renders_as_one_list_with_item_10_whole():
    """Ticket #178 acceptance, against Trac's own renderer on /trac_test:
    the write leg's output for a ten-item list whose 10th item has a second
    paragraph renders as ONE `<ol>`, the paragraph inside item 10. Read-only
    (`wiki_to_html`, the render path `convert_preview` uses, writes
    nothing)."""
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.instances import InstanceRegistry

    config, _ = bootstrap_config()
    client = TracClient(
        InstanceRegistry(config, {}).resolve("/trac_test")
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
