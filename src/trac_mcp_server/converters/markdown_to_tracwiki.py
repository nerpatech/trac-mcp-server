"""Markdown to TracWiki conversion using mistune AST rendering."""

import html.entities
import re
from collections.abc import Callable
from typing import Any, Literal, cast

import mistune
from mistune.helpers import parse_link, parse_link_label
from mistune.util import unikey

from .common import (
    _LIST_MARKER_RE,
    SCHEME_RE,
    TRACLINK_SCHEMES,
    ConversionResult,
    fence_processor,
    trac_continuation_bound,
)
from .flavors import (
    DEFAULT_FLAVOR,
    apply_flavor,
    flavor_plugins,
    has_gemini_code_marker,
    inline_math_markup,
    protect_syntax,
    restore_syntax,
)

# GitHub-style heading slug, mirrored from auto-pm's docs_linkcheck rule
# (lowercase, whitespace runs → single dash, drop everything that isn't
# alphanumeric / dash / underscore). Inline TracWiki markers produced by
# the renderer pipeline (`backticks`, `'''bold'''`, `''italic''`) are
# stripped before the rule runs so the slug derives from the *visible*
# heading text, not its render-side decoration.
_SLUG_DROP_RE = re.compile(r"[^\w\- ]+")
_SLUG_WS_RE = re.compile(r"\s+")

# Double-bracket syntax ([[Page]], [[Page|Label]], [[TOC]], ...) typed
# directly in Markdown source. Stashed before parsing starts, so a
# macro/link name that happens to be CamelCase-shaped (e.g. [[PageOutline]])
# never has the CamelCase pass below stuff a `!` inside the brackets.
_BRACKET_SYNTAX_RE = re.compile(r"\[\[[^\]\n]*\]\]")
_BRACKET_SYNTAX_STOP_RE = re.compile(r"[\]\n]")


def _sub_bracket_syntax(repl, text: str) -> str:
    """`_BRACKET_SYNTAX_RE.sub(repl, text)` in linear time (#289)."""
    parts: list[str] = []
    done = 0
    pos = 0
    while True:
        start = text.find("[[", pos)
        if start == -1:
            break
        stop = _BRACKET_SYNTAX_STOP_RE.search(text, start + 2)
        if stop is None:
            break
        if text.startswith("]]", stop.start()):
            match = _BRACKET_SYNTAX_RE.match(text, start)
            # Cannot miss: no `]` or newline in (start+2, stop), and
            # `]]` at stop -- exactly what the pattern requires.
            assert match is not None
            parts.append(text[done:start])
            parts.append(repl(match))
            done = pos = match.end()
        else:
            pos = stop.start()
    parts.append(text[done:])
    return "".join(parts)


# Info string of the fence the removed TracWiki -> Markdown converter wrote
# around source it could not convert (#214 deleted that converter). Markdown
# pulled from Trac before #210 still carries it. Converting it would write
# `{{{#!tracwiki-unconverted`, a processor Trac does not have: an error box
# on the page and no warning, so `block_code` refuses it instead.
_LEGACY_EXPORT_FENCE_INFO = "tracwiki-unconverted"

# Sentinel used by `_stash_bracket_syntax`/`_restore_bracket_syntax`.
_PLACEHOLDER_RE = re.compile(r"\x00WK(\d+)\x00")

# Bare inline code span (`` `...` ``) typed directly in the Markdown
# source. Stashed the same way `[[...]]` is below -- must run on the raw
# source before mistune ever sees a backtick. mistune's own table-row
# splitter (`CELL_SPLIT` in its bundled `table` plugin) has no concept of
# code spans: it splits a row on every literal "|" it finds, including
# ones meant to sit *inside* a backticked cell documenting table markup
# itself (e.g. `` `||||` ``), so the cell count no longer matches the
# header and the whole block silently falls back to a plain paragraph
# instead of a table (ticket #45). Stashing the span's body means the
# pipe characters simply aren't there yet when mistune's block-level
# table matcher runs; the original body -- pipes and all -- is restored
# verbatim afterwards, once the table's cell structure has already been
# fixed using the correct (higher) column count. Only single-backtick
# spans are handled.
_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")

# Trac's own WikiCamelCase auto-link pattern: a word with two or more
# "humps" (uppercase letter, then a lowercase run), e.g. WiFi, LoRa,
# PageOutline. Deliberately excludes pure-acronym runs like IPAddress (no
# lowercase between the leading capitals) to match Trac's real behavior.
# `(?<!!)` skips words the author already escaped by hand.
_CAMELCASE_RE = re.compile(r"(?<!!)\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b")

# Matches exactly the "!" a `_CAMELCASE_RE` substitution would have added
# (a "!" directly before a CamelCase-shaped word), so `link()` can undo it
# for its own `text` argument -- see `_unescape_camelcase`.
_CAMELCASE_ESCAPE_RE = re.compile(r"!(?=[A-Z][a-z]+(?:[A-Z][a-z]+)+\b)")

# A bare absolute URL sitting directly in prose (no Markdown link syntax
# around it, no backticks). A resolved `[text](url)`/autolink never
# reaches `text()` at all -- mistune hands its url straight to `link()`
# -- so any URL text() sees here is one mistune left as literal prose. A
# CamelCase-shaped path segment inside it is part of the address, not a
# word Trac's WikiCamelCase grammar should ever see (ticket #44).
_URL_IN_TEXT_RE = re.compile(r"(?:https?|ftps?)://\S+", re.IGNORECASE)

# A bare, unbracketed TracLink sitting directly in prose -- the form
# `Rules/trac/PreferInterTracLinks` on the auto_pm store makes the
# *preferred* way to write a cross-instance reference, e.g.
# `auto_pm:wiki:Rules/trac/RenderVerify` or plain `wiki:SomePage`. Trac
# auto-links these without any bracket syntax, so a WikiPageNames-shaped
# final segment escaped here becomes part of the address and resolves to
# a page that does not exist -- the same destructive case as the bare-URL
# and single-bracket forms above, reached through the one form neither of
# them covers (ticket #44, reopened).
#
# Anchored on a *known* Trac realm (`TRACLINK_SCHEMES`) rather than on
# "anything colon-shaped", with the InterTrac prefix as the optional
# leading segment. That is what keeps the skip from swallowing ordinary
# prose: a CamelCase word must still be escaped when it stands alone
# (`RenderVerify`) or trails a non-realm colon, and only `realm:target` /
# `prefix:realm:target` buys its way out. The two-segment InterTrac page
# form (`prefix:PageName`, no realm) is deliberately NOT matched -- it is
# textually indistinguishable from prose such as `Note:SomeThing`, and
# the store's own rule requires the explicit `wiki:` realm for a wiki
# page anyway.
#
# The prefix starts only at the head of its `[\w.+-]` run (`(?<![\w.+-])`;
# the `\b` stays for the no-prefix branch, or `log:` would match inside
# `changelog:`). Unguarded, `a.a.a...` restarted the scan at every `a` and
# ran to the end of the run: quadratic (ticket #289). The same guard as in
# `preview/checks.py`, with the same accepted differences: a prefix is no
# longer recognised when `-`, `.` or `+` comes right before it
# (`a -x:wiki:P`), when its run is headed by a digit, `_` or a non-ASCII
# letter (`1a.b:wiki:X` read `b:wiki:X`, `é.x:wiki:P` read `x:wiki:P`;
# the realm alone still matches). The store corpus showed no difference.
_BARE_TRACLINK_RE = re.compile(
    r"\b(?:(?<![\w.+-])[A-Za-z][\w.+-]*:)?"
    rf"(?:{'|'.join(sorted(TRACLINK_SCHEMES))}):"
    r"[^\s\]]+"
)

# Every span `text()` must hand through unescaped, in one pass so the
# scan stays single-shot and the two forms can't overlap-clobber each
# other. The URL branch keeps its own scoped `(?i:...)` -- schemes are
# case-insensitive, Trac's realm names are not, and a global IGNORECASE
# here would quietly let `Wiki:SomePage` (prose, not a link) out of the
# escape pass.
_ESCAPE_SKIP_RE = re.compile(
    f"(?i:{_URL_IN_TEXT_RE.pattern})|{_BARE_TRACLINK_RE.pattern}"
)

# An unresolved single-bracket TracWiki/InterTrac link typed directly in
# Markdown source: `[scheme:target label]`, `[prefix:realm:target label]`,
# `[url]`, `[url label]`. Not valid Markdown link syntax (no trailing
# "(url)"), so mistune would otherwise render the brackets and their
# contents as literal text -- both the target and the label arriving at
# text() as plain prose, where the CamelCase pass would turn a
# WikiPageNames-shaped word in the target into a dead link, and put a
# stray "!" in the visible label (ticket #44).
#
# Stashed on the *raw* source, like `[[...]]` below, rather than handled
# from within text(): mistune's link-scanner splits an unresolved "[...]"
# into separate text() fragments (a lone "[" as one call, the rest as
# another), so the target-plus-label span never arrives at text() as one
# contiguous string to recognize -- the brackets have to be protected
# before that split happens. The `(?!\()` guard excludes anything
# immediately followed by "(", which is a real Markdown link whose label
# merely looks scheme-shaped (`[wiki:Page](url)`) -- must reach mistune's
# link parser untouched.
_SINGLE_BRACKET_LINK_RE = re.compile(
    r"\[(?:[A-Za-z][\w+.-]*:)+[^\s\]]+(?:[ \t]+[^\]\n]+)?\](?!\()"
)
_SINGLE_BRACKET_SCHEME_RE = re.compile(r"[A-Za-z][\w+.-]*")
_SINGLE_BRACKET_TARGET_STOP_RE = re.compile(r"[\s\]]")
_SINGLE_BRACKET_LABEL_STOP_RE = re.compile(r"[\]\n]")


def _sub_single_bracket_link(repl, text: str) -> str:
    r"""`_SINGLE_BRACKET_LINK_RE.sub(repl, text)` in linear time (#289).

    The regex restarts at every `[` and reads to the end of a whitespace-
    free run, so `[a:` repeated was quadratic (16 s at 48 KB). What an
    opener at `start` needs, and why a failed one lets the scan skip ahead:

    * A scheme group first: a letter, a run of `[\w+.-]`, then `:`. The
      run holds no `[`, so a failure here (no letter, or the run ends
      without `:`) resumes at the character that ended it: no `[` lies
      between.
    * Then a target `[^\s\]]+` of at least one character. It cannot
      backtrack to a shorter one (the next element is `]` or whitespace,
      neither of which the target holds), and every further scheme group
      is inside it (their characters are target characters), so it ends
      at `stop`, the first whitespace or `]` after the first group.
    * Then either `]` at `stop`, or a space or tab there and a label
      `[^\]\n]+` of at least one character that ends at the first `]` or
      newline `end` after it (a shorter label would have to end on a
      character that is not `]`; one more whitespace in the run makes no
      difference). `(?!\()` then refuses a `(` right after that `]`.

    Whether that last step succeeds depends on `stop` alone. Every opener
    between `start` and `stop` has the same `stop`, so if the step fails
    for `start` it fails for all of them, whatever their scheme groups
    look like, and the scan resumes at `stop` (never a `[`). The label's
    end is remembered: one scan serves every `stop` inside it. If there is
    no whitespace or `]` after the first group, no later opener has one
    either, and the scan ends. A match is built by the regex itself at the
    opener found here.
    """
    parts: list[str] = []
    done = 0
    pos = 0
    label_from = label_to = -1
    while True:
        start = text.find("[", pos)
        if start == -1:
            break
        scheme = _SINGLE_BRACKET_SCHEME_RE.match(text, start + 1)
        if scheme is None:
            pos = start + 1
            continue
        if not text.startswith(":", scheme.end()):
            pos = scheme.end()
            continue
        first_group_end = scheme.end() + 1
        found = _SINGLE_BRACKET_TARGET_STOP_RE.search(
            text, first_group_end
        )
        if found is None:
            break
        stop = found.start()
        ok = False
        if stop > first_group_end:
            if text[stop] == "]":
                ok = not text.startswith("(", stop + 1)
            elif text[stop] in " \t":
                if not label_from <= stop + 1 <= label_to:
                    end_found = _SINGLE_BRACKET_LABEL_STOP_RE.search(
                        text, stop + 1
                    )
                    label_from = stop + 1
                    label_to = (
                        end_found.start() if end_found else len(text)
                    )
                ok = (
                    label_to > stop + 1
                    and text[label_to : label_to + 1] == "]"
                    and not text.startswith("(", label_to + 1)
                )
        if ok:
            match = _SINGLE_BRACKET_LINK_RE.match(text, start)
            # Cannot miss: the steps above are the pattern's.
            assert match is not None
            parts.append(text[done:start])
            parts.append(repl(match))
            done = pos = match.end()
        else:
            pos = stop
    parts.append(text[done:])
    return "".join(parts)


