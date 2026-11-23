"""The preview checks run in linear time (Trac /trac_mcp_server #289).

Several regexes in `preview/checks.py` restarted a scan at every opener
and ran to the end of the text, so one-line hostile input cost O(n**2):
one 84 KB `convert_preview` froze the shared daemon's event loop for 12.5 s.
This file holds the ratio check over those inputs (through `build_warnings`
and through the converter), equivalence tests of every rewritten scan
against the regex it replaced (kept here as test-local constants), checks
that the handlers run the converter and the checks off the event loop, and
a loop-stall test.
"""

import importlib
import random
import re
import string
import threading
from types import SimpleNamespace

import pytest

from tests.test_converter_flavors import (
    CPU_CEILING_S,
    GROWTH_FACTOR,
    GROWTH_LIMIT,
    _as_build,
    _growth,
)
from trac_mcp_server.converters.markdown_to_tracwiki import (
    convert_with_warnings,
)
from trac_mcp_server.mcp.tools import (
    convert_preview,
    render_check,
    write_gate,
)
from trac_mcp_server.preview import checks
from trac_mcp_server.preview.checks import build_warnings
from trac_mcp_server.preview.facts import extract_facts
from trac_mcp_server.preview.live import RenderedSection

# -- 1. Hostile input grows linearly ------------------------------------------
#
# Same method as tests/test_converter_flavors.py section 5 (the ratio check
# of #243; its constants and `_growth` are imported, see there for the
# measured margins), at the sizes written below: the CPU time of a 64 KB
# input (FULL_SIZE) against the same input GROWTH_FACTOR (8) times smaller,
# minimum of REPEATS (7) runs, plus an absolute ceiling. A linear case reads
# about 8 (5.7 to 12.9 measured), a quadratic one about 60 (47 to 88 measured
# on the code before #289).

FULL_SIZE = 64 * 1024

# The small inputs take a few milliseconds, so on a busy host a minimum
# of 3 runs read 17x once for a linear case; 7 runs do not.
REPEATS = 7

HOSTILE_UNITS = {
    "unclosed macro opener": "x [[ y\n",
    "unclosed inline braces": "a {{{b\n",
    "lone bracket": "[",
    "bracket and label": "[x ",
    "double bracket pipe": "[[x|",
    "dotted prefix run": "a.",
    "plus prefix run": "a+",
    "dashed dotted prefix run": "a-b.",
    "digit dotted prefix run": "1a.",
    "bang bracket": "![",
    "bang bracket quote": "!['",
    "bang bracket zero": "![0",
    "bang bracket quote and space": "!['a' ",
    "bang bracket zero slash": "![0/",
}

# The package re-exports the function `markdown_to_tracwiki` under the
# module's name, so the module is imported by path.
converter = importlib.import_module(
    "trac_mcp_server.converters.markdown_to_tracwiki"
)

FACTS = extract_facts("<div><p>x</p></div>")


def _warnings(source):
    return build_warnings(
        markdown_source=source,
        tracwiki=source,
        facts=FACTS,
        probes={},
        check_targets=False,
        source_format="markdown",
    )


@pytest.mark.parametrize(
    "unit", list(HOSTILE_UNITS.values()), ids=list(HOSTILE_UNITS)
)
def test_build_warnings_grows_linearly_on_hostile_input(unit):
    n = FULL_SIZE // len(unit)
    ratio, small, big = _growth(
        _warnings, _as_build(unit), n, repeats=REPEATS
    )
    assert ratio < GROWTH_LIMIT, (
        f"CPU time grew {ratio:.1f}x for {GROWTH_FACTOR}x the input "
        f"(limit {GROWTH_LIMIT}x): {small:.4f}s at {n // GROWTH_FACTOR} "
        f"repetitions, {big:.4f}s at {n} repetitions"
    )
    assert big < CPU_CEILING_S, (
        f"the full-size run ({n} repetitions) took {big:.2f}s of CPU time "
        f"(ceiling {CPU_CEILING_S}s); growth was {ratio:.1f}x"
    )


# The same inputs through the Markdown converter (what `convert_preview`
# and `wiki_file_push` run), plus the shapes of its own scheme regexes.
# `[1/` and `['a' ` were thought to be mistune's own cost (#259); they were
# the bracket shapes' patterns inside mistune's combined scanner.
CONVERTER_UNITS = {
    **HOSTILE_UNITS,
    "numeric bracket slash": "[1/",
    "quoted bracket space": "['a' ",
    "scheme in a bracket": "[a:",
    "realm in a bracket": "[a:wiki:",
}


