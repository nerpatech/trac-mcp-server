"""A ``{{{#!comment}}}`` or ``{{{#!table}}}`` block in a list item stays in the
item, and the lines after it continue it (ticket #192).

**Measured on Trac's own renderer (/trac_test, 2026-11-08, ticket #192
comment 3, ``convert_preview`` with ``format=tracwiki``):**

* M1: ``{{{#!comment}}}`` in an item, indented, at column 0, or with a body
  line at column 1: the item stays open. The lines after the block continue
  it, and an indented marker nests under it.
* M2: ``{{{#!table}}}`` (with a nested ``{{{#!td}}}``) in an item, indented
  or at column 0: the table renders inside the ``<li>``, ``more`` continues
  the item, and the next marker is a sibling.
* M3: ``{{{#!td}}}`` / ``{{{#!th}}}`` in an item, indented or at column 0:
  Trac CLOSES THE LIST. The table follows it and the next marker opens a new
  list.
* M4: a run of ``{{{#!comment}}}`` then ``{{{#!td}}}`` in one item: the list
  ends at the td.

**The defect (read leg, ``master`` ``cc9f971``).** ``_fallback_processor_blocks``
stashed the block from its ``{{{``, so the opener's indentation stayed outside
the stash: the opening fence landed at the item's indent, the placeholder and
the closing fence at column 0. The list passes read the column-0 placeholder
as the end of the list and inserted a blank line after the opening fence
(#144's F3), which ends the item in CommonMark, and the column-0 closer then
OPENED a fence that ran to the end of the document: every later item became a
literal code block (comment 1). A column-0 fence ended the item as well, so
the continuation after it left the item.

**The fix.** Read leg: a block (or a coalesced run) of ``comment``/``table``
blocks whose opener is first on its line, with a list item open there, is
fenced at the item's text column, exactly as #144 fences a plain block
(``_fence_in_list_item``'s core). Everything else (a run holding a td/th,
no open item, a lettered item or a TAB-indented marker, #144's declines)
takes the opener's indentation INTO the stash, so the fence, placeholder and
closer all sit at column 0 and the Markdown ends the list there, as Trac does
(M3). Write leg: a fallback fence whose body is exactly such a run is its own
``code`` run in the item (no blank line before it, a paragraph after it is
adjacent), so the item is not ended by the blank line that set it off.
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

F = FALLBACK_FENCE_INFO


def _md(wiki):
    return tracwiki_to_markdown(wiki).text


def _rt(wiki):
    return markdown_to_tracwiki(_md(wiki))


def _parse(markdown):
    parse = mistune.create_markdown(
        renderer=None, plugins=_MISTUNE_PLUGINS
    )
    return parse(markdown)


# name -> (TracWiki, Markdown, round trip). The in-item forms: the round trip
# is byte-for-byte wherever the source block sat at the item's continuation
# indent. A column-0 block under `- item` comes back at the continuation
# indent, which renders the same (M1; the trade #144 made for a plain block).
IN_ITEM = {
    "ticket shape 1: column-0 comment, continuation and a nested item": (
        "- item\n{{{#!comment\nnote\n}}}\n  continuation text\n  - sub\n",
        f"- item\n  ```{F}\n  {{{{{{#!comment\n  note\n  }}}}}}\n  ```\n"
        "  continuation text\n  - sub\n",
        " * item\n   {{{#!comment\n   note\n   }}}\n   continuation text\n"
        "   * sub",
    ),
    "comment 1 shape 1: later item after an indented comment": (
        " * a\n   {{{#!comment\n   x\n   }}}\n * d\n",
        f" - a\n   ```{F}\n   {{{{{{#!comment\n   x\n   }}}}}}\n   ```\n"
        " - d\n",
        " * a\n   {{{#!comment\n   x\n   }}}\n * d",
    ),
    "comment 1 shape 2: a continuation line, then the next item": (
        " * a\n   {{{#!comment\n   x\n   }}}\n   c\n * d\n",
        f" - a\n   ```{F}\n   {{{{{{#!comment\n   x\n   }}}}}}\n   ```\n"
        "   c\n - d\n",
        " * a\n   {{{#!comment\n   x\n   }}}\n   c\n * d",
    ),
    "M2: indented table with a nested td": (
        " * a\n   {{{#!table\n   {{{#!td\n   x\n   }}}\n   }}}\n   more\n"
        " * b\n",
        f" - a\n   ```{F}\n   {{{{{{#!table\n   {{{{{{#!td\n   x\n"
        f"   }}}}}}\n   }}}}}}\n   ```\n   more\n - b\n",
        " * a\n   {{{#!table\n   {{{#!td\n   x\n   }}}\n   }}}\n   more\n"
        " * b",
    ),
    "M2: column-0 table with a nested td": (
        "- a\n{{{#!table\n{{{#!td\nx\n}}}\n}}}\n  more\n- b\n",
        f"- a\n  ```{F}\n  {{{{{{#!table\n  {{{{{{#!td\n  x\n"
        f"  }}}}}}\n  }}}}}}\n  ```\n  more\n- b\n",
        " * a\n   {{{#!table\n   {{{#!td\n   x\n   }}}\n   }}}\n   more\n"
        " * b",
    ),
    "M1: a comment run (two blocks) is one stash": (
        " * a\n   {{{#!comment\n   x\n   }}}\n   {{{#!comment\n   y\n"
        "   }}}\n   more\n * b\n",
        f" - a\n   ```{F}\n   {{{{{{#!comment\n   x\n   }}}}}}\n"
        f"   {{{{{{#!comment\n   y\n   }}}}}}\n   ```\n   more\n - b\n",
        " * a\n   {{{#!comment\n   x\n   }}}\n   {{{#!comment\n   y\n"
        "   }}}\n   more\n * b",
    ),
    "a column-0 comment under a numbered item": (
        " 1. a\n{{{#!comment\nx\n}}}\n    more\n 2. b\n",
        f" 1. a\n    ```{F}\n    {{{{{{#!comment\n    x\n    }}}}}}\n"
        "    ```\n    more\n 2. b\n",
        " 1. a\n    {{{#!comment\n    x\n    }}}\n    more\n 2. b",
    ),
    # F2's rule: the opener's indent is stripped only if EVERY non-blank line
    # after the opener starts with it. ` x` does not, so nothing is stripped
    # and the closer keeps its 3 columns on top of O. The comment is hidden
    # either way, and the written form is stable from the first pass on.
    "a comment with a body line at column 1": (
        " * a\n   {{{#!comment\n x\n   }}}\n * d\n",
        f" - a\n   ```{F}\n   {{{{{{#!comment\n    x\n      }}}}}}\n"
        "   ```\n - d\n",
        " * a\n   {{{#!comment\n    x\n      }}}\n * d",
    ),
    "an earlier plain block with a column-0 body line": (
        " * a\n   {{{\ncol0 body\n   }}}\n   {{{#!comment\n   x\n   }}}\n"
        " * d\n",
        f" - a\n   ```\n   col0 body\n   ```\n   ```{F}\n"
        f"   {{{{{{#!comment\n   x\n   }}}}}}\n   ```\n - d\n",
        " * a\n   {{{\n   col0 body\n   }}}\n   {{{#!comment\n   x\n   }}}\n"
        " * d",
    ),
    "a nested item whose comment block #179 moves": (
        " 1. outer\n   * inner a\n   {{{#!comment\n   x\n   }}}\n 2. d\n",
        f" 1. outer\n    - inner a\n      ```{F}\n"
        f"      {{{{{{#!comment\n      x\n      }}}}}}\n      ```\n 2. d\n",
        " 1. outer\n   * inner a\n     {{{#!comment\n     x\n     }}}\n"
        " 2. d",
    ),
    "a continuation line with no nested item, column-0 marker": (
        "- item\n{{{#!comment\nx\n}}}\n  continuation\n",
        f"- item\n  ```{F}\n  {{{{{{#!comment\n  x\n  }}}}}}\n  ```\n"
        "  continuation\n",
        " * item\n   {{{#!comment\n   x\n   }}}\n   continuation",
    ),
    "prose at column 0 after the block ends a column-0 list": (
        "- item\n{{{#!comment\nx\n}}}\nprose at col 0\n",
        f"- item\n  ```{F}\n  {{{{{{#!comment\n  x\n  }}}}}}\n  ```\n\n"
        "prose at col 0\n",
        " * item\n   {{{#!comment\n   x\n   }}}\n\nprose at col 0",
    ),
    "a numbered item 10 or more: O is past Trac's bound": (
        " 10. a\n    {{{#!comment\n    x\n    }}}\n    more\n 11. b\n",
        f" 10. a\n     ```{F}\n     {{{{{{#!comment\n     x\n     }}}}}}\n"
        "     ```\n     more\n 11. b\n",
        " 10. a\n     {{{#!comment\n     x\n     }}}\n    more\n 11. b",
    ),
}


@pytest.mark.parametrize(
    ("wiki", "markdown", "written"),
    list(IN_ITEM.values()),
    ids=list(IN_ITEM),
)
def test_read_leg_fences_the_block_at_the_items_text_column(
    wiki, markdown, written
):
    """Master (cc9f971): the placeholder and closer at column 0, a blank
    line after the opening fence, and the closer opening a runaway fence."""
    assert _md(wiki) == markdown


@pytest.mark.parametrize(
    ("wiki", "markdown", "written"),
    list(IN_ITEM.values()),
    ids=list(IN_ITEM),
)
def test_round_trip_keeps_the_block_and_its_continuation_in_the_item(
    wiki, markdown, written
):
    assert markdown_to_tracwiki(_md(wiki)) == written
    assert markdown_to_tracwiki(markdown) == written


@pytest.mark.parametrize(
    "wiki",
    [
        w
        for name, (w, _m, _r) in IN_ITEM.items()
        if "column-0" not in name
        and "column 1" not in name
        and "#179" not in name
        and "plain block" not in name
        and "past Trac's bound" not in name
    ],
)
def test_a_block_at_the_continuation_indent_round_trips_byte_for_byte(
    wiki,
):
    assert _rt(wiki) == wiki.rstrip("\n")


@pytest.mark.parametrize(
    "wiki", [w for (w, _m, _r) in IN_ITEM.values()]
)
def test_the_written_form_is_stable(wiki):
    once = _rt(wiki)
    assert markdown_to_tracwiki(_md(once)) == once


@pytest.mark.parametrize(
    "wiki", [w for (w, _m, _r) in IN_ITEM.values()]
)
def test_the_block_is_inside_the_first_item_in_markdown(wiki):
    """CommonMark (mistune, the write leg's parser) keeps the fence in the
    item that opened it, and the document is one list."""
    tokens = _parse(_md(wiki))
    lists = [t for t in tokens if t["type"] == "list"]
    assert len(lists) == 1, tokens
    first = lists[0]["children"][0]
    fences = [
        c
        for c in _walk(first["children"])
        if c["type"] == "block_code"
        and (c.get("attrs") or {}).get("info") == F
    ]
    assert len(fences) == 1, first
    assert fences[0]["raw"].lstrip().startswith("{{{#!"), fences[0]
    assert not any(t["type"] == "block_code" for t in tokens), tokens


def _walk(children):
    for child in children:
        yield child
        yield from _walk(child.get("children") or [])


def test_no_later_item_becomes_a_code_block():
    """Comment 1: item `d` and everything after it became literal text in a
    runaway `{{{ }}}` block."""
    back = _rt(
        IN_ITEM[
            "comment 1 shape 1: later item after an indented comment"
        ][0]
    )
    assert back.count("{{{") == 1 and back.count("}}}") == 1, back
    assert back.endswith("}}}\n * d")


# -- The td / th and no-item forms: the opener's indent goes INTO the stash ----


def test_td_in_an_item_ends_the_list_and_the_fence_sits_at_column_zero():
    """M3/M5: Trac closes the list at the td. The Markdown ends it there too,
    and the stash holds the source byte for byte, opener indent included.
    Master: the opening fence at column 3 and a blank line after it, the
    placeholder and closer at column 0 (a runaway fence)."""
    wiki = " * a\n   {{{#!td\n   x\n   }}}\n   more\n * b\n"
    md = _md(wiki)
    assert md == (
        f" - a\n```{F}\n   {{{{{{#!td\n   x\n   }}}}}}\n```\n   more\n"
        " - b\n"
    )
    tokens = _parse(md)
    assert [t["type"] for t in tokens if t["type"] != "blank_line"] == [
        "list",
        "block_code",
        "paragraph",
        "list",
    ], tokens
    back = markdown_to_tracwiki(md)
    assert back.count("{{{") == 1 and back.count("}}}") == 1, back
    assert "   {{{#!td\n   x\n   }}}" in back
    assert back.endswith("\n * b"), back


def test_a_run_holding_a_td_ends_the_list_at_the_td():
    """M4: comment then td in one item. The whole run is one column-0 stash."""
    wiki = (
        " * a\n   {{{#!comment\n   n\n   }}}\n   {{{#!td\n   x\n   }}}\n"
        "   more\n * b\n"
    )
    assert _md(wiki) == (
        f" - a\n```{F}\n   {{{{{{#!comment\n   n\n   }}}}}}\n"
        f"   {{{{{{#!td\n   x\n   }}}}}}\n```\n   more\n - b\n"
    )
    back = _rt(wiki)
    assert back.count("{{{") == 2 and back.count("}}}") == 2, back
    assert back.endswith("\n * b"), back


def test_th_at_column_zero_under_an_item_ends_the_list():
    wiki = "- a\n{{{#!th\nx\n}}}\n- b\n"
    assert (
        _md(wiki) == f"- a\n```{F}\n{{{{{{#!th\nx\n}}}}}}\n```\n- b\n"
    )


def test_an_indented_block_with_no_list_above_is_balanced():
    """The indented opener's fence was unbalanced here too (placeholder and
    closer at column 0): the indent now goes into the stash."""
    wiki = "text\n\n   {{{#!comment\n   x\n   }}}\n\nafter\n"
    md = _md(wiki)
    assert md == (
        f"text\n\n```{F}\n   {{{{{{#!comment\n   x\n   }}}}}}\n```\n\nafter\n"
    )
    assert _rt(wiki) == "text\n\n   {{{#!comment\n   x\n   }}}\n\nafter"


def test_a_top_level_block_at_column_zero_keeps_masters_bytes():
    wiki = "text\n\n{{{#!comment\nx\n}}}\n\nafter\n"
    assert _md(wiki) == (
        f"text\n\n```{F}\n{{{{{{#!comment\nx\n}}}}}}\n```\n\nafter\n"
    )


def test_a_lettered_item_declines_and_leaves_no_runaway_fence():
    """#74: a lettered item is itself a fence of unconverted text, and a
    fence under it came back as an inline span. Declined, so the block goes
    to the column-0 path, and no fence runs on to the end of the page."""
    wiki = " a. x\n   {{{#!comment\n   y\n   }}}\n b. z\n"
    md = _md(wiki)
    assert md == (
        f"```{F}\n a. x\n```\n```{F}\n   {{{{{{#!comment\n   y\n"
        f"   }}}}}}\n```\n```{F}\n b. z\n```\n"
    )
    back = markdown_to_tracwiki(md)
    assert back.count("{{{") == 1 and back.count("}}}") == 1, back
    assert " b. z" in back and "   {{{#!comment\n   y\n   }}}" in back


def test_a_tab_indented_marker_declines():
    """#144: CommonMark reads a TAB-indented marker line as indented code."""
    wiki = "\t* a\n\t  {{{#!comment\n\t  x\n\t  }}}\n\t* d\n"
    md = _md(wiki)
    # The fence is a balanced column-0 stash with the source byte for byte
    # (master: an opening fence at the marker line's end, a blank line and a
    # closer that opened a runaway fence). The tab list itself is code, as
    # it was.
    assert md == (
        f"\t* a\n```{F}\n\t  {{{{{{#!comment\n\t  x\n\t  }}}}}}\n```\n"
        "\t* d\n"
    )
    back = markdown_to_tracwiki(md)
    assert back.count("{{{") == back.count("}}}") == 3, back
    assert "\t  {{{#!comment\n\t  x\n\t  }}}" in back


