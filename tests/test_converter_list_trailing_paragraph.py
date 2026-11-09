"""List item children come out in source order (ticket #90, #112).

A paragraph that immediately follows a TracWiki list, with no blank line
and no indentation, is meant to terminate the list -- Trac renders it as a
separate ``<p>``, not part of the last item. The rows below pin the write
leg's side: a list item's children are written in the order the Markdown
has them.
"""

import unittest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)


class TestItemChildrenKeepSourceOrder(unittest.TestCase):
    """Ticket #112: a list item's children must come out in source order.

    ``list_item`` used to split an item's children into "everything that
    is not a list" and "the lists", and emit all of the first group, then
    all of the second -- so a paragraph that FOLLOWS a nested list in the
    Markdown was written ABOVE it. Every byte survived, so no check could
    see it. Every row except the unchanged control fails on the pre-fix
    code. These pin the converter's output string only. How Trac renders
    that shape was measured live on /trac_test and is recorded on the
    ticket: the paragraph follows the sub-list, but Trac closes the outer
    list at the blank line, so later siblings open a new list (an ordered
    one continues its numbering via ``<ol start>``). Since #176 the writer
    no longer puts a blank line there: a paragraph directly after a nested
    list goes one column past the item's marker, which Trac keeps in the
    outer item (``_item_block_separator``'s ``"between"``); the rows below
    pin that form, and the order is still the source's.
    """

    def test_ticket_input_paragraph_stays_after_sub_list(self):
        source = (
            "- **Cause:** unknown. Suspects, in order:\n"
            "  - first suspect;\n"
            "  - second suspect.\n"
            "\n"
            "  A paragraph that belongs after the list.\n"
            "- **Guard:** next item.\n"
        )
        self.assertEqual(
            markdown_to_tracwiki(source),
            " * '''Cause:''' unknown. Suspects, in order:\n"
            "   * first suspect;\n"
            "   * second suspect.\n"
            "  A paragraph that belongs after the list.\n"
            " * '''Guard:''' next item.",
        )

    def test_two_trailing_paragraphs_keep_their_order(self):
        source = "- Cause:\n  - a\n  - b\n\n  Para one.\n\n  Para two.\n- Next\n"
        self.assertEqual(
            markdown_to_tracwiki(source),
            " * Cause:\n"
            "   * a\n"
            "   * b\n"
            "  Para one. [[BR]][[BR]]\n"
            "   Para two.\n"
            " * Next",
        )

    def test_paragraph_after_a_two_level_nested_list(self):
        source = "- Top\n  - mid\n    - deep\n\n  Trailing after nested.\n- Next\n"
        self.assertEqual(
            markdown_to_tracwiki(source),
            " * Top\n"
            "   * mid\n"
            "     * deep\n"
            "  Trailing after nested.\n"
            " * Next",
        )

    def test_paragraph_between_two_sub_lists(self):
        source = "- Top\n  - a\n\n  Between.\n\n  - b\n- Next\n"
        self.assertEqual(
            markdown_to_tracwiki(source),
            " * Top\n   * a\n  Between.\n   * b\n * Next",
        )

    def test_ordered_item_indents_under_its_own_marker(self):
        source = "1. Top\n   - a\n\n   After.\n2. Next\n"
        self.assertEqual(
            markdown_to_tracwiki(source),
            " 1. Top\n   * a\n  After.\n 2. Next",
        )

    def test_sub_list_without_trailing_paragraph_is_unchanged(self):
        # Control: nothing follows the sub-list, so nothing can move.
        source = "- Top\n  - a\n  - b\n- Next\n"
        self.assertEqual(
            markdown_to_tracwiki(source),
            " * Top\n   * a\n   * b\n * Next",
        )


if __name__ == "__main__":
    unittest.main()