# The character references CommonMark decodes in text (spec section
# "Entity and numeric character references"): a name ending in ";", or
# "&#" with 1-7 decimal / "&#x" with 1-6 hex digits, ending in ";".
# mistune 3.2 leaves them raw in text tokens, and Trac would show them
# literally (ticket #239). `html.unescape` is not used: it also decodes
# semicolon-less legacy names (`&copy 2020`) that CommonMark keeps literal,
# and the legacy prefix of an unknown name (`&notanentity;` ->
# `¬anentity;`). A name is decoded only when `name;` is a key of HTML5's
# entity table; a number follows CommonMark (code point 0, above U+10FFFF
# or a surrogate gives U+FFFD, no windows-1252 remapping). The decoded
# text goes into TracWiki as typed, so it is used only when every
# character in it is Trac-safe (`_is_trac_safe`); otherwise the reference
# stays as typed, which Trac shows literally.
_CHAR_REF_RE = re.compile(
    r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"
)

# ASCII characters a decoded reference may produce. Every other ASCII
# character (# ' ` | { } [ ] ! = * - ^ , ~ _ / : and so on) can form
# TracWiki markup, and every control or line-breaking character can break
# Trac's line-based syntax (a heading, a table row, a list item) or vanish,
# so a reference to one stays literal, exactly as it rendered before #239.
# `&`, `<`, `>` and `"` are safe because Trac escapes them in text.
_TRAC_SAFE_ASCII = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    ' &<>"'
)


def _is_trac_safe(decoded: str) -> bool:
    """True when every character of `decoded` is safe in TracWiki text.

    Code points from U+00A0 up are safe, except the line and paragraph
    separators U+2028 and U+2029; below that only `_TRAC_SAFE_ASCII` is.
    """
    for ch in decoded:
        cp = ord(ch)
        if cp < 0xA0:
            if ch not in _TRAC_SAFE_ASCII:
                return False
        elif cp in (0x2028, 0x2029):
            return False
    return True


def _decode_char_ref(m: re.Match[str]) -> str:
    """Replacement for one `_CHAR_REF_RE` match (see the comments there)."""
    ref = m.group(0)
    if ref[1] == "#":
        digits = ref[2:-1]
        if digits[0] in "xX":
            cp = int(digits[1:], 16)
        else:
            cp = int(digits)
        if cp == 0 or cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
            cp = 0xFFFD
        decoded = chr(cp)
    else:
        named = html.entities.html5.get(ref[1:])
        if named is None:
            return ref
        decoded = named
    return decoded if _is_trac_safe(decoded) else ref


def _decode_char_refs(text: str) -> str:
    """Decode CommonMark entity and numeric references in a text token."""
    if "&" not in text:
        return text
    return _CHAR_REF_RE.sub(_decode_char_ref, text)


def _unescape_camelcase(text: str) -> str:
    """Undo `text()`'s CamelCase `!`-escaping for a link's display text.

    A TracWiki link's label (the second half of ``[url text]``) is opaque,
    literal text -- Trac never re-parses it for WikiFormatting, so it can
    never trigger the broken auto-link `text()` defends against (ticket
    #27). But `text()` runs on a link's inline children before `link()`
    ever sees them, with no way to know in advance that this particular
    text is headed into a link label rather than plain prose, so any
    CamelCase word in a link's text (e.g. the "SomePage" in `[SomePage]
    (wiki:SomePage)`, or the text mistune's autolink expansion sets equal
    to the url) arrives here already escaped. `link()` undoes it before
    using `text` for anything -- both so the visible label doesn't show a
    spurious "!", and so a `text == url` comparison used to detect
    autolinks (`<wiki:Page>` -> `[wiki:Page]`, not a doubled target) isn't
    broken by the two sides no longer matching.
    """
    return _CAMELCASE_ESCAPE_RE.sub("", text)


def _stash_bracket_syntax(markdown_text: str) -> tuple[str, list[str]]:
    """Replace `[[...]]` spans with sentinel placeholders.

    Must run on the *raw* Markdown source, before mistune parses it.
    mistune's link-scanning splits a "[...]" span that doesn't resolve to
    a real link into several separate text fragments (open bracket / inner
    content / close bracket rendered via separate `text()` calls), so
    trying to shield these spans from *within* the per-fragment `text()`
    renderer doesn't work reliably -- by the time `text()` sees "PageOutline"
    on its own, the surrounding brackets that would identify it as
    macro/link syntax are already gone. The placeholder has to exist
    before mistune ever sees the "[" character (ticket #19/#27 interaction).

    Code spans are stashed first, before the macro/bracket passes, so a
    macro- or link-shaped example typed literally inside backticks (e.g.
    `` `[[BR]]` ``) is hidden from those regexes the same way it is hidden
    from mistune's own table-row splitter (ticket #45) -- see `_CODE_SPAN_RE`.

    Single-bracket TracWiki/InterTrac link syntax (`_SINGLE_BRACKET_LINK_RE`,
    ticket #44) is stashed last, after `[[...]]` is already out of the way,
    so a double-bracket span never has its own outer brackets mistaken for
    the single-bracket form's opening/closing pair.
    """
    placeholders: list[str] = []

    def stash_literal(m: re.Match[str]) -> str:
        placeholders.append(m.group(0))
        return f"\x00WK{len(placeholders) - 1}\x00"

    def stash_code_span(m: re.Match[str]) -> str:
        placeholders.append(m.group(1))
        return f"`\x00WK{len(placeholders) - 1}\x00`"

    text = _CODE_SPAN_RE.sub(stash_code_span, markdown_text)
    text = _sub_bracket_syntax(stash_literal, text)
    text = _sub_single_bracket_link(stash_literal, text)
    return text, placeholders


def _stash_for_flavor(
    markdown_text: str, flavor: str
) -> tuple[str, list[str]]:
    """`_stash_bracket_syntax`, with the flavour's own syntax (ChatGPT's
    math) kept out of its reach: protected first, put back after, so the
    parser's flavour rules see it whole (#216). For the default flavour
    this is `_stash_bracket_syntax`."""
    protected, saved = protect_syntax(markdown_text, flavor)
    stashed, placeholders = _stash_bracket_syntax(protected)
    if saved:
        # A stashed span holds the math as written; the text proper gets
        # an inline formula hidden as one more placeholder (#216).
        placeholders = [restore_syntax(p, saved) for p in placeholders]
        stashed = restore_syntax(stashed, saved, hide=placeholders)
    return stashed, placeholders


def _restore_bracket_syntax(text: str, placeholders: list[str]) -> str:
    """Restore sentinels stashed by `_stash_bracket_syntax` after rendering.

    A placeholder's own stored text can itself contain another sentinel:
    `_stash_bracket_syntax` stashes code spans before `[[...]]` and
    single-bracket links, so a code span quoted *inside* one of those
    (e.g. ``[[html(<code>`{{{-}}}`)]]``) is already a placeholder by the
    time the outer span is stashed, and that inner sentinel becomes part
    of the outer span's own stored text. A single non-recursive
    substitution pass leaves it behind -- a raw NUL byte in the final
    output (ticket #91). Looping until no sentinel remains resolves any
    nesting depth; a stashed span can only ever contain a placeholder
    from an *earlier* pass (never itself or a later one), so each pass
    strictly reduces what is left and this always terminates.
    """

    def restore(m: re.Match[str]) -> str:
        return placeholders[int(m.group(1))]

    while _PLACEHOLDER_RE.search(text):
        text = _PLACEHOLDER_RE.sub(restore, text)
    return text


# The TracLink resolver allowlist and `scheme:target` pattern live in
# `common`, shared with the link-target checks there (tickets #8, #13, #14,
# #17).


# Trac's quoted-target bracket link: `['Page']` or `["Page"]`, with an
# optional label (`['Page' label]`). Measured on this host:
# `app.config['SERVER_NAME']` and `d["key"]` render wiki links, so Markdown
# prose that subscripts with a string key would become one (and the write
# gate refuses the push). The label is whitespace other than a newline (NBSP
# and U+3000 included) and then at least one character that is not `]`,
# which backtracks as Trac does: `x['a' ] y` is plain text, `x['a'  ] y`
# (two spaces) is a link with an empty label. There is no lookahead for `[`
# or `(`: whether the span is a Markdown link is decided by mistune's link
# parser (`_parse_quoted_bracket`), so chained subscripts (`x['a']['b']`)
# are all caught and `handlers['get'](req, resp)` is too. A span with no
# closing bracket (`y['k'`) is not a link and does not match (ticket #241).
_QUOTED_BRACKET_LINK_RE = re.compile(
    r"""\[(?:"[^"\n]+"|'[^'\n]+')(?:[^\S\n]+[^\]\n]+)?\]"""
)

# mistune's own link-opening pattern (`!?\[`), compiled once: the position
# of a quoted bracket is handed to `InlineParser.parse_link` through a match
# of it.
_LINK_OPEN_RE = re.compile(
    mistune.inline_parser.InlineParser.SPECIFICATION["link"]
)

# mistune 3.4 resolves a link at its closing `]` (a delimiter stack, with a
# `link_close` rule), where 3.2 and 3.3 answered at the `[`:
# `InlineParser.parse_link` returned None when no link formed. `_bracket_rule`
# keeps the 3.2/3.3 code on those versions and takes the 3.4 path when this is
# true (ticket #263).
_DEFERRED_LINK_CLOSE = (
    "link_close" in mistune.inline_parser.InlineParser.SPECIFICATION
)

# The whitespace-delimited token before a quoted bracket, when it is a Trac
# shref-shaped run: `scheme:...` (after optional opening `(<"'`) that no
# `)`, `>` or `]` closes. Trac never forms a bracket link after one
# (measured: `https://e.com/a['b']` links `https://e.com/a['b`, while
# `HTTPS://E.COM/['a'] y` and `ftps://h/p['a'] y` render as plain text), so
# an escape there would only add a visible `!`.
_SCHEME_RUN_RE = re.compile(
    r"""[(<"']*[A-Za-z][\w.+-]*:[^\s<>()\[\]]*\Z"""
)

# A token longer than this is not scanned: bounds the backwards scan so a
# long unbroken run of text costs a constant per quoted bracket.
_SCHEME_RUN_MAX = 2048


def _intraword(src: str, pos: int) -> bool:
    """Whether the character at `pos` follows an alphanumeric one (Unicode
    letters and digits included), as the `_` of `snake_case` does."""
    return pos > 0 and src[pos - 1].isalnum()


def _after_scheme_run(
    src: str, end: int, *, delimiters_end_token: bool = False
) -> bool:
    """Whether the token ending at `end` is a scheme run
    (`_SCHEME_RUN_RE`). Scans backwards from `end` to the previous
    whitespace, and gives up at once on a character the pattern never
    allows (`[`, `]`, `>`, `)`).

    With `delimiters_end_token` a `*` ends the token as whitespace does,
    and so does a `_` unless the character before it is alphanumeric
    (`str.isalnum()`, so Unicode letters and digits count): as in
    CommonMark, an intraword `_` cannot open or close emphasis, while an
    intraword `*` can (`a*x:['a']*`). mistune 3.2 and 3.3 parse the content
    of an emphasis span as an inline source of its own, so a real delimiter
    is a boundary there and the token starts after it (`*x:['a']*` reads
    `x:`; `x:*['a']*` reads nothing). mistune 3.4 parses emphasis over the
    whole source and leaves the delimiters in `src`, so the 3.4 path asks
    for the same boundary. `~` is not one: no strikethrough plugin is
    enabled, so it is text. Known residual: an unclosed `*` or `_`
    directly before a bare `word:` (`_x:[0]`) is a boundary here, where 3.2
    reads the whole token; bare `word:` prefixes are being redesigned on
    #264."""
    i = end
    floor = max(0, end - _SCHEME_RUN_MAX)
    while i > floor:
        c = src[i - 1]
        if c.isspace() or (
            delimiters_end_token
            and (c == "*" or (c == "_" and not _intraword(src, i - 1)))
        ):
            break
        if c in "[]>)":
            return False
        i -= 1
    else:
        if i > 0 and not src[i - 1].isspace():
            return False
    return _SCHEME_RUN_RE.match(src, i, end) is not None


# mistune's own bracket token (`helpers._INLINE_SQUARE_BRACKET_RE`, the same
# pattern in 3.2 and 3.4): a `[` or `]` that an even run of backslashes (none
# included) precedes, so `\[` is not one and `\\[` is.
_SQUARE_BRACKET_RE = re.compile(r"(?<!\\)(?:\\\\)*[\[\]]")


def _close_bracket_map(src: str) -> dict[int, int]:
    """The matching `]` of every `[` in `src` that has one, in one linear
    pass, with `parse_link_text`'s semantics: each bracket token is found
    by `_SQUARE_BRACKET_RE`; a token that is exactly `]` closes the
    innermost open one (with none open it is ignored), and every other
    token opens one, a `[` never closed is not in the map. For a `[` at
    `p`, `_close_bracket_map(src).get(p)` is the position of the `]` that
    `parse_link_text(src, p + 1)` stops at (it counts levels from just
    after `p`, which comes to the same pairing).

    The quirk is mistune's and is kept: `parse_link_text` compares the
    whole token with `"]"`, and the token holds the even backslash run
    before the bracket (`\\\\]` is one token), so a `]` after two
    backslashes counts as an opener there, not a closer."""
    closes: dict[int, int] = {}
    opened: list[int] = []
    for m in _SQUARE_BRACKET_RE.finditer(src):
        if m.group(0) != "]":
            opened.append(m.end() - 1)
        elif opened:
            closes[opened.pop()] = m.end() - 1
    return closes