def test_an_opener_not_first_on_its_line_keeps_masters_bytes():
    wiki = " * a {{{#!comment\n   x\n   }}}\n * d\n"
    assert _md(wiki) == (
        f" - a ```{F}\n\n{{{{{{#!comment\n   x\n   }}}}}}\n```\n - d\n"
    )


def test_warnings_name_the_processor_block():
    result = tracwiki_to_markdown(
        " * a\n   {{{#!comment\n   x\n   }}}\n * d\n"
    )
    assert any("#!comment" in w for w in result.warnings), (
        result.warnings
    )


# -- Interactions ---------------------------------------------------------------


def test_a_line_past_the_bound_after_the_block_still_ends_the_list():
    """#175 across a stashed block: ` * a` takes a line up to column 3; a
    line at column 4 is a blockquote to Trac and ends the list. It must not
    be read as the item's continuation."""
    wiki = " * a\n   {{{#!comment\n   x\n   }}}\n    quote\n * b\n"
    back = _rt(wiki)
    assert back.startswith(" * a\n   {{{#!comment\n   x\n   }}}\n")
    assert back.endswith("\n * b"), back
    assert "quote" in back
    # `quote` is not in item a: something separates it from the block.
    between = back.split("}}}\n", 1)[1].split("quote", 1)[0]
    assert "\n" in between or not between.startswith("   "), back


