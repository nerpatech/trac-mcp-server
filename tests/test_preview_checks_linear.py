"""The preview checks run in linear time (Trac /trac_mcp_server #289).

Several regexes in `preview/checks.py` restarted a scan at every opener
and ran to the end of the text, so one-line hostile input cost O(n**2):
one 84 KB `convert_preview` froze the shared daemon's event loop for 12.5 s.
This file holds the ratio check over those inputs, equivalence tests of
every rewritten scan against the regex it replaced (kept here as test-local
constants), and a check that the checks run off the event loop.
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
# measured margins): CPU time of the input against the same input
# GROWTH_FACTOR times smaller, minimum of 3 runs, plus an absolute ceiling.
# Measured before the fix, at 4 KB -> 16 KB (a linear ratio is about 4):
# `x [[ y\n` 10.9x, `a {{{b\n` 15.6x, `[` 14.7x, `[x ` 12.4x, `[[x|`
# 15.1x, `a.` 17.4x, `![` / `!['` / `![0` 10-13x.

FULL_SIZE = 64 * 1024

# The small inputs take a few milliseconds, so on a busy host a minimum
# of 3 runs sometimes read 17x for a linear case; 7 runs do not.
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
    (OLD_TICKET_COMMENT_SCOPED_RE, checks._TICKET_COMMENT_SCOPED_RE),
    (OLD_PREFIX_REALM_RE, checks._PREFIX_REALM_RE),
    (OLD_REALM_REF_RE, checks._REALM_REF_RE),
]
PREFIX_IDS = [
    "prefix_ticket",
    "comment_scoped",
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
            OLD_TICKET_COMMENT_SCOPED_RE,
            checks._TICKET_COMMENT_SCOPED_RE,
            "1a.b://x",
            "b://x",
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
        "comment_scoped",
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
    assert (
        checks._TICKET_COMMENT_SCOPED_RE.search(text).group(0) == text
    )
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


def _record_threads(monkeypatch, module, names):
    seen = {}
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
