"""A blockquote child of a list item (ticket #145).

**The defect.** ``markdown_to_tracwiki`` kept a blockquote child of a list
item in the item's text run, so in a tight item the quote's first line was
glued onto the item text (``- a`` / ``  > q`` became `` * a  q``), and in a
loose item the blank line before it ended the Trac list (a blank line ends
any Trac list). A table, code block or list inside the quote came out
equally broken.

**Measured on Trac's own renderer (/trac_test) before writing the fix.**
Trac's citation syntax, ``> `` on the lines directly under the item text, at
the item's continuation indent, with no blank line before it, renders
``<blockquote class="citation">`` INSIDE the ``<li>``, and the next item
stays a sibling. Measured to hold for: several lines; a lone ``>`` line as
the paragraph break (one blockquote, two ``<p>``); a table
(``   > ||=x=||``); a ``{{{ }}}`` block; a list (``   >  * l1``: ``> `` plus
the list's own `` * l1``); a nested quote (``   > > x``); a nested item; an
ordered item; item text after the quote on the next line without ``>`` (it
renders after the blockquote, still inside the ``<li>``); a code block after
the quote and a quote after a code block; a nested list right after the
quote. Measured to FAIL: an item with empty text (`` *`` alone is not a list
item) and `` * > q`` (a literal ``>``).

**Write leg.** A blockquote child that follows other item content is its own
run kind and renders as citation lines at the item's continuation indent
(``_item_block_separator`` decides the joins). A quote nested in it is
``> > x``, not ``>   x`` (that renders, but reads back as a plain
paragraph). Two sibling quotes join with a lone ``>`` line: Trac shows one
blockquote with two paragraphs (a documented loss). Unchanged, with the
bytes ``master`` (8702a5c) produced: a quote that is the item's first child
(no Trac form exists), a quote after a nested list (Trac attaches an
adjacent block to the innermost nested item, #112/#125), a quote after a
run with a passed-through TracWiki line (#143 review F1), and a top-level
blockquote.

**Read leg.** Plain item text straight after a citation line is a lazy
continuation of the quote to CommonMark, so it gets a blank line before it
(not before the next item, a fence, or at the end): the Markdown then means
what Trac renders, and the write leg puts it back with no blank line.
"""

import re

import pytest

from trac_mcp_server.converters import find_list_item_splits
from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    tracwiki_to_markdown,
)


def _md(wiki):
    return tracwiki_to_markdown(wiki).text


# -- Write leg: the three reported cases ------------------------------------


def test_tight_item_quote_is_not_glued_onto_the_item_text():
    """Master: ' * a  q'."""
    assert markdown_to_tracwiki("- a\n  > q\n- d\n") == (
        " * a\n   > q\n * d"
    )


def test_loose_item_quote_stays_in_the_list():
    """Master: ' * a', blank, '     q' -- the blank line ends the list."""
    assert markdown_to_tracwiki("- a\n\n  > q\n- d\n") == (
        " * a\n   > q\n * d"
    )


def test_table_in_the_quote():
    """Master: ' * a  ||=x=||'. Header-cell bytes are the top-level
    table's: '| x |' converts to '||=x=||'."""
    md = "- a\n  > | x |\n  > |---|\n  > | 1 |\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   > ||=x=||\n   > ||1||\n * d"
    )


# -- Write leg: what the quote holds ----------------------------------------


def test_multi_line_quote():
    assert markdown_to_tracwiki("- a\n  > q\n  > r\n- d\n") == (
        " * a\n   > q\n   > r\n * d"
    )


def test_two_paragraph_quote_uses_a_lone_gt_line():
    md = "- a\n  > p1\n  >\n  > p2\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   > p1\n   >\n   > p2\n * d"
    )


def test_code_in_the_quote():
    md = "- a\n  > ```\n  > code\n  > ```\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   > {{{\n   > code\n   > }}}\n * d"
    )


def test_list_in_the_quote():
    """'> ' plus the list's own ' * l1': the list restarts at depth 0
    inside the quote, however deep the item is."""
    assert markdown_to_tracwiki("- a\n  > - l1\n- d\n") == (
        " * a\n   >  * l1\n * d"
    )
    assert markdown_to_tracwiki("- a\n  - b\n    > - l1\n- d\n") == (
        " * a\n   * b\n     >  * l1\n * d"
    )


def test_nested_quote_uses_nested_markers():
    """'> > x', not '>   x' (that reads back as a plain paragraph)."""
    assert markdown_to_tracwiki("- a\n  > > x\n- d\n") == (
        " * a\n   > > x\n * d"
    )


