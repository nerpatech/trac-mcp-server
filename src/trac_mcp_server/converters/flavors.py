"""Markdown flavour profiles for the Markdown -> TracWiki write leg.

A flavour profile is the converter's answer to text that is not plain
CommonMark because it was pasted from somewhere else: a ChatGPT answer
(`\\( \\)` math, favicon citation residue, `utm_source` parameters) or a
copy of a rendered Gemini page (language labels instead of fences, `•`
bullets, `[1] (url)` citations). A profile is two things:

* text pre-normalisers, run over the Markdown source before mistune sees
  it (`apply_flavor`). They never touch a fenced code block or an inline
  code span: every match is checked against `_markdown_verbatim_mask`, the
  same mask the footnote check reads.
* extra mistune plugins (`flavor_plugins`), for syntax that is a parse
  rule rather than a rewrite. `TracWikiRenderer` renders their tokens.

The default, `commonmark`, is neither: its output is today's, byte for
byte (Trac /trac_mcp_server #216).
"""

import re
from collections.abc import Callable
from typing import Any

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


def _mask(text: str) -> str:
    # Imported here: markdown_to_tracwiki imports this module at load.
    from .markdown_to_tracwiki import _markdown_verbatim_mask

    return _markdown_verbatim_mask(text)


def _sub_outside_code(
    pattern: re.Pattern[str],
    repl: Callable[[re.Match[str]], str],
    text: str,
) -> str:
    """``pattern.sub(repl, text)`` over the parts of ``text`` that are not
    code. The pattern is searched in the code mask (code bodies are
    ``\\x01`` fillers there), a match that touches a filler is skipped, and
    ``repl`` gets the same match made on the real text."""
    mask = _mask(text)
    out: list[str] = []
    last = 0
    for m in pattern.finditer(mask):
        if "\x01" in m.group(0):
            continue
        real = pattern.match(text, m.start())
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
    domain = m.group("domain")
    if "://" not in domain:
        domain = "https://" + domain
    raw = m.group("label")
    if raw is not None:
        raw = raw.strip()
        count = _LABEL_COUNT_RE.search(raw)
        label = raw[: count.start()] if count else raw
        # A label is a site name: one word, or words ending `+N`. A line
        # of prose after the favicon is not eaten.
        if (
            label
            and len(raw) <= _LABEL_MAX_CHARS
            and re.search(r"[A-Za-z]", label)
            and (" " not in label or count)
        ):
            return f"[{label}]({domain})"
    # No label line, or the line after it is prose: the link alone, and
    # whatever followed the favicon line is kept.
    return f"<{domain}>" + m.group(0)[len(m.group("first")) :]


def _chatgpt_citations(text: str) -> tuple[str, list[str]]:
    return _sub_outside_code(_FAVICON_RE, _favicon_citation, text), []


_URL_RE = re.compile(r"(?:https?|ftps?)://[^\s<>\[\]]+", re.IGNORECASE)
_URL_TRAIL = ".,;:!?'\"*"


def _split_url_tail(url: str) -> tuple[str, str]:
    """(url, tail) where the tail is trailing punctuation, or a ``)`` with
    no ``(`` in the URL to close, that belongs to the sentence."""
    end = len(url)
    while end:
        ch = url[end - 1]
        if ch in _URL_TRAIL or (
            ch == ")" and url[:end].count(")") > url[:end].count("(")
        ):
            end -= 1
        else:
            break
    return url[:end], url[end:]


_QUERY_SEP_RE = re.compile(r"(\\?&)")


def _drop_utm(m: re.Match[str]) -> str:
    url, tail = _split_url_tail(m.group(0))
    base, hash_, fragment = url.partition("#")
    path, question, query = base.partition("?")
    if not question:
        return m.group(0)
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
        return m.group(0)
    new_query = ""
    for n, (sep, param) in enumerate(kept):
        new_query += (sep if n else "") + param
    rebuilt = path + ("?" + new_query if new_query else "")
    return rebuilt + hash_ + fragment + tail


def _chatgpt_utm(text: str) -> tuple[str, list[str]]:
    return _sub_outside_code(_URL_RE, _drop_utm, text), []


# -- chatgpt math: mistune rules ----------------------------------------------

# CommonMark backslash escape: a backslash before ASCII punctuation.
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")

# `\(...\)` / `\\(...\\)` and, inside a paragraph, `\[...\]` / `\\[...\\]`.
# One line, non-greedy; the closer repeats the opener's backslashes.
_INLINE_MATH = (
    r"(?P<cgm_d>\\{1,2})\((?P<cgm_text>.+?)(?P=cgm_d)\)"
    r"|(?P<cgm_bd>\\{1,2})\[(?P<cgm_btext>.+?)(?P=cgm_bd)\]"
)

