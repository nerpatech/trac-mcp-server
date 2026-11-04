"""A ``{{{ }}}`` code block inside a list item (ticket #144).

**Measured on Trac's own renderer (/trac_test) before writing the fix:**

* F1: an indented ``{{{ }}}`` block directly under a list item's line
  renders inside the ``<li>``. That holds for bullet or numbered items,
  space or TAB indent, ``#!lang``, and nested items.
* F2: Trac strips the opener's indentation from the body only if EVERY
  non-blank body line starts with it. Blank body lines do not block the
  strip. If any line is shallower nothing is stripped (``   prefixed`` /
  ``  less`` renders verbatim, both indents kept).
* F3: on the read leg the inserted blank line came from
  ``_separate_list_from_trailing_paragraph``: it saw the ``\\x00CODEn\\x00``
  placeholder line at column 0 and decided the list had ended.

**Read leg.** ``_convert_code_blocks`` emitted the fence's body and closer
without the opener's indentation. A block under a list item now keeps the
opener's indentation on the fence, the body and the closer (the body shown
as Trac shows it, per F2), so the list passes read it as a continuation.

**Write leg.** A ``block_code`` child of a list item is its own run, like a
table (#142): appended under the item text at the continuation indent with
NO blank line before it. This also fixes the code block in a loose item
(the code-block half of #143; a paragraph child stays open there).

The negative rows were computed on ``master`` (2a18cf7) before the change
and hard-coded.
"""

import re

import mistune
import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    _MISTUNE_PLUGINS,
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    FALLBACK_FENCE_INFO,
    tracwiki_to_markdown,
)


def _md(wiki):
    return tracwiki_to_markdown(wiki).text


def _fence_raw(markdown):
    """The raw body mistune (CommonMark) reads from the first block_code
    under the first list item."""
    parse = mistune.create_markdown(
        renderer=None, plugins=_MISTUNE_PLUGINS
    )
    item = parse(markdown)[0]["children"][0]
    code = [c for c in item["children"] if c["type"] == "block_code"]
    assert len(code) == 1, item
    return code[0]["raw"]


# name -> (TracWiki, expected Markdown). Each round trips byte-for-byte.
ROUND_TRIPS = {
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
    list(ROUND_TRIPS.values()),
    ids=list(ROUND_TRIPS),
)
def test_read_leg_keeps_the_opener_indent_and_inserts_no_blank_line(
    wiki, markdown
):
    """Master: ' - a\\n   ```\\n\\n   code\\n```\\n - d' (blank line after the
    opener, closer at column 0)."""
    assert _md(wiki) == markdown


@pytest.mark.parametrize(
    ("wiki", "markdown"),
    list(ROUND_TRIPS.values()),
    ids=list(ROUND_TRIPS),
)
def test_round_trip_is_byte_for_byte(wiki, markdown):
    assert markdown_to_tracwiki(_md(wiki)) == wiki
    assert markdown_to_tracwiki(markdown) == wiki


def test_next_item_is_not_pulled_into_the_code_block():
    """The ticket's symptom: ' - d' ended up inside a {{{ }}} block."""
    back = markdown_to_tracwiki(_md(ROUND_TRIPS["plain"][0]))
    assert back.endswith("}}}\n * d")
    assert back.count("{{{") == 1


# -- F2: a shallower body line ------------------------------------------------

SHALLOW_WIKI = " * a\n   {{{\n   prefixed\n  less\n   }}}\n * d"
SHALLOW_MD = " - a\n   ```\n      prefixed\n     less\n   ```\n - d"


def test_shallower_line_means_nothing_is_stripped():
    """F2: Trac shows `   prefixed` / `  less` verbatim, both indents kept.
    The Markdown fence's content, after CommonMark strips the fence's own
    three columns, is that verbatim body."""
    md = _md(SHALLOW_WIKI)
    assert md == SHALLOW_MD
    assert _fence_raw(md) == "   prefixed\n  less\n"


def test_shallower_line_round_trip_preserves_what_trac_shows():
    back = markdown_to_tracwiki(_md(SHALLOW_WIKI))
    assert back == (
        " * a\n   {{{\n      prefixed\n     less\n   }}}\n * d"
    )
    # Trac strips the opener's three columns only when every non-blank
    # line has them (F2); here all do, and what is left is the verbatim body.
    body = back.split("\n")[2:4]
    assert [ln[3:] for ln in body] == ["   prefixed", "  less"]
    # Reading the written form back is stable.
    assert markdown_to_tracwiki(_md(back)) == back


def test_blank_line_does_not_block_the_strip():
    """F2: only NON-blank lines are tested against the indent."""
    wiki = " * a\n   {{{\n   one\n\n   two\n   }}}\n * d"
    assert _fence_raw(_md(wiki)) == "one\n\ntwo\n"


