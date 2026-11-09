"""A ``{{{ }}}`` code block inside a list item (ticket #144).

**Measured on Trac's own renderer (/trac_test) before writing the fix:**

* F1: an indented ``{{{ }}}`` block directly under a list item's line
  renders inside the ``<li>``. That holds for bullet or numbered items,
  space or TAB indent, ``#!lang``, and nested items.
* F2: Trac strips the opener's indentation from the body only if EVERY
  non-blank body line starts with it. Blank body lines do not block the
  strip. If any line is shallower nothing is stripped (``   prefixed`` /
  ``  less`` renders verbatim, both indents kept).

**Write leg.** A ``block_code`` child of a list item is its own run, like a
table (#142): appended under the item text at the continuation indent with
NO blank line before it. This also fixes the code block in a loose item
(the code-block half of #143; a paragraph child stays open there).
"""

import re

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    markdown_to_tracwiki,
)

# name -> (TracWiki, Markdown). The Markdown converts to the TracWiki.
FENCED_ITEMS = {
    "plain": (
        " * a\n   {{{\n   code\n   }}}\n * d",
        " - a\n   ```\n   code\n   ```\n - d",
    ),
    "lang": (
        " * a\n   {{{#!python\n   x = 1\n   }}}\n * d",
        " - a\n   ```python\n   x = 1\n   ```\n - d",
    ),
    "bare div": (
        " * a\n   {{{#!div\n   hello\n   }}}\n * d",
        " - a\n   ```div\n   hello\n   ```\n - d",
    ),
    "nested item": (
        " * a\n   * b\n     {{{\n     code\n     }}}\n * d",
        " - a\n   - b\n     ```\n     code\n     ```\n - d",
    ),
    "numbered item": (
        " 1. a\n    {{{\n    code\n    }}}\n 2. d",
        " 1. a\n    ```\n    code\n    ```\n 2. d",
    ),
    "blank line in the body": (
        " * a\n   {{{\n   one\n\n   two\n   }}}\n * d",
        " - a\n   ```\n   one\n\n   two\n   ```\n - d",
    ),
}


@pytest.mark.parametrize(
    ("wiki", "markdown"),
    list(FENCED_ITEMS.values()),
    ids=list(FENCED_ITEMS),
)
def test_a_fenced_block_in_an_item_converts_to_an_indented_block(
    wiki, markdown
):
    assert markdown_to_tracwiki(markdown) == wiki


def test_next_item_is_not_pulled_into_the_code_block():
    """The ticket's symptom: ' - d' ended up inside a {{{ }}} block."""
    back = markdown_to_tracwiki(FENCED_ITEMS["plain"][1])
    assert back.endswith("}}}\n * d")
    assert back.count("{{{") == 1


# -- Round 2 (F4-F6): any indent, the innermost OPEN item ---------------------
#
# F4: the opener's indentation does not matter -- a block renders inside the
# innermost open list item at any indent (column 0 included). F5: nested, a
# block shallower than the inner marker still joins the inner item. F6: the
# list must still be open: a continuation line keeps it open, flush-left
# prose or a blank line ends it.
#
# The fence goes at the item's TEXT column (marker indent + marker + 1), so
# CommonMark puts it in the item; stored bytes are normalised, the render is
# not (Trac strips per F2). name -> (TracWiki, Markdown, written back).
ANY_INDENT = {
    "indent 2, shallower than the text column": (
        " * a\n  {{{\n  code\n  }}}\n * d",
        " - a\n   ```\n   code\n   ```\n - d",
        " * a\n   {{{\n   code\n   }}}\n * d",
    ),
    "indent 3 under a numbered item": (
        " 1. a\n   {{{\n   code\n   }}}\n 2. d",
        " 1. a\n    ```\n    code\n    ```\n 2. d",
        " 1. a\n    {{{\n    code\n    }}}\n 2. d",
    ),
    "marker column": (
        " * a\n {{{\n code\n }}}\n * d",
        " - a\n   ```\n   code\n   ```\n - d",
        " * a\n   {{{\n   code\n   }}}\n * d",
    ),
    "marker column, numbered, #!yaml": (
        " 1. x\n {{{#!yaml\n k: v\n }}}\n 2. y",
        " 1. x\n    ```yaml\n    k: v\n    ```\n 2. y",
        " 1. x\n    {{{#!yaml\n    k: v\n    }}}\n 2. y",
    ),
    "column zero": (
        " * a\n{{{\ncode\n}}}\n * d",
        " - a\n   ```\n   code\n   ```\n - d",
        " * a\n   {{{\n   code\n   }}}\n * d",
    ),
    "nested dedent joins the innermost item": (
        " * a\n   * b\n {{{\n code\n }}}\n * d",
        " - a\n   - b\n     ```\n     code\n     ```\n - d",
        " * a\n   * b\n     {{{\n     code\n     }}}\n * d",
    ),
    "column zero after a continuation line": (
        " * q\n   continued\n{{{\ncode\n}}}\n * d",
        " - q\n   continued\n   ```\n   code\n   ```\n - d",
        " * q\n   continued\n   {{{\n   code\n   }}}\n * d",
    ),
    "deeper than the text column": (
        " * a\n     {{{\n     code\n     }}}\n * d",
        " - a\n   ```\n   code\n   ```\n - d",
        " * a\n   {{{\n   code\n   }}}\n * d",
    ),
    "second block at column zero in the same item": (
        " * a\n   {{{\n   x\n   }}}\n{{{\ny\n}}}\n * d",
        " - a\n   ```\n   x\n   ```\n   ```\n   y\n   ```\n - d",
        " * a\n   {{{\n   x\n   }}}\n   {{{\n   y\n   }}}\n * d",
    ),
}


