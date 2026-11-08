"""A citation line goes to the list item Trac puts it in, at that item's text
column (ticket #187).

**The defect.** The read leg gave a ``>`` line under a list the owner a plain
text line gets (`_continued_item`: the deepest item whose marker is LEFT of
the line, #149) and moved it by that item's shift (#179). Trac does not treat
a citation as plain text. Measured on /trac_test on 2026-11-08
(``convert_preview``, ``format=tracwiki``; ticket #187 comment 1) and read
off Trac 1.6's ``Formatter.handle_quote_block``: the FIRST line of a run of
``>`` lines closes every list whose marker column is greater than its ``>``
column, and the whole run is one citation blockquote inside the deepest item
left open. So a ``>`` AT an item's marker column stays in that item (plain text
there ends the list), and there is no upper bound (#175): a ``>`` past the
item's text column stays too. The read leg put the quote in the PARENT of the
item Trac chose, or outside the list.

========  =========================  ===========  ===================
shape     source (``/`` = newline)   Trac: in     ``master``: in
========  =========================  ===========  ===================
Q2        `` * a/  * b/  > q``       b            a
Q3        `` * a/  * b/   > q``      b            a
Q4        `` * a/  * b/    > q``     b            b
Q5        `` * a/  * b/     > q``    b            b
Q1        `` * a/  * b/ > q``        a, after b   outside the list
Qm        `` * a/   * b/  > q``      a, after b   outside the list
QO        `` 1. a/    1. b/    > q``  b           a
Q3L       `` * a/  * b/    * c/    > q``  c       b
Qc        `` * a/  * b/    * c/  > q``    b, after c  a
S1        `` * a/ > q``              a            outside the list
S2        `` * a/  > q``             a            outside the list
S4        `` * a/    > q``           a            a
SO        `` 1. a/ > q``             a            outside the list
Qt        `` * a/  * b/  > q/  more/ * c``  q in b, more in b   a
========  =========================  ===========  ===================

**The fix (read leg only).** In `_shift_nested_markers`, a citation line with
an item open takes its owner from the ``>`` column (the deepest item whose
marker column is at or left of it), closes the items below it, and is written
at the owner's Markdown text column, where a ``>`` opens a quote in that item
(right after a deeper item CommonMark closes that item at the dedent: Q1,
Qc). The later ``>`` lines of the run keep that owner and column. Declined
(master's bytes): a ``>`` at column 0 or left of the root marker, a frozen or
tab-gap item involved, a tab in the quote's indent. #145's separator then
puts the blank line and the re-indent after the quote (Qt's ``more``).

Review round 2: the passes that walk raw lines before that one
(`_list_stack_step`, `_open_list_item`, `_split_past_bound`) apply the same
close rule to a ``>`` line (``quote_rule``), so a fence, text past the bound or
a marker after the quote read as Trac renders them (R1-R4); a tab-indented line
inside a run continues it (R5).

The write leg is unchanged: Q1, Qm and Qc read correctly, but the round trip
still ends the list at the quote (#145's known limit, #197).
"""

import mistune
import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    _MISTUNE_PLUGINS,
    markdown_to_tracwiki,
)
from trac_mcp_server.converters.tracwiki_to_markdown import (
    tracwiki_to_markdown,
)

# -- builders -------------------------------------------------------------


def _read(tracwiki: str) -> str:
    return tracwiki_to_markdown(tracwiki).text


def _round_trip(tracwiki: str) -> str:
    return markdown_to_tracwiki(_read(tracwiki))


def _text(inline: list) -> str:
    out = ""
    for node in inline:
        if node["type"] == "softbreak":
            out += "\n"
        elif "children" in node:
            out += _text(node["children"])
        else:
            out += node.get("raw", node.get("text", ""))
    return out


