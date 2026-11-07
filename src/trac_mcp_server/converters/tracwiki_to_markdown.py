"""TracWiki to Markdown conversion using regex patterns."""

import bisect
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

# The opener of a {{{ }}} block that becomes a Markdown fence: either bare or
# carrying a single-word ``#!lang`` directive, and in both cases followed
# immediately by a newline.  A directive with arguments (``{{{#!div class=x``)
# deliberately does not match -- its body is wiki markup rather than verbatim
# text, so it is scanned into rather than fenced (see _convert_code_blocks).
_CODE_BLOCK_OPEN_RE = re.compile(r"\{\{\{(?:#!(\w+))?\n")

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

# Indentation in TracWiki is a blockquote, and the grammar is RELATIVE
# (ticket #73).  Measured against Trac's own renderer -- convert_preview with
# format="tracwiki", reading the HTML -- because the ticket's own section 1
# had it as absolute, "one <blockquote> per space":
#
#   one space / two / three / four / a tab, each standing alone
#       -> exactly ONE <blockquote> every time
#   " one" / "  two" / "   three" on consecutive lines
#       -> THREE nested blockquotes, one per *increase*
#   "   deep" then "  shallower"     -> two SIBLING depth-1 blockquotes
#   " one", blank line, "  two"      -> depth 1, then depth 2
#   a column-zero non-blank line     -> resets to depth 0
#
# So it is an indent stack: deeper pushes a level whatever the size of the
# step, shallower pops, and only column-zero content clears it.  The ladder in
# the second row is how "one blockquote per space" came to be recorded.
#
# The consequence for the fix is that the ticket's own remediation --
# n spaces -> n ">" markers -- is wrong: a lone four-space quote is ONE
# blockquote in Trac and would have become four nested ones in Markdown, which
# is the very "Markdown that means something different from the TracWiki it
# came from" the ticket forbids.  Underneath sits an impossibility: Markdown
# blockquote syntax carries depth but not width, so only one indent width per
# depth can survive byte-for-byte.  Hence the rule in
# _convert_indented_blocks: convert only what the write leg would reproduce
# exactly, and send everything else through ticket #63's verbatim fallback.

# Two spaces per nesting level is what markdown_to_tracwiki's block_quote()
# emits ("> a" -> "  a", "> > b" -> "    b"), so this is the one width per
# depth that round-trips byte-for-byte.
_CANONICAL_INDENT_PER_LEVEL = 2

# Trac expands a tab to eight columns when measuring indentation, which is why
# a tab lands deeper than four spaces in the ladder above.
_TAB_WIDTH = 8

# Lines whose leading whitespace belongs to some other construct, so the quote
# scanner must not claim them.  All measured on the live renderer:
#
#   " * a", "  * a", "   * a", " - a", " 1. a", " a. x", " iv. x"
#       -> a bare <ul>/<ol>, NO <blockquote> wrapper, at any indent
#   "  term:: def"  -> a bare <dl class="wiki">, no blockquote, at any indent
#   " = H ="        -> <h1>, the indent consumed
#
# The list markers are exact rather than generous, because generosity costs
# recall here: " Hello. World" and " xyz. not roman" are BLOCKQUOTES to Trac,
# not lettered lists, so "any word followed by a period" would have skipped
# real quotes.  Trac's alpha marker is a single letter or a roman numeral.
# " 1)" is not a marker either -- also a blockquote.
#
# Split into fragments rather than written out once, because ticket #74 needs
# the lettered/roman subset ON ITS OWN -- those markers have no Markdown
# equivalent and take the verbatim fallback, while the bullet and numeric ones
# convert -- and the ticket's recall gate asks for the two to read from the
# same place instead of re-deriving the grammar.
_BULLET_MARKER = r"[*-]"
_NUMERIC_MARKER = r"\d+\."
_ALPHA_MARKER = r"(?:[a-zA-Z]|[ivxlcdmIVXLCDM]{2,})\."
_LIST_MARKER_RE = re.compile(
    rf"^[ \t]+(?:{_BULLET_MARKER}|{_NUMERIC_MARKER}|{_ALPHA_MARKER})(?=[ \t])"
)

# A table row, with any indentation. ONE pattern for `_convert_tables` and
# the `{{{#!div}}}` unwrap, so the two cannot disagree on what a row is
# (ticket #142 review F8).
_TABLE_ROW_RE = re.compile(r"^\s*\|\|.*\|\|\s*$")

# A line the div unwrap leaves ahead of the rows it released, and
# `_convert_tables` consumes: "these rows came from a {{{#!div}}} inside a
# list item" (ticket #142). Bare indented `||` rows in an item are rendered
# by Trac OUTSIDE the list (measured), so only rows with this mark keep
# their indentation on the way to Markdown. It never reaches the output:
# `_convert_tables` removes every one, and `parse` fails loudly on any NUL.
_DIV_TABLE_MARK = "\x00DIVTABLE\x00"
_DIV_TABLE_MARK_RE = re.compile(r"^[ \t]*\x00DIVTABLE\x00$")


def _enclosing_list_marker(
    prev_lines: list[str], indent: int
) -> re.Match[str] | None:
    """The list-marker match of the item a block indented `indent` columns
    wide sits in, or None. `prev_lines` are the lines above the block.

    Walk back over the contiguous non-blank lines directly above the block
    (stopping at a blank line: Trac ends a list at any blank line) until a
    list-marker line -- by the module's own `_LIST_MARKER_RE` grammar --
    whose marker is indented LESS than the block. Widths are in columns,
    tabs expanded as Trac does (`_indent_width`), the same measure the rest
    of the module uses. A line is tested for a marker before anything else,
    so inline braces on the item's own line (`* use {{{x}}} here`) cannot
    hide it. The match is against the marker line, so a caller can read
    the marker's own indentation and width off it.
    """
    for line in reversed(prev_lines):
        if not line.strip():
            return None
        found = _LIST_MARKER_RE.match(line)
        if found and _indent_width(_indent_of(line)) < indent:
            return found
    return None


# An ordered list marker (digits only, as `_LIST_MARKER_RE` writes it) whose
# number is not 1: CommonMark does not let such a list interrupt a paragraph
# (ticket #144; see `_separate_list_from_trailing_paragraph`).
_NON_ONE_ORDERED_RE = re.compile(r"^(?!0*1\.$)\d+\.$")


