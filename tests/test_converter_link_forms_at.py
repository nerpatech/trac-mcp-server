"""`_link_forms_at`: whether a link forms at a `[` (ticket #263).

mistune 3.4 decides a link at its closing `]`; the converter's bracket
rules must decide at the `[`, so `_link_forms_at` mirrors 3.4's
`_close_bracket` and `_parse_reference` over the source. It reads only
`state.src` and `state.env`, so a stand-in state is enough, and the table
holds on mistune 3.2 as well (the helpers it calls exist in both).
"""

from types import SimpleNamespace

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    _link_forms_at,
)

REF = {"REF": {"url": "http://x", "title": None}}

# (source, index of the `[`, reference definitions, expected)
CASES = {
    "inline destination": ("[a](http://x)", 0, {}, True),
    "inline destination with a title": (
        '[a](http://x "t")',
        0,
        {},
        True,
    ),
    "inline destination after prose": ("x [a](http://x)", 2, {}, True),
    "paren that is not a destination": ("[a](b c)", 0, {}, False),
    "open paren, nothing after": ("[a](", 0, {}, False),
    "no references: shortcut text": ("[ref] x", 0, {}, False),
    "no references: full label": ("[a][ref]", 0, {}, False),
    "no closing bracket": ("[a", 0, REF, False),
    "full reference with a label": ("[a][ref]", 0, REF, True),
    "full reference, label not defined": ("[a][zz]", 0, REF, False),
    "collapsed reference": ("[ref][]", 0, REF, True),
    "collapsed reference, text not defined": ("[zz][]", 0, REF, False),
    "shortcut reference": ("[ref] x", 0, REF, True),
    "shortcut reference after prose": ("see [ref].", 4, REF, True),
    "shortcut reference, other case": ("[REF] x", 0, REF, True),
    "shortcut reference, text not defined": ("[zz] x", 0, REF, False),
    # A label given explicitly is the only one tried (3.4 does not fall
    # back to the text once `[label]` follows).
    "explicit label wins over the text": ("[ref][zz]", 0, REF, False),
    # A nested bracket in the text rules the text out as a label, but an
    # explicit label still works.
    "nested text, shortcut": ("[a [ref]] x", 0, REF, False),
    "nested text, full reference": ("[a [b]][ref]", 0, REF, True),
    "nested text, inline destination": (
        "[a [b]](http://x)",
        0,
        {},
        True,
    ),
    "the inner bracket of nested text": ("[a [ref]] x", 3, REF, True),
}


@pytest.mark.parametrize(
    ("src", "open_pos", "refs", "expected"),
    list(CASES.values()),
    ids=list(CASES),
)
def test_link_forms_at(src, open_pos, refs, expected):
    assert src[open_pos] == "["
    state = SimpleNamespace(src=src, env={"ref_links": refs})
    assert _link_forms_at(state, open_pos) is expected


def test_link_forms_at_without_a_reference_table():
    state = SimpleNamespace(src="[ref] x", env={})
    assert _link_forms_at(state, 0) is False
