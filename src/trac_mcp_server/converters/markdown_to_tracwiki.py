"""Markdown to TracWiki conversion using mistune AST rendering."""

import re
from typing import Any

import mistune

from .common import (
    SCHEME_RE,
    TRACLINK_SCHEMES,
    ConversionResult,
    markdown_to_tracwiki_lang,
)

# GitHub-style heading slug, mirrored from auto-pm's docs_linkcheck rule
# (lowercase, whitespace runs → single dash, drop everything that isn't
# alphanumeric / dash / underscore). Inline TracWiki markers produced by
# the renderer pipeline (`backticks`, `'''bold'''`, `''italic''`) are
# stripped before the rule runs so the slug derives from the *visible*
# heading text, not its render-side decoration.
_SLUG_DROP_RE = re.compile(r"[^\w\- ]+")
_SLUG_WS_RE = re.compile(r"\s+")

# `tracwiki_to_markdown`'s "bracket" mode placeholder for a macro it
# couldn't resolve, e.g. `[MACRO: PageOutline]` or `[MACRO: TOC(depth=2)]`.
# Restored back to `[[PageOutline]]` / `[[TOC(depth=2)]]` before parsing
# even starts (see `_stash_bracket_syntax`) so a wiki_get -> edit ->
# wiki_update round trip doesn't flatten the macro permanently (ticket #19).
_MACRO_PLACEHOLDER_RE = re.compile(r"\[MACRO:\s*([^\]]+)\]")

# Double-bracket syntax ([[Page]], [[Page|Label]], [[TOC]], ...) typed
# directly in Markdown source. Stashed the same way `_MACRO_PLACEHOLDER_RE`
# spans are, before parsing starts, so a macro/link name that happens to be
# CamelCase-shaped (e.g. [[PageOutline]]) never has the CamelCase pass
# below stuff a `!` inside the brackets.
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
_CAMELCASE_ESCAPE_RE = re.compile(r"!(?=[A-Z][a-z]+(?:[A-Z][a-z]*)+\b)")