def _convert(source):
    return convert_with_warnings(source)


@pytest.mark.parametrize(
    "unit", list(CONVERTER_UNITS.values()), ids=list(CONVERTER_UNITS)
)
def test_convert_with_warnings_grows_linearly_on_hostile_input(unit):
    n = FULL_SIZE // len(unit)
    ratio, small, big = _growth(
        _convert, _as_build(unit), n, repeats=REPEATS
    )
    assert ratio < GROWTH_LIMIT, (
        f"CPU time grew {ratio:.1f}x for {GROWTH_FACTOR}x the input "
        f"(limit {GROWTH_LIMIT}x): {small:.4f}s at {n // GROWTH_FACTOR} "
        f"repetitions, {big:.4f}s at {n} repetitions"
    )
    assert big < CPU_CEILING_S, (
        f"the full-size run ({n} repetitions) took {big:.2f}s of CPU time "
        f"(ceiling {CPU_CEILING_S}s); growth was {ratio:.1f}x"
    )


# -- 2. Each rewritten scan matches the regex it replaced ---------------------
#
# The old patterns, kept here as test-local constants, are the reference.

OLD_MACRO_LINK_RE = re.compile(r"\[\[.*?\]\]", re.DOTALL)
OLD_INLINE_BRACES_RE = re.compile(r"\{\{\{.*?\}\}\}", re.DOTALL)
OLD_BRACKET_LINK_RE = re.compile(r"\[[^\]\n]*\]")
OLD_MARKDOWN_LINK_RE = re.compile(r"\[[^\]\n]+\]\([^)\n]+\)")
OLD_PREFIX_TICKET_RE = re.compile(r"\b[A-Za-z][\w.+-]*:#\d+\b")
OLD_TICKET_COMMENT_SCOPED_RE = re.compile(
    r"\bticket:\d+#comment:\d+\b"
    r"|\bcomment:\d+:[A-Za-z]+:\d+\b"
    r"|\b[A-Za-z][\w+.-]*://\S+"
)
OLD_PREFIX_REALM_HEAD = (
    r"[A-Za-z][\w.+-]*:(?:"
    + "|".join(sorted(checks._INTERTRAC_REALMS))
    + r"):"
)
OLD_PREFIX_REALM_RE = re.compile(
    r"(?<=\[)"
    + OLD_PREFIX_REALM_HEAD
    + r"(?:[^\s`\]]+|(?=\]))"
    + r"|\b"
    + OLD_PREFIX_REALM_HEAD
    + r"[^\s`]+"
)
OLD_REALM_REF_RE = re.compile(
    r"\b(?:[A-Za-z][\w.+-]*:)?"
    rf"(?:{'|'.join(sorted(checks.TRACLINK_SCHEMES))}):\S+"
)

ALPHABET = "[]{}()!\n:.a#/w"

EDGE_CASES = [
    "",
    "[",
    "]",
    "[[[",
    "[[]]",
    "[[]]]",
    "[[[]]]",
    "{{{}}}}",
    "{{{{}}}",
    "{{{ a }}} {{{ b }}}",
    "[[x]]]",
    "[[x]\n]]",
    "[[x\n]] [[y",
    "[a](b)[c](d)",
    "[[a](b)",
    "[a]( b\n)",
    "[a](b",
    "[a]()",
    "[]()",
    "[ [a](b)",
    "[a[b](c)",
    "[a]b](c)",
    "[a] (b)",
    "[a](b)(c)",
    "x [[ y\n[[z]] w",
    "![0](a, b) and [docs](x y)",
]


def _corpus():
    rng = random.Random(289)
    cases = list(EDGE_CASES)
    for _ in range(3000):
        length = rng.randint(0, 200)
        cases.append(
            "".join(rng.choice(ALPHABET) for _ in range(length))
        )
    return cases


CORPUS = _corpus()


def _old_blank(pattern, text):
    return pattern.sub(checks._blank_preserving_layout, text)


def test_blank_delimited_macro_spans_equal_the_old_regex():
    for text in CORPUS:
        assert checks._blank_delimited(text, "[[", "]]") == _old_blank(
            OLD_MACRO_LINK_RE, text
        ), repr(text)