def _bracket_cache(
    state: Any,
) -> tuple[dict[int, int], dict[int, bool]]:
    """The bracket map of `state.src` and the answers of `_link_forms_at`
    so far, computed lazily and kept on the state (one per inline source:
    mistune gives a nested source its own state; the cache is rebuilt if
    `state.src` is not the string it was built for)."""
    cache = getattr(state, "_tracwiki_bracket_cache", None)
    if cache is None or cache[0] is not state.src:
        cache = (state.src, _close_bracket_map(state.src), {})
        state._tracwiki_bracket_cache = cache
    return cache[1], cache[2]


def _link_forms_at(state: Any, open_pos: int) -> bool:
    """Whether a link forms at the `[` at `open_pos` of `state.src`.

    It decides at the `[` what mistune 3.4 decides at the `]`: a mirror of
    its `_close_bracket` and `_parse_reference`, built from mistune's own
    helpers. A `]` followed by a `(` destination `helpers.parse_link`
    accepts is an inline link. Otherwise the reference table
    (`state.env["ref_links"]`) is tried with the explicit `[label]` that
    follows, or else with the bracket's own text, which counts only when it
    holds no nested bracket and is not over-long (then `parse_link_label`
    finds no label). The `]` of a text with nested brackets comes from
    `_close_bracket_map`, not from a scan per call. Code spans, autolinks
    and inline HTML that cross the `]` are not modelled; a wrong "yes" is
    caught by the escape on the opener that survives (`_bracket_rule`).
    """
    closes, answers = _bracket_cache(state)
    answer = answers.get(open_pos)
    if answer is None:
        answer = answers[open_pos] = _link_forms_at_uncached(
            state, open_pos, closes
        )
    return answer


def _link_forms_at_uncached(
    state: Any, open_pos: int, closes: dict[int, int]
) -> bool:
    src = state.src
    label, end = parse_link_label(src, open_pos + 1)
    if label is None or end is None:
        close = closes.get(open_pos)
        if close is None:
            return False
        label, end = None, close + 1
    if (
        src.startswith("(", end)
        and parse_link(src, end + 1)[1] is not None
    ):
        return True
    refs = state.env.get("ref_links")
    if not refs:
        return False
    key_label = None
    if src.startswith("[", end):
        key_label, _ = parse_link_label(src, end + 1)
    if not key_label:
        key_label = label
    return key_label is not None and unikey(key_label) in refs


# Trac's numeric changeset and log bracket links, as one pattern (ticket
# #249). Sources, Trac 1.6: trac/versioncontrol/web_ui/changeset.py
#   !?\[(?P<it>[a-zA-Z.+-]*?\s*)(?:[0-9]+|[a-fA-F0-9]{8,})
#       (?:/[^\]]*)?(?:\?[^\]]*)?(?:#[^\]]*)?\]
# and log.py
#   !?\[(?P<it>[a-zA-Z.+-]*?\s*)(?:RANGES|CSET(?:[:-]CSET)?)(?:[/?][^\]]*)?\]
# with CSET `(?:[0-9]+|[a-fA-F0-9]{8,})` and RANGES (trac.util.Ranges.RE_STR)
# `[0-9]+(?:[-:][0-9]+)?(?:,​?[0-9]+(?:[-:][0-9]+)?)*`. Line-local
# adjustments: the prefix whitespace is `[^\S\n]*` and each `[^\]]*` is
# `[^\]\n]*`. Measured on this host (render only): `x[0]`, `rows[12]`,
# `x[1:3]`, `x[0,1]`, `x[1-3]`, `x[deadbeef]`, `x[12/path]`, `x[1:3/p]`,
# `x[12?a]`, `x[12#b]` and `x[trac 12]` render as links; `x[0, 1]`,
# `x[1.5]`, `x[0:]`, `x[:3]`, `x[cafe]` and `x[n-1:n]` do not match; `x[-1]`,
# `x[i+1]`, `x[.5]` and an unknown prefix (`x[abc 12]`, `x[ 12]`, `x[abc12]`)
# match but Trac's resolver yields text. Trac's `!` escape on every shape
# that matches renders plain text with no visible `!`, so every match is
# escaped and the pattern stays an exact mirror (no exclusion rule).
#
# Linear time (the first version took 49 s on `x[1/` + `?#` * 2000 and 32 s
# on `x[` + `a` * 20000). Two changes, neither altering which brackets
# match except for the prefix bound below:
#   - the changeset alternative's three optional tails
#     `(?:/..)?(?:\?..)?(?:#..)?` accept exactly the strings of one tail
#     `(?:[/?#][^\]\n]*)?`, since each tail runs to the first `]`; the
#     three-way split is what made the backtracking cubic. The log
#     alternative keeps its own `[/?]` tail (`#` is not a log tail).
#   - the InterTrac prefix is bounded to 32 characters. Only a KNOWN
#     InterTrac prefix makes a link (measured: an unknown prefix renders as
#     text) and known prefixes are short, so a longer run left bare still
#     renders as text; the bound caps the lazy prefix against the hex run.
# Possessive quantifiers would also work but need Python 3.11, and
# pyproject allows 3.10; an id run is followed by a character that is
# never part of an id, so backtracking into it fails at once.
_NUMERIC_BRACKET_LINK_RE = re.compile(
    r"\[[A-Za-z.+-]{0,32}?[^\S\n]*"
    r"(?:"
    r"(?:[0-9]+|[a-fA-F0-9]{8,})(?:[/?#][^\]\n]*)?\]"
    r"|"
    r"(?:[0-9]+(?:[-:][0-9]+)?(?:,​?[0-9]+(?:[-:][0-9]+)?)*"
    r"|(?:[0-9]+|[a-fA-F0-9]{8,})"
    r"(?:[:-](?:[0-9]+|[a-fA-F0-9]{8,}))?)"
    r"(?:[/?][^\]\n]*)?\]"
    r")"
)


def _closes_before_newline(state: Any, opener: int) -> bool:
    """Whether a `]` lies between the `[` at `opener` and the next newline
    of `state.src` (a bracket shape ends at one and holds no newline). The
    next `]` and the next newline are found once and kept on the state
    while the opener's position stays inside the stretch they were found
    for, so a line of many openers costs one find, not one per opener."""
    src = state.src
    cache = getattr(state, "_tracwiki_stops", None)
    if cache is None or cache[0] is not src:
        cache = [src, -1, -1, -1, -1]
        state._tracwiki_stops = cache
    newline_from = opener + 1
    close_from = opener + 2
    if not cache[3] <= newline_from <= cache[4]:
        found = src.find("\n", newline_from)
        cache[3], cache[4] = (
            newline_from,
            found if found != -1 else len(src),
        )
    if not cache[1] <= close_from <= cache[2]:
        found = src.find("]", close_from)
        cache[1], cache[2] = (
            close_from,
            found if found != -1 else len(src),
        )
    return cache[2] < cache[4]


_BRACKET_RULES = ("quoted_bracket", "numeric_bracket")


def _other_rules(
    inline: Any, gate: re.Match[str], state: Any
) -> int | None:
    """What mistune would have done at `gate` had no bracket-shape rule
    matched there: the first of the other rules that matches at the same
    position (the link rule, which takes every `[`), or None."""
    rules = [r for r in inline.rules if r not in _BRACKET_RULES]
    m = inline.compile_sc(rules).match(state.src, gate.start())
    if m is None:
        return None
    return inline.parse_method(m, state)


def _bracket_rule(
    token_type: str, shape_re: re.Pattern[str]
) -> Callable[[Any, re.Match[str], Any], int | None]:
    """Build an inline rule: a Trac bracket-link shape in prose becomes a
    `token_type` token, which the renderer writes with Trac's `!` escape
    (tickets #241, #249).

    Registered `before="link"`: mistune tries rules at the leftmost
    position in rule order, and a rule after `link` never sees a `[` that
    `link` gave up as text. Because it is an inline rule it fires only
    where mistune sees prose: a link or image destination, a reference
    definition, a link title, an autolink, a code span and a
    backslash-escaped bracket are all consumed by their own rules first.

    Real links first: a link mistune would form at the `[` (an inline link
    `["T"](url)`, or a reference link the document defines: `["T"][id]`,
    `["T"]`, `["T"][]`) is converted as before. Anything else is the
    whole shape as ONE token, whatever surrounds it: no opener is pushed,
    so a `]` inside the shape cannot close an outer opener. The rule does
    not ask whether the shape sits in a link label. The renderer does, at
    the one place it is known (the link depth in `render_token`): a quoted
    bracket always gets the `!` (#241), a numeric one gets it everywhere
    except inside a link's children (#249: Trac does not consume the `!`
    inside a bracket-link label, so it would show, and the bare form links
    nothing there). The parser's `in_link` cannot answer that: inline HTML
    such as `<a name=q>` sets it with no link in sight, and on mistune 3.4
    an inner link deactivates the outer opener, so a label that looked
    like a link's is prose after all.

    The escape fallback is subject to the scheme-run check
    (`_after_scheme_run`): the `[` is left as text when the token just
    before the match is a scheme run, because Trac's URL pattern already
    takes the characters after it. A real link directly after a `word:`
    (`Note:[1](http://x)`) is not affected, as it was not before the rule
    existed.

    mistune 3.2 and 3.3 (`not _DEFERRED_LINK_CLOSE`): the position is
    handed to `inline.parse_link`, which returns None, leaving `state`
    untouched, when no link forms. Declining is returning None. It is not
    asked inside a link label (`state.in_link`), where it would only emit
    the bracket as text.

    mistune 3.4+ (#263) decides a link at its `]`: `parse_link` always
    pushes an opener and returns, so it cannot tell. The rule decides at
    the `[` with `_link_forms_at`, and declining means letting mistune push
    its own opener (`parse_link`), because returning None would leave a
    later `]` to close an outer opener early. The rule is registered with a
    leading `!?` so an image opener `![` does not take the `[` first: a
    bang before a real image is the image's, and any other bang is text.
    The opener of a link that does not form after all (an inner link
    deactivates it) survives as a `{token_type}_open` token, which the
    renderer writes like the shape. The scheme-run check skips emphasis
    delimiters as token boundaries, which 3.4 leaves in the source
    (`delimiters_end_token`).

    What mistune's scanner is given is only a gate (`_bracket_gate`: the
    opener and the character after it), not the shape's own pattern, and
    this function matches the shape (`shape_re`) at the opener itself,
    but only when a `]` lies before the next newline, which every shape
    needs. A pattern in mistune's combined regex is tried at every `[` of
    a line, and a shape's label or tail reads to the end of the line when
    no `]` follows: `['a' ` repeated was quadratic inside one `re` call
    (ticket #289, which holds the GIL). Where there is no shape, the rules
    after this one are asked (`_other_rules`), which is what mistune did
    when the shape's pattern did not match.
    """
    full_re = re.compile(_REGISTER_PREFIX + shape_re.pattern)

    def match_shape(
        inline: Any, m: re.Match[str], state: Any
    ) -> re.Match[str] | None:
        src = state.src
        opener = m.start() + (
            1 if src.startswith("!", m.start()) else 0
        )
        if not _closes_before_newline(state, opener):
            return None
        return full_re.match(src, m.start())

    if _DEFERRED_LINK_CLOSE:

        def deferred_rule(
            inline: Any, gate: re.Match[str], state: Any
        ) -> int | None:
            m = match_shape(inline, gate, state)
            if m is None:
                return _other_rules(inline, gate, state)
            src = state.src
            br = m.start()
            raw = m.group(0)
            if raw.startswith("!"):
                br += 1
                raw = raw[1:]
                if _link_forms_at(state, br):
                    # A real image: its opener takes the bang too.
                    link_m = _LINK_OPEN_RE.match(src, m.start())
                    assert link_m is not None
                    return inline.parse_link(link_m, state)
                inline.process_text("!", state)
            link_m = _LINK_OPEN_RE.match(src, br)
            assert link_m is not None
            if _link_forms_at(state, br):
                index = len(state.tokens)
                pos = inline.parse_link(link_m, state)
                opener = state.tokens[index]
                assert opener["type"] == "text" and opener["raw"] == "["
                opener["type"] = f"{token_type}_open"
                return pos
            if _after_scheme_run(src, br, delimiters_end_token=True):
                return inline.parse_link(link_m, state)
            state.append_token({"type": token_type, "raw": raw})
            return m.end()

        return deferred_rule

    def rule(
        inline: Any, gate: re.Match[str], state: Any
    ) -> int | None:
        m = match_shape(inline, gate, state)
        if m is None:
            return _other_rules(inline, gate, state)
        if not state.in_link:
            link_m = _LINK_OPEN_RE.match(state.src, m.start())
            assert link_m is not None
            pos = inline.parse_link(link_m, state)
            if pos is not None:
                return pos
        if _after_scheme_run(state.src, m.start()):
            return None
        state.append_token({"type": token_type, "raw": m.group(0)})
        return m.end()

    return rule


# Under mistune 3.4+ the rules also match a leading `!` (see `_bracket_rule`).
_REGISTER_PREFIX = "!?" if _DEFERRED_LINK_CLOSE else ""