# A bare absolute URL sitting directly in prose (no Markdown link syntax
# around it, no backticks). A resolved `[text](url)`/autolink never
# reaches `text()` at all -- mistune hands its url straight to `link()`
# -- so any URL text() sees here is one mistune left as literal prose. A
# CamelCase-shaped path segment inside it is part of the address, not a
# word Trac's WikiCamelCase grammar should ever see (ticket #44).
_URL_IN_TEXT_RE = re.compile(r"(?:https?|ftps?)://\S+", re.IGNORECASE)

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
    """Replace `[MACRO: ...]` / `[[...]]` spans with sentinel placeholders.

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

    def stash_macro(m: re.Match[str]) -> str:
        placeholders.append(f"[[{m.group(1)}]]")
        return f"\x00WK{len(placeholders) - 1}\x00"

    def stash_literal(m: re.Match[str]) -> str:
        placeholders.append(m.group(0))
        return f"\x00WK{len(placeholders) - 1}\x00"

    def stash_code_span(m: re.Match[str]) -> str:
        placeholders.append(m.group(1))
        return f"`\x00WK{len(placeholders) - 1}\x00`"

    text = _CODE_SPAN_RE.sub(stash_code_span, markdown_text)
    text = _MACRO_PLACEHOLDER_RE.sub(stash_macro, text)
    text = _BRACKET_SYNTAX_RE.sub(stash_literal, text)
    text = _SINGLE_BRACKET_LINK_RE.sub(stash_literal, text)
    return text, placeholders


def _restore_bracket_syntax(text: str, placeholders: list[str]) -> str:
    """Restore sentinels stashed by `_stash_bracket_syntax` after rendering."""

    def restore(m: re.Match[str]) -> str:
        return placeholders[int(m.group(1))]

    return _PLACEHOLDER_RE.sub(restore, text)


# The TracLink resolver allowlist and `scheme:target` pattern live in
# `common` so both conversion directions share one definition — a target
# this module emits verbatim must not be re-parsed as a link by
# `tracwiki_to_markdown` on the way back (tickets #8, #13, #14, #17).


# A backtick-fenced block sitting literally inside another code block's
# body. CommonMark only lets an *outer* fence be closed by a line with at
# least as many backticks as the opener, so a properly-nested inner fence
# (shorter than its enclosing one, per `tracwiki_to_markdown._fence_for`)
# survives into `block_code`'s `code` argument as plain text rather than
# being parsed as its own block -- see `_restore_nested_fences` (ticket #51).
_NESTED_FENCE_RE = re.compile(
    r"^(`{3,})(\w*)\n(.*?)\n\1[ \t]*$", re.DOTALL | re.MULTILINE
)


def _restore_nested_fences(code: str) -> str:
    """Recursively convert literal backtick fences inside a code block's
    body back into TracWiki ``{{{ }}}`` blocks.

    `tracwiki_to_markdown` emits a nested {{{ }}} block by widening the
    *outer* fence so it never collides with the inner one it contains
    (ticket #51's fix on the read side). On the way back, mistune hands
    that inner fence to `block_code` as inert literal text -- it never
    becomes its own `block_code` call -- so it has to be recognized and
    restored here instead of by the renderer's normal per-token dispatch.
    """

    def restore(m: re.Match[str]) -> str:
        info = m.group(2)
        inner = _restore_nested_fences(m.group(3))
        if info:
            tracwiki_lang = markdown_to_tracwiki_lang(info)
            return f"{{{{{{#!{tracwiki_lang}\n{inner}\n}}}}}}"
        return f"{{{{{{\n{inner}\n}}}}}}"

    return _NESTED_FENCE_RE.sub(restore, code)


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

    def text(self, text: str) -> str:
        """Render plain text.

        Defensively `!`-prefixes bare CamelCase-shaped prose words (Trac's
        own WikiCamelCase auto-link pattern, e.g. WiFi, LoRa) so Trac's
        renderer treats them as literal text instead of attempting a
        broken missing-page link. Markdown has no CamelCase-auto-link
        concept, so any such word reaching this method was never meant as
        a link (ticket #27).

        `[MACRO: Name]` placeholders and literal `[[...]]` syntax typed in
        the Markdown source never reach this method as such -- they're
        stashed as sentinel placeholders by `_stash_bracket_syntax` before
        mistune even starts parsing (ticket #19), so a macro/page name
        that happens to be CamelCase-shaped is never escaped inside its
        brackets.

        A bare URL DOES still reach this method as literal text -- it
        can't be pre-stashed the way `[[...]]` is without risking a real
        Markdown link's own `(url)` destination, so it's located and
        skipped inline instead (ticket #44); see `_URL_IN_TEXT_RE`. The
        single-bracket TracWiki/InterTrac link form is handled earlier,
        by pre-stashing (`_SINGLE_BRACKET_LINK_RE`) rather than here --
        mistune splits an unresolved "[...]" into separate text() calls
        (the "[" on its own), so this method never sees that span whole.
        """

        def escape(segment: str) -> str:
            return _CAMELCASE_RE.sub(
                lambda m: f"!{m.group(0)}", segment
            )

        out: list[str] = []
        last = 0
        for m in _URL_IN_TEXT_RE.finditer(text):
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

        Restores any nested fence first (see `_restore_nested_fences`) --
        a {{{ }}} block nested inside another arrives here as one
        `block_code` call whose `code` still contains the inner fence as
        literal text (ticket #51).

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
        code = _restore_nested_fences(code)
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
        quoted = "\n".join(f"  {line}" for line in lines)
        return f"{quoted}\n\n"

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
        nested lists too: `list_item`'s handling of a nested list already
        strips all trailing newlines from `nested_text` before splicing it
        in, so the extra blank line added here never leaks into a parent
        item.
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
                ordered = token.get("attrs", {}).get("ordered", False)
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
                state.list_item_num = 0  # type: ignore[attr-defined]  # mistune BlockState dynamic attr

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
                    # Separate inline content from nested lists
                    inline_parts = []
                    nested_lists = []

                    for child in children:
                        if child.get("type") == "list":
                            nested_lists.append(child)
                        else:
                            inline_parts.append(child)

                    # Render inline content
                    if inline_parts:
                        text = self.render_tokens(inline_parts, state)
                    else:
                        text = ""

                    # Render nested lists (they handle their own newlines)
                    if nested_lists:
                        nested_text = self.render_tokens(
                            nested_lists, state
                        )
                        # The nested list adds its items directly, don't add to text
                        nested_text = nested_text.rstrip("\n")
                    else:
                        nested_text = ""
                else:
                    text = token.get("raw", "")
                    nested_text = ""

                # Build TracWiki list item with proper depth
                # TracWiki uses indentation for nesting: 1 space for level 0, +2 spaces per level
                # Depth 0: " * item" (1 space + marker)
                # Depth 1: "   * item" (3 spaces + marker)
                # Depth 2: "     * item" (5 spaces + marker)
                indent = " " * (depth * 2 + 1)
                prefix = f"{indent}{marker}"

                text = text.rstrip("\n")

                # Combine text and nested list
                if nested_text:
                    result = f"{prefix} {text}\n{nested_text}\n"
                else:
                    result = f"{prefix} {text}\n"

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
    # Stash code spans / [MACRO: ...] / [[...]] / single-bracket link
    # spans before mistune ever sees a "`" or a "[" (see
    # _stash_bracket_syntax for why this can't happen inside text()).
    # Must run before the renderer is constructed -- heading() needs the
    # placeholder table to resolve a sentinel before slugifying.
    stashed_text, placeholders = _stash_bracket_syntax(markdown_text)

    # Create renderer and parser with table plugin enabled
    renderer = TracWikiRenderer(
        heading_anchors=heading_anchors, placeholders=placeholders
    )
    markdown = mistune.create_markdown(
        renderer=renderer, plugins=["table"]
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
