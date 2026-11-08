"""An indented line under a column-0 list item continues the item, up to
Trac's bound (ticket #190).

**The defect.** A line indented under a column-0 item (``- a`` / ``  q``)
fell out of the list on the read leg: ``_convert_indented_blocks`` read the
run as a quote (``> q``) or a verbatim fence, after the list. Trac treats a
column-0 marker as an ordinary list marker at column 0
(``Formatter._indent_formatter`` / ``_continued_item``): an indented line
continues the item up to column 2 under a bullet and column 3 under a
number, and a line past that ends the list and is a blockquote (#175).
Measured on /trac_test on 2026-11-08 (``convert_preview``,
``format=tracwiki``; ticket #190 comment 1), read leg on ``master``
``bf5a426``:

=====  ===========================  ==============================  ======================
shape  source (``/`` = newline)     Trac renders                    ``master`` read leg
=====  ===========================  ==============================  ======================
B1     ``- a/ q``                   ``q`` in item ``a``             fence after the list
B2     ``- a/  q``                  ``q`` in ``a``                  ``> q`` after the list
B3     ``- a/   q``                 list ends, ``q`` a blockquote   fence (right)
B4     ``- a/  q/  r``              ``q r`` in ``a``                ``> q`` / ``> r``
B5     ``- a/  q/- b``              one list, ``q`` in ``a``        ``> q`` between lists
B6     `` * x/- a/  q``             two lists, ``q`` in ``a``       ``> q`` after the lists
O1     ``1. a/   q``                ``q`` in ``a``                  fence after the list
O2     ``1. a/    q``               list ends, ``q`` a blockquote   fence (right)
=====  ===========================  ==============================  ======================

**The fix (read leg only).** `_convert_indented_blocks`: a run that starts
straight after a column-0 bullet or digits marker line and holds no list
marker is that item's continuation. Its lines up to Trac's bound
(``trac_continuation_bound(0, ordered=...)``) go out as written, where
CommonMark keeps them in the item (a lazy continuation left of the text
column); the rest of the run, from the first line past the bound, goes
through the existing steps as its own run. `_fallback_column_zero_lists`
(#89): an indented line up to column 3 after a lettered or roman column-0
item joins that item's verbatim fence, and a following marker line continues
the same fence. Declined (master's bytes): a tab in the indent of a
continuation line, and a ``>`` line (a citation under a column-0 item is a
separate ticket).

The write leg is unchanged. A continued item round-trips to the write leg's
own form (marker at column 1, text at column 3): not byte-exact, but it
renders the same.
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
# the round trip tw -> md -> tw). The continuation line is in the item Trac
# puts it in.
SHAPES = [
    pytest.param(
        "- a\n q\n",
        "- a\n q\n",
        [_ul(["a\nq"])],
        " * a\n   q",
        id="B1-column-1",
    ),
    pytest.param(
        "- a\n  q\n",
        "- a\n  q\n",
        [_ul(["a\nq"])],
        " * a\n   q",
        id="B2-column-2-ticket-case",
    ),
    pytest.param(
        "- a\n  q\n  r\n",
        "- a\n  q\n  r\n",
        [_ul(["a\nq\nr"])],
        " * a\n   q\n   r",
        id="B4-two-lines",
    ),
    pytest.param(
        "- a\n  q\n- b\n",
        "- a\n  q\n- b\n",
        [_ul(["a\nq"], ["b"])],
        " * a\n   q\n * b",
        id="B5-next-item-follows",
    ),
    # #182 pinned this row at master's bytes (`> q` after the lists, the
    # write leg moving `q` out of the item); #190 flips it: Trac renders
    # two lists with `q` in `a`.
    pytest.param(
        " * x\n- a\n  q\n",
        " - x\n\n- a\n  q\n",
        [_ul(["x"], ["a\nq"])],
        " * x\n * a\n   q",
        id="B6-column-0-item-after-a-list",
    ),
    pytest.param(
        "1. a\n   q\n",
        "1. a\n   q\n",
        [_ol(["a\nq"])],
        " 1. a\n    q",
        id="O1-ordered-column-3",
    ),
    pytest.param(
        "1. a\n  q\n",
        "1. a\n  q\n",
        [_ol(["a\nq"])],
        " 1. a\n    q",
        id="O-ordered-column-2",
    ),
    pytest.param(
        "* a\n  q\n",
        "* a\n  q\n",
        [_ul(["a\nq"])],
        " * a\n   q",
        id="star-bullet",
    ),
    pytest.param(
        "- a\n  q\n   r\n",
        "- a\n  q\n```tracwiki-unconverted\n   r\n```\n",
        [_ul(["a\nq"]), ("code", "   r")],
        " * a\n   q\n\n   r",
        id="past-the-bound-after-continuation-ends-the-list",
    ),
    # Lettered and roman column-0 items: the continuation joins #89's
    # verbatim fence, and a following marker line continues the same fence.
    pytest.param(
        "a. a\n  q\n",
        "```tracwiki-unconverted\na. a\n  q\n```\n",
        [("code", "a. a\n  q")],
        "a. a\n  q",
        id="lettered-continuation-in-the-fence",
    ),
    pytest.param(
        "a. a\n  q\nb. b\n",
        "```tracwiki-unconverted\na. a\n  q\nb. b\n```\n",
        [("code", "a. a\n  q\nb. b")],
        "a. a\n  q\nb. b",
        id="lettered-next-marker-same-fence",
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
def test_text_is_in_the_item_trac_puts_it_in(
    tracwiki, markdown, shape, back
):
    assert _shape(_read(tracwiki)) == shape


@pytest.mark.parametrize(
    ("tracwiki", "markdown", "shape", "back"), SHAPES
)
def test_round_trip(tracwiki, markdown, shape, back):
    result = _round_trip(tracwiki)
    assert isinstance(result, str)
    assert result == back


# -- shapes where Trac ends the list, and the declines: master's bytes ------

# (TracWiki, Markdown on master, structure, round trip).
UNCHANGED = [
    # B3 / O2: past the bound, Trac ends the list and `q` is a blockquote;
    # master's fence after the list is right.
    pytest.param(
        "- a\n   q\n",
        "- a\n```tracwiki-unconverted\n   q\n```\n",
        [_ul(["a"]), ("code", "   q")],
        " * a\n\n   q",
        id="B3-past-the-bullet-bound",
    ),
    pytest.param(
        "1. a\n    q\n",
        "1. a\n```tracwiki-unconverted\n    q\n```\n",
        [_ol(["a"]), ("code", "    q")],
        " 1. a\n\n    q",
        id="O2-past-the-digits-bound",
    ),
    # Declined: a tab in a continuation line's indent keeps master's bytes.
    pytest.param(
        "- a\n\tq\n",
        "- a\n```tracwiki-unconverted\n\tq\n```\n",
        [_ul(["a"]), ("code", "\tq")],
        " * a\n\n\tq",
        id="tab-indented-continuation",
    ),
    pytest.param(
        "- a\n \tq\n",
        "- a\n```tracwiki-unconverted\n \tq\n```\n",
        [_ul(["a"]), ("code", " \tq")],
        " * a\n\n \tq",
        id="space-then-tab-continuation",
    ),
    # Declined: a citation line under a column-0 item. Trac puts the
    # citation `q` in item `a`; master reads `> > q` outside the list, which
    # is wrong. A separate follow-up ticket owns it, so these are master's
    # bytes, pinned so a later change shows up here.
    pytest.param(
        "- a\n  > q\n",
        "- a\n> > q\n",
        [_ul(["a"]), ("quote", "")],
        " * a\n\n    q",
        id="citation-under-a-column-0-item-is-a-follow-up-ticket",
    ),
    pytest.param(
        "- a\n  q\n  > r\n",
        "- a\n> q\n> > r\n",
        [_ul(["a"]), ("quote", "q")],
        " * a\n\n  q\n  \n    r",
        id="citation-after-a-continuation-line-keeps-the-whole-run",
    ),
]


@pytest.mark.parametrize(
    ("tracwiki", "markdown", "shape", "back"), UNCHANGED
)
def test_unchanged_read_leg_markdown(tracwiki, markdown, shape, back):
    assert _read(tracwiki) == markdown


@pytest.mark.parametrize(
    ("tracwiki", "markdown", "shape", "back"), UNCHANGED
)
def test_unchanged_structure(tracwiki, markdown, shape, back):
    assert _shape(_read(tracwiki)) == shape


@pytest.mark.parametrize(
    ("tracwiki", "markdown", "shape", "back"), UNCHANGED
)
def test_unchanged_round_trip(tracwiki, markdown, shape, back):
    result = _round_trip(tracwiki)
    assert isinstance(result, str)
    assert result == back