def test_blank_delimited_brace_spans_equal_the_old_regex():
    for text in CORPUS:
        assert checks._blank_delimited(
            text, "{{{", "}}}"
        ) == _old_blank(OLD_INLINE_BRACES_RE, text), repr(text)


def _old_sub_bracket_syntax(repl, text):
    return converter._BRACKET_SYNTAX_RE.sub(repl, text)


def test_converter_bracket_syntax_sub_equals_the_old_regex_sub():
    def repl(match):
        return "<" + match.group(0) + ">"

    cases = CORPUS + [
        "[[",
        "[[]]",
        "[[a]]]",
        "[[a]" + "]",
        "[[a\n]]",
        "[[[a]]",
        "[[a]] [[b]] [[c",
        "[[a]b]]",
        "x[[a|b]]y[[",
    ]
    for text in cases:
        assert converter._sub_bracket_syntax(repl, text) == (
            _old_sub_bracket_syntax(repl, text)
        ), repr(text)


def test_blank_bracket_links_equal_the_old_regex():
    for text in CORPUS:
        assert checks._blank_bracket_links(text) == _old_blank(
            OLD_BRACKET_LINK_RE, text
        ), repr(text)


def test_the_blanking_chain_of_the_bare_autolink_scan_is_unchanged():
    for text in CORPUS:
        new = checks._blank_bracket_links(
            checks._blank_delimited(text, "[[", "]]")
        )
        old = _old_blank(
            OLD_BRACKET_LINK_RE, _old_blank(OLD_MACRO_LINK_RE, text)
        )
        assert new == old, repr(text)


def test_the_blanked_copy_of_the_escaped_shape_scan_is_unchanged():
    for text in CORPUS:
        new = checks._blank_delimited(
            checks._blank_delimited(text, "{{{", "}}}"), "[[", "]]"
        )
        old = _old_blank(
            OLD_MACRO_LINK_RE, _old_blank(OLD_INLINE_BRACES_RE, text)
        )
        assert new == old, repr(text)


def test_search_markdown_link_equals_the_old_search_at_every_start():
    rng = random.Random(2890)
    for text in CORPUS:
        starts = {0, len(text)} | {
            rng.randint(0, len(text)) for _ in range(3)
        }
        for pos in sorted(starts):
            old = OLD_MARKDOWN_LINK_RE.search(text, pos)
            new = checks._search_markdown_link(text, pos)
            if old is None:
                assert new is None, (text, pos)
            else:
                assert new is not None, (text, pos)
                assert new.span() == old.span(), (text, pos)
                assert new.group(0) == old.group(0), (text, pos)


def _old_first_literal_link_match(prose, tracwiki):
    """`first_literal_link_match` as it was, on the old regex."""
    escaped = checks._escaped_bracket_shapes(tracwiki)
    pos = 0
    while True:
        match = OLD_MARKDOWN_LINK_RE.search(prose, pos)
        if match is None:
            return None
        text = match.group(0)
        bracket = text[: text.index("]") + 1]
        if escaped[bracket] > 0:
            escaped[bracket] -= 1
            pos = match.start() + 1
            continue
        return match


@pytest.mark.parametrize(
    ("prose", "tracwiki"),
    [
        ("['a'](x y) and [docs](x y)", "!['a'](x y)"),
        ("['a' and [docs](x y)", "!['a' and"),
        ("x[0](a, b) y[0](c)", "x![0](a, b)"),
        ("[0](a) [0](b) [0](c)", "![0] ![0]"),
        ("[a](b)", ""),
        ("no link", "![0]"),
        ("[[[0](x)](y)", "![0]"),
    ],
)
def test_first_literal_link_match_equals_the_old_loop(prose, tracwiki):
    old = _old_first_literal_link_match(prose, tracwiki)
    new = checks.first_literal_link_match(prose, tracwiki)
    assert (new is None) == (old is None)
    if old is not None:
        assert new.span() == old.span()


def test_first_literal_link_match_equals_the_old_loop_on_the_corpus():
    for text in CORPUS:
        for tracwiki in ("", text):
            old = _old_first_literal_link_match(text, tracwiki)
            new = checks.first_literal_link_match(text, tracwiki)
            assert (new is None) == (old is None), (text, tracwiki)
            if old is not None:
                assert new.span() == old.span(), (text, tracwiki)


