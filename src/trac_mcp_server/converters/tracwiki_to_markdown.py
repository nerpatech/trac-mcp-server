"""TracWiki to Markdown conversion using regex patterns."""

import re
from typing import Literal

from .common import (
    ConversionResult,
    is_link_target,
    tracwiki_to_markdown_lang,
)

# Type alias for the unknown_macros rendering mode.
#
# The "bracket" mode -- emit ``[MACRO: Name]`` -- was removed for ticket
# #63. It existed only so the reverse converter could re-absorb its own
# output, and ``markdown_to_tracwiki`` carried a matching
# ``_MACRO_PLACEHOLDER_RE`` purely to undo it. "preserve" reaches the same
# round trip without the detour, by leaving ``[[Name]]`` literal for the
# bracket-stashing pass that already exists on the other side. Measured
# across 46 KB of real store content: the two modes produced byte-identical
# Markdown and byte-identical round trips, and no store page emitted a
# single placeholder.
UnknownMacros = Literal["preserve", "drop"]

# Known Trac macro names reachable via ``[[Name]]`` / ``[[Name(args)]]``
# syntax. Anything else shaped like ``[[Word]]`` or ``[[Word|Label]]`` is a
# WikiLink, not a macro -- mirrors how Trac's own macro resolver falls back
# to a page link for names it doesn't recognize (ticket #28).
_KNOWN_MACROS = frozenset(
    {
        "toc",
        "pageoutline",
        "recentchanges",
        "titleindex",
        "intertrac",
        "interwiki",
        "interwikimap",
        "ticketquery",
        "include",
        "timeline",
        "translatedpages",
        "traciniticket",
        "tracguidetoc",
        "workflow",
        "listtagged",
        "macrolist",
        "tagcloud",
        "span",
        "knownmimetypes",
        "milestone",
        "repos",
    }
)


# Info string on the fence tracwiki_to_markdown emits for a construct that has
# no Markdown equivalent (ticket #63).  Deliberately NOT the bare "tracwiki"
# that someone documenting TracWiki inside a Markdown file would type: this tag
# is the signal markdown_to_tracwiki unwraps on, so a collision with a
# hand-written code sample would inject live markup where a code block was
# meant -- the exact class of corruption this ticket removes.
FALLBACK_FENCE_INFO = "tracwiki-unconverted"

# Processor blocks with no Markdown equivalent, emitted verbatim rather than
# rebuilt best-effort.  "table"/"td"/"th" are table structure Markdown cannot
# express; "comment" is content Trac drops entirely, which the old fenced-block
# rendering made *visible* (see Reference/trac/WikiEscapeContexts).
_FALLBACK_PROCESSOR_RE = re.compile(r"\{\{\{#!(comment|table|td|th)\b")

# A TracWiki definition-list term line (ticket #71).
#
# The grammar was measured against Trac's own renderer rather than inferred --
# ``convert_preview`` with ``format="tracwiki"``, reading the HTML for a
# ``<dl class="wiki">`` -- because the previous pattern, ``^(\s*)(.+?)::\s*(.+)$``,
# claimed any line containing a double colon and rewrote ordinary prose such as
# "Use std::vector here." as a bold term.  Three rules, each load-bearing:
#
# 1. ``[ \t]+`` -- leading whitespace is REQUIRED.  At column zero Trac renders
#    ``term:: definition`` as a paragraph, so anchoring the term to the start of
#    the line (as ticket #71 section 4 first proposed) would keep matching
#    exactly the prose Trac treats as prose.
# 2. ``(?:(?!::).)+`` -- a tempered dot, so the term cannot span a double colon
#    and the pair matched is necessarily the FIRST one on the line.  A plain
#    non-greedy ``.+?`` is not enough: it backtracks past a first pair that
#    fails the lookahead and matches a later one, which would convert
#    " std::vector:: a C++ type" that Trac renders as a blockquote.
# 3. ``(?=[ \t]|$)`` -- the colons must be followed by whitespace or the end of
#    the line.  This is what excludes ``std::vector`` and ``3::1``.
#
# The term itself may contain spaces: " multi word term:: a definition" IS a
# definition list to Trac, so forbidding whitespace inside it -- section 4's
# other suggestion -- would have traded a false-positive class for a
# false-negative one.
#
# The definition may be empty (" trailing colons only::"), which the old
# pattern's ``(.+)$`` required to be non-empty and therefore MISSED: a real
# definition list drawing neither warning nor conversion.
_DEFINITION_LIST_RE = re.compile(
    r"^([ \t]+)((?:(?!::).)+)::(?=[ \t]|$)[ \t]*(.*)$"
)


