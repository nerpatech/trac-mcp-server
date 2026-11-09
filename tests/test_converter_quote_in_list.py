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
"""

import re

import pytest

from trac_mcp_server.converters import find_list_item_splits
from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

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


# -- Review fixes -----------------------------------------------------------


def test_quote_in_a_nested_item_in_a_quote_follows_the_first_child_rule():
    """F2: citation mode applies to an item's OWN quote runs only. The
    nested item's first-child quote keeps master's form (` *   x`), as for
    `- > q`; ` * > x` would render the literal text `> x` (measured)."""
    assert markdown_to_tracwiki("- a\n  > - > x\n- b\n") == (
        " * a\n   >  *   x\n * b"
    )


def test_quote_in_a_nested_item_in_a_quote_after_text_is_a_citation():
    md = "- a\n  > - b\n  >   > q\n- c\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   >  * b\n   >    > q\n * c"
    )


@pytest.mark.parametrize(
    ("md", "wiki"),
    [
        # F3: text that would join the quote or open a nested item keeps
        # master's blank line before it (the quote itself is a citation)
        (
            "- a\n  > q\n\n  \\> not\n- b\n",
            " * a\n   > q\n\n   > not\n * b",
        ),
        (
            "- a\n  > q\n\n  1\\. not\n- b\n",
            " * a\n   > q\n\n   1. not\n * b",
        ),
        (
            "- a\n  > q\n\n  a\\. not\n- b\n",
            " * a\n   > q\n\n   a. not\n * b",
        ),
        # F5, master 8702a5c bytes: a run that already holds a blank line
        # (the list has ended there; unmeasured), here raw HTML
        (
            "- a\n\n  <div>x</div>\n\n  > q\n- b\n",
            " * a\n\n   <div>x</div>\n\n     q\n * b",
        ),
    ],
)
def test_review_cases_keep_the_blank_line(md, wiki):
    assert markdown_to_tracwiki(md) == wiki


def test_empty_quote_is_unchanged():
    """No content to cite; master's bytes (trailing spaces included)."""
    assert markdown_to_tracwiki("- a\n  >\n- b\n") == " * a  \n * b"


def test_nested_quote_list_quote_renders_each_quote_once(monkeypatch):
    """F9: a quote run used to render twice (the pre-#145 form was kept to
    pick from), which doubles per level of quote -> list -> quote nesting."""
    from trac_mcp_server.converters.markdown_to_tracwiki import (
        TracWikiRenderer,
    )

    calls = []
    original = TracWikiRenderer.block_quote

    def counting(self, text):
        calls.append(text)
        return original(self, text)

    monkeypatch.setattr(TracWikiRenderer, "block_quote", counting)
    levels = 8
    lines = ["- a0"]
    for i in range(1, levels):
        lines.append("  " + ">   " * (i - 1) + f"> - a{i}")
    markdown_to_tracwiki("\n".join(lines) + "\n")
    assert len(calls) == levels - 1


# -- Text after a citation -------------------------------------------------


def test_text_after_a_citation_is_written_at_the_item_text_column():
    md = " - a\n   > q\n\n   d\n - e"
    assert markdown_to_tracwiki(md) == " * a\n   > q\n   d\n * e"


# -- A table in the quote --------------------------------------------------


def test_table_in_the_quote_is_written_as_cells():
    """The write leg turns the quoted GFM table into `||` rows."""
    first = markdown_to_tracwiki(
        "- a\n  > | x |\n  > |---|\n  > | 1 |\n- d\n"
    )
    assert first == " * a\n   > ||=x=||\n   > ||1||\n * d"


# -- Live: the real renderer on /trac_test ----------------------------------


def _first_top_level_li(html):
    """The first ``<li>`` ... matching ``</li>`` of `html` (nested lists
    counted by depth), and what follows it; ``(None, html)`` if none. A
    non-greedy ``<li>.*?</li>`` would stop at an inner item's close."""
    tags = re.compile(r"<li(?:[\s>])|</li>")
    start = None
    depth = 0
    for m in tags.finditer(html):
        if m.group(0) == "</li>":
            if start is None:
                continue
            depth -= 1
            if depth == 0:
                return html[start : m.end()], html[m.end() :]
        else:
            if start is None:
                start = m.start()
            depth += 1
    return None, html


def test_first_top_level_li_counts_nesting():
    """Unit test of the live test's helper, with hand-made HTML. The case
    that matters: `d` is inside the OUTER item, after an inner item."""
    inside = "<ul><li>a<ul><li>sub</li></ul>d</li><li>e</li></ul>"
    li, rest = _first_top_level_li(inside)
    assert li == "<li>a<ul><li>sub</li></ul>d</li>"
    assert rest == "<li>e</li></ul>"
    assert re.search(r"\bd\b", li)
    sibling = "<ul><li>a<ul><li>sub</li></ul></li><li>d</li></ul>"
    li, rest = _first_top_level_li(sibling)
    assert li == "<li>a<ul><li>sub</li></ul></li>"
    assert not re.search(r"\bd\b", li)
    assert re.search(r"\bd\b", rest)
    assert _first_top_level_li("<p>x</p>") == (None, "<p>x</p>")


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
    from tests.live_helpers import trac_test_client

    client = trac_test_client()

    html = client.wiki_to_html(markdown_to_tracwiki(LIVE[name]))

    # One top-level list; a quote item also opens sub-lists (the
    # nested-list and in-quote list cases), a list split by a blank line
    # would add a further top-level one.
    sub_lists = 1 if name in ("list", "quote then nested list") else 0
    assert len(re.findall(r"<(?:ul|ol)[\s>]", html)) == 1 + sub_lists, (
        html
    )
    first_li, rest = _first_top_level_li(html)
    assert first_li is not None, html
    assert "<blockquote" in first_li, html
    # The last item `d` is a sibling after the first item, not inside it.
    assert re.search(r"\bd\b", rest), html
    assert not re.search(r"\bd\b", first_li), html
    for word in ("q", "r", "p1", "p2", "x", "l1", "tail", "para"):
        if re.search(rf"\b{word}\b", LIVE[name]):
            assert re.search(rf"\b{word}\b", first_li), html
