"""A paragraph child of a loose list item (ticket #143).

**The defect.** Trac ends a list at any blank line, so a blank line kept
before a paragraph child of a list item sends the paragraph outside the
``<li>`` (rendered as a blockquote) and the next item starts a new list. The
write leg kept that blank line in two places: between two paragraphs of the
item's first text run, and before a later text run (the text after a code or
table child, or after a nested list).

**Measured on Trac's own renderer (/trac_test) before writing the fix**
(``convert_preview``, format tracwiki; every row keeps the paragraph inside
its ``<li>`` and the next item in the same list):

* C: ``a [[BR]][[BR]]`` at the end of the previous line, then the paragraph
  on a continuation line: inside, with a visual paragraph gap. Also nested,
  in an ordered list, and in a last item followed by text outside the list.
* D: text on the line straight after a code block's ``}}}``, no blank:
  inside, after the ``<pre>``. The same after a table's ``{{{#!div}}}`` (G),
  and as code, then ``p1 [[BR]][[BR]]``, then ``p2`` (H).
* A plain continuation line is inside but joins the item text (the break is
  lost); a ``{{{#!div}}}`` around the paragraph is inside but reads as a code
  fence in Markdown. Both were rejected.
* Escape hazard: ``Done![[BR]][[BR]]`` renders ``Done[[BR]]<br />`` (the
  ``!`` escapes the first macro); ``Done! [[BR]][[BR]]`` renders correctly,
  so the separator always carries its leading space (as ``linebreak()``
  does for the same reason, #29).

**Write leg.** Paragraph after a paragraph in an item: the earlier
paragraph's last line ends ``" [[BR]][[BR]]"`` and the next paragraph sits
on a continuation line at the item's continuation indent. A text run after
a code or table child gets no blank line before it. Unchanged: a paragraph
after a nested list (a Trac limit, #112; the #125 advisory), and any other
non-paragraph block. (A blockquote child was unchanged
here and moved to a citation inside the item on #145.)
Review fixes: a paragraph with a passed-through TracWiki line (`{{{`,
`}}}`, `||`, `=`, `----`) is never joined, and no run after a nested list
loses its blank line.

The unchanged rows were computed on ``master`` (28d5ccb) and hard-coded.
"""

import re

import pytest

from trac_mcp_server.converters import find_list_item_splits
from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

# -- Write leg: paragraph after a paragraph ---------------------------------


def test_loose_item_two_paragraphs():
    """Master: ' * a', blank, '   para', ' * d' -- ends the list."""
    md = "- a\n\n  para\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a [[BR]][[BR]]\n   para\n * d"
    )


def test_three_paragraphs():
    md = "- a\n\n  one\n\n  two\n- b\n"
    assert markdown_to_tracwiki(md) == (
        " * a [[BR]][[BR]]\n   one [[BR]][[BR]]\n   two\n * b"
    )


def test_ordered_item():
    md = "1. a\n\n   para\n2. d\n"
    assert markdown_to_tracwiki(md) == (
        " 1. a [[BR]][[BR]]\n    para\n 2. d"
    )


def test_nested_item():
    md = "- a\n  - b\n\n    para\n- c\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   * b [[BR]][[BR]]\n     para\n * c"
    )


def test_last_item_followed_by_text_outside_the_list():
    md = "- a\n\n  para\n\nafter\n"
    assert markdown_to_tracwiki(md) == (
        " * a [[BR]][[BR]]\n   para\n\nafter"
    )


def test_paragraph_ending_in_bang_keeps_the_space_before_the_macro():
    """`Done![[BR]]` would be Trac's `!` escape of the first macro."""
    md = "- Done!\n\n  para\n- d\n"
    out = markdown_to_tracwiki(md)
    assert out == " * Done! [[BR]][[BR]]\n   para\n * d"
    assert "![[BR]]" not in out


def test_paragraph_ending_in_colon_valued_token_keeps_the_space():
    """`wiki:Foo[[BR]]` would be read as one `wikiname:target` link (#29)."""
    md = "- see wiki:Foo\n\n  para\n- d\n"
    out = markdown_to_tracwiki(md)
    assert out == " * see wiki:Foo [[BR]][[BR]]\n   para\n * d"
    assert "Foo[[BR]]" not in out


def test_multiline_paragraph_then_paragraph():
    md = "- a\n  b\n\n  c\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   b [[BR]][[BR]]\n   c\n * d"
    )


# -- Write leg: text after a code or table child ----------------------------


def test_loose_item_text_after_a_code_child():
    md = "- a\n\n  ```\n  code\n  ```\n\n  tail\n- b\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{\n   code\n   }}}\n   tail\n * b"
    )


def test_tight_item_text_after_a_code_child():
    md = "- a\n  ```\n  code\n  ```\n  tail\n- b\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{\n   code\n   }}}\n   tail\n * b"
    )


