"""Trac renders a four-space-indented line as a blockquote (ticket #73).

Indentation in TracWiki is a blockquote.  Measured against Trac's own
renderer -- ``convert_preview`` with ``format="tracwiki"`` on ``/trac_test`` --
indentation is **relative, not absolute**: one nested blockquote per increase
in width, a pop on a dedent, a reset on column-zero content.  The renders are
captured from the live renderer into ``tests/fixtures/blockquote_indent/`` per
``Rules/testing/RealSubstrateNotMocks``, and the two tests below pin what they
show: the four-space source is a blockquote, and the code block that an old
converter stored for it is a different content type.
"""

import json
import unittest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "blockquote_indent"
MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text())


def _row(name):
    for row in MANIFEST["rows"]:
        if row["name"] == name:
            return row
    raise AssertionError(f"no manifest row named {name!r}")


def _fixture_html(name):
    row = _row(name)
    return row["tracwiki"], (FIXTURES_DIR / row["html"]).read_text()


class TestRenderedContentType(unittest.TestCase):
    """Ticket #73 section 5: assert the four-space row on the rendered HTML.

    The bytes understate this row.  What the converter used to store was not a
    quote with the wrong depth -- it was a *code block*, which renders
    monospaced and stops interpreting markup.  Both renders below come from the
    live Trac renderer, so this compares two real content types rather than two
    strings someone believed in.
    """

    SOURCE = "    Quoted prose with *markup*."

    def test_source_renders_as_a_blockquote_not_a_code_block(self):
        """The pinned meaning of the source, from the real renderer."""
        tracwiki, html = _fixture_html("four_space_indent")
        self.assertEqual(tracwiki, self.SOURCE)
        self.assertIn("<blockquote>", html)
        self.assertNotIn("<pre", html)

    def test_the_old_output_rendered_as_a_different_content_type(self):
        """What the defect stored, from the same renderer -- the contrast."""
        _, html = _fixture_html("code_block_corruption")
        self.assertIn("<pre", html)
        self.assertNotIn("<blockquote>", html)


if __name__ == "__main__":
    unittest.main()