# The scheme-prefix regexes: equal to the old unbounded ones, except
# where a prefix would start partway through a `[\w.+-]` run whose head is
# not a letter (`1a.b:#3` matched `b:#3`; it matches nothing now): the
# accepted difference of #289, whose strings the equivalence test leaves
# out and a separate test pins.

PREFIX_PAIRS = [
    (OLD_PREFIX_TICKET_RE, checks._PREFIX_TICKET_RE),
    (OLD_PREFIX_REALM_RE, checks._PREFIX_REALM_RE),
    (OLD_REALM_REF_RE, checks._REALM_REF_RE),
]
PREFIX_IDS = [
    "prefix_ticket",
    "prefix_realm",
    "realm_ref",
]

PIECES = [
    "a",
    "b1",
    ".",
    "+",
    "-",
    ":",
    "#",
    "1",
    "/",
    "//",
    "://",
    " ",
    "[",
    "]",
    "`",
    "wiki",
    "ticket",
    "wiki:",
    ":wiki:",
    "ticket:12",
    "comment:3",
    "http",
    "ticket:1#comment:2",
]


def _prefix_corpus():
    rng = random.Random(289)
    cases = [
        "a:#1",
        "[p:wiki:A]/[q:wiki:B]",
        "x.y+z-w:wiki:Page and p:#12.",
        "http://host/x/ticket/67#comment:1",
        "comment:3:ticket:4",
        "[p:wiki:] [p:wiki: label]",
        "a.a.a.a:#1",
        "a" * 64 + ":#1",
        "a" * 65 + ":#1",
        "a" * 63 + ":wiki:X",
        "a" * 64 + "://x",
        "changelog:wiki:X",
    ]
    for _ in range(12000):
        parts = [rng.choice(PIECES) for _ in range(rng.randint(0, 40))]
        cases.append("".join(parts)[:120])
    return cases


PREFIX_CORPUS = _prefix_corpus()

_RUN_RE = re.compile(r"[\w.+-]+")


def _has_run_with_a_non_letter_head(text):
    return any(
        m.group(0)[0] not in string.ascii_letters
        for m in _RUN_RE.finditer(text)
    )


@pytest.mark.parametrize(("old", "new"), PREFIX_PAIRS, ids=PREFIX_IDS)
def test_prefix_regexes_equal_the_old_ones(old, new):
    compared = 0
    for text in PREFIX_CORPUS:
        if _has_run_with_a_non_letter_head(text):
            continue
        compared += 1
        assert [m.span() for m in new.finditer(text)] == [
            m.span() for m in old.finditer(text)
        ], repr(text)
    assert compared > 300


@pytest.mark.parametrize(
    ("old", "new", "text", "old_hit", "new_hit"),
    [
        (
            OLD_PREFIX_TICKET_RE,
            checks._PREFIX_TICKET_RE,
            "1a.b:#3",
            "b:#3",
            None,
        ),
        (
            OLD_PREFIX_REALM_RE,
            checks._PREFIX_REALM_RE,
            "1a.b:wiki:X",
            "b:wiki:X",
            None,
        ),
        (
            OLD_REALM_REF_RE,
            checks._REALM_REF_RE,
            "1a.b:wiki:X",
            "b:wiki:X",
            "wiki:X",
        ),
    ],
    ids=[
        "prefix_ticket",
        "prefix_realm",
        "realm_ref",
    ],
)
def test_a_prefix_no_longer_starts_inside_a_run_with_a_non_letter_head(
    old, new, text, old_hit, new_hit
):
    # The accepted change of #289.
    assert old.search(text).group(0) == old_hit
    found = new.search(text)
    assert (found.group(0) if found else None) == new_hit


@pytest.mark.parametrize(
    "text",
    [
        "a -rookery:#3 b",
        "a +rookery:#3 b",
        "a .rookery:#3 b",
        "\u00e9.trac:#3",
        "_a.trac:#3",
        "a:#1.b:#2",
    ],
)
def test_the_prefix_regex_drops_what_the_comment_documents(text):
    # The accepted differences of #289, as written in checks.py.
    old = [m.group(0) for m in OLD_PREFIX_TICKET_RE.finditer(text)]
    new = [m.group(0) for m in checks._PREFIX_TICKET_RE.finditer(text)]
    assert len(new) < len(old), (old, new)


# `_TICKET_COMMENT_SCOPED_RE` only BLANKS, so its URL alternative takes the
# whole run up to `://`: a superset of what the old `\b[A-Za-z]` form took.

