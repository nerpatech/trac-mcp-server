"""An ordered list keeps its start number on the write leg (ticket #150).

`markdown_to_tracwiki` numbered every ordered list from 1, ignoring the
`start` attribute mistune puts on the list token (it sets one only when the
first number is not 1). So ` 5. a` / ` 6. b`, which Trac renders as
`<ol start="5">` and the read leg keeps as ` 5.`, came back as ` 1.` / ` 2.`.

**Measured on /trac_test on 2026-11-07** (`convert_preview`,
`format=tracwiki`): Trac takes the FIRST item's number as `start` and counts
on from it, whatever the later items say. ` 5.` / ` 7.` / ` 9.` renders
`<ol start="5">`, and ` 3.` / ` 1.` renders `<ol start="3">`, with three and
two plain `<li>`. ` 0.` gives `start="0"`, ` 007.` gives `start="7"`, and a
nested list's own start is honoured (`<ol start="4">` inside an item). So the
write leg emits consecutive numbers from `start`, which is exactly what Trac
shows for the source.
"""

import re

import mistune
import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    tracwiki_to_markdown,
)


def _tw(markdown: str) -> str:
    return markdown_to_tracwiki(markdown)


def _round_trip(wiki: str) -> str:
    return _tw(tracwiki_to_markdown(wiki).text)


WRITE_ROWS = {
    # The ticket's repro.
    "start_5": ("5. a\n6. b\n", " 5. a\n 6. b"),
    # Trac counts on from the first number; later numbers are not kept.
    "non_consecutive": ("5. a\n7. b\n9. c\n", " 5. a\n 6. b\n 7. c"),
    "descending": ("3. x\n1. y\n", " 3. x\n 4. y"),
    "zero": ("0. z\n1. o\n", " 0. z\n 1. o"),
    "zero_padded": ("007. a\n008. b\n", " 7. a\n 8. b"),
    # Unchanged: a list from 1, and a bullet list.
    "start_1": ("1. a\n2. b\n", " 1. a\n 2. b"),
    "bullets": ("- x\n- y\n", " * x\n * y"),
    # A nested list keeps its own start, and the outer list's counter is
    # restored after it.
    "nested_start": (
        "- outer\n\n  4. inner a\n  5. inner b\n\n- outer2\n",
        " * outer\n   4. inner a\n   5. inner b\n * outer2",
    ),
    # Each list starts from its own number; a later list from 1 stays at 1.
    "sibling_lists": (
        "5. a\n\npara\n\n1. b\n2. c\n",
        " 5. a\n\npara\n\n 1. b\n 2. c",
    ),
}


@pytest.mark.parametrize("name", sorted(WRITE_ROWS))
def test_write_leg_numbers_from_the_list_start(name):
    markdown, expected = WRITE_ROWS[name]
    assert _tw(markdown) == expected


ROUND_TRIP_ROWS = {
    # The ticket's own example, and #25's steps 5 to 8.
    "start_5": " 5. a\n 6. b\n",
    "steps_5_to_8": " 5. one\n 6. two\n 7. three\n 8. four\n",
    "two_digit_start": " 10. j\n 11. k\n",
    "zero": " 0. z\n 1. o\n",
}


@pytest.mark.parametrize("name", sorted(ROUND_TRIP_ROWS))
def test_round_trip_keeps_the_start_number(name):
    """`tw -> md -> tw` gives back the source's bytes (less the final
    newline, which the write leg never emits)."""
    source = ROUND_TRIP_ROWS[name]
    assert _round_trip(source) == source.rstrip("\n")


def test_round_trip_of_non_consecutive_numbers_is_what_trac_shows():
    """The source's later numbers are not kept, but Trac renders the same
    list either way (measured: it counts on from the first number)."""
    assert _round_trip(" 0. a\n 1. b\n 1. c\n 1. d\n") == (
        " 0. a\n 1. b\n 2. c\n 3. d"
    )


def test_nested_list_from_4_under_a_bullet_keeps_its_tracwiki_bytes():
    """The stored TracWiki stays byte-stable, and since #180 the Markdown
    view keeps the nested list too: the read leg puts a blank line before
    `4.` (CommonMark lets only a list starting at 1 interrupt a paragraph),
    so mistune reads an ordered list starting at 4 inside the first item."""
    source = " * outer\n   4. inner a\n   5. inner b\n * outer2\n"
    assert _round_trip(source) == source.rstrip("\n")

    tokens = mistune.create_markdown(renderer=None)(
        tracwiki_to_markdown(source).text
    )
    first_item = next(
        item
        for item in tokens[0]["children"]
        if item["type"] == "list_item"
    )
    nested = [
        child
        for block in first_item["children"]
        for child in [block, *block.get("children", [])]
        if child["type"] == "list"
    ]
    assert len(nested) == 1
    assert nested[0]["attrs"]["ordered"] is True
    assert nested[0]["attrs"]["start"] == 4
    assert len(nested[0]["children"]) == 2


def _list_shape(html: str) -> list[str]:
    """Every `<ol>`/`<ul>` open and close tag and every `<li>`, in document
    order, whitespace collapsed. Captures nesting and the `start` attribute
    together, enough to tell a renumbered, split or flattened list apart."""
    return [
        " ".join(m.group(0).split())
        for m in re.finditer(
            r"<(?:ol|ul)\b[^>]*>|</(?:ol|ul)>|<li\b[^>]*>", html
        )
    ]


def test_list_shape_tells_renumbering_and_nesting_apart():
    """The live row's comparison would be vacuous if it could not."""
    assert _list_shape(
        '<ol start="5"><li>a</li><li>b</li></ol>'
    ) != _list_shape("<ol><li>a</li><li>b</li></ol>")
    assert _list_shape(
        '<ul><li>a<ol start="4"><li>x</li></ol></li></ul>'
    ) != _list_shape('<ul><li>a</li></ul><ol start="4"><li>x</li></ol>')


LIVE_SOURCES = {
    "start_5": " 5. a\n 6. b\n",
    "non_consecutive": " 5. a\n 7. b\n 9. c\n",
    "zero": " 0. z\n 1. o\n",
    "nested_under_bullet": " * outer\n   4. inner a\n   5. inner b\n * outer2\n",
}


@pytest.mark.live
@pytest.mark.parametrize("name", sorted(LIVE_SOURCES))
def test_live_source_and_round_trip_render_the_same_lists(name):
    """Ticket #150 acceptance, against Trac's own renderer on /trac_test: the
    TracWiki source and its `tw -> md -> tw` round trip render the same
    list tags (nesting and start attribute included) and items.
    Read-only (wiki.wikiToHtml writes nothing)."""
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.instances import InstanceRegistry

    config, _ = bootstrap_config()
    client = TracClient(
        InstanceRegistry(config, {}).resolve("/trac_test")
    )

    source = LIVE_SOURCES[name]
    before = client.wiki_to_html(source)
    after = client.wiki_to_html(_round_trip(source))

    assert _list_shape(before), before
    assert _list_shape(before) == _list_shape(after), (before, after)