# What mistune's scanner is given for each rule (`_bracket_rule`): the
# opener and the one character after it that tells the two shapes apart (a
# quote opens a quoted target; a numeric one starts with anything else but
# `]` or a newline). The shape itself is matched by the rule.
_QUOTED_BRACKET_GATE = _REGISTER_PREFIX + r"""\[(?=["'])"""
_NUMERIC_BRACKET_GATE = _REGISTER_PREFIX + r"""\[(?=[^\]\n"'])"""

_parse_quoted_bracket = _bracket_rule(
    "quoted_bracket", _QUOTED_BRACKET_LINK_RE
)
_parse_numeric_bracket = _bracket_rule(
    "numeric_bracket", _NUMERIC_BRACKET_LINK_RE
)


def _quoted_bracket_links(md: Any) -> None:
    """mistune plugin: `_parse_quoted_bracket` as an inline rule before
    `link`."""
    md.inline.register(
        "quoted_bracket",
        _QUOTED_BRACKET_GATE,
        _parse_quoted_bracket,
        before="link",
    )


def _numeric_bracket_links(md: Any) -> None:
    """mistune plugin: `_parse_numeric_bracket` as an inline rule before
    `link`."""
    md.inline.register(
        "numeric_bracket",
        _NUMERIC_BRACKET_GATE,
        _parse_numeric_bracket,
        before="link",
    )


# The mistune plugin set for every parse of Markdown in this module. The
# converter and the `list_item_split` detector (#125) must agree on what is
# a table, so both read this one list (ticket #142). `table_in_list` and
# `table_in_quote` let a GFM table indented under a list item or after a
# `>` marker parse as a table instead of lazy paragraph text; they are
# addressed by dotted path because a bare name raises ValueError.
_MISTUNE_PLUGINS = [
    "table",
    "mistune.plugins.table.table_in_list",
    "mistune.plugins.table.table_in_quote",
    _quoted_bracket_links,
    _numeric_bracket_links,
]


def _indent_nonblank(lines: list[str], indent: str) -> list[str]:
    """Prefix every non-blank line with `indent`; a blank line stays blank
    rather than picking up trailing whitespace (tickets #75, #112)."""
    return [indent + line if line else line for line in lines]


def _indent_text_lines(
    lines: list[str],
    text_indent: str,
    continuation_indent: str,
    *,
    list_open: bool,
) -> list[str]:
    """Indent the non-blank `lines` of a text run: with `text_indent` while
    Trac still has the list open, with `continuation_indent` (master's text
    column) from the first EMPTY line on, or from the start when `list_open`
    is False (an empty line was already emitted before the run). Trac ends a
    list at an empty line, so the marker + 3 cap (`text_indent`, #178) means
    nothing after one. A blank line stays blank."""
    out: list[str] = []
    for line in lines:
        if not line:
            list_open = False
            out.append(line)
        else:
            out.append(
                (text_indent if list_open else continuation_indent)
                + line
            )
    return out


def _is_paragraph_token(token: dict[str, Any]) -> bool:
    """True for a paragraph-like block token (``paragraph``/``block_text``)."""
    return token.get("type") in ("paragraph", "block_text")


# A line that starts like this is TracWiki passed through verbatim (a
# `{{{#!html` block, a heading, a `||` row, a rule). Ticket #143 review
# (F1): " [[BR]][[BR]]" appended to such a paragraph lands on its last line,
# and `}}} [[BR]][[BR]]` never closes the block (measured on Trac: it
# swallows the rest of the page).
_PASSTHROUGH_LINE_PREFIXES = ("{{{", "}}}", "||", "=", "----")


def _has_passthrough_line(
    text: str, *, first_only: bool = False
) -> bool:
    """True when a line of `text` (only its first non-blank one with
    `first_only`) starts, stripped, like passed-through TracWiki."""
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(_PASSTHROUGH_LINE_PREFIXES):
            return True
        if first_only:
            return False
    return False


def _starts_a_block_in_trac(text: str) -> bool:
    """True when the first line of `text`, stripped, starts a Trac block of
    its own: a ``>`` citation line, or a list marker (``*``, ``-``, ``1.``,
    ``a.``, ``ii.``; Trac's list-marker grammar, `_LIST_MARKER_RE`).
    Directly under a citation either would join the quote or open a nested
    item (#145 review F3, e.g. a Markdown ``\\>`` or ``1\\.``)."""
    first = text.lstrip().split("\n", 1)[0].strip()
    return first.startswith(">") or bool(
        _LIST_MARKER_RE.match(" " + first)
    )