def test_paragraph_after_a_table_child():
    md = "- a\n  | x | y |\n  |---|---|\n  | 1 | 2 |\n\n  para\n- b\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!div\n   ||=x=||=y=||\n   ||1||2||\n   }}}\n"
        "   para\n * b"
    )


def test_code_then_two_paragraphs():
    md = "- a\n\n  ```\n  c\n  ```\n\n  p1\n\n  p2\n- b\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{\n   c\n   }}}\n   p1 [[BR]][[BR]]\n   p2\n * b"
    )


def test_ordered_text_after_a_code_child():
    md = "1. a\n   ```\n   code\n   ```\n   tail\n2. b\n"
    assert markdown_to_tracwiki(md) == (
        " 1. a\n    {{{\n    code\n    }}}\n    tail\n 2. b"
    )


def test_nested_text_after_a_code_child():
    md = "- a\n  - b\n    ```\n    code\n    ```\n    tail\n- c\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   * b\n     {{{\n     code\n     }}}\n     tail\n * c"
    )


# -- Write leg: passthrough TracWiki lines keep the blank line (F1) --------
#
# A paragraph can carry TracWiki passed through verbatim (`{{{#!html`, a
# heading, a `||` row, a rule). Appending " [[BR]][[BR]]" to such a paragraph
# corrupts it: `}}} [[BR]][[BR]]` never closes the block (measured on Trac:
# it swallows the rest of the page). The pre-#143 output is kept whenever
# either paragraph has such a line. Expected strings are `master` (28d5ccb).

PASSTHROUGH = {
    "html": "{{{#!html\n  <b>x</b>\n  }}}",
    "heading": "= H =",
    "row": "||x||y||",
    "rule": "----",
}
PASSTHROUGH_WIKI = {
    "html": "{{{#!html\n   <b>x</b>\n   }}}",
    "heading": "= H =",
    "row": "||x||y||",
    "rule": "----",
}


@pytest.mark.parametrize("name", list(PASSTHROUGH))
def test_passthrough_block_before_a_paragraph_keeps_the_blank_line(
    name,
):
    md = f"- a\n\n  {PASSTHROUGH[name]}\n\n  para\n- d\n"
    assert markdown_to_tracwiki(md) == (
        f" * a\n\n   {PASSTHROUGH_WIKI[name]}\n\n   para\n * d"
    )


@pytest.mark.parametrize("name", list(PASSTHROUGH))
def test_passthrough_block_after_a_paragraph_keeps_the_blank_line(name):
    md = f"- para\n\n  {PASSTHROUGH[name]}\n- d\n"
    assert markdown_to_tracwiki(md) == (
        f" * para\n\n   {PASSTHROUGH_WIKI[name]}\n * d"
    )


@pytest.mark.parametrize("name", list(PASSTHROUGH))
def test_passthrough_block_after_two_paragraphs_joins_only_the_paragraphs(
    name,
):
    md = f"- a\n\n  para\n\n  {PASSTHROUGH[name]}\n- d\n"
    assert markdown_to_tracwiki(md) == (
        f" * a [[BR]][[BR]]\n   para\n\n   {PASSTHROUGH_WIKI[name]}\n * d"
    )


def test_passthrough_line_inside_a_multiline_paragraph_blocks_the_join():
    """Any line of either paragraph counts, not only the first."""
    md = (
        "- a\n\n  para\n  {{{#!html\n  <b>x</b>\n  }}}\n\n  more\n- d\n"
    )
    out = markdown_to_tracwiki(md)
    assert "}}} [[BR]]" not in out
    assert out.count("[[BR]]") == 0


def test_text_after_a_code_child_that_starts_with_passthrough_keeps_blank():
    md = "- a\n  ```\n  c\n  ```\n\n  = H =\n\n  para\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{\n   c\n   }}}\n\n   = H =\n\n   para\n * d"
    )


def test_table_row_after_a_code_child_keeps_the_blank_line():
    md = "- a\n  ```\n  c\n  ```\n\n  ||x||\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{\n   c\n   }}}\n\n   ||x||\n * d"
    )


# -- Write leg: no adjacency once a nested list has appeared (F2) -----------


def test_text_after_code_after_a_nested_list_keeps_the_blank_line():
    """Trac attaches `tail` to the NESTED item when it follows the block
    with no blank line (measured). Once a
    nested list has appeared in the item, every later run keeps `master`'s
    output."""
    md = "- a\n  - b\n\n  ```\n  c\n  ```\n\n  tail\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   * b\n   {{{\n   c\n   }}}\n\n   tail\n * d"
    )


def test_text_after_a_table_after_a_nested_list_keeps_the_blank_line():
    md = (
        "- a\n  - b\n\n  | x | y |\n  |---|---|\n  | 1 | 2 |\n\n"
        "  tail\n- d\n"
    )
    out = markdown_to_tracwiki(md)
    assert out.endswith("   }}}\n\n   tail\n * d"), out