URL_HEADS = ["-", ".", "+", "1.", "9", "_", ""]


@pytest.mark.parametrize("head", URL_HEADS)
def test_a_url_after_a_non_letter_head_is_not_a_dangling_comment(head):
    tracwiki = f"see {head}https://h/t/ticket/5#comment:99 x"
    found = build_warnings(
        markdown_source=None,
        tracwiki=tracwiki,
        facts=FACTS,
        probes={},
        check_targets=False,
        source_format="tracwiki",
        known_comment_numbers=frozenset({1}),
    )
    assert [
        w for w in found if w["code"] == "dangling_comment_ref"
    ] == []


def _blanked_indices(text, blank):
    return {
        i
        for i, (a, b) in enumerate(zip(text, blank(text), strict=True))
        if a != b
    }


def test_the_url_blanking_covers_the_old_alternation():
    cases = list(PREFIX_CORPUS) + [
        f"see {head}https://h/t/ticket/5#comment:99 x"
        for head in URL_HEADS
    ]
    cases += [
        "ticket:1#comment:2-http+a://-]`b1.[://://ticket.",
        "ticket:1#comment:2-https://h/x#comment:3",
        "comment:1:ticket:2.https://h/x",
    ]

    def old_blank(text):
        return OLD_TICKET_COMMENT_SCOPED_RE.sub(
            checks._blank_preserving_layout, text
        )

    def new_blank(text):
        return checks._blank_union(
            text,
            (checks._TICKET_COMMENT_SCOPED_RE, checks._URL_RUN_RE),
        )

    differing = 0
    for text in cases:
        old = _blanked_indices(text, old_blank)
        new = _blanked_indices(text, new_blank)
        assert old <= new, (text, sorted(old - new))
        differing += old != new
    assert differing > 10


# `_escaped_bracket_shapes` skips the match at a `!` with no `]` before the
# next newline: neither shape can match there. Same Counter as trying every
# `!`, on strings full of quotes, numbers, `!`, `]` and newlines.

ESCAPE_PIECES = [
    "!",
    "!",
    "[",
    "]",
    "'",
    '"',
    "a",
    "0",
    "1",
    "/",
    "?",
    "#",
    " ",
    "\n",
    "(",
    ")",
    "!['a'",
    "![0",
    "['k']",
    "[12/p]",
    "`",
]


def _old_escaped_bracket_shapes(tracwiki):
    scan = checks.blank_inline_code_spans(
        checks.blank_code_fences(tracwiki)
    )
    scan = checks._blank_delimited(scan, "{{{", "}}}")
    scan = checks._blank_delimited(scan, "[[", "]]")
    shapes = checks.Counter()
    pos = scan.find("!")
    while pos != -1:
        for shape_re in (
            checks._QUOTED_BRACKET_LINK_RE,
            checks._NUMERIC_BRACKET_LINK_RE,
        ):
            shape = shape_re.match(scan, pos + 1)
            if shape is not None:
                shapes[shape.group(0)] += 1
                break
        pos = scan.find("!", pos + 1)
    return shapes


def test_escaped_bracket_shapes_equal_trying_every_bang():
    rng = random.Random(289)
    cases = [
        "!['a' ",
        "!['a' ]",
        "!['a' \n]",
        "x!['a']!['b'] ![0] ![0/p]",
        "!\n['a']",
        "![0/\n]",
    ]
    for _ in range(20000):
        parts = [
            rng.choice(ESCAPE_PIECES) for _ in range(rng.randint(0, 30))
        ]
        cases.append("".join(parts))
    counted = 0
    for text in cases:
        old = _old_escaped_bracket_shapes(text)
        counted += bool(old)
        assert checks._escaped_bracket_shapes(text) == old, repr(text)
    assert counted > 1000


# The converter hands mistune only a gate for the bracket shapes and matches
# the shape inside the rule (`_bracket_rule`), so the scan is not quadratic.
# Same output as registering the shapes' own patterns with mistune, which is
# what it did before: compared on Markdown full of shapes, links, images and
# reference definitions.

CONVERT_PIECES = [
    "!",
    "[",
    "]",
    "(",
    ")",
    "'",
    '"',
    "a",
    "0",
    "1",
    "/",
    "?",
    "#",
    " ",
    " ",
    "\n",
    "\n\n",
    ":",
    "['k']",
    "[0]",
    "[12/p]",
    "![0]",
    "[a](b)",
    "![i](u)",
    "`",
    "*",
    "_",
    "<",
    "http://h/x",
    "[1:3]",
    "[a b]",
    "- ",
    "> ",
    "[x]: y\n",
    "WikiWord",
    "\\",
]


