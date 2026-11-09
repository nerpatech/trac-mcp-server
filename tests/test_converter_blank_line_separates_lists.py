"""Two Trac lists separated only by a blank line stay two lists (ticket #177).

Trac ends a list at any blank line (#143), so ` * a` / blank / ` * c` is two
`<ul>`. Two adjacent Markdown lists are written with a blank line between
them and each keeps its start number (#150); a single loose Markdown list is
written as one list.

Measured on /trac_test on 2026-11-07 (ticket #177 comment 1, P1-P6): every
blank line between two list items ends the list, and the next marker opens a
new top-level list at any indent.
"""

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)


def _tw(md: str) -> str:
    return markdown_to_tracwiki(md)


def test_a_single_loose_markdown_list_still_writes_as_one_list():
    assert _tw("- a\n\n- c\n") == " * a\n * c"
    assert _tw("1. a\n\n1. c\n") == " 1. a\n 2. c"


def test_adjacent_markdown_lists_still_write_apart():
    assert _tw("- a\n\n* c\n") == " * a\n\n * c"
    assert _tw("3. a\n\n7) c\n") == " 3. a\n\n 7. c"