def _item_runs(
    children: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group a list item's children into runs, in SOURCE order (#112): the
    one grouping `render_token`'s list_item case and `find_list_item_splits`
    share, so the two cannot drift.

    The kinds are ``list`` (one nested list), ``table`` (#142: a GFM table,
    rendered as a ``{{{#!div}}}`` block), ``quote`` (#145: a blockquote,
    only once the item has content and only when it is not empty),
    ``code`` (#144: a fenced block) and ``text`` (every other child,
    adjacent ones merged into one run). Every list,
    table, code block and quote is a run of its own.
    """
    runs: list[tuple[str, list[dict[str, Any]]]] = []
    content_seen = False
    for child in children:
        child_type = child.get("type")
        kind = (
            "list"
            if child_type == "list"
            else "table"
            if child_type == "table"
            else "quote"
            if child_type == "block_quote"
            and content_seen
            and any(
                c.get("type") != "blank_line"
                for c in child.get("children") or []
            )
            else "code"
            if child_type == "block_code"
            else "text"
        )
        if child_type != "blank_line":
            content_seen = True
        if runs and kind == "text" and runs[-1][0] == "text":
            runs[-1][1].append(child)
        else:
            runs.append((kind, [child]))
    return runs


def _ends_with_citation(text: str) -> bool:
    """Whether the last non-empty line of ``text`` is a Trac citation line
    (starts with ``>`` once stripped)."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return bool(lines) and lines[-1].startswith(">")


def _item_block_separator(
    prev_kind: str,
    next_kind: str,
    list_seen: bool,
    prev_text: str,
    next_text: str,
) -> Literal[
    "br", "adjacent", "blank", "quote_gap", "between", "marker"
]:
    """How two neighbouring blocks of a list item are separated (#143, #145).

    Trac ends a list at any blank line, so the pre-#143 "blank" is the
    fallback and the other two keep the block inside the ``<li>``:

    * ``"br"``: the earlier paragraph's last line ends ``" [[BR]][[BR]]"``
      and the next paragraph follows on the next line. Kinds ``para`` then
      ``para``.
    * ``"adjacent"``: the next paragraph goes on the line after a ``code``
      or ``table`` child, no blank line.
    * ``"blank"``: everything else (a text run after a nested list that
      ``"between"`` refuses, ...).
    * ``"quote_gap"`` (#145): a lone ``>`` line, between two sibling
      blockquotes of one item. Trac shows one blockquote with two paragraphs.
    * ``"between"`` (#176): a ``para`` run directly after a nested list
      (``prev_kind == "list"``): no blank line, its first line at the item's
      marker column + 1, strictly between its marker and the nested list's,
      and every later line at the item's continuation indent.
      Trac then closes the nested list and continues the OUTER item
      (measured on /trac_test), which is innermost again afterwards, so the
      caller resets `list_seen`. Refused (``"blank"``, today's form) when
      the run holds a passthrough line (any line, F1), holds a blank line
      (``"\\n\\n"``: it would end the list) or starts a block in Trac
      (``_starts_a_block_in_trac``: a list marker at that column opens an
      item, ``>`` a citation).
    * ``"marker"`` (#197): a ``quote`` directly after a nested list
      (``prev_kind == "list"``): no blank line, every ``>`` line at the
      item's MARKER column. Such a citation line closes every nested list
      whose marker column is greater, so the quote lands in the outer item
      (measured on /trac_test: several lines, text after it, a second quote
      joined by ``>``, an ordered item, depth 1). The item is innermost
      again afterwards, so the caller resets `list_seen`. Refused
      (``"blank"``, the pre-#197 form) when the nested list's text
      (`prev_text`) holds a blank line (Trac has closed every list there, so
      the ``>`` renders outside the item and the next item starts a new
      list) or ends with a citation line (the ``>`` joins that
      blockquote); measured on /trac_test.

    A ``quote`` (#145) is Trac's citation syntax, ``> `` lines at the item's
    continuation indent: ``"adjacent"`` after the item text (kind ``para``),
    a ``para``/``text`` run, or a ``code``/``table`` child, and a following
    paragraph is ``"adjacent"`` to it too. ``"blank"`` keeps the pre-#145
    form (blank line, two-space-indented quote) where Trac cannot take the
    citation at the continuation indent: once a nested list has appeared and
    no paragraph or quote has closed it since (it would attach to the
    innermost nested item, #112/#125; a ``"between"`` run closes it, #176,
    and so does a quote directly after the list, ``"marker"``, #197), and
    after a run with a passthrough line (F1 below). Where the list was seen
    but the quote does not directly follow it (after a ``code`` or ``table``
    child that attached to the nested item, a ``text`` run, ...) it stays
    ``"blank"``: unmeasured.

    Review fixes (#145): a ``quote`` is also ``"blank"`` after a run whose
    text already holds a blank line, and a paragraph after a quote is
    ``"blank"`` when its first line starts a block of its own in Trac
    (``_starts_a_block_in_trac``: ``>`` would join the quote, a list marker
    would open a nested item). ``plain_quote`` is the kind of a quote that
    kept the pre-#145 form; whatever follows it is ``"blank"``, as before.

    Kinds are ``para`` (a paragraph, or a run starting with one), ``code``,
    ``table``, ``quote``, ``plain_quote``, ``list`` and ``text`` (any other
    block). The texts
    are the rendered TracWiki on either side of the boundary (`prev_text` may be
    empty for a ``code``/``table`` child, which cannot hold a passthrough
    line at its end).

    Review fixes: a boundary with a passthrough line (`_has_passthrough_line`;
    ANY line of either paragraph for ``"br"``, the first line of the next run
    for ``"adjacent"``) stays ``"blank"`` (F1). `list_seen` says a nested list
    came earlier in the item: ``"adjacent"`` then becomes ``"blank"``, since
    Trac attaches a block with no blank line to the innermost open item, the
    nested one (F2). ``"br"`` inside one run is unaffected: the run itself is
    already where it is.
    """
    if next_kind == "quote":
        if list_seen:
            if (
                prev_kind == "list"
                and "\n\n" not in prev_text
                and not _ends_with_citation(prev_text)
            ):
                return "marker"
            return "blank"
        if prev_kind == "quote":
            return "quote_gap"
        if prev_kind in ("code", "table"):
            return "adjacent"
        if (
            prev_kind in ("para", "text")
            and not _has_passthrough_line(prev_text)
            and "\n\n" not in prev_text
        ):
            return "adjacent"
        # A list, a quote that kept the pre-#145 form, or a run that
        # already holds a blank line (the list ended there; unmeasured).
        return "blank"
    if next_kind != "para":
        return "blank"
    if prev_kind == "list":
        if (
            _has_passthrough_line(next_text)
            or "\n\n" in next_text
            or _starts_a_block_in_trac(next_text)
        ):
            return "blank"
        return "between"
    if prev_kind == "quote":
        if (
            list_seen
            or _has_passthrough_line(next_text, first_only=True)
            or _starts_a_block_in_trac(next_text)
        ):
            return "blank"
        return "adjacent"
    if prev_kind == "para":
        if _has_passthrough_line(prev_text) or _has_passthrough_line(
            next_text
        ):
            return "blank"
        return "br"
    if prev_kind in ("code", "table"):
        if list_seen or _has_passthrough_line(
            next_text, first_only=True
        ):
            return "blank"
        return "adjacent"
    return "blank"


def _heading_slug(rendered_text: str) -> str:
    """Return the GitHub-style anchor slug for a rendered heading text.

    Used by :meth:`TracWikiRenderer.heading` to emit an explicit Trac
    heading anchor (``== Heading == #heading``) so cross-page links
    written as Markdown ``[text](#heading)`` resolve after conversion.
    Without this, Trac auto-generates a heading id by stripping
    whitespace + non-alphanumerics WITHOUT lowercasing — ``#Heading``
    or ``#WikiTaskIndexPageSchema`` — which never matches the
    Markdown source's ``#heading`` / ``#wiki-task-index-page-schema``.
    """
    cleaned = rendered_text
    # Strip TracWiki inline markers our own renderer emits before us.
    cleaned = (
        cleaned.replace("'''", "").replace("''", "").replace("`", "")
    )
    cleaned = _SLUG_DROP_RE.sub("", cleaned)
    cleaned = cleaned.strip().lower()
    cleaned = _SLUG_WS_RE.sub("-", cleaned)
    return cleaned


class TracWikiRenderer(mistune.BaseRenderer):
    """Renderer that converts Markdown AST to TracWiki syntax."""

    NAME = "tracwiki"

    def __init__(
        self,
        heading_anchors: bool = False,
        placeholders: list[str] | None = None,
    ):
        """Initialize renderer with state tracking for table rendering.

        Args:
            heading_anchors: When True, emit an explicit ``#slug`` anchor on
                each heading so Markdown-source cross-references resolve after
                conversion.  Default is False: plain ``= Heading =`` syntax,
                because Trac auto-generates heading anchors and explicit slugs
                like ``#4-non-goals`` cause ``#4`` to be misread as a ticket
                reference.
            placeholders: The sentinel table built by `_stash_bracket_syntax`
                for the document being rendered, if any. `heading()` needs it
                to resolve a `\\x00WKn\\x00` sentinel (code span, `[[...]]`,
                or single-bracket link body) back to real text before
                slugifying -- the global restore pass that undoes stashing
                for everything else only runs once, after the whole document
                has been rendered, which is too late for a slug computed
                mid-render (ticket #45 regression guard).
        """
        super().__init__()
        self._heading_anchors = heading_anchors
        self._placeholders = (
            placeholders if placeholders is not None else []
        )
        # Track column alignments for current table
        self._table_alignments: list[str | None] = []
        # Last character emitted by the immediately preceding sibling,
        # updated by render_token() after every call. Lets linebreak()
        # tell whether "[[BR]]" would land directly against a non-space
        # character (ticket #29).
        self._last_char = "\n"
        # True while a list item's blockquote child renders (ticket #145):
        # `block_quote()` then emits Trac citation lines, `> `-prefixed, at
        # every nesting level, instead of the two-space-indented form.
        self._citation = False
        # How many `link` tokens' children are being rendered (maintained by
        # `render_token`): a numeric bracket shape inside a link label is
        # written without the `!` escape (#249).
        self._link_depth = 0
        # (name, opener) of each code fence `block_code` wrote as a plain
        # `#!text` block because Trac does not highlight the language, in
        # order of appearance, with repeats (ticket #240). The name is as
        # typed, so it may still hold placeholder sentinels.
        self.downgraded_fences: list[tuple[str, str]] = []

    def text(self, text: str) -> str:
        """Render plain text.

        Defensively `!`-prefixes bare CamelCase-shaped prose words (Trac's
        own WikiCamelCase auto-link pattern, e.g. WiFi, LoRa) so Trac's
        renderer treats them as literal text instead of attempting a
        broken missing-page link. Markdown has no CamelCase-auto-link
        concept, so any such word reaching this method was never meant as
        a link (ticket #27).

        Literal `[[...]]` syntax typed in the Markdown source never
        reaches this method -- it is
        stashed as sentinel placeholders by `_stash_bracket_syntax` before
        mistune even starts parsing (ticket #19), so a macro/page name
        that happens to be CamelCase-shaped is never escaped inside its
        brackets.

        Two link forms DO still reach this method as literal text, and
        both are located and skipped inline via `_ESCAPE_SKIP_RE`
        (ticket #44):

        - a bare absolute URL, which can't be pre-stashed the way
          `[[...]]` is without risking a real Markdown link's own
          `(url)` destination (`_URL_IN_TEXT_RE`);
        - a bare, unbracketed TracLink/InterTrac reference such as
          `auto_pm:wiki:Rules/trac/RenderVerify`, which carries no
          bracket syntax at all to pre-stash (`_BARE_TRACLINK_RE`).
        The single-bracket TracWiki/InterTrac link form is handled
        earlier, by pre-stashing (`_SINGLE_BRACKET_LINK_RE`) rather than
        here -- mistune splits an unresolved "[...]" into separate
        text() calls (the "[" on its own), so this method never sees
        that span whole.

        Entity and numeric character references (``&amp;``, ``&#x20;``)
        are decoded first, as CommonMark does in text; code spans and
        code blocks never reach this method, so theirs stay as typed
        (ticket #239). A reference is decoded only when the result is
        Trac-safe (`_is_trac_safe`): one that would decode to markup
        (``&#35;``, ``&#124;``) or to a control or line break stays as
        typed. A token that began with a reference at the start of a
        line has its leading spaces stripped after decoding, since two
        would make Trac render a blockquote and HTML collapses them
        there anyway. Decoding comes before the CamelCase escape, so a
        reference that spells a CamelCase word still gets its ``!``.
        """
        typed = text
        text = _decode_char_refs(text)
        if typed.startswith("&") and self._last_char == "\n":
            text = text.lstrip(" ")

        def escape(segment: str) -> str:
            return _CAMELCASE_RE.sub(
                lambda m: f"!{m.group(0)}", segment
            )

        out: list[str] = []
        last = 0
        for m in _ESCAPE_SKIP_RE.finditer(text):
            out.append(escape(text[last : m.start()]))
            out.append(m.group(0))
            last = m.end()
        out.append(escape(text[last:]))
        return "".join(out)

    def emphasis(self, text: str) -> str:
        """Render italic text (single emphasis)."""
        return f"''{text}''"

    def strong(self, text: str) -> str:
        """Render bold text (double emphasis)."""
        return f"'''{text}'''"

    def quoted_bracket(self, text: str) -> str:
        """Render a quoted-target bracket link shape found in prose
        (`_parse_quoted_bracket`, ticket #241) with Trac's `!` escape, so
        Trac shows ``x['KEY']`` as text instead of a wiki link, inside a
        link's label too. A `!` typed in Markdown is a literal character,
        so it is not an escape already: ``Wow!['a']`` gives
        ``Wow!!['a']``."""
        return f"!{text}"

    def numeric_bracket(self, text: str) -> str:
        """Render a numeric changeset or log bracket shape found in prose
        (`_parse_numeric_bracket`, ticket #249) with Trac's `!` escape, so
        Trac shows ``x[0]`` as text instead of a changeset link. Inside a
        link's label (`_link_depth`) it is written as it is: Trac does not
        consume the `!` there (``[http://x a ![0] b]`` renders the label
        ``a ![0``) and the bare form links nothing."""
        return text if self._link_depth else f"!{text}"

    def quoted_bracket_open(self, text: str) -> str:
        """Render the `[` of a quoted bracket shape that mistune 3.4 left
        as text (`_bracket_rule`, ticket #263): the rule let mistune open a
        link there, and none formed, so the shape that follows is plain
        text in the AST and the opener alone carries the escape."""
        return f"!{text}"

    def numeric_bracket_open(self, text: str) -> str:
        """Render the `[` of a numeric bracket shape that mistune 3.4 left
        as text (`_bracket_rule`, ticket #263); see `quoted_bracket_open`,
        and `numeric_bracket` for the link label."""
        return text if self._link_depth else f"!{text}"

    def codespan(self, text: str) -> str:
        """Render inline code."""
        return f"`{text}`"

    def inline_math(self, text: str) -> str:
        """Render inline math from a flavour's math rule (#216).

        Trac on this host has no math processor, so a formula is a code
        span, or `{{{ }}}` when it holds a backtick (a code span cannot).
        """
        return inline_math_markup(text)

    def block_math(self, text: str) -> str:
        """Render display math from a flavour's math rule (#216).

        `{{{#!latex}}}`: no math processor is installed, and Trac accepts
        `latex` as a highlighting language, so the formula shows as source.
        """
        return f"{{{{{{#!latex\n{text}\n}}}}}}\n\n"

    def linebreak(self) -> str:
        """Render line break.

        Trac's wiki-link grammar treats a colon-valued token immediately
        followed by "[[BR]]" (no space) as a candidate `wikiname:target`
        TracLink, greedily consuming the "[[BR]]" into the failed
        link-target parse instead of recognizing it as a macro -- it
        renders as the literal string "[[BR]]" rather than a line break.
        Inserting a leading space when the preceding character isn't
        already whitespace avoids the collision (ticket #29).
        """
        if self._last_char and not self._last_char.isspace():
            return " [[BR]]\n"
        return "[[BR]]\n"

    def softbreak(self) -> str:
        """Render soft break."""
        return "\n"

    def blank_line(self) -> str:
        """Render blank line."""
        return ""

    def heading(self, text: str, level: int, **attrs) -> str:
        """Render heading.

        TracWiki heading syntax uses leading = markers (trailing = optional).
        We produce the canonical form with trailing markers AND an explicit
        anchor (``#slug``) so Markdown-source cross-references like
        ``[text](#some-heading)`` resolve after conversion. Trac's default
        heading id (whitespace + punctuation stripped, case preserved) does
        NOT match the Markdown slug rule (lowercase + whitespace→dash);
        emitting an explicit anchor makes the Markdown slug authoritative.

            = H1 = #h1
            == H2 == #h2

        If the heading text slugifies to empty (e.g. punctuation-only),
        the explicit anchor is omitted and Trac's default id applies.

        When ``self._heading_anchors`` is False (set via ``--heading-anchors
        off`` on the CLI), the slug computation is skipped entirely and plain
        ``= Heading =`` syntax is emitted — useful when the caller does not
        need Markdown cross-reference compatibility.
        """
        marker = "=" * level
        # --heading-anchors off: skip slug computation, emit plain heading.
        if not self._heading_anchors:
            return f"{marker} {text} {marker}\n"
        # Resolve any stash sentinel (code span, [[...]], single-bracket
        # link) still in `text` at this point -- the global restore pass
        # for everything else runs once, after the whole document is
        # rendered, too late for a slug computed here mid-render.
        slug = _heading_slug(
            _restore_bracket_syntax(text, self._placeholders)
        )
        if slug:
            return f"{marker} {text} {marker} #{slug}\n"
        return f"{marker} {text} {marker}\n"

    def _render_text_run(
        self, children: list[dict[str, Any]], state: Any
    ) -> str:
        """Render a list item's run of plain blocks (ticket #143).

        Concatenates the children exactly as ``render_tokens`` does, except
        at a paragraph-to-paragraph boundary: the blank line between them
        would end the Trac list (Trac ends a list at any blank line), so the
        earlier paragraph's last line ends ``" [[BR]][[BR]]"`` and the next
        paragraph follows on the next line. The leading space is always
        there: ``Done![[BR]]`` is Trac's ``!`` escape of the first macro,
        and ``wiki:Foo[[BR]]`` reads as one link target (#29). Any other
        neighbour of a paragraph (blockquote, heading)
        keeps today's blank line, and so does a paragraph with a line of
        passed-through TracWiki (``_item_block_separator``, review F1).
        """
        pieces: list[str] = []
        prev_paragraph = False
        for child in children:
            if child.get("type") == "blank_line":
                continue  # renders as "" and must not split two paragraphs
            rendered = self.render_tokens([child], state)
            is_paragraph = _is_paragraph_token(child)
            if (
                is_paragraph
                and prev_paragraph
                and pieces
                and _item_block_separator(
                    "para", "para", False, pieces[-1], rendered
                )
                == "br"
            ):
                pieces[-1] = pieces[-1].rstrip() + " [[BR]][[BR]]\n"
            pieces.append(rendered)
            prev_paragraph = is_paragraph and bool(rendered.strip())
        return "".join(pieces)

    def _render_after_run(
        self, kind: str, run: list[dict[str, Any]], state: Any
    ) -> tuple[str, str]:
        """Render one non-quote run of a list item that follows the item's
        own text, and name its kind for `_item_block_separator`: a ``text``
        run that starts with a paragraph is ``para`` (ticket #143). The
        rendering is the TracWiki before the global bracket restore, which is
        what the separator's predicates see. One method for the writer and
        for `find_list_item_splits` (#176), so the two cannot disagree."""
        rendered = self._render_text_run(run, state).strip("\n")
        if rendered:
            first = next(
                (c for c in run if c.get("type") != "blank_line"), {}
            )
            if kind == "text" and _is_paragraph_token(first):
                kind = "para"
        return kind, rendered

    def paragraph(self, text: str) -> str:
        """Render paragraph."""
        return f"{text}\n\n"

    def block_text(self, text: str) -> str:
        """Render block text."""
        return text

    def block_code(self, code: str, info: str | None = None) -> str:
        """Render code block.

        TracWiki syntax:
        {{{#!language
        code
        }}}

        Language identifiers are mapped from Markdown to TracWiki equivalents
        (e.g., 'bash' -> 'sh'). A language Trac does not highlight is
        written as `{{{#!text lang=NAME`, never as a processor Trac lacks
        (error box) or owns (div, span, comment, default); see
        `common.fence_processor` (ticket #240).

        A {{{ }}} block nested inside another arrives here as one
        `block_code` call whose `code` still contains the inner block's
        delimiters as literal text -- Trac renders the outer block
        verbatim, so those delimiters are content, not a construct of
        their own (ticket #72), and are emitted back unchanged. A helper
        used to rewrite a quoted backtick fence found in that literal
        text into a `{{{ }}}` block (ticket #51); removed rather than
        kept, since by ticket #88 it had become a pure source of
        corruption -- rewriting fences the author quoted on purpose, that
        were never a real nested block at all.

        Ends with a blank line (two trailing newlines), like `paragraph()`
        and `table()`, so a sibling block immediately following in the
        Markdown source stays separated after conversion rather than
        running together with this one on the next read (ticket #53). Any
        caller that needs this block's own trailing whitespace collapsed
        -- `list_item()`, `block_quote()` -- already `rstrip("\\n")`s its
        children's combined text before using it, so the extra newline
        here never leaks into their output.
        """
        if info and info.strip() == _LEGACY_EXPORT_FENCE_INFO:
            raise ValueError(
                "markdown_to_tracwiki: a "
                f"{_LEGACY_EXPORT_FENCE_INFO} fence is the round-trip "
                "protocol of the TracWiki -> Markdown converter removed in "
                "#214, so this Markdown was exported from Trac before #210; "
                "edit the page as TracWiki instead (wiki_get, or "
                "`trac-mcp wiki-pull PAGE FILE --format tracwiki`)."
            )
        code = code.rstrip("\n")
        if info and info.split():
            # Map Markdown language to TracWiki processor directive, or
            # to a plain `#!text lang=NAME` block when Trac has none
            # (ticket #240)
            opener, downgraded = fence_processor(info)
            if downgraded is not None:
                self.downgraded_fences.append((downgraded, opener))
            return f"{{{{{{{opener}\n{code}\n}}}}}}\n\n"
        else:
            return f"{{{{{{\n{code}\n}}}}}}\n\n"

    def block_quote(self, text: str) -> str:
        """Render blockquote.

        TracWiki uses two-space indent for quotes.

        Ends with a blank line for the same reason `block_code()` does
        (ticket #53) -- `text.rstrip("\\n")` before quoting means an
        extra trailing newline from a nested child never leaks into the
        quoted lines here.
        """
        lines = text.rstrip("\n").split("\n")
        if self._citation:
            # Ticket #145: a list item's quote is Trac's citation syntax,
            # which keeps it inside the <li>. An empty line is a bare ">"
            # (no trailing space); a nested quote nests the markers
            # ("> > x"), since ">   x" reads back as a plain paragraph.
            quoted = "\n".join(
                f"> {line}" if line else ">" for line in lines
            )
        else:
            quoted = "\n".join(f"  {line}" for line in lines)
        return f"{quoted}\n\n"

    def _render_item_quote(
        self, children: list[dict[str, Any]], state: Any
    ) -> str:
        """Render a list item's blockquote child as citation lines (#145).

        The quote's content is a document of its own, so a list in it
        restarts at depth 0 (``> `` plus the list's own `` * l1``), however
        deep the item is; the list state is put back afterwards.
        """
        old_depth = getattr(state, "list_depth", -1)
        old_citation = self._citation
        state.list_depth = -1
        self._citation = True
        try:
            return self.render_tokens(children, state)
        finally:
            self._citation = old_citation
            state.list_depth = old_depth

    def block_html(self, html: str) -> str:
        """Render block HTML (pass through)."""
        return html + "\n"

    def block_error(self, text: str) -> str:
        """Render block error."""
        return text

    def thematic_break(self) -> str:
        """Render horizontal rule.

        Ends with a blank line for the same reason `block_code()` does
        (ticket #53).
        """
        return "----\n\n"

    def list(self, text: str, ordered: bool, **attrs) -> str:
        """Render list.

        Emits a trailing blank line after the list's own content so it is
        always separated from whatever block follows. This matters when a
        paragraph sits tight against the list in the Markdown source (no
        blank line between them): CommonMark then treats that paragraph as
        a lazy continuation of the *last list item* rather than a sibling
        block, so it never passes through `paragraph()` (which supplies
        its own trailing blank line) -- without this, the separator that
        should terminate that absorbed paragraph gets consumed as the
        separator that terminated the list instead, silently merging it
        into whatever real paragraph comes next (ticket #52). Safe for
        nested lists too: `render_token`'s list-item branch already strips
        all surrounding newlines from each rendered run (a nested list, or
        a continuation paragraph after one) before joining the runs, so
        the extra blank line added here never leaks into a parent item.
        """
        return text + "\n"

    def list_item(self, text: str) -> str:
        """Render list item.

        TracWiki uses space prefix:
        Unordered: ' * item'
        Ordered: ' 1. item'
        Nested: ' * * nested'

        The nesting is handled by tracking depth in the render_token override.
        """
        # Clean up extra newlines from nested content
        text = text.rstrip("\n")
        return text + "\n"

    def link(self, text: str, url: str, title=None) -> str:
        """Render link.

        Markdown: [text](url)
        TracWiki: [url text] for external URLs
                  [url text] for already-resolved TracLinks (wiki:, ticket:, ...)
                  [wiki:page text] for internal wiki pages

        Refuses non-URL-shaped "links" (e.g., sentinels like ``auto-pm:``)
        so state-marker syntax such as ``[auto-pm: state NEEDS_CODE]``
        is kept as typed instead of getting mangled into a broken
        TracWiki link.
        """
        # A link's label is opaque to Trac's WikiFormatting -- undo any
        # CamelCase "!"-escaping text() applied before it knew this text
        # was headed into a link rather than plain prose (ticket #27).
        text = _unescape_camelcase(text)

        # External URLs - no prefix needed
        if url.startswith(("http://", "https://", "ftp://", "mailto:")):
            return f"[{url} {text}]"

        # Anchor-only links - keep as-is
        if url.startswith("#"):
            return f"[{url} {text}]"

        # Server-relative targets (`//other_instance/ticket/13`) are the
        # sanctioned way to link across Trac instances on the same host
        # without hardcoding scheme/host/port. A leading "//" with no
        # scheme is a valid URL reference (protocol-relative in the
        # general case, server-relative as Trac uses it) -- pass it
        # through verbatim rather than falling into the "no ':'" internal
        # wiki-page branch below, which would wrongly wiki:-prefix it into
        # a dead link (ticket #40).
        if url.startswith("//"):
            return f"[{url} {text}]"

        # Already-resolved TracLinks (`wiki:Page`, `ticket:42`,
        # `source:trunk/f.py`, ...) are valid TracWiki targets as they
        # stand — emit them verbatim; wrapping one in a second `wiki:`
        # prefix would corrupt it (ticket #17).
        traclink = SCHEME_RE.match(url)
        if (
            traclink
            and traclink.group("scheme").lower() in TRACLINK_SCHEMES
        ):
            # `<wiki:Page>` autolinks arrive with text == url; `[target]`
            # is the tidier equivalent of `[target target]`.
            if text == url:
                return f"[{url}]"
            return f"[{url} {text}]"

        # Refuse non-URL-shaped "links". A real URL or wiki link either
        # starts with a known scheme (handled above), is an anchor
        # (handled above), or is a wiki-page-shaped path. Wiki page names
        # never contain ":" — Trac reserves it for the resolvers listed in
        # TRACLINK_SCHEMES — so any ":" still present here means the url
        # is a sentinel like "auto-pm:" or "foo:bar", not a page path.
        # Emit the original Markdown link syntax verbatim so the text is
        # preserved downstream rather than wrapped as a broken wiki link.
        if ":" in url:
            return f"[{text}]({url})"

        # Internal wiki links - add wiki: prefix
        return f"[wiki:{url} {text}]"

    def image(self, text: str, url: str, title=None) -> str:
        """Render image.

        Markdown: ![alt](url)
        TracWiki: [[Image(url)]]
        """
        return f"[[Image({url})]]"

    def newline(self) -> str:
        """Render newline."""
        return ""

    def inline_html(self, html: str) -> str:
        """Render inline HTML (pass through)."""
        return html

    # Table rendering methods for GFM tables
    def table(self, text: str) -> str:
        """Render complete table.

        TracWiki tables use ||cell|| syntax.
        Tables are block elements and should be separated from other content.
        """
        # Reset alignments after table is complete
        self._table_alignments = []
        # Table is a block element, add trailing newlines for paragraph separation
        return text.rstrip("\n") + "\n\n"

    def table_head(self, text: str) -> str:
        """Render table header section.

        Header cells are concatenated by mistune with || between them.
        We strip the trailing || from cells and wrap the whole row.
        """
        # Cells are concatenated with || between them (each cell adds trailing ||)
        # Remove the trailing || and wrap with || on both ends
        text = text.rstrip("|")
        return f"||{text}||\n"

    def table_body(self, text: str) -> str:
        """Render table body section."""
        return text

    def table_row(self, text: str) -> str:
        """Render table row.

        Body cells are concatenated by mistune with || between them.
        We strip the trailing || from cells and wrap the whole row.
        """
        # Cells are concatenated with || between them (each cell adds trailing ||)
        # Remove the trailing || and wrap with || on both ends
        text = text.rstrip("|")
        return f"||{text}||\n"

    def table_cell(
        self, text: str, align: str | None = None, head: bool = False
    ) -> str:
        """Render table cell.

        Args:
            text: Cell content
            align: Alignment ('left', 'center', 'right', or None)
            head: True if this is a header cell

        TracWiki alignment is determined by whitespace:
        - Left aligned: ||text || (text flush left, space right)
        - Right aligned: || text|| (space left, text flush right)
        - Centered: || text || (space both sides)

        TracWiki header cells use ||= Header =|| syntax.

        Note: Cells are concatenated by mistune. We add || after each cell,
        and table_row/table_head will strip the trailing || and wrap properly.
        """
        # For header cells, wrap with = markers and apply alignment
        if head:
            # Handle empty cells. A bare "" would concatenate directly
            # against the adjacent cell's leading "||", producing "||||" --
            # TracWiki's colspan-2 marker, not two separate cells -- and
            # shifting every following header left by one (ticket #20). A
            # single space keeps the cell genuinely empty without merging.
            if not text:
                cell_content = " "
            else:
                match align:
                    case "left":
                        cell_content = f"={text} ="
                    case "right":
                        cell_content = f"= {text}="
                    case "center":
                        cell_content = f"= {text} ="
                    case _:
                        # No alignment: minimal spacing
                        cell_content = f"={text}="
        else:
            # Apply TracWiki alignment via whitespace for body cells
            match align:
                case "left":
                    # Left aligned: text flush left, space on right
                    cell_content = f"{text} "
                case "right":
                    # Right aligned: space on left, text flush right
                    cell_content = f" {text}"
                case "center":
                    # Centered: space on both sides
                    cell_content = f" {text} "
                case _:
                    # No alignment: just the text. Same "||||" colspan
                    # hazard as the header branch above applies to an
                    # empty body cell too, so fall back to a single space.
                    cell_content = text or " "

        # Add || separator after cell (will be concatenated with next cell)
        return cell_content + "||"

    def render_token(self, token: dict[str, Any], state) -> str:
        """Override token rendering to handle list depth tracking and extract text/attrs.

        Every branch funnels through the ``result = ...`` / fallthrough
        pattern below rather than returning directly, so the single
        bookkeeping line after the ``match`` can update ``self._last_char``
        for whichever token was actually emitted -- children are rendered
        (and update the state themselves) before a branch's own wrapping
        text is appended, so this always reflects the true last character
        of this call's return value, not an intermediate child's (ticket
        #29).
        """
        # Get the token type
        token_type: str = token.get("type") or ""
        func = self._get_method(token_type)
        attrs = token.get("attrs")

        match token_type:
            # For lists, track ordered state and reset item counter
            case "list":
                ordered = (attrs or {}).get("ordered", False)
                depth = getattr(
                    state, "list_depth", -1
                )  # Start at -1 so first level is 0

                # Save current state
                old_ordered = getattr(state, "list_ordered", False)
                old_depth = depth
                old_item_num = getattr(state, "list_item_num", 0)

                # Set new state
                state.list_ordered = ordered  # pyright: ignore[reportAttributeAccessIssue]  # mistune BlockState dynamic attr
                state.list_depth = depth + 1  # pyright: ignore[reportAttributeAccessIssue]  # mistune BlockState dynamic attr
                # Ticket #150: number from the list's own start (mistune
                # sets `start` only when it is not 1). Trac renders
                # `<ol start="N">` from the FIRST item's number and counts
                # on from there whatever the later items say (measured on
                # /trac_test: ` 5.` / ` 7.` / ` 9.` and ` 3.` / ` 1.` both
                # count up from the first), so consecutive numbers from
                # `start` are what Trac shows.
                start = (attrs or {}).get("start", 1)
                state.list_item_num = start - 1  # pyright: ignore[reportAttributeAccessIssue]  # mistune BlockState dynamic attr

                # Render children
                if "children" in token:
                    text = self.render_tokens(token["children"], state)
                else:
                    text = ""

                # Restore state
                state.list_ordered = old_ordered  # pyright: ignore[reportAttributeAccessIssue]  # mistune BlockState dynamic attr
                state.list_depth = old_depth  # pyright: ignore[reportAttributeAccessIssue]  # mistune BlockState dynamic attr
                state.list_item_num = old_item_num  # pyright: ignore[reportAttributeAccessIssue]  # mistune BlockState dynamic attr

                # Call list renderer with text and ordered flag
                if attrs:
                    result = func(text, **attrs)
                else:
                    result = func(text, False)

            # For list items, we need to determine depth and type
            case "list_item":
                # Ticket #145 review (F2): an item's children are not in
                # the citation of a quote this item sits in. Only the
                # item's own quote runs are (`_render_item_quote`); a first-
                # child quote of a nested item keeps the pre-#145 form.
                outer_citation = self._citation
                self._citation = False

                # Track list depth from state
                depth = getattr(state, "list_depth", 0)

                # Check if parent list is ordered
                ordered = getattr(state, "list_ordered", False)

                # Increment and get item number
                item_num = getattr(state, "list_item_num", 0) + 1
                state.list_item_num = item_num  # pyright: ignore[reportAttributeAccessIssue]  # mistune BlockState dynamic attr

                # Determine marker
                if ordered:
                    marker = f"{item_num}."
                else:
                    marker = "*"

                # Render children - check if there's a nested list
                if "children" in token:
                    children = token["children"]
                    # Split the children into runs, in SOURCE order
                    # (`_item_runs`, shared with `find_list_item_splits`):
                    # a run of non-list blocks, or one nested list. Ticket
                    # #112: collecting "all non-lists, then all lists"
                    # moved a paragraph that follows a sub-list above it.
                    # Ticket #142: a GFM table child is its own run kind
                    # ("table"): `||` rows indented under an item end the
                    # list in Trac (measured on /trac_test), so it is
                    # rendered as a `{{{#!div}}}` block below.
                    # Ticket #144 (and the code-block half of #143): a
                    # fenced code child is its own run kind ("code"), for
                    # the same reason -- it goes under the item text at the
                    # continuation indent with no blank line before it, in
                    # a tight or a loose item alike (Trac strips the indent
                    # back off the body).
                    # Ticket #145: a blockquote child is its own run kind
                    # ("quote"), rendered as Trac citation lines. Not when
                    # it is the item's first non-blank child: no Trac form
                    # exists for a quote with no item text before it (` *`
                    # alone is not an item, ` * > q` renders a literal
                    # ">"), so it stays in the text run, as before. Nor
                    # when it is empty: nothing to cite.
                    runs = _item_runs(children)

                    # The item's own text is the run before the first list.
                    if runs and runs[0][0] == "text":
                        text = self._render_text_run(runs[0][1], state)
                        runs = runs[1:]
                    else:
                        text = ""

                    # Everything after it, in order. A nested list handles
                    # its own newlines; a later non-list run is a
                    # continuation paragraph that follows a sub-list.
                    # Ticket #143: a text run that starts with a paragraph
                    # is marked "para" so the combine step below can leave
                    # out the blank line after a code or table run.
                    # A quote run is rendered in the combine step below
                    # (#145), once its separator has said which form it
                    # takes; its entry carries the tokens, not the text.
                    after_runs: list[
                        tuple[str, str, list[dict[str, Any]]]
                    ] = []
                    for kind, run in runs:
                        if kind == "quote":
                            after_runs.append((kind, "", run))
                            continue
                        kind, rendered = self._render_after_run(
                            kind, run, state
                        )
                        if rendered:
                            after_runs.append((kind, rendered, run))
                else:
                    text = token.get("raw", "")
                    after_runs = []

                # Build TracWiki list item with proper depth
                # TracWiki uses indentation for nesting: 1 space for level 0, +2 spaces per level
                # Depth 0: " * item" (1 space + marker)
                # Depth 1: "   * item" (3 spaces + marker)
                # Depth 2: "     * item" (5 spaces + marker)
                indent = " " * (depth * 2 + 1)
                prefix = f"{indent}{marker}"

                text = text.rstrip("\n")
                continuation_indent = " " * (len(prefix) + 1)
                # Ticket #178: where a TEXT continuation line goes. The item
                # text column (`len(prefix) + 1`) is marker column + 2 for a
                # bullet, within Trac's bound, but marker + 4 for an ordered
                # item numbered 10 or more (+ 5 from 100): Trac continues an
                # ordered item only up to marker + 3 (measured on /trac_test,
                # depth 0 and 1), and a text line past that ends the list. So
                # an ordered item's text lines are capped at marker + 3. Only
                # text: a code or table block joins at any indent (#144), and
                # a `>` citation line stays inside the item at marker + 4
                # (measured), so those keep the text column
                # (`continuation_indent`), except a #197 marker quote,
                # which goes at the item's marker column (`len(indent)`).
                # The cap holds only
                # while Trac still has the list open: a line after an EMPTY
                # line keeps `continuation_indent` (`_indent_text_lines`).
                # For a bullet the bound IS the text column, so the `min` is
                # a no-op there.
                text_indent = " " * min(
                    len(prefix) + 1,
                    trac_continuation_bound(
                        len(indent), ordered=ordered
                    ),
                )

                # A soft break inside the item (or a blank line between two
                # loose paragraphs) lands in `text` as a literal "\n": at
                # column zero it dedents the continuation out of the item
                # into its own paragraph on the way back through Trac's
                # renderer (ticket #75). Indent every line after the first
                # to line up under the item text -- Trac's list grammar
                # only requires "deeper than the marker", not an exact
                # column, but aligning under the text is what the source
                # itself does. A blank line stays blank rather than
                # picking up trailing whitespace.
                if "\n" in text:
                    lines = text.split("\n")
                    text = "\n".join(
                        [lines[0]]
                        + _indent_text_lines(
                            lines[1:],
                            text_indent,
                            continuation_indent,
                            list_open=True,
                        )
                    )

                # Combine the item text with what follows it, in source
                # order. A continuation paragraph after a sub-list goes
                # between the item's marker and the nested marker, with no
                # blank line (`_item_block_separator`'s "between", #176):
                # its first line closes the nested list and continues THIS
                # item.
                # Where that is refused it is set off by a blank line and
                # indented under the item text; Trac renders it as a
                # blockquote (a Trac limit, #112), but the order is the
                # author's.
                parts = [f"{prefix} {text}"]
                prev_kind = "para" if text.strip() else ""
                prev_text = text
                list_seen = False
                for kind, rendered, run in after_runs:
                    emitted_kind = kind
                    if kind == "list":
                        list_seen = True
                        parts.append(rendered)
                    elif kind == "quote":
                        # Ticket #145: citation lines at this item's
                        # continuation indent, directly under what came
                        # before; `_item_block_separator` says when Trac
                        # cannot take them, and the pre-#145 form stays.
                        # Rendered once, in the form chosen.
                        separator = _item_block_separator(
                            prev_kind, kind, list_seen, prev_text, ""
                        )
                        quote_indent = continuation_indent
                        if separator == "blank":
                            parts.append("")
                            rendered = self._render_text_run(
                                run, state
                            ).strip("\n")
                            emitted_kind = "plain_quote"
                        else:
                            rendered = self._render_item_quote(
                                run, state
                            ).strip("\n")
                            if separator == "marker":
                                # #197: directly after a nested list the
                                # `>` goes at this item's MARKER column, no
                                # blank line: that closes the nested
                                # list(s) and the citation stays in this
                                # item (measured). It is innermost again,
                                # so `list_seen` resets; what follows is as
                                # after any citation (a quote
                                # `"quote_gap"` at the continuation indent,
                                # text `"adjacent"`).
                                quote_indent = " " * len(indent)
                                list_seen = False
                            elif separator == "quote_gap":
                                parts.append(continuation_indent + ">")
                        parts.extend(
                            _indent_nonblank(
                                rendered.split("\n"), quote_indent
                            )
                        )
                    elif kind == "code":
                        # Ticket #144. Appended at THIS item's continuation
                        # indent, in source order. Directly after a nested
                        # list that is a Trac limit, parallel to #112's
                        # continuation paragraph: Trac attaches a block to
                        # the innermost open item (measured, F5), so here it
                        # lands on the nested item. After a "between"
                        # paragraph (#176) this item is innermost again
                        # (`list_seen` was reset), so the block joins it.
                        parts.extend(
                            _indent_nonblank(
                                rendered.split("\n"),
                                continuation_indent,
                            )
                        )
                    elif kind == "table":
                        # Wrapped in a `{{{#!div}}}` so Trac keeps the
                        # table inside the <li>, and appended with NO blank
                        # line before it: Trac ends a list at any blank
                        # line, so the loose-item separator used for
                        # continuation paragraphs would split the list.
                        parts.extend(
                            _indent_nonblank(
                                f"{{{{{{#!div\n{rendered}\n}}}}}}".split(
                                    "\n"
                                ),
                                continuation_indent,
                            )
                        )
                    else:
                        # Ticket #143: the blank line (or its absence)
                        # before a text run is `_item_block_separator`'s
                        # call, in one place for this and for the join
                        # inside a run.
                        separator = _item_block_separator(
                            prev_kind, kind, list_seen, "", rendered
                        )
                        if separator == "between":
                            # #176: no blank line. The FIRST line goes one
                            # column past this item's marker, which closes
                            # the nested list; this item is innermost again
                            # (measured C10, Q1, D1, N1), so every later
                            # line is a continuation line at the usual
                            # indent (measured C6: both paragraphs stay in
                            # the item), and what follows behaves as in an
                            # item with no nested list. The later lines go
                            # at `text_indent`, the text column (marker + 3
                            # for an ordered item numbered 10 or more, #178):
                            # a line at marker + 1 after a `[[BR]][[BR]]`
                            # break would leave the item.
                            first, *rest = rendered.split("\n")
                            parts.append(
                                " " * (len(indent) + 1) + first
                            )
                            parts.extend(
                                _indent_nonblank(rest, text_indent)
                            )
                            list_seen = False
                        else:
                            # The #178 cap only while no empty line has
                            # ended the list: not after this separator's
                            # own blank line, nor after one inside the run.
                            if separator != "adjacent":
                                parts.append("")
                            parts.extend(
                                _indent_text_lines(
                                    rendered.split("\n"),
                                    text_indent,
                                    continuation_indent,
                                    list_open=separator == "adjacent",
                                )
                            )
                    prev_kind = emitted_kind
                    prev_text = rendered
                result = "\n".join(parts) + "\n"
                self._citation = outer_citation

            # Default rendering: extract text from raw, text, or children, pass attrs
            case _:
                if "raw" in token:
                    text = token["raw"]
                elif "text" in token:
                    # Used by table_cell tokens
                    text = token["text"]
                elif "children" in token:
                    if token_type == "link":
                        # A link's label is where a numeric bracket shape
                        # is not escaped (`numeric_bracket`).
                        self._link_depth += 1
                        try:
                            text = self.render_tokens(
                                token["children"], state
                            )
                        finally:
                            self._link_depth -= 1
                    else:
                        text = self.render_tokens(
                            token["children"], state
                        )
                else:
                    # No text content, just call with attrs
                    if attrs:
                        result = func(**attrs)
                    else:
                        result = func()
                    if result:
                        self._last_char = result[-1]
                    return result

                # Call function with text and attrs
                if attrs:
                    result = func(text, **attrs)
                else:
                    result = func(text)

        if result:
            self._last_char = result[-1]
        return result


# Markdown footnote syntax (ticket #63).  TracWiki has no footnote construct,
# and this is the one Markdown input the write leg cannot express -- see
# _reject_unrepresentable_footnotes for why it fails the call.
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\n]+)\]:", re.MULTILINE)
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\n]+)\](?!:)")