def _convert_corpus():
    rng = random.Random(2891)
    cases = [
        "['a' ",
        "!['a' ",
        "![0/",
        "x['k'] y",
        "x[0] y[12/p] z",
        "[a](b) ['q'](u) [0](v)",
        "['a'](x)",
        "['a' b](x)",
        "[r]: u\n\n['r'] [0] [r]",
        "a:['a'] b ![0]",
        "* ['a'] [0]\n  - x[1]",
    ]
    for _ in range(3000):
        parts = [
            rng.choice(CONVERT_PIECES)
            for _ in range(rng.randint(0, 30))
        ]
        cases.append("".join(parts))
    return cases


def test_the_bracket_gate_converts_like_the_shapes_own_patterns(
    monkeypatch,
):
    corpus = _convert_corpus()

    def convert_all():
        out = []
        for text in corpus:
            result = convert_with_warnings(text)
            out.append((result.text, result.warnings))
        return out

    gated = convert_all()

    def own_patterns(md):
        prefix = converter._REGISTER_PREFIX
        md.inline.register(
            "quoted_bracket",
            prefix + converter._QUOTED_BRACKET_LINK_RE.pattern,
            converter._parse_quoted_bracket,
            before="link",
        )
        md.inline.register(
            "numeric_bracket",
            prefix + converter._NUMERIC_BRACKET_LINK_RE.pattern,
            converter._parse_numeric_bracket,
            before="link",
        )

    plugins = [
        p
        for p in converter._MISTUNE_PLUGINS
        if p
        not in (
            converter._quoted_bracket_links,
            converter._numeric_bracket_links,
        )
    ]
    monkeypatch.setattr(
        converter, "_MISTUNE_PLUGINS", [*plugins, own_patterns]
    )
    assert convert_all() == gated
    assert sum("![" in text for text, _ in gated) > 500


OLD_SINGLE_BRACKET_LINK_RE = re.compile(
    r"\[(?:[A-Za-z][\w+.-]*:)+[^\s\]]+(?:[ \t]+[^\]\n]+)?\](?!\()"
)
OLD_BARE_TRACLINK_RE = re.compile(
    r"\b(?:[A-Za-z][\w.+-]*:)?"
    rf"(?:{'|'.join(sorted(checks.TRACLINK_SCHEMES))}):"
    r"[^\s\]]+"
)

SINGLE_BRACKET_PIECES = [
    "[",
    "[",
    "]",
    "]",
    "(",
    ")",
    ":",
    ":",
    " ",
    " ",
    "\t",
    "\n",
    "\r",
    "a",
    "a:",
    "b1",
    "1",
    ".",
    "+",
    "-",
    "#",
    "/",
    "wiki:",
    "[a:",
    "[a:b ",
    "](",
]


def _single_bracket_corpus():
    rng = random.Random(289)
    cases = [
        "[a:b]",
        "[a:b c]",
        "[a:b c](d)",
        "[a:b](c)",
        "[a:]",
        "[a: b]",
        "[a:b ]",
        "[a:b  ]",
        "[a:b\n]",
        "[a:b c\n]",
        "[a:b\tc]",
        "[a:b\rc]",
        "[a:b:c:]",
        "[a:b:c: d]",
        "[[a:b]]",
        "[[a:b c]",
        "x [a:b c] y [d:e] [f:g",
        "[a:[b:c d]",
        "[a[b:c d]",
        "[1a:b]",
        "[:a]",
        "[a.b+c-d:e]",
        "[a:b c]d",
        "[a:b c]]",
        "[a:b ]]",
    ]
    for _ in range(20000):
        parts = [
            rng.choice(SINGLE_BRACKET_PIECES)
            for _ in range(rng.randint(0, 24))
        ]
        cases.append("".join(parts))
    return cases


SINGLE_BRACKET_CORPUS = _single_bracket_corpus()


def test_converter_single_bracket_sub_equals_the_old_regex_sub():
    def repl(match):
        return "<" + match.group(0) + ">"

    hits = 0
    for text in SINGLE_BRACKET_CORPUS:
        old = OLD_SINGLE_BRACKET_LINK_RE.sub(repl, text)
        hits += old != text
        assert converter._sub_single_bracket_link(repl, text) == old, (
            repr(text)
        )
    assert hits > 1000