# A paragraph that is wholly `\[...\]` / `\\[...\\]`: starts a paragraph
# (start of text or after a blank line), up to 3 spaces of indent, no
# blank line inside, and a blank line or the end after the closer. The
# content holds no closer, so two `\[x\]` lines in a row are two inline
# spans, not one block.
_BLOCK_MATH = (
    r"^(?:(?<![\s\S])|(?<=\n\n)|(?<=\n[ \t]\n)) {0,3}"
    r"(?P<cgb_d>\\{1,2})\[(?P<cgb_text>(?:(?!\n[ \t]*\n)(?!(?P=cgb_d)\])[\s\S])+?)"
    r"(?P=cgb_d)\][ \t]*$(?=\n[ \t]*\n|\n?\Z)"
)


def _math_text(raw: str, doubled: bool) -> str:
    """The formula. In the doubled form the backslashes are Markdown
    escapes, so `R\\_{x}` is `R_{x}`; letters after a backslash (`\\text`)
    are not escapes and stay."""
    if doubled:
        raw = _ESCAPE_RE.sub(r"\1", raw)
    return raw.strip()


def _parse_inline_math(
    inline: Any, m: re.Match[str], state: Any
) -> int | None:
    if m.group("cgm_d") is not None:
        raw, doubled = m.group("cgm_text"), len(m.group("cgm_d")) == 2
    else:
        raw, doubled = m.group("cgm_btext"), len(m.group("cgm_bd")) == 2
    text = _math_text(raw, doubled)
    if not text:
        return None
    state.append_token({"type": "inline_math", "raw": text})
    return m.end()


def _parse_block_math(
    block: Any, m: re.Match[str], state: Any
) -> int | None:
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


# -- gemini -------------------------------------------------------------------

GEMINI_CODE_MARKER = "Use code with caution."

# The language label the Gemini page shows above a code block.
_GEMINI_LANGUAGES = frozenset(
    "html xml css javascript js typescript ts json yaml yml toml ini "
    "bash sh shell zsh console powershell python py java kotlin c cpp "
    "c++ csharp c# go rust ruby php sql swift r perl lua dockerfile "
    "makefile diff text plaintext markdown".split()
)


def _fence_for(code: list[str]) -> str:
    """A backtick fence longer than any fence-like line in the code."""
    longest = 2
    for line in code:
        stripped = line.lstrip()
        run = len(stripped) - len(stripped.lstrip("`"))
        if run >= 3:
            longest = max(longest, run)
    return "`" * (longest + 1)


def _fence_info(label: str) -> str:
    """The fence info string for a language label, as written, except
    ``html``: the converter passes ``html`` through as `{{{#!html`, and
    Trac's `html` processor renders the markup instead of showing it, so
    a block of HTML source would vanish into the page (measured on
    /trac_test). `text/html` is the Pygments listing of the same source."""
    return "text/html" if label.lower() == "html" else label


def _gemini_code_blocks(text: str) -> tuple[str, list[str]]:
    """Rebuild fenced blocks from `<language>` ... `Use code with
    caution.` runs. The mask is recomputed after each block, so a
    fence-like line in the code cannot hide what follows."""
    rest = text.split("\n")
    head: list[str] = []
    rebuilt = 0
    while True:
        masked = _mask("\n".join(rest)).split("\n")
        opened = None
        for i, line in enumerate(rest):
            if (
                masked[i] == line
                and line.strip().lower() in _GEMINI_LANGUAGES
            ):
                closed = next(
                    (
                        k
                        for k in range(i + 1, len(rest))
                        if rest[k].strip() == GEMINI_CODE_MARKER
                    ),
                    None,
                )
                if closed is None:
                    # No later label has a closing marker either.
                    break
                opened = (i, closed)
                break
        if opened is None:
            break
        i, closed = opened
        head.extend(rest[:i])
        if head and head[-1].strip():
            head.append("")
        code = rest[i + 1 : closed]
        fence = _fence_for(code)
        head.append(fence + _fence_info(rest[i].strip()))
        head.extend(code)
        head.append(fence)
        rest = rest[closed + 1 :]
        rebuilt += 1
    if not rebuilt:
        return text, []
    warnings = [
        f"gemini: rebuilt {rebuilt} code block(s) from "
        f"'{GEMINI_CODE_MARKER}' markers"
    ]
    return "\n".join(head + rest), warnings


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
    "chatgpt": (_chatgpt_citations, _chatgpt_utm),
    "gemini": (_gemini_code_blocks, _gemini_bullets, _gemini_citations),
}

_PLUGINS: dict[str, tuple[Callable[[Any], None], ...]] = {
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
    for normalise in _NORMALISERS[flavor]:
        text, found = normalise(text)
        warnings.extend(found)
    return text, warnings


def flavor_plugins(flavor: str) -> list[Callable[[Any], None]]:
    """The extra mistune plugins of a flavour (none for the default)."""
    check_flavor(flavor)
    return list(_PLUGINS[flavor])


def has_gemini_code_marker(text: str) -> bool:
    """Whether a line outside code is exactly `Use code with caution.`"""
    masked = _mask(text).split("\n")
    return any(
        line.strip() == GEMINI_CODE_MARKER and masked[i] == line
        for i, line in enumerate(text.split("\n"))
    )