# -- Unchanged output -------------------------------------------------------


def test_paragraph_after_a_nested_list_goes_between_the_markers():
    """#176: no blank line, one column past the item's marker (Trac keeps
    it in the outer item), and the advisory (#125) stays quiet. The refused
    runs keep the #112 blank line (tests/test_converter_between_column_
    continuation.py)."""
    md = "- a\n  - b\n\n  para\n- c\n"
    assert markdown_to_tracwiki(md) == (" * a\n   * b\n  para\n * c")
    assert find_list_item_splits(md) == []


def test_two_paragraphs_after_a_nested_list_separate_by_br():
    """The first goes between the markers (the rest at the continuation
    indent), joined by the break the #143 form uses between paragraphs."""
    md = "- a\n  - b\n\n  p1\n\n  p2\n- c\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   * b\n  p1 [[BR]][[BR]]\n   p2\n * c"
    )


# The three tests below pinned the pre-#145 blockquote output as #143's
# deferral. #145 moves them to the citation form (see
# tests/test_converter_quote_in_list.py, which covers the rest).


def test_blockquote_child_is_a_citation_inside_the_item():
    md = "- a\n\n  > q\n- b\n"
    assert markdown_to_tracwiki(md) == " * a\n   > q\n * b"


def test_paragraph_then_blockquote_is_adjacent():
    md = "- a\n\n  para\n\n  > q\n- b\n"
    assert markdown_to_tracwiki(md) == (
        " * a [[BR]][[BR]]\n   para\n   > q\n * b"
    )


def test_code_then_blockquote_is_adjacent():
    md = "- a\n  ```\n  code\n  ```\n\n  > q\n- b\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{\n   code\n   }}}\n   > q\n * b"
    )


def test_tight_item_is_unchanged():
    assert markdown_to_tracwiki("- a\n- b\n") == " * a\n * b"
    assert markdown_to_tracwiki("- a\n  b\n- c\n") == " * a\n   b\n * c"


# -- Live: the real renderer on /trac_test ----------------------------------

LIVE = {
    "two paragraphs": "- a\n\n  para\n- d\n",
    "ordered": "1. a\n\n   para\n2. d\n",
    "nested": "- a\n  - b\n\n    para\n- c\n",
    "loose code then tail": "- a\n\n  ```\n  code\n  ```\n\n  tail\n- b\n",
    "tight code then tail": "- a\n  ```\n  code\n  ```\n  tail\n- b\n",
    "table then paragraph": (
        "- a\n  | x | y |\n  |---|---|\n  | 1 | 2 |\n\n  para\n- b\n"
    ),
    "code then two paragraphs": (
        "- a\n\n  ```\n  c\n  ```\n\n  p1\n\n  p2\n- b\n"
    ),
    "ends in a bang": "- Done!\n\n  para\n- d\n",
    "last item code then tail then outside": (
        "- a\n\n  ```\n  code\n  ```\n\n  tail\n\nafter\n"
    ),
}

# Cases whose list ends with the item under test: one item, and the
# trailing paragraph "after" sits outside the list.
LIVE_LAST_ITEM = {"last item code then tail then outside"}


@pytest.mark.live
@pytest.mark.parametrize("name", list(LIVE))
def test_live_paragraphs_render_inside_the_list_item(name):
    """Ticket #143 acceptance, against Trac's own renderer on /trac_test:
    one top-level list, no blockquote, every paragraph's text inside the
    first ``<li>`` and the second item a sibling. Read-only
    (wiki.wikiToHtml writes nothing)."""
    from tests.live_helpers import TEST_INSTANCE, trac_test_registry
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient

    config, _ = bootstrap_config()
    client = TracClient(
        trac_test_registry(config).resolve(TEST_INSTANCE)
    )

    html = client.wiki_to_html(markdown_to_tracwiki(LIVE[name]))

    # The nested case opens its sub-list inside the first item, so it has
    # two list openings; a list split by a blank line would add one more.
    expected_lists = 2 if name == "nested" else 1
    assert (
        len(re.findall(r"<(?:ul|ol)[\s>]", html)) == expected_lists
    ), html
    assert "<blockquote" not in html, html
    assert "[[BR]]" not in html, html
    first_li = re.search(r"<li[\s>].*?</li>", html, re.DOTALL)
    assert first_li is not None, html
    if name in LIVE_LAST_ITEM:
        assert len(re.findall(r"<li[\s>]", html)) == 1, html
        outside = html[html.rindex("</ul>") :]
        assert re.search(r"\bafter\b", outside), html
        assert not re.search(r"\bafter\b", first_li.group(0)), html
    else:
        assert len(re.findall(r"<li[\s>]", html)) >= 2, html
    for word in ("para", "tail", "p1", "p2"):
        if re.search(rf"\b{word}\b", LIVE[name]):
            assert re.search(rf"\b{word}\b", first_li.group(0)), html
