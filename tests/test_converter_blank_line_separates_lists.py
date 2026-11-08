"""Two Trac lists separated only by a blank line stay two lists (ticket #177).

Trac ends a list at any blank line (#143), so ` * a` / blank / ` * c` is two
`<ul>`. Passed through unchanged, CommonMark reads the same text as ONE loose
list, and the write leg then merged them (and renumbered an ordered list).

Measured on /trac_test on 2026-11-07 (ticket #177 comment 1, P1-P6): every
blank line between two list items ends the list, and the next marker opens a
new top-level list at any indent.

* Read leg (`_separate_adjacent_lists`): a top-level marker after a blank line
  that CommonMark would make a sibling of the open list switches its marker
  character (`-`/`*`, `.`/`)`), so the Markdown has two lists. The later
  top-level items of the same Trac list take the same character.
* Write leg: unchanged. Two adjacent Markdown lists are written with a blank
  line between them and each keeps its start number (#150).

P4 (a marker at or past the previous item's text column) is nested by
CommonMark and is ticket #183's; it keeps master's bytes here.
"""

from html.parser import HTMLParser

import pytest

from tests.test_converter_list_marker_column import (
    _nested_items,
    _parse,
)
from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    tracwiki_to_markdown,
)

P1 = " 1. a\n 1. b\n\n 1. c\n 1. d\n\n 1. e\n"
P2 = " * a\n\n * c\n"
P3 = " * a\n\n  * c\n"
P4 = " * a\n\n   * c\n * d\n"
P5 = " 3. a\n\n 7. c\n"
P6 = " * a\n   * b\n\n * c\n"
TIGHT2 = " * a\n\n * c\n * d\n"
NESTED_FORM = " * a\n\n * * x\n"
PARAGRAPH = " * a\n\nhello\n\n * c\n"
BULLET_THEN_ORDERED = " * a\n\n 1. c\n"
ORDERED_THEN_BULLET = " 1. a\n\n * c\n"


def _md(wiki: str) -> str:
    return tracwiki_to_markdown(wiki).text


def _tw(md: str) -> str:
    return markdown_to_tracwiki(md)


# -- Read leg ---------------------------------------------------------------

READ_ROWS = {
    "P1": (P1, " 1. a\n 1. b\n\n 1) c\n 1) d\n\n 1. e\n"),
    "P2": (P2, " - a\n\n * c\n"),
    "P3": (P3, " - a\n\n  * c\n"),
    "P5": (P5, " 3. a\n\n 7) c\n"),
    "P6": (P6, " - a\n   - b\n\n * c\n"),
    "two tight items after the blank": (
        TIGHT2,
        " - a\n\n * c\n * d\n",
    ),
    "the one-line nested form switches only its first marker": (
        NESTED_FORM,
        " - a\n\n * - x\n",
    ),
    "a third bullet list switches back": (
        " * a\n\n * b\n\n * c\n",
        " - a\n\n * b\n\n - c\n",
    ),
    "a switched list's nested list is untouched": (
        " * a\n\n * b\n   * x\n * c\n",
        " - a\n\n * b\n   - x\n * c\n",
    ),
}


@pytest.mark.parametrize("name", list(READ_ROWS))
def test_read_leg_rows(name):
    wiki, expected = READ_ROWS[name]
    assert _md(wiki) == expected


# (source, master's bytes): outside the pass.
MASTER_ROWS = {
    "a paragraph between two lists": (
        PARAGRAPH,
        " - a\n\nhello\n\n - c\n",
    ),
    "a bullet list then an ordered one": (
        BULLET_THEN_ORDERED,
        " - a\n\n 1. c\n",
    ),
    "an ordered list then a bullet one": (
        ORDERED_THEN_BULLET,
        " 1. a\n\n - c\n",
    ),
    # Not master's bytes any more (#183): `   * c` after the blank line
    # opens a NEW top-level list in Trac, so the read leg carries it
    # verbatim in a fence (master left it as `   - c`, which CommonMark
    # nests in `a`).
    "P4 is fenced, not nested (#183)": (
        P4,
        " - a\n\n```tracwiki-unconverted\n   * c\n```\n - d\n",
    ),
    "one list, no blank line": (" * a\n * c\n", " - a\n - c\n"),
    "a code placeholder at column 0 ends the list": (
        " * a\n\n{{{\nx\n}}}\n\n * c\n",
        " - a\n\n```\nx\n```\n\n - c\n",
    ),
}


