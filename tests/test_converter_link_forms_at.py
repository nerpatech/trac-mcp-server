"""`_link_forms_at`: whether a link forms at a `[` (ticket #263).

mistune 3.4 decides a link at its closing `]`; the converter's bracket
rules must decide at the `[`, so `_link_forms_at` mirrors 3.4's
`_close_bracket` and `_parse_reference` over the source. It reads only
`state.src` and `state.env`, so a stand-in state is enough, and the table
holds on mistune 3.2 as well (the helpers it calls exist in both).
"""

from types import SimpleNamespace

import pytest
from mistune.helpers import parse_link_text

from trac_mcp_server.converters.markdown_to_tracwiki import (
    _close_bracket_map,
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


# Inputs for the bracket map: nesting, unclosed openers, stray closers and
# backslash escapes (an even run before a bracket leaves it live, an odd run
# escapes it).
MAP_INPUTS = [
    "[a]",
    "[a [b] c]",
    "[[[x]]]",
    "[a",
    "a]",
    "] [a] ]",
    "[a [b",
    "[a] [b] [c]",
    "\\[a] [b]",
    "\\\\[a]",
    "\\\\\\[a]",
    "[a\\]",
    "[a\\\\]",
    "[a\\\\\\]",
    "[a \\[b] c]",
    "[a [b\\] c]",
    "x['a' [b] y",
    "[a](b) [c](d [e])",
    "[]",
    "[][]",
    "",
    "no brackets",
]


@pytest.mark.parametrize("src", MAP_INPUTS)
def test_the_close_map_agrees_with_parse_link_text(src):
    """For every live `[`, the map holds the `]` mistune's own
    `parse_link_text` finds from just after it, or nothing when it finds
    none; an escaped `[` is not in the map. That includes mistune's quirk
    that a `]` after an even run of backslashes opens a level instead of
    closing one."""
    closes = _close_bracket_map(src)
    for pos, char in enumerate(src):
        if char != "[":
            continue
        run = len(src[:pos]) - len(src[:pos].rstrip("\\"))
        if run % 2:
            assert pos not in closes, (src, pos)
            continue
        text, end = parse_link_text(src, pos + 1)
        expected = None if text is None else end - 1
        assert closes.get(pos) == expected, (src, pos)
    assert all(src[j] == "]" for j in closes.values())


def test_link_forms_at_reads_the_source_once_per_state():
    """The map and the per-position answers are cached on the state."""
    state = SimpleNamespace(
        src="[a [b] c](http://x)", env={"ref_links": {}}
    )
    assert _link_forms_at(state, 0) is True
    cached = state._tracwiki_bracket_cache
    assert _link_forms_at(state, 0) is True
    assert _link_forms_at(state, 3) is False
    assert state._tracwiki_bracket_cache is cached