def _shape(markdown: str) -> list:
    """The structure CommonMark (mistune, the write leg's reader) reads out
    of `markdown`: a list is `("ul"|"ol", [item, ...])`, an item the list of
    its blocks (a text block is its text, a quote `("quote", text)`, a code
    block `("code", body)`)."""

    def blocks(tokens: list) -> list:
        out: list = []
        for tok in tokens:
            kind = tok["type"]
            if kind in ("paragraph", "block_text"):
                out.append(_text(tok["children"]))
            elif kind == "list":
                out.append(
                    (
                        "ol" if tok["attrs"]["ordered"] else "ul",
                        [
                            blocks(item["children"])
                            for item in tok["children"]
                        ],
                    )
                )
            elif kind == "block_code":
                out.append(("code", tok["raw"].strip("\n")))
            elif kind == "block_quote":
                out.append(
                    (
                        "quote",
                        " ".join(
                            _text(c["children"])
                            for c in tok["children"]
                            if c["type"] == "paragraph"
                        ),
                    )
                )
            elif kind != "blank_line":
                out.append((kind,))
        return out

    return blocks(
        mistune.create_markdown(
            renderer=None, plugins=_MISTUNE_PLUGINS
        )(markdown)
    )


def _ul(*items: list) -> tuple:
    return ("ul", list(items))


def _ol(*items: list) -> tuple:
    return ("ol", list(items))


# -- the shapes of the ticket ------------------------------------------------