@pytest.mark.parametrize("name", list(MASTER_ROWS))
def test_outside_the_pass_the_read_leg_is_masters(name):
    wiki, expected = MASTER_ROWS[name]
    assert _md(wiki) == expected


LAZY = " * a\n  text\n\n * c\n"


def test_a_lazy_continuation_line_keeps_the_list_open():
    """` * a` / `  text` (column 2, left of text column 3, no blank line
    before it) is a lazy continuation of the item's paragraph in CommonMark,
    and Trac keeps it in the item: the Markdown list is still open, so `c`,
    after the blank line, is a new list and switches."""
    assert _md(LAZY) == " - a\n  text\n\n * c\n"


def test_a_lazy_continuation_line_round_trips_apart():
    assert _tw(_md(LAZY)) == " * a\n   text\n\n * c"


def test_a_paragraph_after_a_blank_line_ends_the_list_after_a_lazy_line():
    """The lazy line keeps the list open, but a column-0 paragraph after a
    blank line ends it: the next list is not a sibling, so no switch."""
    wiki = " * a\n  text\n\npara\n\n * c\n"
    assert _md(wiki) == " - a\n  text\n\npara\n\n - c\n"


def test_a_quote_at_column_1_after_the_item_ends_the_list():
    """A `>` line interrupts the paragraph. Master's own pass puts a blank
    line before it, and the quote then ends the Markdown list, so `c` is a
    new list on its own and keeps master's bytes."""
    assert _md(" * a\n > q\n\n * c\n") == " - a\n\n > q\n\n - c\n"


def test_a_quote_at_column_2_after_the_item_ends_the_list():
    """`>` at column 2 (left of the text column) interrupts the paragraph
    with no blank line before it: it is no lazy continuation, the list ends,
    and `c` keeps master's bytes."""
    assert _md(" * a\n  > q\n\n * c\n") == " - a\n  > q\n\n - c\n"


def test_a_rule_at_column_2_after_the_item_ends_the_list():
    assert _md(" * a\n  ----\n\n * c\n") == " - a\n  ----\n\n - c\n"


def test_a_plain_line_at_the_text_column_keeps_the_list_open():
    assert _md(" * a\n   text\n\n * c\n") == " - a\n   text\n\n * c\n"


def test_a_marker_after_an_unrelated_block_is_not_switched():
    assert _md(" * a\n\n= h =\n\n * c\n") == " - a\n\n# h\n - c\n"


def test_a_lettered_list_is_never_switched():
    """Lettered lists are fenced by the fallback pass and never get here."""
    out = _md(" a. x\n\n a. y\n")
    assert ") y" not in out and "* y" not in out


# -- A whitespace-only line is not a Trac blank line (code review) ----------

# The read leg rewrites a `[[BR]]` alone on a continuation line, and a macro
# dropped with unknown_macros="drop", to a whitespace-only Markdown line. It
# is item content, not the blank line that ends a Trac list, so one Trac list
# stays one list: the round trip is master's.
BR_ITEM = " * a\n   [[BR]]\n * b\n"
BR_ORDERED = " 1. a\n   [[BR]]\n 2. b\n"
DROPPED_MACRO = " * a\n   [[PageOutline]]\n * b\n"
WHITESPACE_BLANK = " * a\n \n * b\n"


def _md_drop(wiki: str) -> str:
    return tracwiki_to_markdown(wiki, unknown_macros="drop").text