def _markdown_verbatim_mask(text: str) -> str:
    """Return ``text`` with code regions blanked to ``\\x01`` fillers.

    Exists because a check that runs before the converter's own stashing
    has to do its own shielding, or it fires on syntax the author quoted
    rather than wrote.  Newlines are preserved so the mask stays
    line-aligned and ``re.MULTILINE`` anchors still land correctly.

    Covers fenced blocks and inline code spans.  Indented code blocks need no
    handling here: a footnote *definition* is anchored to column 0, so an
    indented line cannot match it, and a bare reference inside one is
    harmless (measured -- it passes through untouched).
    """
    mask = list(text)
    in_fence = False
    fence_char = ""
    fence_len = 0
    pos = 0
    for line in text.split("\n"):
        stripped = line.lstrip()
        is_fence = False
        for ch in ("`", "~"):
            if stripped.startswith(ch * 3):
                run = len(stripped) - len(stripped.lstrip(ch))
                if not in_fence:
                    in_fence, fence_char, fence_len = True, ch, run
                    is_fence = True
                elif ch == fence_char and run >= fence_len:
                    in_fence = False
                    is_fence = True
                break
        if in_fence and not is_fence:
            for k in range(pos, pos + len(line)):
                mask[k] = "\x01"
        elif not in_fence and not is_fence:
            # Inline code spans on an ordinary line.
            i = 0
            while i < len(line):
                if line[i] == "`":
                    j = line.find("`", i + 1)
                    if j == -1:
                        break
                    for k in range(pos + i + 1, pos + j):
                        mask[k] = "\x01"
                    i = j + 1
                else:
                    i += 1
        pos += len(line) + 1
    return "".join(mask)