def test_a_nested_marker_after_the_block_nests_in_the_item():
    wiki = " * a\n   {{{#!comment\n   x\n   }}}\n   * sub\n * b\n"
    back = _rt(wiki)
    assert back == (
        " * a\n   {{{#!comment\n   x\n   }}}\n   * sub\n * b"
    )


def test_a_blank_line_after_the_block_ends_the_list():
    """A blank line ends a Trac list: what follows is not the item's."""
    wiki = " * a\n   {{{#!comment\n   x\n   }}}\n\n   more\n * b\n"
    back = _rt(wiki)
    assert back.count("{{{") == 1 and back.count("}}}") == 1, back
    assert back.endswith("\n * b"), back


def test_item_after_item_stays_one_list():
    wiki = (
        " * a\n   {{{#!comment\n   x\n   }}}\n * b\n   {{{#!comment\n"
        "   y\n   }}}\n * c\n"
    )
    assert _rt(wiki) == wiki.rstrip("\n")
    lists = [t for t in _parse(_md(wiki)) if t["type"] == "list"]
    assert len(lists) == 1
    assert len(lists[0]["children"]) == 3


def test_a_plain_block_in_an_item_keeps_masters_bytes():
    """The plain-block path is untouched (#144)."""
    wiki = " * a\n   {{{\n   code\n   }}}\n * d"
    assert _md(wiki) == " - a\n   ```\n   code\n   ```\n - d"