def _convert_definition_lists(text: str) -> str:
    """Convert TracWiki definition lists to a bold term plus a colon.

    Line-based rather than a single ``re.sub`` so that a term whose definition
    sits on the following indented line can absorb it (ticket #71).  Leaving
    that line in place let the blockquote pass claim it and emit ``> `` inside
    the definition; Trac reads it as part of the ``<dd>``.
    """
    lines = text.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        match = _DEFINITION_LIST_RE.match(lines[index])
        if match is None:
            out.append(lines[index])
            index += 1
            continue

        indent, term, definition = match.groups()
        index += 1
        if not definition:
            # Absorb continuation lines: indented further than the term, up to
            # the first blank line or a line that dedents back out.
            continuation: list[str] = []
            while index < len(lines):
                following = lines[index]
                if not following.strip():
                    break
                following_indent = len(following) - len(
                    following.lstrip()
                )
                if following_indent <= len(indent):
                    break
                continuation.append(following.strip())
                index += 1
            definition = " ".join(continuation)

        converted = f"{indent}**{term}**:"
        if definition:
            converted += f" {definition}"
        out.append(converted)

    return "\n".join(out)


class TracWikiParser:
    """Parser for converting TracWiki syntax to Markdown format."""

    def __init__(self, *, unknown_macros: UnknownMacros = "preserve"):
        """Initialize parser with empty warnings list.

        Args:
            unknown_macros: Controls how unrecognized ``[[MacroName]]`` tokens
                are rendered in the Markdown output.

                - ``"preserve"`` (default): leave ``[[MacroName]]`` literal
                  in the output so the TracWiki syntax survives the
                  conversion unchanged, and the round trip back through
                  ``markdown_to_tracwiki`` restores it byte-for-byte.
                - ``"drop"``: silently omit the macro from the output.

                Controlled by the ``--unknown-macros`` CLI flag.  Known macros
                (``Image``, ``BR``) are unaffected regardless of this setting.
        """
        self.warnings: list[str] = []
        self._unknown_macros = unknown_macros
        self._link_placeholders: list[str] = []
        self._code_placeholders: list[str] = []

    def parse(self, tracwiki_text: str) -> ConversionResult:
        """
        Parse TracWiki text and convert to Markdown format.

        This is a best-effort conversion using regex replacements. Unknown TracWiki
        macros and unsupported features pass through unchanged without errors.

        Args:
            tracwiki_text: TracWiki formatted text

        Returns:
            ConversionResult with Markdown text and warnings about lossy conversions
        """
        self.warnings = []
        self._link_placeholders = []
        self._code_placeholders = []
        self._detect_lossy_elements(tracwiki_text)

        text = tracwiki_text
        text = self._apply_fallbacks(text)
        text = self._convert_code_blocks(text)
        text = self._convert_macros(text)
        text = self._convert_headings(text)
        text = self._convert_formatting(text)
        text = self._convert_links(text)
        text = self._convert_lists(text)
        text = self._convert_other_elements(text)
        text = self._convert_tables(text)
        text = self._restore_link_placeholders(text)
        text = self._restore_code_placeholders(text)

        if "\x00" in text:
            raise ValueError(
                "tracwiki_to_markdown: unrestored placeholder sentinel "
                "(NUL byte) survived to converter output -- a stash/"
                "restore pass has a bug; failing loudly instead of "
                "emitting corrupted content (see ticket #51)"
            )

        return ConversionResult(
            text=text,
            source_format="tracwiki",
            target_format="markdown",
            converted=True,
            warnings=self.warnings,
        )

    def _detect_lossy_elements(self, text: str) -> None:
        """Detect lossy elements before conversion and add warnings."""
        # Detect unsupported macros (preserved but not functional).
        # Only genuine macro names trigger this -- [[Page]] / [[Page|Label]]
        # WikiLinks are converted to real Markdown links, not placeholders.
        for m in re.finditer(
            r"\[\[(?!Image|BR)(\w+)(\([^)]*\))?\]\]",
            text,
            re.IGNORECASE,
        ):
            if self._is_backtick_wrapped(text, m.start(), m.end()):
                continue
            if m.group(1).lower() in _KNOWN_MACROS or m.group(2):
                # Mode-aware: the two modes are lossy to different degrees,
                # and the old text named a [MACRO: ...] notation that is no
                # longer emitted in either of them (ticket #63).
                if self._unknown_macros == "drop":
                    self.warnings.append(
                        "Trac macros detected - omitted from the output entirely (--unknown-macros=drop)"
                    )
                else:
                    self.warnings.append(
                        "Trac macros detected - left literal as [[Name]] (inert in Markdown renderers, restored unchanged converting back)"
                    )
                break

        # Detect definition lists.  Shares _DEFINITION_LIST_RE with the
        # conversion itself: this warning used to carry its own copy of the
        # pattern, so the two could drift and -- ticket #71 -- the warning
        # fired on every line containing a double colon, announcing a
        # definition list in prose that has none.  Tickets #57 and #59 are
        # this project's evidence that an over-firing check gets muted and
        # takes its true positives with it.
        if any(
            _DEFINITION_LIST_RE.match(line) for line in text.split("\n")
        ):
            self.warnings.append(
                "Definition lists detected - converted to bold text (semantic preservation)"
            )

        # Table-feature and processor-cell warnings used to live here and
        # described a best-effort reconstruction -- "merged into single cell",
        # "converted to plain text".  Ticket #63 replaced that reconstruction
        # with a verbatim fallback, so _stash_fallback now raises the warning
        # at the point it actually fires, naming what it did.  Warning off the
        # source without consulting what the conversion did is the same defect
        # the macro warning had before slice (b) of this ticket.

        # Detect TracLinks
        if re.search(r"(#\d+|ticket:\d+|wiki:\w+|changeset:\w+)", text):
            self.warnings.append(
                "TracLinks detected - preserved as-is (agents can interpret, but not clickable in Markdown renderers)"
            )

    # ------------------------------------------------------------------
    # Unrepresentable-construct fallback (ticket #63)
    # ------------------------------------------------------------------

    @staticmethod
    def _matching_brace(text: str, start: int) -> int:
        """Return the index just past the ``}}}`` closing the ``{{{`` at start.

        Depth-counted rather than regex-matched, because ``{{{#!table}}}``
        legitimately contains ``{{{#!td}}}`` blocks and the lazy ``.*?`` the
        other passes use stops at the *first* ``}}}``, which is the inner
        one.  Returns -1 when the block is unterminated.
        """
        depth = 0
        i = start
        while i < len(text):
            if text.startswith("{{{", i):
                depth += 1
                i += 3
            elif text.startswith("}}}", i):
                depth -= 1
                i += 3
                if depth == 0:
                    return i
            else:
                i += 1
        return -1

    def _stash_fallback(self, source: str, kind: str) -> str:
        """Wrap unrepresentable TracWiki verbatim in a fallback fence.

        The body is stashed behind the same \\x00CODE<n>\\x00 placeholder the
        code-block pass uses, so no later pass rewrites the markup we are
        deliberately preserving as source text.
        """
        self.warnings.append(
            f"{kind} cannot be represented in Markdown - emitted verbatim "
            f"in a {FALLBACK_FENCE_INFO} block; markdown_to_tracwiki "
            f"restores it unchanged"
        )
        self._code_placeholders.append(source)
        placeholder = f"\x00CODE{len(self._code_placeholders) - 1}\x00"
        fence = self._fence_for(source)
        return f"{fence}{FALLBACK_FENCE_INFO}\n{placeholder}\n{fence}"

    def _verbatim_mask(self, text: str) -> str:
        """Return ``text`` with verbatim regions blanked to ``\\x01`` fillers.

        Verbatim means "Trac parses no markup here": the interior of any
        ``{{{ }}}`` block and of any backtick code span.  Newlines are kept so
        the mask stays line-aligned with the source and callers can test the
        mask while emitting the original.

        This exists because the fallback passes run *first* -- before the
        code-block and code-span stashing that shields everything else -- so
        they are the one place in the converter that has to do its own
        shielding.  Without it the fallback fires on a ``{{{#!table}}}`` token
        quoted inside a code span, reopening tickets #45 and #46, and on a
        ``{{{#!comment}}}`` nested inside a plain code block, reopening #51.
        """
        mask = list(text)
        i = 0
        n = len(text)
        while i < n:
            if text.startswith("{{{", i):
                end = self._matching_brace(text, i)
                if end == -1:
                    break
                for j in range(i + 3, min(end - 3, n)):
                    if mask[j] != "\n":
                        mask[j] = "\x01"
                i = end
            elif text[i] == "`":
                j = text.find("`", i + 1)
                nl = text.find("\n", i + 1)
                if j != -1 and (nl == -1 or j < nl):
                    for k in range(i + 1, j):
                        mask[k] = "\x01"
                    i = j + 1
                else:
                    i += 1
            else:
                i += 1
        return "".join(mask)

    def _fallback_processor_blocks(self, text: str) -> str:
        """Emit {{{#!comment}}}, {{{#!table}}}, {{{#!td}}}, {{{#!th}}} verbatim.

        Each has no Markdown equivalent, and each was previously rebuilt
        best-effort into something else -- a fenced block that *displays*
        content Trac drops entirely, or a one-column table that reads back as
        a header cell.  See TestUnrepresentableInventory for the measured
        before-behaviour.

        Only a block at top level qualifies.  One nested inside another
        ``{{{ }}}`` block is that block's content, not a construct of its own
        (ticket #51), and one inside a code span is a quoted token (#46).
        """
        out: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            if text.startswith("{{{", i):
                end = self._matching_brace(text, i)
                if end == -1:
                    out.append(text[i:])
                    break
                m = _FALLBACK_PROCESSOR_RE.match(text, i)
                if m:
                    # Coalesce a run of adjacent fallback blocks into ONE
                    # region.  Emitting a fence each would put the closing
                    # fence of one against the opening fence of the next --
                    # ```````` on a single line, which is not a fence pair at
                    # all -- and would also lose the exact whitespace between
                    # them.  One region round-trips the whole run verbatim.
                    names = [m.group(1).lower()]
                    while True:
                        j = end
                        while j < n and text[j] in " \t\n":
                            j += 1
                        nxt = _FALLBACK_PROCESSOR_RE.match(text, j)
                        if not nxt:
                            break
                        close = self._matching_brace(text, j)
                        if close == -1:
                            break
                        names.append(nxt.group(1).lower())
                        end = close
                    kind = (
                        "Processor block"
                        + ("s " if len(names) > 1 else " ")
                        + ", ".join(
                            f"#!{x}" for x in dict.fromkeys(names)
                        )
                    )
                    out.append(self._stash_fallback(text[i:end], kind))
                else:
                    out.append(text[i:end])
                i = end
            elif text[i] == "`":
                j = text.find("`", i + 1)
                nl = text.find("\n", i + 1)
                if j != -1 and (nl == -1 or j < nl):
                    out.append(text[i : j + 1])
                    i = j + 1
                else:
                    out.append(text[i])
                    i += 1
            else:
                out.append(text[i])
                i += 1
        return "".join(out)

    def _fallback_tables(self, text: str) -> str:
        """Emit a table verbatim when it uses a Markdown-inexpressible feature.

        Two features qualify, both measured on ticket #63 as destroying the
        table on the way back:

        * cell spanning (``||||``) -- the converted row carries fewer cells
          than its header, mistune rejects it, and the whole table is stored
          as literal text;
        * a backslash-continued multi-line row -- the rows are joined into one
          over-wide row that reads back as a *header* row.

        Detection runs against ``_verbatim_mask`` so a ``||||`` quoted in a
        code span does not trigger it (ticket #45), while the emitted text is
        always the original.
        """
        lines = text.split("\n")
        masked = self._verbatim_mask(text).split("\n")
        out: list[str] = []
        i = 0
        while i < len(lines):
            if not masked[i].lstrip().startswith("||"):
                out.append(lines[i])
                i += 1
                continue
            start = i
            while i < len(lines) and masked[i].lstrip().startswith(
                "||"
            ):
                i += 1
            probe = "\n".join(masked[start:i])
            spanning = "||||" in probe
            multiline = any(
                ln.rstrip().endswith("\\") for ln in masked[start:i]
            )
            if spanning or multiline:
                kind = (
                    "Table cell spanning"
                    if spanning
                    else "Multi-line table row"
                )
                out.append(
                    self._stash_fallback(
                        "\n".join(lines[start:i]), kind
                    )
                )
            else:
                out.extend(lines[start:i])
        return "\n".join(out)

    def _apply_fallbacks(self, text: str) -> str:
        """Run every unrepresentable-construct fallback, outermost first."""
        text = self._fallback_processor_blocks(text)
        text = self._fallback_tables(text)
        return text

    # _convert_processor_cells was deleted on ticket #63.  It rebuilt
    # {{{#!td}}} / {{{#!th}}} into ||cell|| markup so the table pass could
    # turn it into a Markdown table -- a reconstruction the verbatim fallback
    # replaces.  Deleting it also fixed a live defect it carried: it ran
    # before the code-block stashing, so a processor cell nested inside a
    # plain code block was rewritten too, destroying quoted source.  See
    # TestUnrepresentableFallback.test_processor_cell_inside_a_code_block_is_content.

    @staticmethod
    def _fence_for(content: str) -> str:
        """Return a backtick fence strictly longer than any backtick run
        already present in ``content``.

        A {{{ }}} block nested inside another is matched innermost-first
        (the lazy ``.*?`` in the regexes below stops at the nearest
        ``}}}``), so by the time the *outer* block is converted, its
        captured ``code`` already contains the inner block's own
        ```` ``` ```` fence as literal text. Emitting a fixed 3-backtick
        fence for the outer block too would collide with it: CommonMark
        closes a fence on the first line with at least as many backticks
        as the opener, so the inner fence would terminate the outer one
        early and the rest of the document would be swallowed as raw
        code (ticket #51).
        """
        longest = 0
        for m in re.finditer(r"`+", content):
            longest = max(longest, len(m.group(0)))
        return "`" * max(3, longest + 1)

    def _convert_code_blocks(self, text: str) -> str:
        """Convert code blocks (after processor cells).

        Code block with language: {{{#!lang\ncode\n}}} -> ```lang\ncode\n```
        Code block without language: {{{\ncode\n}}} -> ```\ncode\n```
        Inline code span: `code` -> `code` (already valid Markdown; the
        body still needs shielding -- see below).

        Every code body is stashed behind an opaque \x00CODE<n>\x00
        placeholder and restored verbatim by _restore_code_placeholders(),
        after every other pass in parse() has run. Without this, the
        seven passes that used to run right after this one (macros,
        headings, formatting, links, lists, other-elements, tables) walked
        straight through the fence/span they had just created and
        rewrote any TracWiki-shaped markup inside -- silently corrupting
        config/log/terminal excerpts that only *resemble* wiki syntax
        (ticket #31).

        The fence itself is sized by _fence_for() rather than fixed at
        3 backticks, so a nested {{{ }}} block's fence never collides
        with the fence enclosing it (ticket #51).
        """

        def stash(body: str) -> str:
            self._code_placeholders.append(body)
            return f"\x00CODE{len(self._code_placeholders) - 1}\x00"

        # Map TracWiki processor directive to Markdown language (e.g., 'sh' -> 'bash')
        def convert_code_block_with_lang(match: re.Match[str]) -> str:
            tracwiki_lang = match.group(1)
            code = match.group(2)
            md_lang = tracwiki_to_markdown_lang(tracwiki_lang)
            fence = self._fence_for(code)
            return f"{fence}{md_lang}\n{stash(code)}\n{fence}"

        text = re.sub(
            r"\{\{\{#!(\w+)\n(.*?)\n\}\}\}",
            convert_code_block_with_lang,
            text,
            flags=re.DOTALL,
        )

        # Code block without language
        def convert_code_block_no_lang(match: re.Match[str]) -> str:
            code = match.group(1)
            fence = self._fence_for(code)
            return f"{fence}\n{stash(code)}\n{fence}"

        text = re.sub(
            r"\{\{\{\n(.*?)\n\}\}\}",
            convert_code_block_no_lang,
            text,
            flags=re.DOTALL,
        )
        # Inline code span: `code`. Not TracWiki syntax on its own (Trac's
        # WikiFormatting doesn't recognize a bare backtick), so it already
        # passes through unchanged -- but its contents are still ordinary
        # text to every later pass unless shielded the same way.
        text = re.sub(
            r"`([^`\n]+)`",
            lambda m: f"`{stash(m.group(1))}`",
            text,
        )
        return text

    @staticmethod
    def _is_backtick_wrapped(text: str, start: int, end: int) -> bool:
        """True if text[start:end] is directly flanked by a literal
        backtick on both sides.

        Used to keep a macro/link literal when it is meant to sit inside a
        code span -- see the note on _convert_macros for why this can't
        just rely on the code-span stashing in _convert_code_blocks alone.
        """
        return (
            start > 0
            and text[start - 1] == "`"
            and end < len(text)
            and text[end] == "`"
        )

    def _convert_macros(self, text: str) -> str:
        """Convert macros (before links, since they use square brackets).

        Images: [[Image(url)]] -> ![](url)
        Line break: [[BR]] -> newline
        Unknown macros: [[MacroName(args)]] -> placeholder for later restoration

        Every conversion below first checks _is_backtick_wrapped and leaves
        the macro untouched when both flanking characters are literal
        backticks. This is a belt-and-suspenders backstop to the code-span
        stashing in _convert_code_blocks (which runs first and normally
        hides backticked content from this method entirely): that stashing
        pairs backticks by simple nearest-neighbor matching, so one stray,
        unrelated unpaired backtick earlier in the text (a typo, a literal
        apostrophe-like backtick, an unclosed span) can steal one side of a
        *different*, well-formed `[[BR]]` span's delimiters, leaving the
        macro's own two flanking backticks literal in the text but no
        longer recognized as a matched pair -- exactly the failure mode
        from ticket #43. Since both flanking backticks are still physically
        adjacent to the macro at this point even when stashing mis-paired,
        this check catches it independent of whatever happened upstream.

        The check requires backticks on *both* sides, not just one --
        `[[BR]] legitimately follows a closing backtick belonging to a
        *different*, preceding code span with no space in between (e.g.
        `` `substrate:trac`[[BR]] ``, ticket #30), and that must still
        convert.
        """

        # Images
        def convert_image(m: re.Match[str]) -> str:
            if self._is_backtick_wrapped(text, m.start(), m.end()):
                return m.group(0)
            return f"![]({m.group(1)})"

        text = re.sub(
            r"\[\[Image\(([^)]+)\)\]\]",
            convert_image,
            text,
            flags=re.IGNORECASE,
        )

        # Line break: emit a CommonMark hard break (two trailing spaces +
        # a single newline), not a bare "\n". A bare "\n" is only a soft
        # break in Markdown, and when [[BR]] terminates a line that
        # already has its own trailing "\n" in the source (e.g. one
        # field per line, each ended with the macro), the extra "\n"
        # this substitution used to add stacked with that existing one
        # into a blank line -- silently downgrading the hard break to a
        # paragraph break and losing round-trip fidelity on read
        # (ticket #30). Consuming any newline (and trailing whitespace)
        # that immediately follows the macro in the source avoids
        # emitting a second one.
        def convert_br(m: re.Match[str]) -> str:
            # "[[BR]]" itself is always 6 chars; anything after that in
            # the match is the consumed trailing whitespace/newline, which
            # isn't part of the backtick-adjacency check.
            core_end = m.start() + len("[[BR]]")
            if self._is_backtick_wrapped(text, m.start(), core_end):
                return m.group(0)
            return "  \n"

        text = re.sub(
            r"\[\[BR\]\][ \t]*\r?\n?",
            convert_br,
            text,
            flags=re.IGNORECASE,
        )

        # TracLinks: Keep as-is since Markdown has no equivalent
        # Examples: #123, ticket:1, wiki:Page, changeset:abc123
        # These should pass through unchanged - they're not ambiguous with Markdown
        # Already valid in plaintext, agents can understand the notation

        # Handle remaining double-bracket syntax: either a genuine macro
        # ([[PageOutline]], [[TOC]], [[RecentChanges(args)]], ...) or a plain
        # WikiLink ([[Page]], [[Page|Label]]). Only names in _KNOWN_MACROS
        # (or anything carrying explicit "(args)", which plain links never
        # do) are treated as macros; everything else is a page link -- see
        # ticket #28, where routing plain links through the macro-placeholder
        # path corrupted adjacent links.
        # Rendering mode for genuine macros is controlled by
        # self._unknown_macros (set via --unknown-macros CLI flag). Known
        # macros (Image, BR) are already handled above and are unaffected by
        # this setting.
        def handle_bracket(m: re.Match[str]) -> str:
            if self._is_backtick_wrapped(text, m.start(), m.end()):
                return m.group(0)
            name = m.group(1)
            args = m.group(2) if m.group(2) else ""
            label = m.group(3)
            if args or name.lower() in _KNOWN_MACROS:
                match self._unknown_macros:
                    case "drop":
                        # Silently omit the macro from the output.
                        return ""
                    case _:
                        # "preserve" (default): leave [[MacroName(args)]]
                        # literal. markdown_to_tracwiki's _BRACKET_SYNTAX_RE
                        # stashes and restores it verbatim, so the macro
                        # survives the round trip unchanged (ticket #63).
                        return m.group(0)
            # Plain WikiLink: mirror the [text](wiki:Page) shape that
            # single-bracket [wiki:Page text] links already produce, so the
            # round trip back through markdown_to_tracwiki (ticket #17)
            # restores it correctly. Stashed behind an opaque placeholder
            # (like macros already are) rather than emitted literally --
            # later passes such as _convert_links and _convert_tables
            # re-scan for "[...]" / "|" shapes and would otherwise mangle a
            # link whose label contains a space, "|", or other punctuation
            # (ticket #28).
            display = label if label else name
            self._link_placeholders.append(f"[{display}](wiki:{name})")
            return f"\x00LINK{len(self._link_placeholders) - 1}\x00"

        text = re.sub(
            r"\[\[(?!Image|BR)(\w+)(\([^)]*\))?(?:\|([^\]]+))?\]\]",
            handle_bracket,
            text,
            flags=re.IGNORECASE,
        )
        return text

    def _convert_headings(self, text: str) -> str:
        """Convert headings: = H1 = -> # H1.

        Handle headings with or without trailing equals (trailing = is
        optional in TracWiki). Also strip an explicit-anchor suffix
        (``== Heading == #anchor``) — the Markdown roundtrip target uses
        slug-derived implicit anchors, so the explicit anchor token is
        load-bearing on TracWiki side only.
        """
        # Process from H6 to H1 to avoid conflicts
        for level in range(6, 0, -1):
            marker = "=" * level
            text = re.sub(
                rf"^{re.escape(marker)}\s+(.*?)(?:\s+{re.escape(marker)})?(?:\s+#\S+)?\s*$",
                r"%s \1" % ("#" * level),
                text,
                flags=re.MULTILINE,
            )
        return text

    def _convert_formatting(self, text: str) -> str:
        """Convert bold/italic formatting (bold before italic to handle nesting).

        Bold+italic: '''''text''''' -> ***text***
        Bold: '''text''' -> **text**
        Italic: ''text'' -> *text*
        """
        text = re.sub(r"'''''(.*?)'''''", r"***\1***", text)
        text = re.sub(r"'''(.*?)'''", r"**\1**", text)
        text = re.sub(r"''(.*?)''", r"*\1*", text)
        return text

    def _convert_links(self, text: str) -> str:
        """Convert links.

        Link with text: [url text] -> [text](url)
        Link without text: [url] -> <url>

        Both patterns are constrained so a bracket construct is only rewritten
        when it is genuinely a link:

        * The target may not contain ``]`` or whitespace, so it cannot swallow
          a complete preceding link. The old ``\\S+`` target matched ``]`` and
          ``(``, letting ``[a](b)`` on one line and ``[c]`` on the next collapse
          into a single mangled construct (ticket #14).
        * Target and label are separated by *horizontal* whitespace only, so a
          match can never span a line break (ticket #14).
        * The target must satisfy :func:`is_link_target`, so sentinel markers
          like ``[auto-pm: state NEEDS_EDIT]`` stay literal instead of being
          rewritten as ``[state NEEDS_EDIT](auto-pm:)`` (ticket #13).
        """

        # Link with text: [target label]
        def convert_link_with_text(match: re.Match[str]) -> str:
            target = match.group("target")
            if not is_link_target(target):
                # Bracketed prose, not a link — leave it exactly as written.
                return match.group(0)
            return f"[{match.group('label')}]({target})"

        text = re.sub(
            r"\[(?P<target>[^\s\]]+)[^\S\n]+(?P<label>[^\]\n]+)\]",
            convert_link_with_text,
            text,
        )

        # Link without text: [target]
        # Must not match if followed by (url), which would be a Markdown link
        # we just created. Must not match if content starts with [, which would
        # be a macro like [[TOC]].
        def convert_simple_link(match: re.Match[str]) -> str:
            content = match.group(1)
            if content.startswith("["):
                return match.group(0)  # Keep macros unchanged
            if not is_link_target(content):
                return match.group(0)  # Bracketed prose, not a link
            return f"<{content}>"

        text = re.sub(
            r"\[([^\s\]]+)\](?!\()", convert_simple_link, text
        )
        return text

    def _convert_lists(self, text: str) -> str:
        """Convert lists.

        Unordered lists: ' * item' -> '- item'
        Handle nested lists: ' * * item' -> ' - - item'
        Ordered lists are already compatible: ' 1. item' is valid in both
        """

        def convert_list_marker(match):
            leading_space = match.group(1)
            full_match = match.group(0)
            asterisk_part = full_match[len(leading_space) :]
            # Count how many '* ' patterns there are
            count = asterisk_part.count("* ")
            return leading_space + "- " * count

        text = re.sub(
            r"^( +)(\* )+",
            convert_list_marker,
            text,
            flags=re.MULTILINE,
        )
        return text

    def _convert_other_elements(self, text: str) -> str:
        """Convert other elements (horizontal rules, blockquotes, definition lists).

        Horizontal rule: ---- -> ---
        Blockquote: two-space indent -> > prefix
        Definition lists: term:: definition -> **term**: definition

        Definition lists are converted *before* blockquotes (ticket #71).  The
        old order let the blockquote pass claim a definition's continuation
        line first, so ``  the definition`` became ``> the definition`` and the
        stray marker survived into the stored bytes.  Running the definition
        pass first lets it absorb its own continuation lines, and it also
        stops a two-space-indented quote containing a double colon from being
        rewritten as a bold term.
        """
        # Horizontal rule
        text = re.sub(r"^----+\s*$", r"---", text, flags=re.MULTILINE)

        # Definition lists: term:: definition -> **term**: definition
        # TracWiki uses :: separator, Markdown has no native definition list
        # Convert to bold term + regular text (semantic preservation)
        text = _convert_definition_lists(text)

        # Blockquote: two-space indent -> > prefix
        # This is tricky because two-space indent is also used for other things in TracWiki
        # We'll do a simple conversion for lines that start with exactly two spaces
        def convert_blockquote(match):
            lines = match.group(0).split("\n")
            converted = []
            for line in lines:
                if line.startswith("  ") and not line.startswith("   "):
                    converted.append("> " + line[2:])
                else:
                    converted.append(line)
            return "\n".join(converted)

        # Apply blockquote conversion to paragraphs (between blank lines)
        text = re.sub(
            r"(?:^|\n\n)((?:  [^\n]+\n?)+)",
            convert_blockquote,
            text,
            flags=re.MULTILINE,
        )

        return text

    def _convert_tables(self, text: str) -> str:
        """Convert tables: TracWiki ||c1||c2|| -> Markdown |c1|c2|.

        Enhanced conversion with header detection, alignment, spanning, and multi-line support.
        """
        # Handle multi-line rows (backslash continuation) before parsing
        # Join lines that end with \ followed by lines starting with ||
        text = re.sub(r"\\\s*\n\s*\|\|", "||", text)

        # Note: Processor-based table cells ({{{#!td}}} / {{{#!th}}}) are already
        # handled at the beginning of conversion, before code block processing.

        # Parse and convert table rows
        lines = text.split("\n")
        result = []
        table_rows = []  # Accumulate table rows for processing
        table_alignments = []  # Track alignments from first row

        def flush_table():
            """Process accumulated table rows and add to result."""
            nonlocal table_rows, table_alignments
            if not table_rows:
                return

            # Determine number of columns from first row
            num_cols = len(table_rows[0][0]) if table_rows else 0

            # Build separator row from alignments
            if table_alignments:
                separator = (
                    "|"
                    + "|".join(
                        self._alignment_to_separator(a)
                        for a in table_alignments
                    )
                    + "|"
                )
            else:
                separator = "|" + " --- |" * num_cols

            # Check if first row is header
            first_cells, _, first_is_header = table_rows[0]

            if first_is_header:
                # First row is header - use it directly
                result.append("| " + " | ".join(first_cells) + " |")
                result.append(separator)
                # Add remaining rows as body
                for cells, _, _ in table_rows[1:]:
                    result.append("| " + " | ".join(cells) + " |")
            else:
                # No header row - first row becomes header (Markdown requires header)
                result.append("| " + " | ".join(first_cells) + " |")
                result.append(separator)
                # Add remaining rows as body
                for cells, _, _ in table_rows[1:]:
                    result.append("| " + " | ".join(cells) + " |")

            table_rows = []
            table_alignments = []

        for line in lines:
            if re.match(r"^\s*\|\|.*\|\|\s*$", line):
                cells, aligns, is_header = self._parse_tracwiki_row(
                    line
                )
                table_rows.append((cells, aligns, is_header))
                # Use alignments from first row
                if not table_alignments:
                    table_alignments = aligns
            else:
                # End of table - flush accumulated rows
                flush_table()
                result.append(line)

        # Flush any remaining table at end of document
        flush_table()

        return "\n".join(result)

    def _detect_cell_alignment(self, cell_content: str) -> str | None:
        """Detect TracWiki cell alignment from whitespace.

        TracWiki alignment:
        - Left: 'text ' (flush left, space right)
        - Right: ' text' (space left, flush right)
        - Center: ' text ' (space both sides)

        Returns: 'left', 'right', 'center', or None
        """
        if not cell_content:
            return None
        has_leading_space = (
            cell_content.startswith(" ") and len(cell_content) > 1
        )
        has_trailing_space = (
            cell_content.endswith(" ") and len(cell_content) > 1
        )
        match (has_leading_space, has_trailing_space):
            case (True, True):
                return "center"
            case (True, False):
                return "right"
            case (False, True):
                return "left"
            case _:
                return None

    def _parse_tracwiki_row(
        self, row: str
    ) -> tuple[list[str], list[str | None], bool]:
        """Parse a TracWiki table row into cells with alignment info.

        Returns: (cells, alignments, is_header)
        - cells: list of cell content strings
        - alignments: list of alignment values ('left', 'right', 'center', None)
        - is_header: True if this row contains header cells (||= ... =||)
        """
        cells: list[str] = []
        alignments: list[str | None] = []
        is_header = False

        # Check if row has header markers
        if re.search(r"\|\|=.*=\|\|", row):
            is_header = True

        # Split by || but preserve empty cells for spanning detection
        # First, strip leading/trailing ||
        row = row.strip()
        if row.startswith("||"):
            row = row[2:]
        if row.endswith("||"):
            row = row[:-2]

        # Split by ||
        raw_cells = row.split("||")

        # Process cells, handling spanning (empty cells merge with previous)
        pending_span = 0
        for raw_cell in raw_cells:
            if raw_cell == "":
                # Empty cell indicates spanning - will merge with next non-empty
                pending_span += 1
            else:
                # Extract content, handling header markers
                cell = raw_cell

                # Check for header markers: =text= or = text =
                header_match = re.match(r"^=(.*)=$", cell.strip())
                if header_match:
                    cell = header_match.group(1)
                    is_header = True

                # Detect alignment before stripping
                align = self._detect_cell_alignment(cell)

                # Strip and clean the content
                cell = cell.strip()

                # If there were preceding empty cells, this is a spanned cell
                # Add indicator text if spanning occurred
                if pending_span > 0:
                    # Markdown doesn't support spanning - merge content
                    cell = (
                        f"[span:{pending_span + 1}] {cell}"
                        if cell
                        else f"[span:{pending_span + 1}]"
                    )
                    pending_span = 0

                cells.append(cell)
                alignments.append(align)

        # Handle trailing empty cells (would indicate colspan to end)
        # These are already stripped off, but raw_cells might have them
        if pending_span > 0 and cells:
            cells[-1] = f"{cells[-1]} [span:{pending_span + 1}]"

        return cells, alignments, is_header

    def _alignment_to_separator(self, align: str | None) -> str:
        """Convert alignment to Markdown separator format."""
        match align:
            case "left":
                return ":---"
            case "right":
                return "---:"
            case "center":
                return ":---:"
            case _:
                return "---"

    def _restore_link_placeholders(self, text: str) -> str:
        """Restore WikiLink placeholders stashed by ``_convert_macros``.

        Convert ``\x00LINKn\x00`` back to the finished ``[text](wiki:Page)``
        Markdown, once every pass that might otherwise re-parse "[...]" /
        "|" characters inside the link's own label has already run.
        """

        def restore(m: re.Match[str]) -> str:
            return self._link_placeholders[int(m.group(1))]

        return re.sub(r"\x00LINK(\d+)\x00", restore, text)

    def _restore_code_placeholders(self, text: str) -> str:
        """Restore code-body placeholders stashed by ``_convert_code_blocks``.

        Runs last, after every pass that could otherwise mistake a code
        body for TracWiki markup, so the restored text is byte-identical
        to the original source (ticket #31).

        A ``{{{ }}}`` block nested inside another one gets stashed twice:
        the inner block is replaced by its own ``CODEn`` placeholder first,
        and then the *outer* block's capture -- which still contains that
        unresolved placeholder literally -- gets stashed as a second,
        higher-numbered placeholder whose stored body itself contains the
        first placeholder's sentinel bytes. ``re.sub`` only scans its input
        once, so a single restore pass would substitute the outer
        placeholder and leave the inner sentinel it exposes untouched in
        the output. Looping to a fixed point resolves arbitrarily deep
        nesting (ticket #51).
        """

        def restore(m: re.Match[str]) -> str:
            return self._code_placeholders[int(m.group(1))]

        pattern = re.compile(r"\x00CODE(\d+)\x00")
        while pattern.search(text):
            text = pattern.sub(restore, text)
        return text


def tracwiki_to_markdown(
    tracwiki_text: str, *, unknown_macros: UnknownMacros = "preserve"
) -> ConversionResult:
    """
    Convert TracWiki text to Markdown format.

    This is a best-effort conversion using regex replacements. Unknown TracWiki
    macros and unsupported features pass through unchanged without errors.

    Args:
        tracwiki_text: TracWiki formatted text
        unknown_macros: How to render unrecognized ``[[Macro]]`` tokens.
            ``"preserve"`` (default) leaves ``[[Name]]`` literal, so the
            macro survives a round trip back to TracWiki unchanged;
            ``"drop"`` silently omits the macro.
            Mirrors the ``--unknown-macros`` CLI flag.

    Returns:
        ConversionResult with Markdown text and warnings about lossy conversions
    """
    parser = TracWikiParser(unknown_macros=unknown_macros)
    return parser.parse(tracwiki_text)