def test_quote_with_text_then_nested_quote():
    md = "- a\n  > q\n  >\n  > > x\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   > q\n   >\n   > > x\n * d"
    )


# -- Write leg: depth and list kind -----------------------------------------


def test_quote_in_a_nested_item():
    md = "- a\n  - e\n    > nq\n- d\n"
    assert markdown_to_tracwiki(md) == (" * a\n   * e\n     > nq\n * d")


def test_quote_in_an_ordered_item():
    assert markdown_to_tracwiki("1. g\n   > oq\n2. d\n") == (
        " 1. g\n    > oq\n 2. d"
    )


def test_quote_in_a_nested_ordered_item():
    md = "1. a\n   1. b\n      > q\n2. c\n"
    assert markdown_to_tracwiki(md) == (
        " 1. a\n   1. b\n      > q\n 2. c"
    )


# -- Write leg: neighbours of the quote -------------------------------------


def test_quote_then_text_goes_on_the_next_line_without_a_gt():
    md = "- a\n  > q\n\n  tail\n- d\n"
    assert markdown_to_tracwiki(md) == (" * a\n   > q\n   tail\n * d")


def test_quote_then_two_paragraphs():
    md = "- a\n  > q\n\n  t1\n\n  t2\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   > q\n   t1 [[BR]][[BR]]\n   t2\n * d"
    )


def test_paragraph_then_quote_is_adjacent():
    """Was #143's deferral: kept its blank line, which ended the list."""
    md = "- a\n\n  para\n\n  > q\n- b\n"
    assert markdown_to_tracwiki(md) == (
        " * a [[BR]][[BR]]\n   para\n   > q\n * b"
    )


def test_quote_then_code():
    md = "- a\n  > q\n  ```\n  c\n  ```\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   > q\n   {{{\n   c\n   }}}\n * d"
    )


def test_code_then_quote():
    """Was #143's deferral: kept its blank line, which ended the list."""
    md = "- a\n  ```\n  c\n  ```\n\n  > q\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{\n   c\n   }}}\n   > q\n * d"
    )


def test_table_then_quote():
    md = "- a\n  | x |\n  |---|\n  | 1 |\n\n  > q\n- d\n"
    out = markdown_to_tracwiki(md)
    assert out == (
        " * a\n   {{{#!div\n   ||=x=||\n   ||1||\n   }}}\n   > q\n * d"
    )


def test_quote_then_table():
    md = "- a\n  > q\n\n  | x |\n  |---|\n  | 1 |\n- d\n"
    out = markdown_to_tracwiki(md)
    assert out == (
        " * a\n   > q\n   {{{#!div\n   ||=x=||\n   ||1||\n   }}}\n * d"
    )


def test_quote_then_nested_list_has_no_blank_line():
    md = "- a\n  > q\n\n  - sub\n- d\n"
    assert markdown_to_tracwiki(md) == (" * a\n   > q\n   * sub\n * d")


def test_quote_then_tight_nested_list():
    md = "- a\n  > q\n  - sub\n- d\n"
    assert markdown_to_tracwiki(md) == (" * a\n   > q\n   * sub\n * d")


def test_two_sibling_quotes_join_with_a_lone_gt_line():
    """Trac shows one blockquote with two paragraphs (documented loss)."""
    md = "- a\n  > q\n\n  > r\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   > q\n   >\n   > r\n * d"
    )


def test_quote_in_a_nested_item_then_the_next_nested_item():
    md = "- a\n  - b\n    > q\n  - c\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   * b\n     > q\n   * c\n * d"
    )


# -- Write leg: unchanged output (bytes from master 8702a5c) ----------------


def test_quote_after_a_nested_list_is_unchanged():
    """A Trac limit (#112/#125): an adjacent block attaches to the
    innermost nested item, so the blank line and the indent stay."""
    md = "- a\n  - b\n\n  > q\n- d\n"
    assert markdown_to_tracwiki(md) == " * a\n   * b\n\n     q\n * d"
    assert find_list_item_splits(md) == [
        {"item": "a", "block": "blockquote", "depth": 0}
    ]


def test_second_quote_after_a_nested_list_is_unchanged():
    md = "- a\n  - b\n\n  > q\n\n  > r\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   * b\n\n     q\n\n     r\n * d"
    )


