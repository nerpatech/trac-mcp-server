"""List-item continuation recall gate for ticket #75.

The write leg emitted a list item's continuation line (a soft break inside
the item, or the second-plus paragraph of a loose item) at *column zero*.
Trac's list grammar only continues an item when the following line is
indented deeper than the marker; flush against the margin, the continuation
breaks out of the ``<li>`` into a sibling ``<p>`` -- a change of document
structure, not just whitespace, and silent.
"""

import unittest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)


class TestRecallGate(unittest.TestCase):
    """Constructs pinned by earlier tickets that this fix must not disturb."""

    def test_absorbed_paragraph_still_separates_from_the_next_real_paragraph(
        self,
    ):
        """Ticket #52's territory, the interaction ticket #75 section 4
        flags explicitly: a paragraph with no blank line before it in the
        Markdown source is a lazy continuation of the *last* list item
        (confirmed on mistune's own AST), so this fix correctly indents it
        to keep it inside that item -- and `list()`'s own trailing blank
        line must still separate it from the real sibling paragraph that
        follows, rather than being swallowed by this fix."""
        markdown = (
            "- alpha\n"
            "- beta\n"
            "**Bold lead:** first paragraph.\n"
            "\n"
            "Second paragraph.\n"
        )
        result = markdown_to_tracwiki(markdown)
        self.assertIn(
            "'''Bold lead:''' first paragraph.\n\nSecond paragraph.",
            result,
        )
        self.assertIn(
            " * beta\n   '''Bold lead:''' first paragraph.\n\n", result
        )