@pytest.mark.parametrize(
    "wiki,read",
    [
        (BR_ITEM, " - a\n     \n - b\n"),
        (BR_ORDERED, " 1. a\n     \n 2. b\n"),
        (WHITESPACE_BLANK, " - a\n \n - b\n"),
        ("\r\n".join(P2.split("\n")), " - a\r\n\r\n - c\r\n"),
    ],
    ids=["br", "br ordered", "whitespace-only blank", "crlf"],
)
def test_a_whitespace_only_line_keeps_masters_read_leg(wiki, read):
    assert _md(wiki) == read


@pytest.mark.parametrize(
    "wiki,written",
    [
        (BR_ITEM, " * a\n * b"),
        (BR_ORDERED, " 1. a\n 2. b"),
        (WHITESPACE_BLANK, " * a\n * b"),
    ],
    ids=["br", "br ordered", "whitespace-only blank"],
)
def test_a_whitespace_only_line_round_trips_as_one_list(wiki, written):
    assert _tw(_md(wiki)) == written


def test_a_dropped_macro_inside_an_item_round_trips_as_one_list():
    md = _md_drop(DROPPED_MACRO)
    assert md == " - a\n   \n - b\n"
    assert _tw(md) == " * a\n * b"


def test_a_dropped_macro_at_column_0_between_items():
    """Trac ends the list at a column-0 line, so two lists. The dropped macro
    leaves an EMPTY line, which is a blank line to this pass: `b` switches
    and the round trip keeps two lists, as Trac renders them."""
    md = _md_drop(" * a\n[[PageOutline]]\n * b\n")
    assert md == " - a\n\n * b\n"
    assert _tw(md) == " * a\n\n * b"


# -- Indented code and tabs (code review) ------------------------------------


def test_a_marker_at_column_4_is_indented_code_and_is_not_switched():
    assert _md(" 10. a\n\n    10. b\n") == " 10. a\n\n    10. b\n"
    assert _tw(_md(" 10. a\n\n    10. b\n")) == (
        " 10. a\n\n{{{\n10. b\n}}}"
    )


def test_a_tab_indented_marker_is_not_switched():
    assert _md("\t* a\n\n\t* c\n") == "\t* a\n\n\t* c\n"
    assert _tw(_md("\t* a\n\n\t* c\n")) == "{{{\n* a\n\n* c\n}}}"


# -- Write leg: unchanged ---------------------------------------------------


def test_a_single_loose_markdown_list_still_writes_as_one_list():
    assert _tw("- a\n\n- c\n") == " * a\n * c"
    assert _tw("1. a\n\n1. c\n") == " 1. a\n 2. c"


def test_adjacent_markdown_lists_still_write_apart():
    assert _tw("- a\n\n* c\n") == " * a\n\n * c"
    assert _tw("3. a\n\n7) c\n") == " 3. a\n\n 7. c"


# -- Round trip -------------------------------------------------------------

ROUND_TRIP_ROWS = {
    "P1": (P1, " 1. a\n 2. b\n\n 1. c\n 2. d\n\n 1. e"),
    "P2": (P2, " * a\n\n * c"),
    "P3": (P3, " * a\n\n * c"),
    "P5": (P5, " 3. a\n\n 7. c"),
    "P6": (P6, " * a\n   * b\n\n * c"),
    "two tight items after the blank": (
        TIGHT2,
        " * a\n\n * c\n * d",
    ),
}


@pytest.mark.parametrize("name", list(ROUND_TRIP_ROWS))
def test_round_trip_keeps_the_lists_apart(name):
    wiki, expected = ROUND_TRIP_ROWS[name]
    assert _tw(_md(wiki)) == expected


def test_p5_keeps_its_start_numbers():
    assert _tw(_md(P5)) == " 3. a\n\n 7. c"


@pytest.mark.parametrize(
    "name",
    ["P1", "P2", "P3", "P5", "P6", "two tight items after the blank"],
)
def test_the_markdown_has_as_many_lists_as_the_source(name):
    wiki, _ = ROUND_TRIP_ROWS[name]
    lists = [t for t in _parse(_md(wiki)) if t["type"] == "list"]
    expected = {"P1": 3, "P6": 2}.get(name, 2)
    assert len(lists) == expected


