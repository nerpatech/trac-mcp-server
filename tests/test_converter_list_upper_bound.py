"""A line indented past a list item's bound ends the list (ticket #175).

Trac 1.6's `Formatter._indent_formatter` continues the innermost list item
only while an indented, non-marker line's width `d` is at most `m + 2` for a
bullet item or `m + 3` for a digits-only ordered one (`m` is the marker's
column). Past that the list ENDS and the line is a blockquote.

**Measured on /trac_test on 2026-11-07** (`convert_preview`,
`format=tracwiki`):

* K1 `" * a" / "   * b" / " {{{" / " x" / " }}}" / "     more" / " * c"`:
  `<ul><li>a<ul><li>b<pre>x</pre>more</li></ul></li><li>c</li></ul>`; the
  block and `more` are both inside item b. A block joins the innermost item
  at any indent (#144), so the item stays open across it.

The bound is Trac's rule about the raw TracWiki line. A block opener past
the bound is still a block (#144), and table rows are not quote text. The
write leg stores the rows below as literals; each is `master`'s (c76efac)
output.
"""

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

K1_MARKDOWN = (
    " - a\n   - b\n     ```\n     x\n     ```\n     more\n - c"
)
K1_WRITTEN = " * a\n   * b\n     {{{\n     x\n     }}}\n     more\n * c"

R1 = " 10. a\n     {{{\n     x\n     }}}\n 11. b"
R2 = (
    " 10. a\n     {{{\n     x\n     }}}\n     {{{\n     y\n     }}}\n"
    " 11. b"
)
R3 = " * a\n      {{{\n  code\n      }}}\n   more"
R5 = " 10. a\n     {{{#!div\n     ||=x=||=y=||\n     ||1||2||\n     }}}\n 11. b"

# row -> (the Markdown, what the write leg stores)
RAW_ONLY_ROWS = {
    "R1": (" 10. a\n     ```\n     x\n     ```\n 11. b", R1),
    "R2": (
        " 10. a\n     ```\n     x\n     ```\n     ```\n     y\n     ```\n"
        " 11. b",
        R2,
    ),
    "R3": (
        " - a\n   ```\n     code\n   ```\n   more",
        " * a\n   {{{\n     code\n   }}}\n   more",
    ),
    "R5": (
        " 10. a\n     | x | y |\n     |---|---|\n     | 1 | 2 |\n 11. b",
        R5,
    ),
}


@pytest.mark.parametrize("name", list(RAW_ONLY_ROWS))
def test_raw_only_rows_write_leg_is_masters(name):
    markdown, expected = RAW_ONLY_ROWS[name]
    assert markdown_to_tracwiki(markdown) == expected


def test_k1_block_and_text_after_it_are_written_in_the_open_item():
    assert markdown_to_tracwiki(K1_MARKDOWN) == K1_WRITTEN