def _reject_unrepresentable_footnotes(markdown_text: str) -> None:
    """Fail the conversion on a Markdown footnote that would be destroyed.

    Operator decision on ticket #63, option (c).  Wrapping the construct
    verbatim does not work here: a footnote is not a block.  Its reference
    and its definition sit in *different places* in the document, so wrapping
    either half alone breaks the pairing and wrapping the whole document to
    catch both is disproportionate.

    Measured on ticket #63, and the reason the trigger is this narrow -- only
    the PAIR is destructive:

        Text[^1] + [^1]: note   ->  Text[wiki:note ^1]   definition DELETED,
                                                         reference replaced by
                                                         a fabricated wiki link
        Text[^1] alone          ->  unchanged
        [^1]: orphan alone      ->  unchanged

    Only the pair is rejected, so an author who happens to write a bracketed
    caret in prose is unaffected.  The escape hatch is real and costs one
    parameter: declare the source as TracWiki and nothing converts at all.
    """
    masked = _markdown_verbatim_mask(markdown_text)
    defined = {m.group(1) for m in _FOOTNOTE_DEF_RE.finditer(masked)}
    if not defined:
        return
    referenced = {m.group(1) for m in _FOOTNOTE_REF_RE.finditer(masked)}
    both = sorted(defined & referenced)
    if not both:
        return
    names = ", ".join(f"[^{n}]" for n in both)
    raise ValueError(
        "markdown_to_tracwiki: TracWiki has no footnote construct, and "
        f"converting would destroy these: {names}. The definition line is "
        "deleted outright and the reference becomes a link to a page named "
        "after the note text, with no warning -- so this fails the call "
        "instead (ticket #63). Either remove the footnote, or declare the "
        'source as TracWiki (format="tracwiki") so nothing converts.'
    )