def test_quote_after_a_passthrough_line_is_unchanged():
    """#143 review F1: a passed-through line (here a heading) keeps the
    blank line before the next block."""
    md = "- a\n\n  = H =\n\n  > q\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n\n   = H =\n\n     q\n * d"
    )


def test_text_and_quotes_after_a_plain_quote_are_unchanged():
    """The quote kept the pre-#145 form, so what follows it keeps master's
    bytes too: a citation or adjacent text under the indented quote would
    be read as part of it."""
    md = "- a\n\n  = H =\n\n  > q\n\n  more\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n\n   = H =\n\n     q\n\n   more\n * d"
    )
    md = "- a\n\n  = H =\n\n  > q\n\n  > r\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n\n   = H =\n\n     q\n\n     r\n * d"
    )


def test_text_after_a_quote_after_a_nested_list_is_unchanged():
    md = "- a\n  - b\n\n  > q\n\n  more\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   * b\n\n     q\n\n   more\n * d"
    )


def test_item_starting_with_a_quote_is_unchanged():
    """No Trac form exists (' * > q' renders a literal '>'; ' *' alone is
    not an item): the quote stays in the text run."""
    assert markdown_to_tracwiki("- > q\n- d\n") == " *   q\n * d"


def test_top_level_blockquote_is_unchanged():
    assert markdown_to_tracwiki("> a\n> b\n") == "  a\n  b"
    assert markdown_to_tracwiki("> a\n\n> > b\n") == "  a\n\n    b"


def test_quote_after_a_list_in_the_document_is_unchanged():
    """A quote that follows the list is not a child of an item."""
    assert (
        markdown_to_tracwiki("- a\n- b\n\n> q\n") == " * a\n * b\n\n  q"
    )


def test_tight_item_is_unchanged():
    assert markdown_to_tracwiki("- a\n- b\n") == " * a\n * b"


# -- Read leg ----------------------------------------------------------------


def test_citation_then_item_text_gets_a_blank_line():
    """Without it CommonMark takes `tail` for a lazy continuation of the
    quote, though Trac renders it after the blockquote."""
    assert _md(" * a\n   > q\n   tail\n * d") == (
        " - a\n   > q\n\n   tail\n - d"
    )


def test_ordered_citation_then_item_text_gets_a_blank_line():
    assert _md(" 1. a\n    > q\n    tail\n 2. d") == (
        " 1. a\n    > q\n\n    tail\n 2. d"
    )


def test_citation_then_item_text_in_the_last_item():
    assert _md(" * a\n   > q\n   tail") == " - a\n   > q\n\n   tail"


def test_citation_followed_by_the_next_item_gets_no_blank_line():
    assert _md(" * a\n   > q\n * d") == " - a\n   > q\n - d"


def test_citation_at_the_end_gets_no_blank_line():
    assert _md(" * a\n   > q") == " - a\n   > q"


def test_citation_followed_by_a_fence_gets_no_blank_line():
    wiki = " * a\n   > q\n   {{{\n   c\n   }}}\n * d"
    assert _md(wiki) == " - a\n   > q\n   ```\n   c\n   ```\n - d"


def test_multi_line_citation_then_text_gets_one_blank_line():
    assert _md(" * a\n   > p1\n   >\n   > p2\n   tail\n * d") == (
        " - a\n   > p1\n   >\n   > p2\n\n   tail\n - d"
    )


def test_citation_then_a_nested_item_gets_no_blank_line():
    assert _md(" * a\n   > q\n   * sub\n * d") == (
        " - a\n   > q\n   - sub\n - d"
    )


def test_item_text_then_citation_gets_no_blank_line():
    """The quote interrupts the paragraph in CommonMark by itself."""
    assert _md(" * a\n   b\n   > q\n * d") == (
        " - a\n   b\n   > q\n - d"
    )


# -- TracWiki -> Markdown -> TracWiki byte stability -------------------------

STABLE = {
    "plain": " * a\n   > q\n * d",
    "multi-line": " * a\n   > q\n   > r\n * d",
    "multi-paragraph": " * a\n   > p1\n   >\n   > p2\n * d",
    "table": " * a\n   > ||=x=||\n   > ||1||\n * d",
    "code": " * a\n   > {{{\n   > code\n   > }}}\n * d",
    "list": " * a\n   >  * l1\n * d",
    "nested quote": " * a\n   > > x\n * d",
    "nested item": " * a\n   * e\n     > nq\n * d",
    "ordered": " 1. g\n    > oq\n 2. d",
    "quote then tail": " * a\n   > q\n   tail\n * d",
    "quote then tail, last item": " * a\n   > q\n   tail",
    "quote then code": " * a\n   > q\n   {{{\n   c\n   }}}\n * d",
    "code then quote": " * a\n   {{{\n   c\n   }}}\n   > q\n * d",
    "quote then nested list": " * a\n   > q\n   * sub\n * d",
    "text then quote": " * a\n   b\n   > q\n * d",
    "quote, then two paragraphs": (
        " * a\n   > q\n   t1 [[BR]][[BR]]\n   t2\n * d"
    ),
}