# -- Write leg -------------------------------------------------------------------


def test_write_leg_keeps_a_comment_fence_in_the_item_with_no_blank_line():
    md = f" - a\n   ```{F}\n   {{{{{{#!comment\n   x\n   }}}}}}\n   ```\n - d\n"
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!comment\n   x\n   }}}\n * d"
    )


def test_write_leg_loose_comment_fence_stays_in_the_item_too():
    """Master set the block off with a blank line (' * a\\n\\n   {{{#!comment
    ...'), which ends the list in Trac even when Markdown kept the fence in
    the item."""
    md = (
        f" - a\n\n   ```{F}\n   {{{{{{#!comment\n   x\n   }}}}}}\n   ```\n"
        " - d\n"
    )
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!comment\n   x\n   }}}\n * d"
    )


def test_write_leg_comment_then_table_run_is_one_code_run():
    md = (
        f" - a\n   ```{F}\n   {{{{{{#!comment\n   x\n   }}}}}}\n"
        f"   {{{{{{#!table\n   {{{{{{#!td\n   y\n   }}}}}}\n   }}}}}}\n"
        "   ```\n - d\n"
    )
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!comment\n   x\n   }}}\n   {{{#!table\n"
        "   {{{#!td\n   y\n   }}}\n   }}}\n * d"
    )


def test_write_leg_paragraph_after_the_block_is_adjacent():
    md = (
        f" - a\n   ```{F}\n   {{{{{{#!comment\n   x\n   }}}}}}\n   ```\n"
        "   more\n - d\n"
    )
    assert markdown_to_tracwiki(md) == (
        " * a\n   {{{#!comment\n   x\n   }}}\n   more\n * d"
    )


# Fences the write leg still keeps in the text run, byte for byte as on master
# (cc9f971): a td/th run, a spanned table, a lettered list, a block with text
# after it. Loose form, as master's read leg wrote them.
TEXT_RUN_FENCES = {
    "td": (
        f" - a\n\n   ```{F}\n   {{{{{{#!td\n   x\n   }}}}}}\n   ```\n - d\n",
        " * a\n\n   {{{#!td\n   x\n   }}}\n * d",
    ),
    "comment then th": (
        f" - a\n\n   ```{F}\n   {{{{{{#!comment\n   x\n   }}}}}}\n"
        f"   {{{{{{#!th\n   y\n   }}}}}}\n   ```\n - d\n",
        " * a\n\n   {{{#!comment\n   x\n   }}}\n   {{{#!th\n   y\n   }}}\n"
        " * d",
    ),
    "spanned table": (
        f" - a\n\n   ```{F}\n   || a |||| b ||\n   ```\n - d\n",
        " * a\n\n   || a |||| b ||\n * d",
    ),
    "lettered list": (
        f" - a\n\n   ```{F}\n    a. x\n    b. y\n   ```\n - d\n",
        " * a\n\n    a. x\n    b. y\n * d",
    ),
    "a comment with text after its closer": (
        f" - a\n\n   ```{F}\n   {{{{{{#!comment\n   x\n   }}}}}} tail\n"
        "   ```\n - d\n",
        " * a\n\n   {{{#!comment\n   x\n   }}} tail\n * d",
    ),
}


@pytest.mark.parametrize(
    ("md", "expected"),
    list(TEXT_RUN_FENCES.values()),
    ids=list(TEXT_RUN_FENCES),
)
def test_other_fallback_fences_stay_in_the_text_run(md, expected):
    assert markdown_to_tracwiki(md) == expected


def test_top_level_fallback_fence_is_unchanged():
    md = f"```{F}\n{{{{{{#!comment\nx\n}}}}}}\n```\n"
    assert markdown_to_tracwiki(md) == "{{{#!comment\nx\n}}}"


def test_find_list_item_splits_follows_the_run_kind():
    """`find_list_item_splits` shares `_item_runs`: a comment fence after a
    nested list is a `code` run there too, not text."""
    from trac_mcp_server.converters.markdown_to_tracwiki import (
        _item_runs,
    )

    tokens = _parse(
        f" - a\n   ```{F}\n   {{{{{{#!comment\n   x\n   }}}}}}\n   ```\n"
        f" - b\n   ```{F}\n   {{{{{{#!td\n   x\n   }}}}}}\n   ```\n"
    )
    items = tokens[0]["children"]
    kinds = [[k for k, _ in _item_runs(i["children"])] for i in items]
    assert kinds[0] == ["text", "code"], kinds
    assert kinds[1] == ["text"], kinds


# -- Live: the real renderer on /trac_test ---------------------------------------


def _normalise(html):
    return re.sub(r"\s+", " ", html).strip()


LIVE_SAME_HTML = {
    "ticket shape 1": IN_ITEM[
        "ticket shape 1: column-0 comment, continuation and a nested item"
    ][0],
    "comment 1 shape 1": IN_ITEM[
        "comment 1 shape 1: later item after an indented comment"
    ][0],
    "comment 1 shape 2": IN_ITEM[
        "comment 1 shape 2: a continuation line, then the next item"
    ][0],
    "M2 indented table": IN_ITEM["M2: indented table with a nested td"][
        0
    ],
    "M2 column-0 table": IN_ITEM["M2: column-0 table with a nested td"][
        0
    ],
}


@pytest.mark.live
@pytest.mark.parametrize("name", list(LIVE_SAME_HTML))
def test_live_round_trip_renders_like_the_source(name):
    """Ticket #192 acceptance against Trac's own renderer: the source and
    its tw -> md -> tw round trip render the same HTML (whitespace
    normalised). Read-only: `wiki.wikiToHtml` writes nothing."""
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.instances import InstanceRegistry

    config, _ = bootstrap_config()
    client = TracClient(
        InstanceRegistry(config, {}).resolve("/trac_test")
    )
    wiki = LIVE_SAME_HTML[name]

    source_html = client.wiki_to_html(wiki)
    trip_html = client.wiki_to_html(_rt(wiki))

    assert _normalise(trip_html) == _normalise(source_html), (
        source_html,
        trip_html,
    )


@pytest.mark.live
def test_live_td_shape_keeps_the_later_item_a_list_item():
    """M3/M5: the td closes the list in the source and in the round trip;
    the later item is a list item, never code (`<pre>`)."""
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.instances import InstanceRegistry

    config, _ = bootstrap_config()
    client = TracClient(
        InstanceRegistry(config, {}).resolve("/trac_test")
    )
    wiki = " * a\n   {{{#!td\n   x\n   }}}\n   more\n * b\n"

    html = client.wiki_to_html(_rt(wiki))

    assert "<pre" not in html, html
    assert re.search(r"<li[^>]*>\s*b\s*</li>", html), html
