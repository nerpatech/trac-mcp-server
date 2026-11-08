"""Markdown to TracWiki conversion using mistune AST rendering."""

import re
from typing import Any, Literal

import mistune

from .common import (
    SCHEME_RE,
    TRACLINK_SCHEMES,
    ConversionResult,
    markdown_to_tracwiki_lang,
    trac_continuation_bound,
)
from .tracwiki_to_markdown import _LIST_MARKER_RE, FALLBACK_FENCE_INFO

# GitHub-style heading slug, mirrored from auto-pm's docs_linkcheck rule
# (lowercase, whitespace runs → single dash, drop everything that isn't
# alphanumeric / dash / underscore). Inline TracWiki markers produced by
# the renderer pipeline (`backticks`, `'''bold'''`, `''italic''`) are
# stripped before the rule runs so the slug derives from the *visible*
# heading text, not its render-side decoration.
_SLUG_DROP_RE = re.compile(r"[^\w\- ]+")
_SLUG_WS_RE = re.compile(r"\s+")

# Double-bracket syntax ([[Page]], [[Page|Label]], [[TOC]], ...) typed
# directly in Markdown source, and -- since ticket #63 -- also the form a
# macro arrives in from `tracwiki_to_markdown`, which now leaves it
# literal rather than flattening it to `[MACRO: ...]` for this module to
# rebuild. Stashed before parsing starts, so a macro/link name that
# happens to be CamelCase-shaped (e.g. [[PageOutline]]) never has the
# CamelCase pass below stuff a `!` inside the brackets. This one pass now
# carries the whole wiki_get -> edit -> wiki_update round trip that
# ticket #19 added `_MACRO_PLACEHOLDER_RE` for.
_BRACKET_SYNTAX_RE = re.compile(r"\[\[[^\]\n]*\]\]")

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
# spans are handled, mirroring the equivalent regex on the read-path
# converter (`tracwiki_to_markdown._convert_code_blocks`).
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
_BARE_TRACLINK_RE = re.compile(
    r"\b(?:[A-Za-z][\w.+-]*:)?"
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
    text = _BRACKET_SYNTAX_RE.sub(stash_literal, text)
    text = _SINGLE_BRACKET_LINK_RE.sub(stash_literal, text)
    return text, placeholders


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
# `common` so both conversion directions share one definition — a target
# this module emits verbatim must not be re-parsed as a link by
# `tracwiki_to_markdown` on the way back (tickets #8, #13, #14, #17).


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
    nothing after one, and the read leg reads those lines as the next
    block's. A blank line stays blank."""
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


def _is_fallback_fence(info: str | None) -> bool:
    """True when a fenced block's info string is the #63 verbatim-unwrap
    marker (`FALLBACK_FENCE_INFO`). The one test, for `block_code()` and
    the list-item run split."""
    return bool(info) and info.strip() == FALLBACK_FENCE_INFO


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
    ``a.``, ``ii.``; the grammar of the read leg's `_LIST_MARKER_RE`).
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
    ``code`` (#144: a fenced block that is not the #63 fallback fence, whose
    body is TracWiki source and stays in the text run) and ``text`` (every
    other child, adjacent ones merged into one run). Every list, table, code
    block and quote is a run of its own.
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
            and not _is_fallback_fence(
                (child.get("attrs") or {}).get("info")
            )
            else "text"
        )
        if child_type != "blank_line":
            content_seen = True
        if runs and kind == "text" and runs[-1][0] == "text":
            runs[-1][1].append(child)
        else:
            runs.append((kind, [child]))
    return runs