@pytest.mark.parametrize(
    "name", ["_BARE_TRACLINK_RE", "_ESCAPE_SKIP_RE"]
)
def test_converter_scheme_regexes_equal_the_old_ones(name):
    new = getattr(converter, name)
    old = OLD_BARE_TRACLINK_RE
    if name == "_ESCAPE_SKIP_RE":
        old = re.compile(
            f"(?i:{converter._URL_IN_TEXT_RE.pattern})|{old.pattern}"
        )
    compared = 0
    for text in PREFIX_CORPUS:
        if _has_run_with_a_non_letter_head(text):
            continue
        compared += 1
        assert [m.span() for m in new.finditer(text)] == [
            m.span() for m in old.finditer(text)
        ], repr(text)
    assert compared > 300


def test_converter_bare_traclink_loses_a_prefix_inside_a_non_letter_run():
    assert OLD_BARE_TRACLINK_RE.search("1a.b:wiki:X").group(0) == (
        "b:wiki:X"
    )
    assert converter._BARE_TRACLINK_RE.search("1a.b:wiki:X").group(
        0
    ) == ("wiki:X")
    assert converter._BARE_TRACLINK_RE.search("changelog:Page") is None
    text = f"{LONG_DOTTED}:wiki:Page"
    assert converter._BARE_TRACLINK_RE.search(text).group(0) == text


LONG_DOTTED = "org." + "x" * 62 + ".tr"


def test_a_long_dotted_prefix_matches_whole():
    # A cap on the prefix length matched only its tail (`tr:#12`).
    assert len(LONG_DOTTED) > 64
    text = f"{LONG_DOTTED}:#12"
    assert checks._PREFIX_TICKET_RE.search(text).group(0) == text
    text = f"[{LONG_DOTTED}:wiki:Page]glued"
    assert [
        m.group(0) for m in checks._PREFIX_REALM_RE.finditer(text)
    ] == [f"{LONG_DOTTED}:wiki:Page"]
    text = f"{LONG_DOTTED}://host/x"
    assert checks._URL_RUN_RE.search(text).group(0) == text
    text = f"{LONG_DOTTED}:wiki:Page"
    assert checks._REALM_REF_RE.search(text).group(0) == text


def test_the_realm_reference_regex_still_starts_at_a_word_boundary():
    # Without the leading `\b`, `log:` inside `changelog:` matched.
    assert checks._REALM_REF_RE.search("changelog:Page") is None
    assert OLD_REALM_REF_RE.search("changelog:Page") is None
    assert (
        checks._REALM_REF_RE.search("changelog:wiki:X").group(0)
        == "changelog:wiki:X"
    )


# -- 3. The checks run off the event loop -------------------------------------
#
# `extract_facts`, the converter call and `build_warnings` are CPU work
# over the whole document; on the daemon's loop one hostile input froze
# every client. Each name is replaced in the handler's module by a wrapper
# that records the thread it ran on, which must not be the loop's.


def _record_threads(monkeypatch, module, names, seen=None):
    seen = {} if seen is None else seen
    for name in names:
        real = getattr(module, name)

        def wrapper(*args, _real=real, _name=name, **kwargs):
            seen.setdefault(_name, []).append(threading.get_ident())
            return _real(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)
    return seen


def _fake_client(html="<p>x</p>"):
    return SimpleNamespace(
        wiki_to_html=lambda tracwiki: html, config=SimpleNamespace()
    )


def _assert_off_loop(seen, names):
    loop_thread = threading.get_ident()
    for name in names:
        assert seen.get(name), f"{name} was not called"
        assert loop_thread not in seen[name], f"{name} ran on the loop"


async def test_convert_preview_runs_its_cpu_work_off_the_loop(
    monkeypatch,
):
    names = ["convert_with_warnings", "extract_facts", "build_warnings"]
    seen = _record_threads(monkeypatch, convert_preview, names)

    result = await convert_preview._handle_convert_preview(
        _fake_client(),
        {"content": "some **text** and [a](b)", "check_targets": False},
    )

    assert not result.isError, result.content
    _assert_off_loop(seen, names)


async def test_the_write_gate_runs_its_checks_off_the_loop(monkeypatch):
    names = ["extract_facts", "build_warnings"]
    seen = _record_threads(monkeypatch, write_gate, names)

    warnings = await write_gate._run_checks(
        _fake_client(), "some text", 10, None
    )

    assert isinstance(warnings, list)
    _assert_off_loop(seen, names)