# (id, TracWiki, Markdown the read leg gives, the structure mistune reads,
# the round trip tw -> md -> tw). The quote is in the item Trac puts it in.
# Q4, Q5 and S4 were already in that item on master: their structure and
# round trip are unchanged. Q5 and S4 (a `>` past the item's text column) now
# read at the item's text column, one column left of master's bytes (#187).
SHAPES = [
    pytest.param(
        " * a\n  * b\n  > q\n",
        " - a\n   - b\n     > q\n",
        [_ul(["a", _ul(["b", ("quote", "q")])])],
        " * a\n   * b\n     > q",
        id="Q2-ticket-case",
    ),
    pytest.param(
        " * a\n  * b\n   > q\n",
        " - a\n   - b\n     > q\n",
        [_ul(["a", _ul(["b", ("quote", "q")])])],
        " * a\n   * b\n     > q",
        id="Q3",
    ),
    pytest.param(
        " * a\n  * b\n    > q\n",
        " - a\n   - b\n     > q\n",
        [_ul(["a", _ul(["b", ("quote", "q")])])],
        " * a\n   * b\n     > q",
        id="Q4-unchanged",
    ),
    pytest.param(
        " * a\n  * b\n     > q\n",
        " - a\n   - b\n     > q\n",
        [_ul(["a", _ul(["b", ("quote", "q")])])],
        " * a\n   * b\n     > q",
        id="Q5-same-item-past-the-bound",
    ),
    pytest.param(
        " * a\n  * b\n > q\n",
        " - a\n   - b\n   > q\n",
        [_ul(["a", _ul(["b"]), ("quote", "q")])],
        " * a\n   * b\n\n     q",
        id="Q1-at-the-root-marker",
    ),
    pytest.param(
        " * a\n   * b\n  > q\n",
        " - a\n   - b\n   > q\n",
        [_ul(["a", _ul(["b"]), ("quote", "q")])],
        " * a\n   * b\n\n     q",
        id="Qm",
    ),
    pytest.param(
        " 1. a\n    1. b\n    > q\n",
        " 1. a\n    1. b\n       > q\n",
        [_ol(["a", _ol(["b", ("quote", "q")])])],
        " 1. a\n   1. b\n      > q",
        id="QO-ordered",
    ),
    pytest.param(
        " * a\n  * b\n    * c\n    > q\n",
        " - a\n   - b\n     - c\n       > q\n",
        [_ul(["a", _ul(["b", _ul(["c", ("quote", "q")])])])],
        " * a\n   * b\n     * c\n       > q",
        id="Q3L-three-levels",
    ),
    pytest.param(
        " * a\n  * b\n    * c\n  > q\n",
        " - a\n   - b\n     - c\n     > q\n",
        [_ul(["a", _ul(["b", _ul(["c"]), ("quote", "q")])])],
        " * a\n   * b\n     * c\n\n       q",
        id="Qc-after-the-nested-list",
    ),
    pytest.param(
        " * a\n > q\n",
        " - a\n   > q\n",
        [_ul(["a", ("quote", "q")])],
        " * a\n   > q",
        id="S1-single-level-at-the-marker",
    ),
    pytest.param(
        " * a\n  > q\n",
        " - a\n   > q\n",
        [_ul(["a", ("quote", "q")])],
        " * a\n   > q",
        id="S2-single-level",
    ),
    pytest.param(
        " * a\n    > q\n",
        " - a\n   > q\n",
        [_ul(["a", ("quote", "q")])],
        " * a\n   > q",
        id="S4-same-item-past-the-bound",
    ),
    pytest.param(
        " 1. a\n > q\n",
        " 1. a\n    > q\n",
        [_ol(["a", ("quote", "q")])],
        " 1. a\n    > q",
        id="SO-ordered-single-level",
    ),
    pytest.param(
        " * a\n  * b\n  > q\n  more\n * c\n",
        " - a\n   - b\n     > q\n\n     more\n - c\n",
        [_ul(["a", _ul(["b", ("quote", "q"), "more"])], ["c"])],
        " * a\n   * b\n     > q\n     more\n * c",
        id="Qt-text-after-the-quote",
    ),
    pytest.param(
        " * a\n  * b\n  > q\n   > r\n > s\n",
        " - a\n   - b\n     > q\n     > r\n     > s\n",
        [_ul(["a", _ul(["b", ("quote", "q\nr\ns")])])],
        " * a\n   * b\n     > q\n     > r\n     > s",
        id="run-keeps-its-owner",
    ),
    # Review round 2 (#187): the passes that run on RAW lines before
    # `_shift_nested_markers` (`_list_stack_step`, upper_bound=True) closed the
    # list at a `>` at the root marker's column, which the owner rule keeps.
    # R1-R6 are measured on /trac_test on 2026-11-08 (see the ticket).
    pytest.param(
        " * a\n > q\n   {{{\n   x\n   }}}\n",
        " - a\n   > q\n   ```\n   x\n   ```\n",
        [_ul(["a", ("quote", "q"), ("code", "x")])],
        " * a\n   > q\n   {{{\n   x\n   }}}",
        id="R1-code-block-after-a-quote-at-the-root-marker",
    ),
    pytest.param(
        " * a\n > q\n\n   * c\n",
        # `c` stays verbatim outside the list (#183's fence for a marker at
        # the text column after a blank line); written back, Trac reads it
        # as a new list, as it did in the source.
        " - a\n   > q\n\n```tracwiki-unconverted\n   * c\n```\n",
        [_ul(["a", ("quote", "q")]), ("code", "   * c")],
        " * a\n   > q\n\n   * c",
        id="R2-list-after-a-blank-is-a-new-list",
    ),
    pytest.param(
        " * a\n > q\n     more\n",
        " - a\n   > q\n\n```tracwiki-unconverted\n     more\n```\n",
        [_ul(["a", ("quote", "q")]), ("code", "     more")],
        " * a\n   > q\n\n     more",
        id="R3-text-past-the-bound-is-outside-the-list",
    ),
    pytest.param(
        " * a\n > q\n    more\n",
        " - a\n   > q\n\n```tracwiki-unconverted\n    more\n```\n",
        [_ul(["a", ("quote", "q")]), ("code", "    more")],
        " * a\n   > q\n\n    more",
        id="R3-variant-at-column-4",
    ),
    pytest.param(
        " * a\n  * b\n > q\n     more\n",
        " - a\n   - b\n   > q\n\n```tracwiki-unconverted\n     more\n```\n",
        [_ul(["a", _ul(["b"]), ("quote", "q")]), ("code", "     more")],
        " * a\n   * b\n\n     q\n\n     more",
        id="R3-nested-variant",
    ),
    pytest.param(
        " * a\n  * b\n  > q\n    c\n",
        " - a\n   - b\n     > q\n\n     c\n",
        [_ul(["a", _ul(["b", ("quote", "q"), "c"])])],
        " * a\n   * b\n     > q\n     c",
        id="R4-text-after-a-nested-quote",
    ),
    pytest.param(
        " * a\n  * b\n  > q\n\t> r\n  > s\n",
        " - a\n   - b\n     > q\n     > r\n     > s\n",
        [_ul(["a", _ul(["b", ("quote", "q\nr\ns")])])],
        " * a\n   * b\n     > q\n     > r\n     > s",
        id="R5-tab-line-continues-the-run",
    ),
]


@pytest.mark.parametrize(
    ("tracwiki", "markdown", "shape", "back"), SHAPES
)
def test_read_leg_markdown(tracwiki, markdown, shape, back):
    assert _read(tracwiki) == markdown