def _lines_since_blank(pieces: list[str]) -> list[str]:
    """The complete lines of `"".join(pieces)` after its last blank line
    (all of them if there is none), the partial last line excluded.

    `_open_list_item` depends on nothing before a blank line (it empties the
    stack), so this is all it needs, and it is found by looking back from
    the end in growing slices instead of joining everything for each block
    (ticket #144 review: the join made a page with many blocks quadratic).
    A slice's first line may be partial, so a blank line is only trusted
    past index 0 unless the slice is the whole thing.
    """
    size = 256
    while True:
        whole = size >= len(pieces)
        lines = "".join(pieces[-size:]).split("\n")
        for idx in range(len(lines) - 2, -1 if whole else 0, -1):
            if not lines[idx].strip():
                return lines[idx + 1 : -1]
        if whole:
            return lines[:-1]
        size *= 4


def _open_list_item(lines: list[str]) -> re.Match[str] | None:
    """The list-marker match of the innermost list item still OPEN after
    `lines`, or None (ticket #144).

    The stack walk `_separate_list_from_trailing_paragraph` uses, over
    columns in Trac's tab-expanded width: a marker line pops every entry at
    or past its own column and pushes itself; any other non-blank line pops
    every entry at or past its column; a blank line empties the stack (Trac
    ends a list at any blank line, #143). What is left on top is the
    innermost open item, whose match the caller reads the marker's
    indentation and width off (as `_enclosing_list_marker` documents).
    """
    stack: list[tuple[int, re.Match[str]]] = []
    for line in lines:
        if not line.strip():
            stack.clear()
            continue
        column = _indent_width(_indent_of(line))
        found = _LIST_MARKER_RE.match(line)
        while stack and stack[-1][0] >= column:
            stack.pop()
        if found:
            stack.append((column, found))
    return stack[-1][1] if stack else None


# The subset of the marker set above that Markdown cannot express at all
# (ticket #74).  CommonMark's ordered-list marker is digits only, so there is
# nothing for ``a.`` or ``iv.`` to convert TO: measured, mistune parses
# " a. first\n b. second" as one paragraph and the same input with "1."/"2."
# as a list.  Converting to a numbered list is rejected explicitly -- it would
# renumber a./b./c. as 1./2./3., changing what the page says, and the write leg
# could not tell the difference on the way back.
#
# Indented only, like every other pattern here.  A lettered list at column zero
# is an <ol class="loweralpha"> to Trac as well and is lost the same way, but
# its grammar differs (a marker line opens a list mid-paragraph, and a
# column-zero continuation line ENDS it) -- handled separately below by
# _ALPHA_LIST_MARKER_COL0_RE and _fallback_column_zero_lists (ticket #89),
# rather than folded in here, because a run there is bounded by consecutive
# MARKER lines rather than by consecutive indented ones.
_ALPHA_LIST_MARKER_RE = re.compile(rf"^[ \t]+{_ALPHA_MARKER}(?=[ \t])")
_INDENTED_TABLE_RE = re.compile(r"^[ \t]+\|\|")
_INDENTED_HEADING_RE = re.compile(r"^[ \t]+=")

# The column-zero counterpart of _ALPHA_LIST_MARKER_RE (ticket #89), built
# from the same _ALPHA_MARKER fragment so the marker grammar has one
# definition read from three places -- indented, column zero, and this
# module's quote-scanner exclusion list -- instead of three copies free to
# drift apart.  Trac opens one of these lists at column zero even in the
# middle of a paragraph (measured against the live renderer: "some prose"
# then "a. first" renders as a <p> followed by a separate <ol>), so unlike
# the indented form this pattern is anchored directly on the marker with no
# leading-whitespace requirement.
_ALPHA_LIST_MARKER_COL0_RE = re.compile(rf"^{_ALPHA_MARKER}(?=[ \t])")

# A line shape that CommonMark already treats as "interrupting" -- it starts
# a new sibling block on its own, without needing a blank line, rather than
# being read as a lazy continuation of the preceding list item's paragraph
# (ticket #90). Measured against mistune with this project's own plugin set
# (``plugins=["table"]``, which was ``markdown_to_tracwiki``'s whole plugin
# set at the time; since #142 that module's ``_MISTUNE_PLUGINS`` also loads
# ``table_in_list`` / ``table_in_quote``, and the column-zero table row
# below is the case whose reading that changes -- see ``_convert_tables``):
# an ATX heading, a blockquote marker, a fenced code block, and a thematic
# break all interrupt; a GFM table row does NOT (it lazily absorbs exactly
# like plain prose) and is deliberately left off this list so it still gets
# the separating blank line. By the point ``_convert_lists`` runs, a
# TracWiki heading is already "#", and a blockquote line is already "> "
# (``_convert_indented_blocks`` runs earlier in ``_apply_fallbacks``) or
# already a fenced fallback placeholder starting with a code fence -- but a
# TracWiki horizontal rule is still "----", not yet "---"
# (``_convert_other_elements`` runs after this pass), so the thematic-break
# check matches Trac's own dash-run syntax directly rather than the
# Markdown it will eventually become.
_LAZY_CONTINUATION_INTERRUPT_RE = re.compile(
    r"^(?:#{1,6}(?:[ \t]|$)|>|```|~~~|-{3,}[ \t]*$)"
)


def _indent_of(line: str) -> str:
    """Return ``line``'s literal leading whitespace."""
    return line[: len(line) - len(line.lstrip(" \t"))]


def _indent_width(indent: str) -> int:
    """Return ``indent``'s width in columns, tabs expanded as Trac does."""
    return len(indent.expandtabs(_TAB_WIDTH))


def _quote_depths(
    lines: list[str], stack: list[int]
) -> tuple[list[int], list[int]]:
    """Return each line's blockquote depth, plus the indent stack after them.

    ``stack`` is carried across calls because a blank line does NOT reset
    Trac's indentation stack -- only column-zero content does (measured; see
    the note above).
    """
    depths: list[int] = []
    for line in lines:
        width = _indent_width(_indent_of(line))
        while stack and stack[-1] > width:
            stack.pop()
        if not stack or width > stack[-1]:
            stack.append(width)
        depths.append(len(stack))
    return depths, stack