async def test_render_check_runs_its_checks_off_the_loop(monkeypatch):
    section = RenderedSection(
        kind="page",
        ref=None,
        html="<p>x</p>",
        tracwiki="x",
        source_paired=True,
    )
    monkeypatch.setattr(
        render_check, "fetch_wiki_render", lambda *a, **k: section
    )
    names = ["extract_facts", "_section_result"]
    seen = _record_threads(monkeypatch, render_check, names)

    result = await render_check._handle_wiki_render_check(
        _fake_client(), {"page_name": "P", "check_targets": True}
    )

    assert not result.isError, result.content
    _assert_off_loop(seen, names)
    # `_probe_all_sections` and `_section_result` each parse the section.
    assert len(seen["extract_facts"]) == 2


async def test_ticket_render_check_runs_section_results_off_the_loop(
    monkeypatch,
):
    sections = [
        RenderedSection(
            kind=kind,
            ref=ref,
            html="<p>x</p>",
            tracwiki="x",
            source_paired=True,
        )
        for kind, ref in (("description", None), ("comment", "1"))
    ]
    monkeypatch.setattr(
        render_check, "fetch_ticket_sections", lambda *a, **k: sections
    )
    names = ["extract_facts", "_section_result"]
    seen = _record_threads(monkeypatch, render_check, names)

    result = await render_check._handle_ticket_render_check(
        _fake_client(), {"ticket_id": 1, "check_targets": True}
    )

    assert not result.isError, result.content
    _assert_off_loop(seen, names)
    assert len(seen["_section_result"]) == 2


async def test_wiki_file_push_runs_the_converter_and_its_checks_off_the_loop(
    monkeypatch,
):
    class StopHere(Exception):
        pass

    async def stop(*args, **kwargs):
        raise StopHere

    from trac_mcp_server.mcp.tools import wiki_file

    monkeypatch.setattr(wiki_file, "gate_or_refuse", stop)
    seen = _record_threads(
        monkeypatch,
        wiki_file,
        ["find_code_block_indentation_loss", "find_list_item_splits"],
    )
    _record_threads(
        monkeypatch, converter, ["convert_with_warnings"], seen
    )

    with pytest.raises(StopHere):
        await wiki_file._handle_push(
            _fake_client(),
            {
                "page_name": "P",
                "content": "# T\n\n- a\n  - b\n\n  c\n",
                "format": "markdown",
            },
        )

    _assert_off_loop(
        seen,
        [
            "convert_with_warnings",
            "find_code_block_indentation_loss",
            "find_list_item_splits",
        ],
    )


# -- 4. A hostile preview does not stall the event loop -----------------------
#
# `run_sync` alone does not protect the loop: a regex call holds the GIL
# for its whole run, so a quadratic one stalls every other task however it
# is called. That is why the regexes themselves had to be linear, and why
# the ratio tests above are the real guard. This is the end-to-end check:
# a ticker task runs on the loop while `convert_preview` handles hostile
# input, and the longest gap between its ticks stays far under the seconds
# the quadratic regexes held the loop for (12.5 s for an 84 KB input). The
# bound is deliberately generous (one second, against about 0.3 s of CPU
# in all and a few milliseconds per GIL switch) so a busy host does not
# flake it.

LOOP_STALL_LIMIT_S = 1.0


@pytest.mark.parametrize(
    "source",
    ["[" * 65536, "a." * 32768, "[a:" * 21845],
    ids=["lone bracket", "dotted prefix run", "scheme in a bracket"],
)
async def test_a_hostile_convert_preview_does_not_stall_the_loop(
    source,
):
    import asyncio

    loop = asyncio.get_running_loop()
    gaps = []
    stop = False

    async def ticker():
        last = loop.time()
        while not stop:
            await asyncio.sleep(0.005)
            now = loop.time()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)
    result = await convert_preview._handle_convert_preview(
        _fake_client(), {"content": source, "check_targets": False}
    )
    stop = True
    await task

    assert not result.isError, result.content
    assert len(gaps) > 5
    assert max(gaps) < LOOP_STALL_LIMIT_S, (
        f"the loop stalled for {max(gaps):.2f}s "
        f"(limit {LOOP_STALL_LIMIT_S}s)"
    )
