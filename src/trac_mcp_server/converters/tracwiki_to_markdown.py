"""TracWiki to Markdown conversion using regex patterns."""

import re
from typing import Literal

from .common import (
    ConversionResult,
    is_link_target,
    tracwiki_to_markdown_lang,
)

# Type alias for the unknown_macros rendering mode.
UnknownMacros = Literal["bracket", "preserve", "drop"]

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


class TracWikiParser:
    """Parser for converting TracWiki syntax to Markdown format."""

    def __init__(self, *, unknown_macros: UnknownMacros = "bracket"):
        """Initialize parser with empty warnings list.

        Args:
            unknown_macros: Controls how unrecognized ``[[MacroName]]`` tokens
                are rendered in the Markdown output.

                - ``"bracket"`` (default): emit ``[MACRO: MacroName]`` — makes
                  the macro visible but non-functional; current behavior.
                - ``"preserve"``: leave ``[[MacroName]]`` literal in the output
                  so the TracWiki syntax survives the conversion unchanged.
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
        text = self._convert_processor_cells(text)
        text = self._convert_code_blocks(text)
        text = self._convert_macros(text)
        text = self._convert_headings(text)
        text = self._convert_formatting(text)
        text = self._convert_links(text)
        text = self._convert_lists(text)
        text = self._convert_other_elements(text)
        text = self._convert_tables(text)
        text = self._restore_macro_placeholders(text)
        text = self._restore_link_placeholders(text)
        text = self._restore_code_placeholders(text)

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
                self.warnings.append(
                    "Unknown macros detected - preserved as [MACRO: ...] notation (not functional in Markdown)"
                )
                break

        # Detect definition lists
        if re.search(r"^\s*.+?::\s*.+$", text, re.MULTILINE):
            self.warnings.append(
                "Definition lists detected - converted to bold text (semantic preservation)"
            )

        # Detect tables and their features
        has_regular_tables = re.search(r"\|\|.*\|\|", text)
        has_processor_tables = re.search(r"\{\{\{#!t[dh]", text)

        if has_regular_tables:
            # Check for cell spanning (|||| indicates spanning)
            if re.search(r"\|\|\|\|", text):
                self.warnings.append(
                    "Table cell spanning detected - merged into single cell (Markdown limitation)"
                )
            # Check for multi-line rows (backslash continuation)
            if re.search(r"\\\s*\n\s*\|\|", text):
                self.warnings.append(
                    "Multi-line table rows detected - joined into single line (Markdown limitation)"
                )

        # Check for processor-based tables (can exist without regular || tables)
        if has_processor_tables:
            self.warnings.append(
                "Processor-based table cells (#td/#th) detected - converted to plain text (Markdown limitation)"
            )

        # Detect TracLinks
        if re.search(r"(#\d+|ticket:\d+|wiki:\w+|changeset:\w+)", text):
            self.warnings.append(
                "TracLinks detected - preserved as-is (agents can interpret, but not clickable in Markdown renderers)"
            )

    def _convert_processor_cells(self, text: str) -> str:
        """Convert processor-based table cells BEFORE code blocks.

        {{{#!td ... }}} or {{{#!th ... }}} - these are table cells, not code blocks.
        Convert to regular table cell content with marker for later table processing.
        """

        def convert_processor_cell(match):
            cell_type = match.group(1)  # 'td' or 'th'
            content = match.group(2).strip()
            # Replace newlines with spaces for single-line cell content
            content = " ".join(content.split())
            if cell_type == "th":
                return f"||={content}=||"
            else:
                return f"||{content}||"

        return re.sub(
            r"\{\{\{#!(t[dh])\s*(.*?)\s*\}\}\}",
            convert_processor_cell,
            text,
            flags=re.DOTALL,
        )

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
        """

        def stash(body: str) -> str:
            self._code_placeholders.append(body)
            return f"\x00CODE{len(self._code_placeholders) - 1}\x00"

        # Map TracWiki processor directive to Markdown language (e.g., 'sh' -> 'bash')
        def convert_code_block_with_lang(match: re.Match[str]) -> str:
            tracwiki_lang = match.group(1)
            code = match.group(2)
            md_lang = tracwiki_to_markdown_lang(tracwiki_lang)
            return f"```{md_lang}\n{stash(code)}\n```"

        text = re.sub(
            r"\{\{\{#!(\w+)\n(.*?)\n\}\}\}",
            convert_code_block_with_lang,
            text,
            flags=re.DOTALL,
        )
        # Code block without language
        text = re.sub(
            r"\{\{\{\n(.*?)\n\}\}\}",
            lambda m: f"```\n{stash(m.group(1))}\n```",
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
                    case "bracket":
                        # Current default: emit a placeholder restored to [MACRO: ...]
                        return f"\x00MACRO:{name}{args}\x00"
                    case "preserve":
                        # Leave the original [[MacroName(args)]] literal unchanged.
                        return m.group(0)
                    case "drop":
                        # Silently omit the macro from the output.
                        return ""
                    case _:
                        return ""
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
        """
        # Horizontal rule
        text = re.sub(r"^----+\s*$", r"---", text, flags=re.MULTILINE)

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

        # Definition lists: term:: definition -> **term**: definition
        # TracWiki uses :: separator, Markdown has no native definition list
        # Convert to bold term + regular text (semantic preservation)
        text = re.sub(
            r"^(\s*)(.+?)::\s*(.+)$",
            r"\1**\2**: \3",
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

    def _restore_macro_placeholders(self, text: str) -> str:
        """Restore macro placeholders.

        Convert \x00MACRO:Name(args)\x00 back to [MACRO: Name(args)].

        The placeholder body is matched as "anything but \x00" rather than
        "anything but )" -- the latter let a greedy match span past the
        closing \x00 of one placeholder into the next when a page had
        several placeholders near each other (e.g. two [[TOC]]-style macros
        on nearby lines), merging them into one bracket and leaking raw
        \x00 bytes into the output (ticket #28).
        """
        return re.sub(r"\x00MACRO:([^\x00]+)\x00", r"[MACRO: \1]", text)

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
        """

        def restore(m: re.Match[str]) -> str:
            return self._code_placeholders[int(m.group(1))]

        return re.sub(r"\x00CODE(\d+)\x00", restore, text)


def tracwiki_to_markdown(
    tracwiki_text: str, *, unknown_macros: UnknownMacros = "bracket"
) -> ConversionResult:
    """
    Convert TracWiki text to Markdown format.

    This is a best-effort conversion using regex replacements. Unknown TracWiki
    macros and unsupported features pass through unchanged without errors.

    Args:
        tracwiki_text: TracWiki formatted text
        unknown_macros: How to render unrecognized ``[[Macro]]`` tokens.
            ``"bracket"`` (default) emits ``[MACRO: Name]``,
            ``"preserve"`` leaves ``[[Name]]`` literal,
            ``"drop"`` silently omits the macro.
            Mirrors the ``--unknown-macros`` CLI flag.

    Returns:
        ConversionResult with Markdown text and warnings about lossy conversions
    """
    parser = TracWikiParser(unknown_macros=unknown_macros)
    return parser.parse(tracwiki_text)