@pytest.mark.parametrize(
    ("wiki", "markdown", "written"),
    list(ANY_INDENT.values()),
    ids=list(ANY_INDENT),
)
def test_any_indent_joins_the_innermost_open_item(
    wiki, markdown, written
):
    """The fence goes at the item's text column, so CommonMark puts it in
    the item; it is written at the text column whatever indent the block
    had in Trac (a shallower or deeper indent, column zero, a nested
    dedent)."""
    assert markdown_to_tracwiki(markdown) == written


# -- F2: a shallower body line ------------------------------------------------

SHALLOW_MD = " - a\n   ```\n      prefixed\n     less\n   ```\n - d"


def test_shallower_line_is_written_verbatim():
    back = markdown_to_tracwiki(SHALLOW_MD)
    assert back == (
        " * a\n   {{{\n      prefixed\n     less\n   }}}\n * d"
    )
    # Trac strips the opener's three columns only when every non-blank
    # line has them (F2); here all do, and what is left is the verbatim body.
    body = back.split("\n")[2:4]
    assert [ln[3:] for ln in body] == ["   prefixed", "  less"]


# -- Write leg ----------------------------------------------------------------

TIGHT_MD = "- a\n  ```\n  code\n  ```\n- d\n"
LOOSE_MD = "- a\n\n  ```\n  code\n  ```\n- d\n"
WRITTEN = " * a\n   {{{\n   code\n   }}}\n * d"


def test_tight_item():
    """Master glued the opener onto the item text: ' * a{{{\\n   code...'."""
    assert markdown_to_tracwiki(TIGHT_MD) == WRITTEN


def test_loose_item():
    """The code-block half of #143. A blank line before the block would end
    the Trac list, so none is emitted (master put one there)."""
    assert markdown_to_tracwiki(LOOSE_MD) == WRITTEN


def test_loose_item_paragraph_after_the_block_stays_in_the_item():
    """Ticket #143 closed what #144 left open: a paragraph after the block
    goes on the next line with no blank line, so it stays in the item."""
    md = "- a\n\n  ```\n  code\n  ```\n\n  more\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{\n   code\n   }}}\n   more\n * d"
    )


def test_two_code_blocks_in_one_item_are_two_blocks():
    md = "- a\n  ```\n  x\n  ```\n\n  ```\n  y\n  ```\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{\n   x\n   }}}\n   {{{\n   y\n   }}}\n * d"
    )


def test_code_after_a_nested_list_is_still_one_block():
    md = "- a\n  - b\n\n  ```\n  code\n  ```\n- d\n"
    out = markdown_to_tracwiki(md)
    assert out.count("{{{") == 1
    assert out.endswith("   {{{\n   code\n   }}}\n * d")


def test_item_whose_first_child_is_a_code_block_keeps_its_content():
    """Out of scope (no leading text). Pinned so it neither crashes nor loses
    the block: the empty item text leaves ' * ' and the block follows.
    (Master: ' * {{{\\n   code\\n   }}}\\n * d' -- the opener on the marker
    line, which Trac does not read as a block.)"""
    md = "- ```\n  code\n  ```\n- d\n"
    assert (
        markdown_to_tracwiki(md) == " * \n   {{{\n   code\n   }}}\n * d"
    )


def test_language_info_survives_the_write_leg():
    md = "- a\n  ```python\n  x = 1\n  ```\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!python\n   x = 1\n   }}}\n * d"
    )


def test_top_level_block_writes_back_as_before():
    assert markdown_to_tracwiki("```\ncode\n```\n") == "{{{\ncode\n}}}"
    assert markdown_to_tracwiki("```python\nx\n```\n") == (
        "{{{#!python\nx\n}}}"
    )


# -- Live: the real renderer on /trac_test --------------------------------------


@pytest.mark.live
def test_live_code_block_renders_inside_the_list_item():
    """Ticket #144 acceptance, against Trac's own renderer on /trac_test:
    the write-leg output renders ONE <ul> whose first <li> holds the <pre>,
    with the body exact, and the list continues. Read-only
    (wiki.wikiToHtml writes nothing)."""
    from tests.live_helpers import TEST_INSTANCE, trac_test_registry
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient

    config, _ = bootstrap_config()
    client = TracClient(
        trac_test_registry(config).resolve(TEST_INSTANCE)
    )

    html = client.wiki_to_html(markdown_to_tracwiki(TIGHT_MD))

    assert len(re.findall(r"<ul[\s>]", html)) == 1, html
    first_li = re.search(r"<li[\s>].*?</li>", html, re.DOTALL)
    assert first_li is not None, html
    assert "<pre" in first_li.group(0), html
    assert "code" in first_li.group(0), html
    assert html.count("<li") == 2, html
    assert html.count("<pre") == 1, html


def test_code_after_a_nested_list_is_written_at_the_outer_indent():
    """Pinned. Trac attaches a block that follows a nested list to the
    innermost open item (`sub`; F5, measured), a Trac limit parallel to
    #112's continuation paragraph: no TracWiki form attaches a block to the
    outer item after a nested list. The writer keeps the author's order and
    the outer indent."""
    md = "- a\n  - sub\n  ```\n  x\n  ```\n- b\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   * sub\n   {{{\n   x\n   }}}\n * b"
    )
