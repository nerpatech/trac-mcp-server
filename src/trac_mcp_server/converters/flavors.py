"""Markdown flavour profiles for the Markdown -> TracWiki write leg.

A flavour profile is the converter's answer to text that is not plain
CommonMark because it was pasted from somewhere else: a ChatGPT answer
(`\\( \\)` math, favicon citation residue, `utm_source` parameters) or a
copy of a rendered Gemini page (language labels instead of fences, `•`
bullets, `[1] (url)` citations). A profile is two things:

* text pre-normalisers, run over the Markdown source before mistune sees
  it (`apply_flavor`). They never touch code: every match is checked
  against `_code_mask` (fenced blocks, indented code blocks, inline code
  spans including multi-backtick and multi-line ones).
* extra mistune plugins (`flavor_plugins`), for syntax that is a parse
  rule rather than a rewrite. `TracWikiRenderer` renders their tokens.

The default, `commonmark`, is neither: its output is today's, byte for
byte (Trac /trac_mcp_server #216).
"""

import re
import string
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from mistune.plugins import Plugin

from .common import (
    blank_fenced_code,
    blank_inline_code_spans,
    indent_columns,
)

FLAVORS: tuple[str, ...] = ("commonmark", "chatgpt", "gemini")
DEFAULT_FLAVOR = "commonmark"

# A pre-normaliser: Markdown source in, (source, warnings) out.
Normaliser = Callable[[str], tuple[str, list[str]]]


def check_flavor(flavor: str) -> str:
    """Return ``flavor`` if it names a profile, else raise ValueError
    naming the valid ones."""
    if flavor not in FLAVORS:
        raise ValueError(
            f"unknown Markdown flavor {flavor!r}: valid values are "
            + ", ".join(repr(f) for f in FLAVORS)
        )
    return flavor


# The `flavor` property of the two tools that convert Markdown
# (`wiki_file_push`, `convert_preview`).
FLAVOR_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": list(FLAVORS),
    "default": DEFAULT_FLAVOR,
    "description": (
        "Markdown flavour of the source: chatgpt converts ChatGPT's "
        "\\( \\) and \\[ \\] math (code spans and {{{#!latex}}} "
        "blocks), favicon citation residue and utm_* URL parameters; "
        "gemini rebuilds a copied Gemini page's code blocks (from the "
        "'Use code with caution' marker), its bullet characters and "
        "[N] (url) citations; a flavour other than the default "
        "commonmark makes the source Markdown, so it conflicts with "
        "format tracwiki."
    ),
}


def flavor_problem(flavor: object, fmt: str) -> tuple[str, str] | None:
    """What is wrong with a ``flavor`` argument next to the declared
    ``fmt``, as ``(message, hint)`` for a validation error, or None.

    A flavour is a Markdown profile, so a non-default one cannot go with a
    TracWiki source, which is stored unconverted.
    """
    if not isinstance(flavor, str) or flavor not in FLAVORS:
        valid = ", ".join(FLAVORS)
        return (
            f"flavor must be one of {valid}, got {flavor!r}",
            f"Provide flavor as one of: {valid}.",
        )
    if flavor != DEFAULT_FLAVOR and fmt == "tracwiki":
        return (
            f"flavor '{flavor}' is a Markdown flavour profile and "
            "format='tracwiki' is not converted, so it has nothing to "
            "apply to",
            "Drop flavor, or declare the source as Markdown "
            "(format='markdown', or leave format at its default).",
        )
    return None


# A list item marker at the start of a line (CommonMark: `-`, `*`, `+`, or
# digits then `.` or `)`, then whitespace).
_LIST_ITEM_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s")


def _blank_indented_code(text: str) -> str:
    """``text`` with indented code blocks replaced by spaces, same length.

    CommonMark's rule: a line indented 4 columns or more (a tab counts to
    the next multiple of 4) is code unless it continues a paragraph (the
    line before it is neither blank nor itself indented code) or is the
    content of a list item (the nearest preceding unindented line is a
    list item).
    """
    out: list[str] = []
    after_blank = True
    after_code = False
    in_list = False
    for line in text.split("\n"):
        if not line.strip():
            out.append(line)
            after_blank, after_code = True, False
            continue
        cols = indent_columns(line)
        if cols >= 4 and (after_blank or after_code) and not in_list:
            out.append(" " * len(line))
            after_code = True
        else:
            out.append(line)
            after_code = False
        if cols == 0:
            in_list = bool(_LIST_ITEM_RE.match(line))
        after_blank = False
    return "\n".join(out)


