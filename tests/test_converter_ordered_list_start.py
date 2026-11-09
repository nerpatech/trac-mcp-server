"""An ordered list keeps its start number on the write leg (ticket #150).

`markdown_to_tracwiki` numbered every ordered list from 1, ignoring the
`start` attribute mistune puts on the list token (it sets one only when the
first number is not 1). So ` 5. a` / ` 6. b`, which Trac renders as
`<ol start="5">`, came back as ` 1.` / ` 2.`.

**Measured on /trac_test on 2026-11-07** (`convert_preview`,
`format=tracwiki`): Trac takes the FIRST item's number as `start` and counts
on from it, whatever the later items say. ` 5.` / ` 7.` / ` 9.` renders
`<ol start="5">`, and ` 3.` / ` 1.` renders `<ol start="3">`, with three and
two plain `<li>`. ` 0.` gives `start="0"`, ` 007.` gives `start="7"`, and a
nested list's own start is honoured (`<ol start="4">` inside an item). So the
write leg emits consecutive numbers from `start`, which is exactly what Trac
shows for the source.
"""

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)


def _tw(markdown: str) -> str:
    return markdown_to_tracwiki(markdown)


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