@pytest.mark.parametrize("name", list(STABLE))
def test_tracwiki_markdown_tracwiki_is_byte_stable(name):
    wiki = STABLE[name]
    assert markdown_to_tracwiki(_md(wiki)) == wiki


# -- Markdown -> TracWiki -> Markdown: a table in the quote ------------------


def test_table_in_the_quote_survives_the_round_trip_as_cells():
    """The write leg turns the quoted GFM table into `||` rows. The read
    leg does not convert a blockquoted `||` row back into a GFM table (a
    `> ||` line is not a table row to `_convert_tables`), so the second
    Markdown differs from the first -- but it is stable from there, and
    the TracWiki it writes is the same. KNOWN LIMITATION, pinned as it is;
    not part of #145."""
    first = markdown_to_tracwiki(
        "- a\n  > | x |\n  > |---|\n  > | 1 |\n- d\n"
    )
    assert first == " * a\n   > ||=x=||\n   > ||1||\n * d"
    second = _md(first)
    assert second == " - a\n   > ||=x=||\n   > ||1||\n - d"
    assert markdown_to_tracwiki(second) == first


# -- Live: the real renderer on /trac_test ----------------------------------

LIVE = {
    "tight": "- a\n  > q\n- d\n",
    "loose": "- a\n\n  > q\n- d\n",
    "table": "- a\n  > | x |\n  > |---|\n  > | 1 |\n- d\n",
    "multi-paragraph": "- a\n  > p1\n  >\n  > p2\n- d\n",
    "code": "- a\n  > ```\n  > code\n  > ```\n- d\n",
    "list": "- a\n  > - l1\n- d\n",
    "nested quote": "- a\n  > > x\n- d\n",
    "ordered": "1. a\n   > q\n2. d\n",
    "quote then tail": "- a\n  > q\n\n  tail\n- d\n",
    "quote then code": "- a\n  > q\n  ```\n  c\n  ```\n- d\n",
    "code then quote": "- a\n  ```\n  c\n  ```\n\n  > q\n- d\n",
    "paragraph then quote": "- a\n\n  para\n\n  > q\n- d\n",
    "quote then nested list": "- a\n  > q\n\n  - sub\n- d\n",
    "two sibling quotes": "- a\n  > q\n\n  > r\n- d\n",
}


@pytest.mark.live
@pytest.mark.parametrize("name", list(LIVE))
def test_live_quote_renders_inside_the_list_item(name):
    """Ticket #145 acceptance, against Trac's own renderer on /trac_test:
    the blockquote sits inside the first ``<li>`` and the last item is a
    sibling. Read-only (wiki.wikiToHtml writes nothing)."""
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.instances import InstanceRegistry

    config, _ = bootstrap_config()
    client = TracClient(
        InstanceRegistry(config, {}).resolve("/trac_test")
    )

    html = client.wiki_to_html(markdown_to_tracwiki(LIVE[name]))

    # One top-level list; a quote item also opens sub-lists (the
    # nested-list and in-quote list cases), a list split by a blank line
    # would add a further top-level one.
    sub_lists = 1 if name in ("list", "quote then nested list") else 0
    assert len(re.findall(r"<(?:ul|ol)[\s>]", html)) == 1 + sub_lists, (
        html
    )
    first_li = re.search(r"<li[\s>].*?</li>", html, re.DOTALL)
    assert first_li is not None, html
    assert "<blockquote" in first_li.group(0), html
    # Everything before the last item's text belongs to the first item;
    # the last item `d` is a sibling after it.
    assert re.search(r"\bd\b", html[html.index("</li>") :]), html
    assert not re.search(r"\bd\b", first_li.group(0)), html
    for word in ("q", "r", "p1", "p2", "x", "l1", "tail", "para"):
        if re.search(rf"\b{word}\b", LIVE[name]):
            assert re.search(rf"\b{word}\b", first_li.group(0)), html