def _item_block_separator(
    prev_kind: str,
    next_kind: str,
    list_seen: bool,
    prev_text: str,
    next_text: str,
) -> Literal["br", "adjacent", "blank", "quote_gap", "between"]:
    """How two neighbouring blocks of a list item are separated (#143, #145).

    Trac ends a list at any blank line, so the pre-#143 "blank" is the
    fallback and the other two keep the block inside the ``<li>``:

    * ``"br"``: the earlier paragraph's last line ends ``" [[BR]][[BR]]"``
      and the next paragraph follows on the next line. Kinds ``para`` then
      ``para``.
    * ``"adjacent"``: the next paragraph goes on the line after a ``code``
      or ``table`` child, no blank line.
    * ``"blank"``: everything else (#63 fallback fence, a text run after a
      nested list that ``"between"`` refuses, ...).
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

    A ``quote`` (#145) is Trac's citation syntax, ``> `` lines at the item's
    continuation indent: ``"adjacent"`` after the item text (kind ``para``),
    a ``para``/``text`` run, or a ``code``/``table`` child, and a following
    paragraph is ``"adjacent"`` to it too. ``"blank"`` keeps the pre-#145
    form (blank line, two-space-indented quote) where Trac cannot take the
    citation: once a nested list has appeared and no paragraph has closed it
    since (it would attach to the innermost nested item, #112/#125; a
    ``"between"`` run closes it, #176), and after a run with a passthrough
    line (F1 below).

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

    def text(self, text: str) -> str:
        """Render plain text.

        Defensively `!`-prefixes bare CamelCase-shaped prose words (Trac's
        own WikiCamelCase auto-link pattern, e.g. WiFi, LoRa) so Trac's
        renderer treats them as literal text instead of attempting a
        broken missing-page link. Markdown has no CamelCase-auto-link
        concept, so any such word reaching this method was never meant as
        a link (ticket #27).

        Literal `[[...]]` syntax typed in the Markdown source (or arriving
        from `tracwiki_to_markdown`) never reaches this method -- it is
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
        """

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

    def codespan(self, text: str) -> str:
        """Render inline code."""
        return f"`{text}`"

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
        neighbour of a paragraph (blockquote, heading, #63 fallback fence)
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
        (e.g., 'bash' -> 'sh').

        A {{{ }}} block nested inside another arrives here as one
        `block_code` call whose `code` still contains the inner block's
        delimiters as literal text -- Trac renders the outer block
        verbatim, so those delimiters are content, not a construct of
        their own (ticket #72), and are emitted back unchanged. A helper
        used to rewrite a quoted backtick fence found in that literal
        text into a `{{{ }}}` block, restoring a shape the read leg no
        longer produces (ticket #51, superseded by #72's read-leg fix);
        removed rather than kept, since by ticket #88 it had become a
        pure source of corruption -- rewriting fences the author quoted
        on purpose, that were never a real nested block at all.

        Ends with a blank line (two trailing newlines), like `paragraph()`
        and `table()`, so a sibling block immediately following in the
        Markdown source stays separated after conversion rather than
        running together with this one on the next read (ticket #53). Any
        caller that needs this block's own trailing whitespace collapsed
        -- `list_item()`, `block_quote()` -- already `rstrip("\\n")`s its
        children's combined text before using it, so the extra newline
        here never leaks into their output.
        """
        code = code.rstrip("\n")
        if _is_fallback_fence(info):
            # Unwrap, per the operator decision on ticket #63: the read leg
            # emitted this fence around TracWiki it could not express, and the
            # body is the ORIGINAL SOURCE verbatim.  Restoring it is a literal
            # unwrap with nothing to infer -- unlike the [MACRO: ...]
            # placeholder deleted earlier on this ticket, which had to be
            # reconstructed by guessing and carried the bugs that came with
            # that.  Without this, a read-edit-write cycle would demote the
            # construct to a literal code block: text-lossless but no longer a
            # table.
            return f"{code}\n\n"
        if info:
            # Map Markdown language to TracWiki processor directive
            tracwiki_lang = markdown_to_tracwiki_lang(info)
            return f"{{{{{{#!{tracwiki_lang}\n{code}\n}}}}}}\n\n"
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
        state.list_depth = -1  # type: ignore[attr-defined]  # mistune BlockState dynamic attr
        self._citation = True
        try:
            return self.render_tokens(children, state)
        finally:
            self._citation = old_citation
            state.list_depth = old_depth  # type: ignore[attr-defined]  # mistune BlockState dynamic attr

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
        survives round-tripping instead of getting mangled into a broken
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
        # stand — emit them verbatim. This is what `tracwiki_to_markdown`
        # produces, so a wiki_get -> wiki_update round-trip that leaves
        # existing links untouched no longer corrupts them (ticket #17).
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
                state.list_ordered = ordered  # type: ignore[attr-defined]  # mistune BlockState dynamic attr
                state.list_depth = depth + 1  # type: ignore[attr-defined]  # mistune BlockState dynamic attr
                # Ticket #150: number from the list's own start (mistune
                # sets `start` only when it is not 1). Trac renders
                # `<ol start="N">` from the FIRST item's number and counts
                # on from there whatever the later items say (measured on
                # /trac_test: ` 5.` / ` 7.` / ` 9.` and ` 3.` / ` 1.` both
                # count up from the first), so consecutive numbers from
                # `start` are what Trac shows.
                start = (attrs or {}).get("start", 1)
                state.list_item_num = start - 1  # type: ignore[attr-defined]  # mistune BlockState dynamic attr

                # Render children
                if "children" in token:
                    text = self.render_tokens(token["children"], state)
                else:
                    text = ""

                # Restore state
                state.list_ordered = old_ordered  # type: ignore[attr-defined]  # mistune BlockState dynamic attr
                state.list_depth = old_depth  # type: ignore[attr-defined]  # mistune BlockState dynamic attr
                state.list_item_num = old_item_num  # type: ignore[attr-defined]  # mistune BlockState dynamic attr

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
                state.list_item_num = item_num  # type: ignore[attr-defined]  # mistune BlockState dynamic attr

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
                    # back off the body). A #63 fallback fence stays in the
                    # text run: its body is TracWiki source, unwrapped.
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
                # (`continuation_indent`). The read leg moves a line at this
                # column back to the Markdown text column. The cap holds only
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
                        if separator == "blank":
                            parts.append("")
                            rendered = self._render_text_run(
                                run, state
                            ).strip("\n")
                            emitted_kind = "plain_quote"
                        else:
                            if separator == "quote_gap":
                                parts.append(continuation_indent + ">")
                            rendered = self._render_item_quote(
                                run, state
                            ).strip("\n")
                        parts.extend(
                            _indent_nonblank(
                                rendered.split("\n"),
                                continuation_indent,
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
                            # item with no nested list. The read leg needs
                            # the later lines at `text_indent`, the text
                            # column (marker + 3 for an ordered item numbered
                            # 10 or more, #178, which it moves to the text
                            # column): it turns `[[BR]][[BR]]` into a blank
                            # line, and a line at marker + 1 after one would
                            # leave the item.
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
                    text = self.render_tokens(token["children"], state)
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
# _reject_unrepresentable_footnotes for why it fails the call rather than
# wrapping the way the read leg does.
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\n]+)\]:", re.MULTILINE)
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\n]+)\](?!:)")