def test_p6_nested_list_stays_in_the_first_list():
    first, second = [t for t in _parse(_md(P6)) if t["type"] == "list"]
    assert len(first["children"]) == 1
    assert len(_nested_items(first["children"][0])) == 1
    assert len(second["children"]) == 1


def test_p5_start_numbers_in_markdown():
    first, second = [t for t in _parse(_md(P5)) if t["type"] == "list"]
    assert first["attrs"]["start"] == 3
    assert second["attrs"]["start"] == 7


@pytest.mark.parametrize(
    "wiki",
    [P2, P5, P6, TIGHT2, PARAGRAPH],
    ids=["P2", "P5", "P6", "tight2", "para"],
)
def test_second_cycle_gives_the_first_markdown_byte_for_byte(wiki):
    """tw -> md -> tw -> md. The source has no final newline, as the
    writer's own output has none, so the comparison is exact."""
    first_md = _md(wiki.rstrip("\n"))
    assert _md(_tw(first_md)) == first_md


@pytest.mark.parametrize(
    "wiki",
    [P1, P3, NESTED_FORM, LAZY],
    ids=["P1", "P3", "nested", "lazy"],
)
def test_second_cycle_is_stable_once_the_writer_has_normalised(wiki):
    """P1's `1. 1.` is renumbered `1. 2.`, P3's column 2 becomes column 1,
    the lazy line moves to the text column and the one-line nested form
    becomes two lines: the writer's own, unchanged choices. After that first
    normalisation both legs are a fixed point."""
    first_wiki = _tw(_md(wiki.rstrip("\n")))
    second_md = _md(first_wiki)
    assert _tw(second_md) == first_wiki
    assert _md(_tw(second_md)) == second_md


# -- Live: the real renderer on /trac_test -----------------------------------


class _ListShape(HTMLParser):
    """The nesting of ul/ol/li in a render, with each list's `start`."""

    def __init__(self) -> None:
        super().__init__()
        self.root: list = ["root", []]
        self._open: list[list] = [self.root]

    def handle_starttag(self, tag, attrs):
        if tag in ("ul", "ol", "li"):
            label = tag
            if tag == "ol":
                label += f" start={dict(attrs).get('start', '1')}"
            node: list = [label, []]
            self._open[-1][1].append(node)
            self._open.append(node)

    def handle_endtag(self, tag):
        if tag in ("ul", "ol", "li") and len(self._open) > 1:
            self._open.pop()


def _list_shape(html: str) -> list:
    parser = _ListShape()
    parser.feed(html)
    return parser.root


def test_list_shape_helper_tells_lists_and_starts_apart():
    one = _list_shape("<ul><li>a</li><li>c</li></ul>")
    two = _list_shape("<ul><li>a</li></ul><ul><li>c</li></ul>")
    s3 = _list_shape('<ol start="3"><li>a</li></ol>')
    s1 = _list_shape("<ol><li>a</li></ol>")
    assert one != two
    assert s3 != s1


@pytest.mark.live
@pytest.mark.parametrize(
    "name,source",
    [("P1", P1), ("P2", P2), ("P5", P5), ("P6", P6)],
)
def test_live_source_and_round_trip_render_the_same_list_structure(
    name, source
):
    """Ticket #177 acceptance, against Trac's own renderer on /trac_test: the
    TracWiki source and `markdown_to_tracwiki(tracwiki_to_markdown(source))`
    render the same `<ul>`/`<ol>` structure, `start` attributes included.
    Read-only (wiki.wikiToHtml writes nothing)."""
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.instances import InstanceRegistry

    config, _ = bootstrap_config()
    client = TracClient(
        InstanceRegistry(config, {}).resolve("/trac_test")
    )

    round_tripped = _tw(_md(source))

    before = client.wiki_to_html(source)
    after = client.wiki_to_html(round_tripped)

    assert _list_shape(before) == _list_shape(after), (before, after)