def _code_mask(text: str) -> str:
    """``text`` with every code region replaced by ``\\x01`` fillers, same
    length and the same line breaks, so a pattern searched in the mask
    cannot match inside code and its offsets are the text's own.

    Code is a fenced block (backtick or tilde), an indented code block, or
    an inline code span, which may be multi-backtick or run over a line
    break. Built on `common.blank_fenced_code` (one scanner for both fence
    characters) and `common.blank_inline_code_spans`; the blocks are
    blanked first, so a backtick inside one is not read as a span
    delimiter.
    """
    blanked = blank_fenced_code(text)
    blanked = _blank_indented_code(blanked)
    blanked = blank_inline_code_spans(blanked, multiline=True)
    return "".join(
        "\x01" if kept != original else original
        for kept, original in zip(blanked, text, strict=True)
    )


def _sub_outside_code(
    pattern: re.Pattern[str],
    repl: Callable[[re.Match[str]], str],
    text: str,
) -> str:
    """``pattern.sub(repl, text)`` over the parts of ``text`` that are not
    code. The pattern is searched in the code mask (code is ``\\x01``
    fillers there), a match that touches a filler is skipped, and ``repl``
    gets the same match made on the real text."""
    mask = _code_mask(text)
    out: list[str] = []
    last = 0
    for m in pattern.finditer(mask):
        if "\x01" in m.group(0):
            continue
        real = pattern.match(text, m.start(), m.end())
        if real is None or real.end() != m.end():
            continue
        out.append(text[last : m.start()])
        out.append(repl(real))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


# -- chatgpt ------------------------------------------------------------------

# The ChatGPT copy button gives `\(x\)` / `\[x\]`; the dialogue text in the
# real samples has the backslashes doubled, `\\(x\\)`, with CommonMark
# escapes (`\_`) inside.
_FAVICON_RE = re.compile(
    r"^(?P<first>\[image\]\(https://www\.google\.com/s2/favicons"
    r"\?domain=(?P<domain>[^&\\)\s]+)[^)\s]*\)[ \t]*)$"
    r"(?:\n(?:[ \t]*\n)*(?P<label>[^\s\[\]()<>`*|#\\][^\[\]()<>`*|#\\]*?)"
    r"[ \t]*$)?",
    re.MULTILINE,
)
_LABEL_COUNT_RE = re.compile(r"\+\d+$")
_LABEL_MAX_CHARS = 60


def _favicon_citation(m: re.Match[str]) -> str:
    domain = unquote(m.group("domain"))
    if "://" not in domain:
        domain = "https://" + domain
    raw = m.group("label")
    if raw is not None:
        raw = raw.strip()
        count = _LABEL_COUNT_RE.search(raw)
        label = (raw[: count.start()] if count else raw).strip()
        # A label is a site name: one word, or words ending `+N`. A line
        # of prose after the favicon is not eaten.
        if (
            label
            and len(raw) <= _LABEL_MAX_CHARS
            and re.search(r"[A-Za-z]", label)
            and (" " not in label or count)
        ):
            return f"[{label}]({domain})"
    # No label line, or the line after it is prose: the link alone, named
    # by its domain (a Markdown link, not `<...>`, which the converter's
    # HTML-tag check reads as a tag), and whatever followed the favicon
    # line is kept.
    name = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", domain).rstrip(
        "/"
    )
    return f"[{name}]({domain})" + m.group(0)[len(m.group("first")) :]


def _chatgpt_citations(text: str) -> tuple[str, list[str]]:
    return _sub_outside_code(_FAVICON_RE, _favicon_citation, text), []


_URL_START_RE = re.compile(r"(?:https?|ftps?)://", re.IGNORECASE)
# A bare URL or autolink is ASCII URL characters only: no whitespace,
# backtick, `<>"`, brackets or non-ASCII text glued to it. (`\\` is there
# for the `\&` ChatGPT writes.)
_BARE_URL_CHARS = frozenset(
    string.ascii_letters + string.digits + "-._~:/?#@!$&'()*+,;=%\\"
)
_URL_TRAIL = ".,;:!?'\"*"