@pytest.mark.parametrize(
    ("tracwiki", "markdown", "shape", "back"), SHAPES
)
def test_quote_is_in_the_item_trac_puts_it_in(
    tracwiki, markdown, shape, back
):
    assert _shape(_read(tracwiki)) == shape


@pytest.mark.parametrize(
    ("tracwiki", "markdown", "shape", "back"), SHAPES
)
def test_round_trip(tracwiki, markdown, shape, back):
    assert _round_trip(tracwiki) == back


# -- shapes that must not move: master's bytes -------------------------------

# (TracWiki, Markdown on master). Either the quote is not under an open item
# the new rule may use (column 0, a tab, a frozen or tab-gap item), or the
# line is not a citation, or a blank line separates it from the list.
UNCHANGED = [
    pytest.param(
        " * a\n  * b\n  {{{\n  x\n  }}}\n",
        " - a\n   - b\n     ```\n     x\n     ```\n",
        id="fence-at-the-nested-marker-column",
    ),
    pytest.param(
        " * a\n  * b\n {{{\n x\n }}}\n",
        " - a\n   - b\n     ```\n     x\n     ```\n",
        id="fence-at-the-column-1-variant",
    ),
    pytest.param(
        " * a\n  * b\n> q\n",
        " - a\n   - b\n> q\n",
        id="quote-at-column-0-after-a-list",
    ),
    pytest.param(
        "  * a\n > q\n",
        "  - a\n\n > q\n",
        id="quote-left-of-the-root-marker",
    ),
    pytest.param(
        "\t* a\n\t> q\n",
        "\t* a\n\n\t> q\n",
        id="tab-indented-list-with-a-quote",
    ),
    pytest.param(
        " * a\n\t> q\n",
        " - a\n\n\t> q\n",
        id="tab-in-the-quote-indent",
    ),
    pytest.param(
        "    * a\n      > q\n",
        "    - a\n      > q\n",
        id="frozen-root-at-column-4",
    ),
    pytest.param(
        "    * a\n     * b\n      > q\n",
        "    - a\n     - b\n      > q\n",
        id="frozen-nested-under-a-column-4-root",
    ),
    pytest.param(
        " *\ta\n  > q\n",
        " *\ta\n  > q\n",
        id="tab-gap-root",
    ),
    pytest.param(
        " * a\n  *\tb\n  > q\n",
        " - a\n   *\tb\n   > q\n",
        id="tab-gap-nested",
    ),
    pytest.param(
        " * a\n  * b\n\n  > q\n",
        " - a\n   - b\n\n> > q\n",
        id="quote-after-a-blank-line",
    ),
    # KNOWN GAP (#187 review R6, filed as #200): Trac puts the
    # `q` of ` * a/  * b/    {{{/    code/    }}}/  > q` in `b`, after the
    # code block; the read leg ends the list at the fence and leaves the
    # quote at column 0 (`_convert_indented_blocks`), the same bytes as on
    # `master`. Pinned so the fix shows up as a changed pin.
    pytest.param(
        " * a\n  * b\n    {{{\n    code\n    }}}\n  > q\n",
        " - a\n   - b\n     ```\n     code\n     ```\n> > q\n",
        id="R6-known-gap-quote-after-a-code-block",
    ),
    # Review finding 7: a citation is found with `lstrip(" \t")` everywhere,
    # so a line whose leading whitespace holds a no-break space is not one for
    # this rule and keeps master's bytes: at column 1 the line is plain text
    # that ends the list, at column 2 it continues the nested item.
    pytest.param(
        " * a\n  * b\n \xa0> q\n",
        " - a\n   - b\n\n \xa0> q\n",
        id="no-break-space-in-the-indent-at-column-1",
    ),
    pytest.param(
        " * a\n  * b\n  \xa0> q\n",
        " - a\n   - b\n     \xa0> q\n",
        id="no-break-space-in-the-indent-at-column-2",
    ),
]


@pytest.mark.parametrize(("tracwiki", "markdown"), UNCHANGED)
def test_declined_and_unrelated_shapes_keep_masters_bytes(
    tracwiki, markdown
):
    assert _read(tracwiki) == markdown