def _markdown_verbatim_mask(text: str) -> str:
    """Return ``text`` with code regions blanked to ``\\x01`` fillers.

    Mirrors ``tracwiki_to_markdown._verbatim_mask`` on the other leg, and
    exists for the same reason: a check that runs before the converter's own
    stashing has to do its own shielding, or it fires on syntax the author
    quoted rather than wrote.  Newlines are preserved so the mask stays
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

    Operator decision on ticket #63, option (c).  The read leg answers an
    unrepresentable construct by emitting it verbatim in a fallback fence, but
    that answer does not work here: a footnote is not a block.  Its reference
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


def markdown_to_tracwiki(
    markdown_text: str, *, heading_anchors: bool = False
) -> str:
    """
    Convert Markdown text to TracWiki format.

    Args:
        markdown_text: Markdown formatted text
        heading_anchors: When True, each heading includes an explicit
            ``#slug`` anchor for Markdown cross-reference compatibility.
            Default is False: Trac auto-generates anchors and explicit slugs
            can be misread as ticket references (e.g. ``#4-non-goals`` → #4).

    Returns:
        TracWiki formatted text
    """
    # Stash code spans / [[...]] / single-bracket link
    # spans before mistune ever sees a "`" or a "[" (see
    # _stash_bracket_syntax for why this can't happen inside text()).
    # Must run before the renderer is constructed -- heading() needs the
    # placeholder table to resolve a sentinel before slugifying.
    _reject_unrepresentable_footnotes(markdown_text)

    stashed_text, placeholders = _stash_bracket_syntax(markdown_text)

    # Create renderer and parser with table plugin enabled
    renderer = TracWikiRenderer(
        heading_anchors=heading_anchors, placeholders=placeholders
    )
    markdown = mistune.create_markdown(
        renderer=renderer, plugins=_MISTUNE_PLUGINS
    )

    # Parse and render
    result: str = markdown(stashed_text)  # type: ignore[assignment]

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


def convert_with_warnings(
    markdown_text: str, *, heading_anchors: bool = False
) -> ConversionResult:
    """
    Convert Markdown to TracWiki and detect unsupported features.

    Args:
        markdown_text: Markdown formatted text
        heading_anchors: Forwarded to :func:`markdown_to_tracwiki`.  When
            True, headings include an explicit ``#slug`` anchor for
            Markdown cross-reference compatibility.  Default is False.

    Returns:
        ConversionResult with TracWiki text and any warnings
    """
    warnings = []

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

    # Convert the markdown
    tracwiki = markdown_to_tracwiki(
        markdown_text, heading_anchors=heading_anchors
    )

    return ConversionResult(
        text=tracwiki,
        source_format="markdown",
        target_format="tracwiki",
        converted=True,
        warnings=warnings,
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


def find_list_item_splits(markdown_text: str) -> list[dict]:
    """List items whose content continues after a nested list (#125).

    Trac cannot continue a list item below a blank line once a nested list
    has started: it ends the list, renders the following block outside it (a
    paragraph as a blockquote, a fenced block as a `<pre>`), and starts a new
    list for the next item. Since #112 the converter keeps an item's children
    in source order, so this shape converts faithfully and then renders
    split, with nothing to tell the author.

    One form keeps the item whole (#176): a paragraph run directly after the
    nested list, which the writer starts at a column between the item's marker
    and the nested marker, with no blank line (`_item_block_separator`'s
    ``"between"``). It is not a finding, and the walk resets as the writer's
    does: the item is innermost again, so a block after it is the same as in
    an item with no nested list. The runs are `_item_runs`, the writer's own
    grouping, and the decision is the writer's too: the run is rendered by
    the same renderer method (`_render_after_run`) and put through the same
    `_item_block_separator` call. A run the writer refuses (a passthrough
    line, a blank line of its own, a first line that starts a block in the
    TracWiki, not in the Markdown) is still a finding, and so is any other
    block directly after a nested list.

    Parses the same way `markdown_to_tracwiki` does (mistune's AST with
    the same `_MISTUNE_PLUGINS`, over the text after `_stash_bracket_syntax`), so
    the detector and the converter agree on what is a list and what is
    inside a fenced block. Nested items count too.

    Returns one dict per offending item, in document order, stopping at
    the first offending block within an item:
    ``{"item": <first line of the item's leading text, restored and
    truncated to 60 chars>, "block": <label>, "depth": <0 for a
    top-level item, 1 for an item of a nested list, ...>}``.
    """
    stashed, placeholders = _stash_bracket_syntax(markdown_text)
    parse = mistune.create_markdown(
        renderer=None, plugins=_MISTUNE_PLUGINS
    )
    tokens = parse(stashed)
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
                for run_kind, whole_run in _item_runs(children):
                    run = [
                        c
                        for c in whole_run
                        if c.get("type") != "blank_line"
                    ]
                    if not run:
                        continue
                    child_type = run[0].get("type")
                    if run_kind == "list":
                        seen_list = True
                    elif seen_list:
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
                                    child_type, str(child_type)
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