def _url_end(mask: str, start: int, link_target: bool) -> int:
    """Where the URL that starts at ``start`` of the code mask ends.

    A Markdown link target ends at its closing ``)``: balanced
    parentheses inside it are part of it, and whitespace (a title
    follows), a backtick or code ends it too. A bare URL or autolink is
    ASCII URL characters only; an unbalanced ``)`` ends it, and so does
    trailing punctuation, which belongs to the sentence. Text after the
    URL is never part of it."""
    depth = 0
    i = start
    while i < len(mask):
        ch = mask[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif link_target:
            if ch.isspace() or ch in "`\x01":
                break
        elif ch not in _BARE_URL_CHARS:
            break
        i += 1
    if not link_target:
        while i > start and mask[i - 1] in _URL_TRAIL:
            i -= 1
    return i


_QUERY_SEP_RE = re.compile(r"(\\?&)")


def _drop_utm(url: str) -> str:
    base, hash_, fragment = url.partition("#")
    path, question, query = base.partition("?")
    if not question:
        return url
    parts = _QUERY_SEP_RE.split(query)
    # parts: param, sep, param, sep, ... ; a param is kept with the
    # separator that came before it, the first kept one without.
    kept: list[tuple[str, str]] = []
    changed = False
    for i in range(0, len(parts), 2):
        param = parts[i]
        sep = parts[i - 1] if i else ""
        if param.lower().startswith("utm_"):
            changed = True
            continue
        kept.append((sep, param))
    if not changed:
        return url
    new_query = ""
    for n, (sep, param) in enumerate(kept):
        new_query += (sep if n else "") + param
    rebuilt = path + ("?" + new_query if new_query else "")
    return rebuilt + hash_ + fragment


def _chatgpt_utm(text: str) -> tuple[str, list[str]]:
    mask = _code_mask(text)
    out: list[str] = []
    last = 0
    consumed = 0
    for m in _URL_START_RE.finditer(mask):
        start = m.start()
        if start < consumed:
            continue
        end = _url_end(
            mask, start, mask[max(start - 2, 0) : start] == "]("
        )
        consumed = max(end, m.end())
        url = text[start:end]
        new = _drop_utm(url)
        if new != url:
            out.append(text[last:start])
            out.append(new)
            last = end
    out.append(text[last:])
    return "".join(out), []


# -- chatgpt math: mistune rules ----------------------------------------------

# CommonMark backslash escape: a backslash before ASCII punctuation.
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")

# `\(...\)` / `\\(...\\)` and, inside a paragraph, `\[...\]` / `\\[...\\]`.
# One line, non-greedy, at most 1000 characters, and never across another
# opener (`\(` or `\[`): an unclosed opener is rejected at the next one, so
# a long run of them is linear, not quadratic.
_NOT_AN_OPENER = r"(?!\\[(\[])"
_INLINE_MATH = (
    r"(?P<cgm_d>\\{1,2})\((?P<cgm_text>(?:"
    + _NOT_AN_OPENER
    + r".){1,1000}?)"
    r"(?P=cgm_d)\)"
    r"|(?P<cgm_bd>\\{1,2})\[(?P<cgm_btext>(?:"
    + _NOT_AN_OPENER
    + r".){1,1000}?)"
    r"(?P=cgm_bd)\]"
)

# A paragraph that is wholly `\[...\]` / `\\[...\\]`: up to 3 spaces of
# indent, no blank line inside, no closer inside (so two `\[x\]` lines in a
# row are two inline spans), no other opener line inside (an unclosed
# opener is rejected at the next one), at most 5000 characters, and a
# blank line or the end after the closer. That it STARTS a paragraph (the
# line before is blank, or there is none) is `_starts_a_paragraph`'s check:
# a blank line may hold any whitespace, which a lookbehind cannot say.
_BLOCK_MATH = (
    r"^ {0,3}(?P<cgb_d>\\{1,2})\[(?P<cgb_text>(?:(?!\n[ \t]*\n)"
    r"(?!\n {0,3}\\{1,2}\[)(?!(?P=cgb_d)\])[\s\S]){1,5000}?)"
    r"(?P=cgb_d)\][ \t]*$(?=\n[ \t]*\n|\n?\Z)"
)


def _starts_a_paragraph(src: str, pos: int) -> bool:
    """Whether the line starting at ``pos`` is the first of its paragraph:
    it is the first line, or the line before holds only whitespace."""
    if pos == 0:
        return True
    previous = src[src.rfind("\n", 0, pos - 1) + 1 : pos - 1]
    return not previous.strip()


def _math_text(raw: str, doubled: bool) -> str:
    """The formula. In the doubled form the backslashes are Markdown
    escapes, so `R\\_{x}` is `R_{x}`; letters after a backslash (`\\text`)
    are not escapes and stay."""
    if doubled:
        raw = _ESCAPE_RE.sub(r"\1", raw)
    return raw.strip()


def _unrepresentable(formula: str) -> bool:
    """A formula holding a backtick AND `}}}` has no inline TracWiki form:
    a code span cannot hold the backtick and `{{{ }}}` ends at the `}}}`."""
    return "`" in formula and "}}}" in formula


def inline_math_markup(text: str) -> str:
    """TracWiki for inline math: a code span, or `{{{ }}}` when the
    formula holds a backtick (a code span cannot)."""
    if "`" in text:
        return f"{{{{{{{text}}}}}}}"
    return f"`{text}`"


def _inline_math_of(m: re.Match[str]) -> str:
    """The formula of an inline math match, as `_parse_inline_math` reads
    it ("" when it is empty)."""
    if m.group("cgm_d") is not None:
        return _math_text(
            m.group("cgm_text"), len(m.group("cgm_d")) == 2
        )
    return _math_text(m.group("cgm_btext"), len(m.group("cgm_bd")) == 2)


def _parse_inline_math(
    inline: Any, m: re.Match[str], state: Any
) -> int | None:
    text = _inline_math_of(m)
    if not text or _unrepresentable(text):
        # Not math: read the opener as CommonMark does (`\\(` is a
        # backslash, `\(` an escaped bracket) instead of returning None,
        # which would drop the backslash and skip the escape rule.
        opener = m.group("cgm_d") or m.group("cgm_bd")
        bracket = "(" if m.group("cgm_d") is not None else "["
        state.append_token(
            {
                "type": "text",
                "raw": "\\" if len(opener) == 2 else bracket,
            }
        )
        return m.start() + 2
    state.append_token({"type": "inline_math", "raw": text})
    return m.end()


def _parse_block_math(
    block: Any, m: re.Match[str], state: Any
) -> int | None:
    if not _starts_a_paragraph(state.src, m.start()):
        return None
    text = _math_text(m.group("cgb_text"), len(m.group("cgb_d")) == 2)
    if not text:
        return None
    state.append_token({"type": "block_math", "raw": text})
    return min(m.end() + 1, state.cursor_max)


def chatgpt_math(md: Any) -> None:
    """mistune plugin: ChatGPT's bracket math, as `inline_math` and
    `block_math` tokens (the shape of mistune's own math plugin)."""
    md.block.register(
        "chatgpt_block_math",
        _BLOCK_MATH,
        _parse_block_math,
        before="list",
    )
    md.inline.register(
        "chatgpt_inline_math",
        _INLINE_MATH,
        _parse_inline_math,
        before="escape",
    )


# Math is protected from the converter's bracket stash (a `[word:x]` span
# there is a TracLink and would be cut out of a formula like `\[f:A\]`
# before the math rule runs): `protect_syntax` swaps each math span for a
# sentinel and the stash runs. A display paragraph is then put back as
# written, for mistune's block rule. An inline formula is NOT: like a code
# span it is hidden from the parser (a `|` in it must not split a table
# cell) and rendered when the output is restored, so `restore_syntax` hands
# it to the stash's placeholder list.
_MATH_BLOCK_RE = re.compile(_BLOCK_MATH, re.MULTILINE)
_MATH_INLINE_RE = re.compile(_INLINE_MATH)
_MATH_SENTINEL_RE = re.compile(r"\x00MT(\d+)\x00")

# What was swapped out: (the text as written, its rendering for an inline
# formula or None for a display paragraph).
Protected = tuple[str, str | None]


def _math_spans(
    text: str, mask: str
) -> list[tuple[int, int, str, str]]:
    """The math of ``text`` outside code, in order: ``(start, end, kind,
    formula)``, kind "block" (a display paragraph), "inline" or "bad" (an
    inline formula with no TracWiki form)."""
    spans: list[tuple[int, int, str, str]] = []
    for m in _MATH_BLOCK_RE.finditer(mask):
        if "\x01" not in m.group(0) and _starts_a_paragraph(
            text, m.start()
        ):
            spans.append((m.start(), m.end(), "block", ""))
    inline_mask = list(mask)
    for start, end, _, _ in spans:
        inline_mask[start:end] = "\x01" * (end - start)
    for m in _MATH_INLINE_RE.finditer("".join(inline_mask)):
        if "\x01" in m.group(0):
            continue
        formula = _inline_math_of(m)
        if formula:
            kind = "bad" if _unrepresentable(formula) else "inline"
            spans.append((m.start(), m.end(), kind, formula))
    spans.sort(key=lambda span: span[0])
    return spans


def _protect_math(text: str) -> tuple[str, list[Protected]]:
    spans = [
        span
        for span in _math_spans(text, _code_mask(text))
        if span[2] != "bad"
    ]
    out: list[str] = []
    saved: list[Protected] = []
    last = 0
    for start, end, kind, formula in spans:
        out.append(text[last:start])
        out.append(f"\x00MT{len(saved)}\x00")
        rendered = (
            inline_math_markup(formula) if kind == "inline" else None
        )
        saved.append((text[start:end], rendered))
        last = end
    out.append(text[last:])
    return "".join(out), saved


def _chatgpt_math_notes(text: str) -> tuple[str, list[str]]:
    """A warning for each formula left as written (see `_unrepresentable`);
    the text is not changed."""
    notes = [
        f"chatgpt: formula left as written, TracWiki has no inline form "
        f"for a formula holding both a backtick and '}}}}}}': "
        f"{formula[:40]!r}"
        for _, _, kind, formula in _math_spans(text, _code_mask(text))
        if kind == "bad"
    ]
    return text, notes


_PROTECTORS: dict[str, Callable[[str], tuple[str, list[Protected]]]] = {
    "chatgpt": _protect_math,
}


def protect_syntax(
    text: str, flavor: str
) -> tuple[str, list[Protected]]:
    """Swap the flavour's own syntax for sentinels before the converter's
    bracket stash, so that stash cannot cut it up; ``(text, [])`` for a
    flavour with none. Undo with `restore_syntax`."""
    check_flavor(flavor)
    protect = _PROTECTORS.get(flavor)
    return protect(text) if protect else (text, [])


def restore_syntax(
    text: str,
    saved: list[Protected],
    hide: list[str] | None = None,
) -> str:
    """Put back what `protect_syntax` swapped out: everything as written,
    except that with ``hide`` (the stash's placeholder list) an inline
    formula is appended there, rendered, and replaced by that
    placeholder's sentinel."""
    if not saved:
        return text

    def put_back(m: re.Match[str]) -> str:
        original, rendered = saved[int(m.group(1))]
        if hide is None or rendered is None:
            return original
        hide.append(rendered)
        return f"\x00WK{len(hide) - 1}\x00"

    return _MATH_SENTINEL_RE.sub(put_back, text)


# -- gemini -------------------------------------------------------------------

GEMINI_CODE_MARKER = "Use code with caution."

# The language label the Gemini page shows above a code block.
_GEMINI_LANGUAGES = frozenset(
    "html xml css javascript js typescript ts json yaml yml toml ini "
    "bash sh shell zsh console powershell python py java kotlin c cpp "
    "c++ csharp c# go rust ruby php sql swift r perl lua dockerfile "
    "makefile diff text plaintext markdown".split()
)


def _fence_info(label: str) -> str:
    """The fence info string for a language label, lowercased (the label is
    matched case-insensitively, and the converter maps lowercase names:
    `Python` would be `{{{#!Python`), except ``html``: the converter passes ``html`` through as `{{{#!html`, and
    Trac's `html` processor renders the markup instead of showing it, so
    a block of HTML source would vanish into the page (measured on
    /trac_test). `text/html` is the Pygments listing of the same source."""
    label = label.lower()
    return "text/html" if label == "html" else label


def _is_fence_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(("```", "~~~"))


def _gemini_code_blocks(text: str) -> tuple[str, list[str]]:
    """Rebuild fenced blocks from `<language>` ... `Use code with
    caution.` runs.

    Each marker takes the NEAREST language line above it that has code
    between it and the marker (a language word directly above the marker
    is code, so it is skipped), and only when no fence line lies between
    them (so the code of a rebuilt block never holds one, and a plain
    fence always closes it). The label and the marker start at column 0;
    the label must be outside code. A label is used by one marker only,
    and the scan back stops at the previous marker, so the whole pass is
    linear. No block is ever empty.
    """
    lines = text.split("\n")
    masked = _code_mask(text).split("\n")
    out: list[str] = []
    emitted = 0
    floor = 0
    rebuilt = 0
    for marker, line in enumerate(lines):
        if (
            line.rstrip() != GEMINI_CODE_MARKER
            or masked[marker] != line
        ):
            continue
        label = None
        has_code = False
        for k in range(marker - 1, floor - 1, -1):
            above = lines[k]
            if _is_fence_line(above):
                break
            if (
                above == above.lstrip()
                and above.rstrip().lower() in _GEMINI_LANGUAGES
                and masked[k] == above
                and has_code
            ):
                label = k
                break
            # Anything else is code, a language word included when it
            # sits directly above the marker: the block needs a line.
            if above.strip():
                has_code = True
        floor = marker + 1
        if label is None:
            continue
        out.extend(lines[emitted:label])
        if out and out[-1].strip():
            out.append("")
        out.append("```" + _fence_info(lines[label].strip()))
        out.extend(lines[label + 1 : marker])
        out.append("```")
        emitted = marker + 1
        rebuilt += 1
    if not rebuilt:
        return text, []
    out.extend(lines[emitted:])
    warnings = [
        f"gemini: rebuilt {rebuilt} code block(s) from "
        f"'{GEMINI_CODE_MARKER}' markers"
    ]
    return "\n".join(out), warnings


_BULLET_RE = re.compile(r"^[ \t]*•[ \t]*", re.MULTILINE)
_CITATION_RE = re.compile(r"\[(\d+)\] \((https?://[^\s)]+)\)")


def _gemini_bullets(text: str) -> tuple[str, list[str]]:
    return _sub_outside_code(_BULLET_RE, lambda m: "- ", text), []


def _gemini_citations(text: str) -> tuple[str, list[str]]:
    return (
        _sub_outside_code(
            _CITATION_RE,
            lambda m: f"[{m.group(1)}]({m.group(2)})",
            text,
        ),
        [],
    )


# -- profiles -----------------------------------------------------------------

_NORMALISERS: dict[str, tuple[Normaliser, ...]] = {
    "commonmark": (),
    "chatgpt": (_chatgpt_citations, _chatgpt_utm, _chatgpt_math_notes),
    "gemini": (_gemini_code_blocks, _gemini_bullets, _gemini_citations),
}

_PLUGINS: dict[str, tuple[Plugin, ...]] = {
    "commonmark": (),
    "chatgpt": (chatgpt_math,),
    "gemini": (),
}


def apply_flavor(text: str, flavor: str) -> tuple[str, list[str]]:
    """Run the flavour's pre-normalisers over Markdown source.

    Returns ``(text, warnings)``; ``commonmark`` returns ``text`` itself.
    Raises ValueError for a flavour that is not in `FLAVORS`.
    """
    check_flavor(flavor)
    warnings: list[str] = []
    if flavor != DEFAULT_FLAVOR:
        # CRLF and lone CR to LF, as the transport does elsewhere: the
        # patterns end at `$`, which never matches before a `\r`. The
        # default keeps its bytes.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    for normalise in _NORMALISERS[flavor]:
        text, found = normalise(text)
        warnings.extend(found)
    return text, warnings


def flavor_plugins(flavor: str) -> list[Plugin]:
    """The extra mistune plugins of a flavour (none for the default)."""
    check_flavor(flavor)
    return list(_PLUGINS[flavor])


def has_gemini_code_marker(text: str) -> bool:
    """Whether a line outside code is exactly `Use code with caution.`,
    at column 0 as the rebuild reads it."""
    if GEMINI_CODE_MARKER not in text:
        return False
    masked = _code_mask(text).split("\n")
    return any(
        line.rstrip() == GEMINI_CODE_MARKER and masked[i] == line
        for i, line in enumerate(text.split("\n"))
    )