def _render_markdown(
    markdown_text: str,
    *,
    heading_anchors: bool,
    flavor: str,
    downgraded_fences: list[tuple[str, str]] | None = None,
) -> str:
    """Convert Markdown that has already been through the flavour's
    pre-normalisers (`apply_flavor`).

    ``downgraded_fences``, when given, is extended with the
    ``(name, opener)`` of each code fence written as a plain ``#!text``
    block, with placeholders resolved (ticket #240)."""
    # Stash code spans / [[...]] / single-bracket link
    # spans before mistune ever sees a "`" or a "[" (see
    # _stash_bracket_syntax for why this can't happen inside text()).
    # Must run before the renderer is constructed -- heading() needs the
    # placeholder table to resolve a sentinel before slugifying.
    _reject_unrepresentable_footnotes(markdown_text)

    stashed_text, placeholders = _stash_for_flavor(
        markdown_text, flavor
    )

    # Create renderer and parser with table plugin enabled
    renderer = TracWikiRenderer(
        heading_anchors=heading_anchors, placeholders=placeholders
    )
    markdown = mistune.create_markdown(
        renderer=renderer,
        plugins=[*_MISTUNE_PLUGINS, *flavor_plugins(flavor)],
    )

    # Parse and render
    result: str = markdown(stashed_text)  # pyright: ignore[reportAssignmentType]
    if downgraded_fences is not None:
        downgraded_fences.extend(
            (
                _restore_bracket_syntax(name, placeholders),
                _restore_bracket_syntax(opener, placeholders),
            )
            for name, opener in renderer.downgraded_fences
        )

    result = _restore_bracket_syntax(result, placeholders)

    # Clean up extra newlines (but preserve double newlines for paragraph separation)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = result.rstrip("\n")

    if "\x00" in result:
        raise ValueError(
            "markdown_to_tracwiki: unrestored placeholder sentinel (NUL "
            "byte) survived to converter output -- a stash/restore pass "
            "has a bug; failing loudly instead of emitting corrupted "
            "content (see ticket #51)"
        )

    return result


def markdown_to_tracwiki(
    markdown_text: str,
    *,
    heading_anchors: bool = False,
    flavor: str = DEFAULT_FLAVOR,
) -> str:
    """
    Convert Markdown text to TracWiki format.

    Args:
        markdown_text: Markdown formatted text
        heading_anchors: When True, each heading includes an explicit
            ``#slug`` anchor for Markdown cross-reference compatibility.
            Default is False: Trac auto-generates anchors and explicit slugs
            can be misread as ticket references (e.g. ``#4-non-goals`` → #4).
        flavor: The Markdown flavour profile of the source
            (``converters.flavors.FLAVORS``). The default, ``commonmark``,
            changes nothing; ``chatgpt`` and ``gemini`` normalise pasted
            bot output first (Trac /trac_mcp_server #216). Raises
            ValueError for an unknown name.

    Returns:
        TracWiki formatted text
    """
    markdown_text, _ = apply_flavor(markdown_text, flavor)
    return _render_markdown(
        markdown_text, heading_anchors=heading_anchors, flavor=flavor
    )


_GEMINI_HINT = (
    'Line "Use code with caution." looks like text copied from a Gemini '
    'page; convert with flavor "gemini" (--flavor gemini) to rebuild its '
    "code blocks."
)


def convert_with_warnings(
    markdown_text: str,
    *,
    heading_anchors: bool = False,
    flavor: str = DEFAULT_FLAVOR,
) -> ConversionResult:
    """
    Convert Markdown to TracWiki and detect unsupported features.

    Args:
        markdown_text: Markdown formatted text
        heading_anchors: Forwarded to :func:`markdown_to_tracwiki`.  When
            True, headings include an explicit ``#slug`` anchor for
            Markdown cross-reference compatibility.  Default is False.
        flavor: Forwarded to :func:`markdown_to_tracwiki`. The flavour's
            own notes (what its pre-normalisers rebuilt) come first in
            ``warnings``; under the default, text copied from a Gemini page
            gets a hint to use ``gemini``, and is never switched
            automatically.

    Returns:
        ConversionResult with TracWiki text and any warnings
    """
    markdown_text, warnings = apply_flavor(markdown_text, flavor)

    # Tables are now fully supported via mistune table plugin

    # Check for HTML tags
    if re.search(r"<[a-zA-Z][^>]*>", markdown_text):
        warnings.append(
            "HTML tags detected - these may not render correctly in TracWiki."
        )

    # Check for TOC macros
    if re.search(r"\[TOC\]|\[\[TOC\]\]", markdown_text, re.IGNORECASE):
        warnings.append(
            "TOC macro detected - use [[PageOutline]] in TracWiki instead."
        )

    # Ticket #216: that shape is corrupted silently by the default (a
    # `# comment` becomes a heading, `__name__` becomes bold).
    if flavor == DEFAULT_FLAVOR and has_gemini_code_marker(
        markdown_text
    ):
        warnings.append(_GEMINI_HINT)

    # Convert the markdown
    downgraded_fences: list[tuple[str, str]] = []
    tracwiki = _render_markdown(
        markdown_text,
        heading_anchors=heading_anchors,
        flavor=flavor,
        downgraded_fences=downgraded_fences,
    )

    # Ticket #240: one warning per distinct name, in order of appearance,
    # naming the opener that was written.
    seen: set[str] = set()
    for name, opener in downgraded_fences:
        if name in seen:
            continue
        seen.add(name)
        warnings.append(
            f"Code fence language '{name}' is not a language Trac "
            f"highlights; written as a plain code block "
            f"({{{{{{{opener}}}}}}})."
        )

    return ConversionResult(
        text=tracwiki,
        source_format="markdown",
        target_format="tracwiki",
        converted=True,
        warnings=warnings,
        normalised_source=(
            markdown_text if flavor != DEFAULT_FLAVOR else None
        ),
    )


# -- Ticket #125: a list item that continues after its nested list.

# Human label for the block that follows a nested list, keyed by mistune
# token type. Any type not named here is reported as its token type.
_SPLIT_BLOCK_LABELS = {
    "paragraph": "paragraph",
    "block_text": "paragraph",
    "block_code": "code block",
    "block_quote": "blockquote",
    "table": "table",
    "thematic_break": "horizontal rule",
    "heading": "heading",
    "block_html": "HTML block",
}

_SPLIT_ITEM_MAX_CHARS = 60


def _token_text(token: dict[str, Any]) -> str:
    """Plain text of a mistune AST token; line breaks become newlines."""
    if token.get("type") in ("softbreak", "linebreak"):
        return "\n"
    if "raw" in token:
        return str(token["raw"])
    return "".join(_token_text(c) for c in token.get("children", []))


def find_list_item_splits(
    markdown_text: str, *, flavor: str = DEFAULT_FLAVOR
) -> list[dict]:
    """List items whose content continues after a nested list (#125).

    Trac cannot continue a list item below a blank line once a nested list
    has started: it ends the list, renders the following block outside it (a
    paragraph as a blockquote, a fenced block as a `<pre>`), and starts a new
    list for the next item. Since #112 the converter keeps an item's children
    in source order, so this shape converts faithfully and then renders
    split, with nothing to tell the author.

    Two forms keep the item whole. A paragraph run directly after the
    nested list (#176) is started by the writer at a column between the
    item's marker and the nested marker, with no blank line
    (`_item_block_separator`'s ``"between"``). A blockquote run directly
    after it (#197) has its ``>`` lines at the item's marker column with no
    blank line (``"marker"``), which closes the nested list. Neither is a
    finding, and the walk resets as the writer's does: the item is innermost
    again, so a block after it is the same as in an item with no nested
    list. The runs are `_item_runs`, the writer's own grouping, and the
    decision is the writer's too: a text run, and the nested list a quote
    follows, are rendered by the same renderer method
    (`_render_after_run`) and put through the same
    `_item_block_separator` call. A quote the writer refuses (the nested
    list's text holds a blank line or ends with a citation line) is a
    finding. A text run the writer refuses (a
    passthrough line, a blank line of its own, a first line that starts a
    block in the TracWiki, not in the Markdown) is still a finding, and so
    is any other block (code, table) directly after a nested list.

    Parses the same way `markdown_to_tracwiki` does (mistune's AST with
    the same `_MISTUNE_PLUGINS`, over the text after `_stash_bracket_syntax`), so
    the detector and the converter agree on what is a list and what is
    inside a fenced block. Nested items count too.

    Returns one dict per offending item, in document order, stopping at
    the first offending block within an item:
    ``{"item": <first line of the item's leading text, restored and
    truncated to 60 chars>, "block": <label>, "depth": <0 for a
    top-level item, 1 for an item of a nested list, ...>}``.

    ``markdown_text`` is the text that was converted, so for a flavour it
    is the output of `apply_flavor`, not the raw paste (#216); ``flavor``
    only adds that flavour's parser rules, as the converter has them.
    """
    stashed, placeholders = _stash_for_flavor(markdown_text, flavor)
    parse = mistune.create_markdown(
        renderer=None,
        plugins=[*_MISTUNE_PLUGINS, *flavor_plugins(flavor)],
    )
    # renderer=None makes mistune return its AST: a list of token dicts.
    tokens = cast("list[dict[str, Any]]", parse(stashed))
    findings: list[dict] = []
    # Renders a run as the writer would (#176); the placeholders stay
    # stashed, as they are when the writer's separator reads the text.
    renderer = TracWikiRenderer(placeholders=placeholders)
    render_state = mistune.BlockState()

    def walk(nodes: list[dict[str, Any]], depth: int) -> None:
        for node in nodes:
            kind = node.get("type")
            children = node.get("children", [])
            if kind == "list_item":
                seen_list = False
                list_text = ""
                for run_kind, whole_run in _item_runs(children):
                    run = [
                        c
                        for c in whole_run
                        if c.get("type") != "blank_line"
                    ]
                    if not run:
                        continue
                    child_type = str(run[0].get("type"))
                    if run_kind == "list":
                        seen_list = True
                        # The writer's text for this run, which its
                        # separator reads for a quote after it (#197). Out
                        # of depth context: the two refusals read only
                        # whether it holds a blank line and what its last
                        # line is.
                        renderer._last_char = "\n"
                        _, list_text = renderer._render_after_run(
                            run_kind, whole_run, render_state
                        )
                    elif seen_list:
                        if (
                            run_kind == "quote"
                            and _item_block_separator(
                                "list", "quote", True, list_text, ""
                            )
                            == "marker"
                        ):
                            # #197: the writer puts a quote directly after
                            # a nested list at the marker column, which
                            # keeps it in the item and closes the list.
                            seen_list = False
                            continue
                        if run_kind == "text":
                            renderer._last_char = "\n"
                            kind_after, rendered = (
                                renderer._render_after_run(
                                    run_kind, whole_run, render_state
                                )
                            )
                            if (
                                rendered
                                and _item_block_separator(
                                    "list",
                                    kind_after,
                                    True,
                                    "",
                                    rendered,
                                )
                                == "between"
                            ):
                                seen_list = False
                                continue
                        lead = next(
                            (
                                c
                                for c in children
                                if c.get("type") != "blank_line"
                            ),
                            {},
                        )
                        text = (
                            ""
                            if lead.get("type") == "list"
                            else _restore_bracket_syntax(
                                _token_text(lead), placeholders
                            )
                        )
                        findings.append(
                            {
                                "item": text.split("\n")[0][
                                    :_SPLIT_ITEM_MAX_CHARS
                                ],
                                "block": _SPLIT_BLOCK_LABELS.get(
                                    child_type, child_type
                                ),
                                "depth": depth,
                            }
                        )
                        break
            walk(children, depth + 1 if kind == "list" else depth)

    walk(tokens, -1)
    return findings


def describe_list_item_split(split: dict) -> str:
    """One-sentence message for a `find_list_item_splits` entry."""
    block = split["block"]
    article = "an" if block[:1].lower() in "aeiou" else "a"
    subject = (
        f'List item "{split["item"]}"'
        if split["item"]
        else "A list item that starts with a nested list"
    )
    # Trac renders a paragraph left outside a list as a blockquote; the
    # other blocks have no such aside.
    aside = ", as a blockquote" if block == "paragraph" else ""
    return (
        f"{subject} continues with {article} {block} "
        "after its nested list. Trac cannot continue a list item there: "
        f"it ends the list and renders the {block} outside it{aside}; "
        "any later items start a new list. "
        f"Move the {block} out of the list in the source, or accept "
        "the split."
    )