def _is_canonical_indent(lines: list[str], depths: list[int]) -> bool:
    """True when the write leg would reproduce these lines byte-for-byte.

    Depth has to be uniform across the run, which was measured rather than
    assumed: ``> a\\n> > b`` comes back from ``markdown_to_tracwiki`` as
    ``"  a\\n  \\n    b"`` -- mistune reads the depth change as a nested
    blockquote whose sibling paragraph contributes a blank line, and that
    blank line is quoted too.  A depth change *across a blank line* does
    round-trip exactly (``"  a\\n\\n    b"``), and needs nothing special here
    because each side is its own run and the stack carries between them.
    """
    if len(set(depths)) > 1:
        return False
    for line, depth in zip(lines, depths, strict=True):
        indent = _indent_of(line)
        if "\t" in indent:
            return False
        if len(indent) != _CANONICAL_INDENT_PER_LEVEL * depth:
            return False
    return True


# _convert_definition_lists was deleted on ticket #63.  It rewrote a
# definition list as a bold term plus a colon -- a reconstruction the
# verbatim fallback replaces, and one that did not survive the way back:
# stored at column zero, "'''term''': definition" is prose to Trac, not a
# <dl>.  Its continuation-absorbing loop also disagreed with Trac, taking
# a following line only when the definition was empty AND the line was
# indented deeper, where Trac absorbs one at the same indent too.
# _convert_indented_blocks now carries the whole run verbatim, which
# sidesteps that disagreement rather than having to fix it.


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

        # The definition-list warning used to live here and described the
        # bold conversion deleted on ticket #63.  _stash_fallback now raises
        # it where it fires, which also fixed a live false positive this
        # placement caused: _detect_lossy_elements scans the RAW text with no
        # shielding, so a "term::" line inside a code block announced a
        # definition list in quoted source.  That is the over-firing shape
        # ticket #71 removed, surviving in a second place -- and tickets #57
        # and #59 are this project's evidence that an over-firing check gets
        # muted and takes its true positives with it.  Pinned by
        # TestWarningIsShielded.

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

    def _fallback_column_zero_lists(self, text: str) -> str:
        """Emit a column-zero lettered or roman list verbatim (ticket #89).

        Trac's grammar here is different from the indented form
        ``_convert_indented_blocks`` handles for the same marker set: a
        marker line opens a list even mid-paragraph, and the run is the
        maximal set of *consecutive* marker lines only -- a following
        column-zero line that is not itself a marker ends the list rather
        than being absorbed into the last item (measured against Trac's own
        renderer: ``A. first`` / a continuation line / ``B. second`` comes
        back as ``<ol>``, ``<p>``, ``<ol start="2">``). So this scans for
        runs of the marker itself, not runs of indentation, and never
        touches a line around it -- prose above, below, or between two runs
        is left exactly as written.

        Runs as its own top-level pass rather than a branch inside
        ``_convert_indented_blocks``, per the ticket: a column-zero marker
        line's first character is never whitespace, so the two passes can
        never contend for the same line regardless of ordering.

        Detection runs against ``_verbatim_mask``, like every other fallback
        here, so a marker line quoted inside a code block or code span is
        content, not a construct (tickets #45, #46, #51).
        """
        lines = text.split("\n")
        masked = self._verbatim_mask(text).split("\n")
        out: list[str] = []
        index = 0
        n = len(lines)
        while index < n:
            if not _ALPHA_LIST_MARKER_COL0_RE.match(masked[index]):
                out.append(lines[index])
                index += 1
                continue
            start = index
            while index < n and _ALPHA_LIST_MARKER_COL0_RE.match(
                masked[index]
            ):
                index += 1
            out.append(
                self._stash_fallback(
                    "\n".join(lines[start:index]),
                    "Lettered or roman ordered list",
                )
            )
        return "\n".join(out)

    @staticmethod
    def _line_holds_a_brace(masked_line: str) -> bool:
        """True for a code-block delimiter line.

        ``_verbatim_mask`` blanks a block's *interior* but not its ``{{{`` and
        ``}}}`` markers, so stashing a run containing one half would strand
        the other.  Checked before anything else for that reason.
        """
        return "{{{" in masked_line or "}}}" in masked_line

    @staticmethod
    def _line_is_not_a_quote(masked_line: str) -> bool:
        """True when this indented line's whitespace belongs elsewhere.

        A run containing any such line is left entirely alone rather than
        claimed or fallen back: a list, table or heading consumes its own
        indent, and claiming it would take the construct apart -- a multi-line
        list item's continuation line being the case that matters most.

        Definition lists were on this list until ticket #63, and lettered or
        roman ordered lists until ticket #74.  Both are now handled a step
        earlier, for the same reason: unlike the others they have no Markdown
        equivalent at all, so leaving them to a later pass leaves them as
        prose.  This predicate still matches them -- it is asking whether the
        indent belongs to a construct, which it does -- but by the time it
        runs they have already been claimed.
        """
        return bool(
            _LIST_MARKER_RE.match(masked_line)
            or _INDENTED_TABLE_RE.match(masked_line)
            or _INDENTED_HEADING_RE.match(masked_line)
        )

    def _convert_indented_blocks(self, text: str) -> str:
        """Convert an indented quote, or emit it verbatim (ticket #73).

        Runs here rather than in ``_convert_other_elements`` for two reasons:
        the fallback body has to be stashed before any other pass can rewrite
        the markup being preserved as source, and the classification is more
        honest against raw TracWiki than against half-converted Markdown.
        Being early costs nothing, because the ``> `` lines it emits are
        ordinary text to every later pass, so a quote's contents still get
        their formatting, links and macros converted.

        Five outcomes per maximal run of consecutive non-blank indented
        lines, in order:

        1. **A code-block delimiter** -- left untouched; see
           ``_line_holds_a_brace``.
        2. **A definition list** (ticket #63) -- carried verbatim through the
           fallback.  Markdown has no definition list, and the bold form this
           replaced did not survive the way back: stored at column zero,
           ``'''term''': definition`` is *prose* to Trac, not a ``<dl>``.
           The unit is the run rather than the term line because consecutive
           terms are one ``<dl>``, and a non-term line following a term is
           part of the preceding ``<dd>`` even at the same indent -- both
           measured against Trac's renderer.
        3. **A lettered or roman ordered list** (ticket #74) -- carried
           verbatim through the fallback.  `` a. x``/`` iv. x`` are ordered
           lists to Trac (``<ol class="loweralpha">``/``lowerroman``), and
           CommonMark's ordered-list marker is digits only, so there is
           nothing to convert them *to*: before this they fell to step 4 and
           reached the Markdown as a paragraph, silently.  Renumbering them
           as ``1./2./3.`` is rejected -- it changes what the page says, and
           the write leg could not tell the rewrite from an original.
           A marker at column zero is a sibling defect with a different
           grammar -- ``_fallback_column_zero_lists`` handles it as its own
           top-level pass, before this method runs (ticket #89).
        4. **Not a quote at all** -- the run contains a list item, table row
           or heading, whose indent those constructs consume.  Left untouched,
           which is also what keeps a multi-line list item working: its
           continuation line is indented too, and claiming it would take the
           item apart.
        5. **Canonical** -- every line's indent is exactly two spaces per
           level of its measured depth, so ``> `` markers reproduce it
           byte-for-byte.  Converted.  Anything else -- one space, three,
           four, a tab, a mixed run -- goes out verbatim through the same
           fallback, which restores it unchanged and *warns*.  Before ticket
           #73, one and three spaces lost the indent silently and four spaces
           or a tab became a literal ``{{{`` code block.

        Steps 2 and 3 run **before** step 4, which is the operator decision on
        ticket #63 applied twice: a run holding both a bullet and a definition
        term is a ``<ul>`` followed by a ``<dl>``, and a run holding a numbered
        list with a lettered item nested inside it is one ``<ol>`` containing
        another -- in both cases falling the whole run back is the only option
        that loses nothing.  Skipping it would let the unrepresentable half
        degrade to prose now that nothing converts it, and splitting the run at
        the construct boundary needs a real block parser.  The cost is that a
        representable list in the same run is carried verbatim too.

        Detection runs against ``_verbatim_mask`` so an indented line quoted
        inside a code span or block is content, not a quote (tickets #45,
        #46, #51); the emitted text is always the original.
        """
        lines = text.split("\n")
        masked = self._verbatim_mask(text).split("\n")
        out: list[str] = []
        stack: list[int] = []
        inside: set[int] | None = None
        index = 0
        while index < len(lines):
            probe = masked[index]
            if not probe.strip() or not probe[:1].isspace():
                # Blank lines leave the stack alone; column zero clears it.
                if probe.strip():
                    stack = []
                out.append(lines[index])
                index += 1
                continue

            start = index
            while (
                index < len(lines)
                and masked[index].strip()
                and masked[index][:1].isspace()
            ):
                index += 1
            run = lines[start:index]
            probes = masked[start:index]

            if any(self._line_holds_a_brace(m) for m in probes):
                stack = []
                out.extend(run)
                continue

            if any(_DEFINITION_LIST_RE.match(m) for m in probes):
                stack = []
                out.append(
                    self._stash_fallback(
                        "\n".join(run), "Definition list"
                    )
                )
                continue

            if any(_ALPHA_LIST_MARKER_RE.match(m) for m in probes):
                stack = []
                out.append(
                    self._stash_fallback(
                        "\n".join(run), "Lettered or roman ordered list"
                    )
                )
                continue

            if any(self._line_is_not_a_quote(m) for m in probes):
                stack = []
                out.extend(run)
                continue

            # Ticket #143 review (F3): a run that starts straight after the
            # closing `}}}` of a block in an open list item, deeper than
            # the item's marker, is the item's own text (Trac keeps it
            # inside the <li>). Without a list marker later in the run
            # (the next item) nothing above claims it, and it would be
            # fenced as an unconverted indent when the item is the last
            # one or the list ends after it.
            if self._closes_block_line(masked, start):
                if inside is None:
                    inside = self._lines_inside_blocks(lines)
                if self._continues_item_after_block(
                    lines, inside, start
                ):
                    stack = []
                    out.extend(run)
                    continue

            depths, stack = _quote_depths(run, stack)
            if _is_canonical_indent(run, depths):
                out.extend(
                    "> " * depth + line[len(_indent_of(line)) :]
                    for depth, line in zip(depths, run, strict=True)
                )
            else:
                out.append(
                    self._stash_fallback(
                        "\n".join(run), self._indent_kind(run)
                    )
                )
        return "\n".join(out)

    @staticmethod
    def _closes_block_line(masked: list[str], start: int) -> bool:
        """True when the line before `start` closes a ``{{{ }}}`` block.

        In the mask the closing line's indentation is swallowed into the
        blanked interior, so it starts with the filler and ends in
        ``}}}`` -- which is also why the line after it starts a run of its
        own.
        """
        if start == 0:
            return False
        prev = masked[start - 1]
        return prev.startswith("\x01") and prev.rstrip().endswith("}}}")

    @staticmethod
    def _continues_item_after_block(
        lines: list[str], inside: set[int], start: int
    ) -> bool:
        """True when the line at `start`, which follows a block's closing
        line, continues a list item (ticket #143 review, F3).

        A list item must still be open above the block, and the line must
        sit deeper than the item's marker (Trac's continuation rule, #75;
        a line at the marker's own column is not claimed here). `inside`
        holds the indices of block content lines (`_lines_inside_blocks`),
        which are skipped: a blank or column-zero line in a code body does
        not end the list. `_open_list_item` empties its stack at a blank
        line, so only the lines after the last blank one matter.
        """
        above: list[str] = []
        for idx in range(start - 1, -1, -1):
            if idx in inside:
                continue
            if not lines[idx].strip():
                break
            above.append(lines[idx])
        above.reverse()
        marker = _open_list_item(above)
        if marker is None:
            return False
        return _indent_width(_indent_of(lines[start])) > _indent_width(
            _indent_of(marker.string)
        )

    def _lines_inside_blocks(self, lines: list[str]) -> set[int]:
        """Indices of the lines after a top-level ``{{{`` line, through the
        line holding its closing ``}}}``: block content, whatever it looks
        like (a blank line, a column-zero line)."""
        text = "\n".join(lines)
        starts = [0]
        for line in lines[:-1]:
            starts.append(starts[-1] + len(line) + 1)
        inside: set[int] = set()
        i = 0
        n = len(text)
        while i < n:
            if text.startswith("{{{", i):
                end = self._matching_brace(text, i)
                if end == -1:
                    break
                first = bisect.bisect_right(starts, i) - 1
                last = bisect.bisect_right(starts, end - 1) - 1
                inside.update(range(first + 1, last + 1))
                i = end
            elif text[i] == "`":
                j = text.find("`", i + 1)
                nl = text.find("\n", i + 1)
                i = j + 1 if j != -1 and (nl == -1 or j < nl) else i + 1
            else:
                i += 1
        return inside

    @staticmethod
    def _indent_kind(run: list[str]) -> str:
        """Name the indent width in the warning, so it says what it saw."""
        indents = {_indent_of(line) for line in run}
        if any("\t" in indent for indent in indents):
            return "Indented block (tab indent)"
        if len(indents) > 1:
            return "Indented block (mixed indent widths)"
        return f"Indented block ({len(indents.pop())}-space indent)"

    def _apply_fallbacks(self, text: str) -> str:
        """Run every unrepresentable-construct fallback, outermost first."""
        text = self._fallback_processor_blocks(text)
        text = self._fallback_tables(text)
        text = self._fallback_column_zero_lists(text)
        text = self._convert_indented_blocks(text)
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

        CommonMark closes a fence on the first line carrying at least as
        many backticks as the opener, so a 3-backtick fence around a body
        that itself contains ```` ``` ```` terminates early and the rest
        of the document is swallowed as raw code (ticket #51).

        On #51 the body that did this was the converter's own doing: a
        nested {{{ }}} block was matched innermost-first, so the outer
        block's capture already held the inner one's finished Markdown
        fence. Ticket #72 removed that -- a nested block is now carried
        verbatim -- and the remaining source of a backtick run in a body
        is the ordinary one: a code block quoting Markdown.
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
        3 backticks, so a backtick run inside the body never collides
        with the fence enclosing it (ticket #51).

        Each block's extent is established by _matching_brace() -- a
        depth-counted scan -- rather than by a lazy ``.*?``, which stops
        at the *first* ``}}}`` and so ends a nested block's outer fence
        at the INNER close, leaving the outer ``}}}`` outside the fence
        as body text (ticket #72).  A nested block is content: Trac
        renders the enclosing block verbatim, so the inner delimiters
        are literal text and stay inside the fence unchanged.  Same rule
        _fallback_processor_blocks applies -- only a block at top level
        is a construct of its own (ticket #51).

        A block that belongs to an open list item (ticket #144) is
        fenced at that item's text column, whatever the opener's own
        indentation (F4-F6: Trac renders a ``{{{ }}}`` block inside the
        innermost open item at any indent, column 0 included; flush-left
        prose or a blank line closes the list first), so the Markdown
        fence belongs to the item. Before, the body and closer came out at
        column 0: the list passes saw the column-0 ``\\x00CODEn\\x00``
        placeholder line, decided the list had ended and inserted a blank
        line after the opener (F3), and writing that back pulled the next
        item into a code block.  The body is what Trac shows (F2): the
        opener's indentation is stripped from every line only if EVERY
        non-blank line starts with it, otherwise nothing is stripped.  See
        `_fence_in_list_item`.  A block with no open item, and any block
        whose opener is not first on its line, take the path they always
        did.
        """

        def stash(body: str) -> str:
            self._code_placeholders.append(body)
            return f"\x00CODE{len(self._code_placeholders) - 1}\x00"

        out: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            if text.startswith("{{{", i):
                end = self._matching_brace(text, i)
                m = _CODE_BLOCK_OPEN_RE.match(text, i)
                # Ticket #142: a bare {{{#!div}}} inside a list item that
                # holds nothing but `||` rows is how the write leg keeps a
                # table inside the <li>. Unwrap it into its rows, behind a
                # mark `_convert_tables` uses to keep their indentation.
                # Tried first, with its own brace matching: `end` above
                # counts a `}}}` inside a backtick span in a cell as the
                # block's closer. Everything this declines -- no list item
                # above, a non-row line in the body, arguments on the
                # opener (the opener regex does not match those anyway) --
                # takes the paths below exactly as before.
                if m and m.group(1) == "div":
                    unwrapped = self._unwrap_div_table(text, i, m.end())
                    if unwrapped is not None:
                        replacement, resume, indent_len = unwrapped
                        # The opener's indentation went into `out` one
                        # character at a time; the replacement carries its
                        # own (see `_unwrap_div_table`).
                        del out[len(out) - indent_len :]
                        out.append(replacement)
                        i = resume
                        continue
                # The body runs from the opener's newline to the closing
                # brace, which Trac requires to start its own line -- so
                # everything after the body's last newline must be blank.
                # A block closed mid-line is not one, and is left to the
                # passes below.  The old regex demanded a bare "\n}}}",
                # which an INDENTED closer fails; rather than decline, it
                # went looking for the next "\n}}}" in the document and
                # swallowed everything up to it (measured on the stock
                # TracReports page, where the run was ~80 lines).
                body = (
                    text[m.end() : end - 3] if (m and end != -1) else ""
                )
                nl = body.rfind("\n")
                if (
                    m
                    and end != -1
                    and nl != -1
                    and not body[nl + 1 :].strip(" \t")
                ):
                    code = body[:nl]
                    fence = self._fence_for(code)
                    # Map the processor directive to a Markdown language
                    # (e.g. 'sh' -> 'bash'); a bare {{{ has none.
                    info = (
                        tracwiki_to_markdown_lang(m.group(1))
                        if m.group(1)
                        else ""
                    )
                    # Ticket #144: a block in an open list item is fenced at
                    # the item's text column. Everything it declines takes
                    # the path below.
                    item_fence = self._fence_in_list_item(
                        text, i, out, code, fence, info
                    )
                    if item_fence is not None:
                        replacement, indent_len = item_fence
                        del out[len(out) - indent_len :]
                        out.append(replacement)
                        i = end
                        continue
                    out.append(f"{fence}{info}\n{stash(code)}\n{fence}")
                    i = end
                    continue
                # Not a fenceable block: an unterminated one, or an opener
                # this pass does not claim ({{{#!div class=x, {{{ inline }}}).
                # Emit the brace and keep scanning its interior -- a
                # {{{#!div}}} body is wiki markup, and any real code block
                # inside it still has to be converted.
                out.append("{{{")
                i += 3
            elif text[i] == "`":
                # Step over a code span rather than into it, so a {{{ or
                # }}} quoted inside one cannot shift the brace depth
                # (tickets #45, #46).  The span's own body is shielded by
                # the stashing pass below.
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
        text = "".join(out)

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

    def _fence_in_list_item(
        self,
        text: str,
        start: int,
        emitted: list[str],
        code: str,
        fence: str,
        info: str,
    ) -> tuple[str, int] | None:
        """Fence a block that belongs to an open list item (#144).

        `start` is the index of the opener's `{{{`, `emitted` the pieces
        `_convert_code_blocks` has appended so far (the opener's own
        indentation is the tail of the last line), `code` the block's body
        (already known to be fenceable), `fence`/`info` the Markdown fence
        and info string the caller would emit. Returns (replacement, length
        of the opener's indentation already emitted by the caller), or None
        to leave the block to the caller's ordinary path.

        Takes the block when the opener is the first non-whitespace on its
        line, at ANY indentation (column 0 included), and a list item is
        still open there (`_open_list_item`, over the lines already
        emitted -- earlier blocks in the item are placeholders at the
        item's text column and keep it open). Measured on /trac_test
        (F4-F6): a `{{{ }}}` block renders inside the innermost open item
        whatever its indentation; flush-left prose or a blank line ends the
        list first.

        The output indent O is ALWAYS that item's text column in spaces
        (marker indentation + marker + 1, as `_unwrap_div_table` computes
        for a TAB), because CommonMark only puts a fence inside the item at
        or past that column. The opener's own indentation, already in
        `out`, is replaced by it. The body shown is what Trac shows (F2):
        the opener's ORIGINAL indentation is stripped from every line when
        every non-blank line starts with it (an empty one strips nothing),
        and nothing is stripped otherwise. Fence, each non-blank shown line
        and closer are emitted at O; a blank line stays empty. The body is
        stashed as usual, but behind a placeholder line that is itself at
        O, so `_separate_list_from_trailing_paragraph` and `_convert_lists`
        read the block as the item's continuation (F3). The first shown
        line is carried by the placeholder itself, so the stash holds it
        without O.
        """
        line_start = text.rfind("\n", 0, start) + 1
        indent = text[line_start:start]
        if indent.strip(" \t"):
            return None
        marker = _open_list_item(_lines_since_blank(emitted))
        if marker is None:
            return None
        lead = _indent_of(marker.string)
        # Declined, so the old path stands: a marker Markdown cannot express
        # (lettered/roman, #74 -- the item is fenced away as unconverted
        # text, and a fence under it came back as an inline span), and a
        # TAB-indented marker line (CommonMark reads it as an indented code
        # block, which predates #144).
        if _ALPHA_LIST_MARKER_RE.match(marker.string) or "\t" in lead:
            return None
        token = marker.group(0)[len(lead) :]
        out_indent = " " * (_indent_width(lead) + len(token) + 1)
        lines = code.split("\n")
        if all(ln.startswith(indent) for ln in lines if ln.strip()):
            lines = [ln[len(indent) :] for ln in lines]
        shown = [out_indent + ln if ln.strip() else "" for ln in lines]
        # A blank first line leaves the placeholder line whitespace-only.
        first = shown[0][len(out_indent) :] if shown[0] else ""
        self._code_placeholders.append("\n".join([first] + shown[1:]))
        placeholder = f"\x00CODE{len(self._code_placeholders) - 1}\x00"
        return (
            f"{out_indent}{fence}{info}\n{out_indent}{placeholder}\n"
            f"{out_indent}{fence}",
            len(indent),
        )

    @staticmethod
    def _div_end(text: str, start: int) -> int:
        """Index just past the `}}}` closing the `{{{` at `start`, or -1.

        Like `_matching_brace`, but a backtick span on one line is stepped
        over, so a `}}}` quoted in a table cell does not close the block
        (measured on Trac: such a div renders intact and the list
        continues). Used only to try the div unwrap.
        """
        depth = 0
        i = start
        n = len(text)
        while i < n:
            if text.startswith("{{{", i):
                depth += 1
                i += 3
            elif text.startswith("}}}", i):
                depth -= 1
                i += 3
                if depth == 0:
                    return i
            elif text[i] == "`":
                j = text.find("`", i + 1)
                nl = text.find("\n", i + 1)
                i = j + 1 if j != -1 and (nl == -1 or j < nl) else i + 1
            else:
                i += 1
        return -1

    def _unwrap_div_table(
        self, text: str, start: int, body_start: int
    ) -> tuple[str, int, int] | None:
        """Unwrap a bare `{{{#!div}}}` that is a list item's table (#142).

        `start` is the index of the opener's `{{{`, `body_start` the index
        just past its newline. Returns (replacement, index to resume at,
        length of the opener's indentation already emitted by the caller),
        or None to leave the block to the paths in `_convert_code_blocks`.

        Unwraps only when the opener starts its line, the closer sits on its
        own line, the block is inside a list item (`_enclosing_list_marker`),
        and every body line, indentation included, is a `||` row
        (`_TABLE_ROW_RE`). Braces inside a row are cell content (a backtick
        span or inline `{{{x}}}`), not structure, so they do not decline.

        The replacement replaces the opener's indentation too: a mark line,
        then the rows, all at the opener's indentation. A TAB indentation is
        replaced by the item's content column in spaces (what the write leg
        emits): a tab expands to 8 columns, which Markdown reads as an
        indented code block, and `_convert_lists` does not treat a
        tab-indented line as the item's continuation.
        """
        end = self._div_end(text, start)
        if end == -1:
            return None
        line_start = text.rfind("\n", 0, start) + 1
        indent = text[line_start:start]
        if indent.strip(" \t"):
            return None
        body = text[body_start : end - 3]
        nl = body.rfind("\n")
        if nl == -1 or body[nl + 1 :].strip(" \t"):
            return None
        lines = body[:nl].split("\n")
        if not all(
            ln.startswith(indent) and _TABLE_ROW_RE.match(ln)
            for ln in lines
        ):
            return None
        above = text[:line_start].split("\n")[:-1]
        marker = _enclosing_list_marker(above, _indent_width(indent))
        if marker is None:
            return None
        if "\t" in indent:
            lead = _indent_of(marker.string)
            token = marker.group(0)[len(lead) :]
            new_indent = " " * (_indent_width(lead) + len(token) + 1)
            lines = [new_indent + ln[len(indent) :] for ln in lines]
            indent = new_indent
        return (
            f"{indent}{_DIV_TABLE_MARK}\n" + "\n".join(lines),
            end,
            len(text[line_start:start]),
        )

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

        Runs the blank-line separator (ticket #90) before the marker-
        character rewrite -- the two passes commute, since indentation
        (the only thing the separator reads) is untouched by swapping
        ``*`` for ``-``, but running it first keeps this method's own
        order matching the order the two defects were found in.
        """
        text = self._separate_list_from_trailing_paragraph(text)

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

    def _separate_list_from_trailing_paragraph(self, text: str) -> str:
        """Insert a blank line where a list ends with nothing marking it
        (ticket #90).

        A paragraph that follows a TracWiki list with no blank line and no
        indentation ends the list -- Trac renders it as a separate ``<p>``,
        not part of the last item (the same fact #75 measured for a
        flush-left *continuation* line: not deep enough, so the list ends).
        Passed through unchanged, that paragraph is indistinguishable to
        CommonMark from a genuine lazy continuation of the last list item,
        so mistune (and hence ``markdown_to_tracwiki``) absorbs it into the
        item. Inserting the blank line here reproduces the separation
        Trac's own renderer already gives it, which is exactly what
        CommonMark needs to stop reading it as a continuation.

        Runs after ``_convert_code_blocks``, unlike the fallback passes in
        ``_apply_fallbacks``, so no ``_verbatim_mask`` shielding is needed:
        every ``{{{ }}}`` block and code span is already a
        ``\\x00CODE<n>\\x00`` placeholder by this point, and #89's
        alpha/roman lists are already fenced away the same way -- neither
        can still contain a marker-shaped line for this pass to misread.

        Mechanism: a stack of marker columns, the same shape as
        ``_convert_indented_blocks``'s ``_quote_depths`` stack, walked line
        by line.

        * A blank line passes through untouched and leaves the stack alone
          -- a loose list survives a blank line, and #52/#53's existing
          blank-line behavior around lists is unaffected since this pass
          never removes a blank line, only adds one where none exists.
        * A line matching ``_LIST_MARKER_RE`` pops every stack entry at or
          past its own column (closing any list level this marker doesn't
          nest under, or replacing a same-column sibling item) and pushes
          its own column. This generalizes to nested lists with no special
          case: a marker at any depth is just another push.
        * Any other non-blank line pops every stack entry at or past its
          own indent width. An empty stack afterward means this line did
          not stay deeper than *any* currently open list level, so the
          list has ended. A blank line is inserted first, unless the
          previously emitted line is already blank OR this line is itself
          one of CommonMark's list-interrupting shapes (heading, blockquote,
          code fence, thematic break -- see
          ``_LAZY_CONTINUATION_INTERRUPT_RE``): those already start a new
          sibling block on their own, so adding a blank line ahead of them
          would be a needless, unmeasured stored-byte change rather than a
          fix for anything. A non-empty stack means the line is a genuine
          continuation of the remaining (outer) level and is left exactly
          as it is.

        Ticket #144: a marker line that arrives with the stack EMPTY (no
        open list), straight after a non-blank line, and whose marker is an
        ORDERED number other than 1 also gets a blank line first. CommonMark
        lets an ordered list interrupt a paragraph only when it starts with
        1, so ``3. x`` right after paragraph text is more paragraph text --
        and whatever is indented under it (a code fence, after #144) is its
        continuation, which the write leg turns into an inline code span.
        Trac renders a new list there either way (found on a store sweep:
        auto_pm #228 comment 9). Bullets and ``1.`` interrupt a paragraph
        already, so they get no blank line (an unmeasured stored-byte
        change); a marker that continues an open list needs none.

        Ticket #145: a continuation line (non-empty stack) that follows a
        citation line, i.e. a line starting with ``>``, also gets a blank
        line first, unless it is itself a ``>`` line or one of
        ``_LAZY_CONTINUATION_INTERRUPT_RE``'s shapes (a fence, ...). Plain
        text straight after ``> q`` is a lazy continuation of the quote to
        CommonMark, but Trac renders it after the blockquote, inside the
        item (measured on /trac_test); the blank line makes the Markdown
        mean what Trac renders, and the write leg takes it back out
        (``_item_block_separator``'s ``"adjacent"``). The next list marker
        needs none, and neither does the end of the text. The line must sit
        deeper than the item's marker and no deeper than its text column
        (measured: Trac keeps it in the item there, and renders a deeper
        one outside the list); it is then re-indented to the text column,
        which is where a Markdown item's content starts. Any other indent is
        left exactly as before. The inserted blank line makes the Markdown
        list loose (CommonMark renders every item with ``<p>``); that is the
        accepted trade-off of this design, the write leg reads a loose item
        the same as a tight one.

        The pop threshold is strict ("deeper than", not "at least as
        deep") -- ticket #75's own measured boundary (section 2's
        two-space continuation under a one-space marker: continues;
        section 4: "deeper than the marker... does not have to align
        exactly"), read directly off the marker's column via
        ``_indent_of``. #75's write-leg re-indentation
        (``len(prefix) + 1``) is that method's own choice of how deep to
        put a continuation back on the way OUT; it is not Trac's read-side
        threshold for what still counts as one, which is the marker's
        column alone.
        """
        lines = text.split("\n")
        out: list[str] = []
        stack: list[int] = []
        text_column: dict[int, int] = {}  # marker column -> text column
        for line in lines:
            if not line.strip():
                out.append(line)
                continue
            marker = _LIST_MARKER_RE.match(line)
            if marker:
                if (
                    not stack
                    and out
                    and out[-1].strip()
                    and _NON_ONE_ORDERED_RE.match(
                        marker.group(0).strip()
                    )
                ):
                    out.append("")
                column = len(_indent_of(line))
                while stack and stack[-1] >= column:
                    stack.pop()
                stack.append(column)
                after = line[marker.end() :]
                gap = len(after) - len(after.lstrip(" \t"))
                text_column[column] = marker.end() + (
                    gap if 1 <= gap <= 4 and after.strip() else 1
                )
                out.append(line)
                continue
            if stack:
                column = len(_indent_of(line))
                while stack and stack[-1] >= column:
                    stack.pop()
                if (
                    not stack
                    and out
                    and out[-1].strip()
                    and not _LAZY_CONTINUATION_INTERRUPT_RE.match(line)
                ):
                    out.append("")
                elif (
                    stack
                    and out
                    and out[-1].lstrip(" \t").startswith(">")
                    and column <= text_column[stack[-1]]
                    and not _LAZY_CONTINUATION_INTERRUPT_RE.match(
                        line.lstrip(" \t")
                    )
                ):
                    out.append("")
                    line = " " * text_column[stack[-1]] + line.lstrip(
                        " \t"
                    )
            out.append(line)
        return "\n".join(out)

    def _convert_other_elements(self, text: str) -> str:
        """Convert horizontal rules: ``----`` -> ``---``.

        The name is now wider than the contents.  Two other elements have
        left: blockquotes on ticket #73 and definition lists on #63, both to
        ``_convert_indented_blocks``, which runs before this pass.

        Blockquotes were handled here until ticket #73 and now run in
        ``_convert_indented_blocks``, before this pass.  The old code
        recognised *exactly two spaces* and emitted one ``> ``; every other
        width fell through to the Markdown untouched, where four-space
        indentation means a code block.  It also had to run after the
        definition-list pass (ticket #71) to stop it claiming a definition's
        continuation line -- a constraint that disappeared with that pass,
        which ticket #63 replaced with the verbatim fallback.

        What is left is the horizontal rule, whose pattern is deliberately
        anchored with no leading whitespace: measured on ticket #73, Trac
        renders `` ----`` as a blockquote containing the literal text
        ``----``, not as a rule.
        """
        return re.sub(r"^----+\s*$", r"---", text, flags=re.MULTILINE)

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
        table_indents: list[str] = []  # Leading whitespace of each row
        # Ticket #142: the group being accumulated came from a list item's
        # unwrapped {{{#!div}}} (it follows a `_DIV_TABLE_MARK` line), and
        # whether the previous emitted group did, with nothing since.
        div_group = False
        prev_div_end = -1

        def flush_table():
            """Process accumulated table rows and add to result."""
            nonlocal table_rows, table_alignments, table_indents
            nonlocal div_group, prev_div_end
            if not table_rows:
                return
            # Ticket #146: bare rows under a list item render OUTSIDE the
            # list in Trac, but with no blank line between the item text
            # and the table Markdown reads the table as part of the item,
            # and the write leg then puts it back in the list as a div. A
            # blank line (no indent) ends the item in Markdown too. Only
            # when an item is still open over the lines above (the walk
            # `_open_list_item` does, from the last blank line); a div
            # group (#142) stays in the item on purpose.
            if not div_group and result and result[-1].strip():
                since_blank = len(result)
                while since_blank and result[since_blank - 1].strip():
                    since_blank -= 1
                if _open_list_item(result[since_blank:]) is not None:
                    result.append("")
            first_line_at = len(result)

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

            # Ticket #142: rows released from a list item's {{{#!div}}} keep
            # the group's common indentation on every emitted line (tabs as
            # spaces of equal width), so the Markdown table stays part of
            # the item and the write leg can put it back in a div. Every
            # other row group -- bare indented rows in an item, which Trac
            # renders outside the list, and a blockquoted table -- drops it
            # as ever.
            if div_group:
                group_indent = min(table_indents, key=_indent_width)
                prefix = group_indent.expandtabs(_TAB_WIDTH)
                result[first_line_at:] = [
                    prefix + ln if ln else ln
                    for ln in result[first_line_at:]
                ]
                prev_div_end = len(result)
            div_group = False

            table_rows = []
            table_alignments = []
            table_indents = []

        for line in lines:
            if _DIV_TABLE_MARK_RE.match(line):
                # The mark ends any group in progress. Two unwrapped divs
                # back to back must stay two tables: with nothing between
                # them Markdown reads one table and demotes the second
                # header to a data row, so put a blank line (no indent) in.
                flush_table()
                if prev_div_end == len(result):
                    result.append("")
                div_group = True
            elif _TABLE_ROW_RE.match(line):
                cells, aligns, is_header = self._parse_tracwiki_row(
                    line
                )
                table_rows.append((cells, aligns, is_header))
                table_indents.append(_indent_of(line))
                # Use alignments from first row
                if not table_alignments:
                    table_alignments = aligns
            else:
                # End of table - flush accumulated rows
                flush_table()
                div_group = False
                # Ticket #143: item text straight after a table released
                # from a list item's {{{#!div}}} needs a blank line (no
                # indent) in the Markdown, or a GFM reader takes it for one
                # more table row (spec example 202). The next list marker,
                # a blank line and a block start (a fence opener, heading,
                # quote or rule: GFM ends a table at those) need none.
                if (
                    prev_div_end == len(result)
                    and line.strip()
                    and not _LIST_MARKER_RE.match(line)
                    and not _LAZY_CONTINUATION_INTERRUPT_RE.match(
                        line.lstrip(" \t")
                    )
                ):
                    result.append("")
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

        Loops to a fixed point rather than substituting once, because
        ``re.sub`` scans its input once and would leave a sentinel that
        another substitution had just exposed sitting in the output.

        That used to happen on every nested ``{{{ }}}`` block: the inner
        one was stashed first, and the outer block's capture -- still
        holding the inner placeholder literally -- was stashed as a
        second, higher-numbered placeholder whose body contained the
        first one's sentinel bytes (ticket #51). Ticket #72 stashes a
        nested block once, as part of its enclosing block's body, so no
        input in the converter suite needs more than one pass any more
        (measured: max 1 iteration across all 309 converter tests). The
        loop stays as the guard for a stash path that has not been
        enumerated; without it such a case leaks a sentinel, which
        ``parse`` turns into a loud ValueError rather than corruption.
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