# -- TAB indent ---------------------------------------------------------------


def test_tab_indented_opener_comes_back_indented_with_spaces():
    """The same choice #142 made for a tab-indented div: the stored bytes
    change (the render does not). A tab is 8 columns, which Markdown reads
    as an indented code block; the item's content column is 3."""
    wiki = " * a\n\t{{{\n\tcode\n\t}}}\n * d"
    md = _md(wiki)
    assert md == " - a\n   ```\n   code\n   ```\n - d"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{\n   code\n   }}}\n * d"
    )


def test_tab_indented_numbered_item_uses_its_content_column():
    wiki = " 1. a\n\t{{{\n\tcode\n\t}}}\n 2. d"
    md = _md(wiki)
    assert md == " 1. a\n    ```\n    code\n    ```\n 2. d"
    assert markdown_to_tracwiki(md) == (
        " 1. a\n    {{{\n    code\n    }}}\n 2. d"
    )


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


def test_loose_item_paragraph_child_is_still_open_in_143():
    """Unchanged: a paragraph after the block keeps the existing
    blank-line continuation treatment (the half of #143 this ticket leaves)."""
    md = "- a\n\n  ```\n  code\n  ```\n\n  more\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{\n   code\n   }}}\n\n   more\n * d"
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


def test_fallback_fence_child_is_unaffected():
    """#63: a `tracwiki-unconverted` fence holds the ORIGINAL SOURCE and is
    unwrapped verbatim. It stays in the text run, so these outputs are
    master's, byte for byte (including the glue in the tight item, which is
    not this ticket's)."""
    body = f"```{FALLBACK_FENCE_INFO}\n  = H =\n  ```"
    assert (
        markdown_to_tracwiki(f"- a\n  {body}\n- d\n")
        == " * a= H =\n * d"
    )
    assert markdown_to_tracwiki(f"- a\n\n  {body}\n- d\n") == (
        " * a\n\n   = H =\n * d"
    )


def test_language_info_survives_the_write_leg():
    md = "- a\n  ```python\n  x = 1\n  ```\n- d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!python\n   x = 1\n   }}}\n * d"
    )


# -- Regression guard: shapes the new path must NOT take ------------------------


# Expected values were computed on master (2a18cf7) BEFORE the change and
# hard-coded; each must stay byte-for-byte.
@pytest.mark.parametrize(
    ("name", "wiki", "expected"),
    [
        (
            "indented block, no list item above",
            "  {{{\n  code\n  }}}\n",
            "  ```\n  code\n```\n",
        ),
        (
            "indented block under a paragraph",
            "text\n  {{{\n  code\n  }}}\n",
            "text\n  ```\n  code\n```\n",
        ),
        (
            "indented #!lang block, no list item above",
            "  {{{#!python\n  x = 1\n  }}}\n",
            "  ```python\n  x = 1\n```\n",
        ),
        ("top-level block", "{{{\ncode\n}}}\n", "```\ncode\n```\n"),
        (
            "block at column zero under a list item",
            " * a\n{{{\ncode\n}}}\n * d",
            " - a\n```\ncode\n```\n - d",
        ),
        (
            "indented block after a blank line (outside the list)",
            " * a\n\n   {{{\n   code\n   }}}\n * d",
            " - a\n\n   ```\n\n   code\n```\n - d",
        ),
        (
            "opener no deeper than the marker",
            " * a\n {{{\n code\n }}}\n * d",
            " - a\n\n ```\n code\n```\n - d",
        ),
        (
            "text before the opener on its line",
            " * a {{{\n   code\n   }}}\n * d",
            " - a ```\n\n   code\n```\n - d",
        ),
        (
            "closer on the body's last line (not a block)",
            " * a\n   {{{\n   code }}}\n * d",
            " - a\n   {{{\n   code }}}\n - d",
        ),
    ],
)
def test_other_shapes_read_back_exactly_as_on_master(
    name, wiki, expected
):
    assert _md(wiki) == expected, name


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
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.instances import InstanceRegistry

    config, _ = bootstrap_config()
    client = TracClient(
        InstanceRegistry(config, {}).resolve("/trac_test")
    )

    html = client.wiki_to_html(markdown_to_tracwiki(TIGHT_MD))

    assert len(re.findall(r"<ul[\s>]", html)) == 1, html
    first_li = re.search(r"<li[\s>].*?</li>", html, re.DOTALL)
    assert first_li is not None, html
    assert "<pre" in first_li.group(0), html
    assert "code" in first_li.group(0), html
    assert html.count("<li") == 2, html
    assert html.count("<pre") == 1, html
