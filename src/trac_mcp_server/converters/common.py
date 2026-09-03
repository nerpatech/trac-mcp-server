"""Common types and utilities for format conversion."""

import re
from dataclasses import dataclass, field

# =============================================================================
# Code Block Language Mapping
# =============================================================================
#
# Bidirectional mapping between Markdown code fence language identifiers and
# TracWiki processor directives.
#
# Markdown: ```python
# TracWiki: {{{#!python}}}
#
# Design:
# - Store Markdown->TracWiki as the canonical direction
# - Derive TracWiki->Markdown mapping automatically
# - Handle asymmetric cases where multiple Markdown names map to one TracWiki name
# - Unknown languages pass through unchanged for forward compatibility
# =============================================================================

# Markdown language identifier -> TracWiki processor directive
# Only include mappings where names differ or where we want to normalize
_MARKDOWN_TO_TRACWIKI_MAP: dict[str, str] = {
    # Shell scripting: Markdown uses bash/shell, TracWiki uses sh
    "bash": "sh",
    "shell": "sh",
    "zsh": "sh",
    # JavaScript variants
    "js": "javascript",
    # TypeScript variants
    "ts": "typescript",
    # C++ variants
    "c++": "cpp",
    # Text/plaintext normalization
    "text": "text",
    "plaintext": "text",
    "plain": "text",
}

# TracWiki processor directive -> Markdown language identifier (canonical form)
# Built from the inverse of _MARKDOWN_TO_TRACWIKI_MAP
# When multiple Markdown names map to the same TracWiki name, we pick one canonical form
_TRACWIKI_TO_MARKDOWN_CANONICAL: dict[str, str] = {
    # Shell: TracWiki 'sh' -> Markdown 'bash' (most common form)
    "sh": "bash",
    # These are identity or prefer the short form in Markdown
    "javascript": "javascript",
    "typescript": "typescript",
    "cpp": "cpp",
    "text": "text",
}

# Languages that are identical in both formats (no mapping needed, but listed for documentation)
# These pass through unchanged: python, java, c, ruby, go, rust, sql, html, css, xml, json, yaml, diff
_IDENTITY_LANGUAGES: frozenset[str] = frozenset(
    {
        "python",
        "java",
        "c",
        "ruby",
        "go",
        "rust",
        "sql",
        "html",
        "css",
        "xml",
        "json",
        "yaml",
        "diff",
        "markdown",
        "md",
        "perl",
        "php",
        "r",
        "scala",
        "swift",
        "kotlin",
        "lua",
        "makefile",
        "dockerfile",
        "nginx",
        "apache",
        "ini",
        "toml",
    }
)


def markdown_to_tracwiki_lang(lang: str) -> str:
    """
    Convert Markdown code fence language to TracWiki processor directive.

    Args:
        lang: Markdown language identifier (e.g., 'bash', 'python', 'js')

    Returns:
        TracWiki processor directive name. Returns input unchanged if no mapping exists.

    Examples:
        >>> markdown_to_tracwiki_lang("bash")
        'sh'
        >>> markdown_to_tracwiki_lang("python")
        'python'
        >>> markdown_to_tracwiki_lang("unknown")
        'unknown'
    """
    # Normalize to lowercase for consistent lookup
    lang_lower = lang.lower()

    # Check explicit mapping first
    if lang_lower in _MARKDOWN_TO_TRACWIKI_MAP:
        return _MARKDOWN_TO_TRACWIKI_MAP[lang_lower]

    # No mapping - pass through unchanged (identity languages and unknown)
    return lang


def tracwiki_to_markdown_lang(processor: str) -> str:
    """
    Convert TracWiki processor directive to Markdown code fence language.

    Args:
        processor: TracWiki processor directive (e.g., 'sh', 'python')

    Returns:
        Markdown language identifier in canonical form. Returns input unchanged
        if no mapping exists.

    Examples:
        >>> tracwiki_to_markdown_lang("sh")
        'bash'
        >>> tracwiki_to_markdown_lang("python")
        'python'
        >>> tracwiki_to_markdown_lang("unknown")
        'unknown'
    """
    # Normalize to lowercase for consistent lookup
    processor_lower = processor.lower()

    # Check explicit mapping first
    if processor_lower in _TRACWIKI_TO_MARKDOWN_CANONICAL:
        return _TRACWIKI_TO_MARKDOWN_CANONICAL[processor_lower]

    # No mapping - pass through unchanged (identity languages and unknown)
    return processor


# =============================================================================
# Link target validation
# =============================================================================
#
# Both converters need the same answer to one question: "is this string a
# real link target, or is it prose that merely looks bracket-shaped?"
# Keeping the rule in one place is what makes the two directions symmetric —
# a target that markdown_to_tracwiki emits verbatim must not be re-parsed as
# a link by tracwiki_to_markdown on the way back (tickets #13, #14, #17).
# =============================================================================

# TracLink resolvers that Trac understands natively as the target of
# `[target text]`. Deliberately an explicit allowlist rather than "anything
# scheme-shaped": non-URL sentinels such as ``auto-pm:`` or ``foo:bar`` must
# stay literal (ticket #8).
TRACLINK_SCHEMES: frozenset[str] = frozenset(
    {
        "attachment",
        "browser",
        "changeset",
        "comment",
        "diff",
        "export",
        "htdocs",
        "log",
        "milestone",
        "query",
        "raw-attachment",
        "report",
        "repos",
        "search",
        "source",
        "ticket",
        "timeline",
        "wiki",
    }
)

# Transport schemes valid as a link target in either format.
URL_SCHEMES: frozenset[str] = frozenset(
    {"http", "https", "ftp", "ftps", "mailto", "irc", "news"}
)

# scheme:target — target must be non-empty, so a bare ``auto-pm:`` sentinel
# never matches even if its scheme were listed above.
SCHEME_RE = re.compile(
    r"(?P<scheme>[A-Za-z][\w+.-]*):(?P<target>\S.*)\Z"
)


def is_link_target(candidate: str) -> bool:
    """Return True if ``candidate`` is a usable link target.

    A target qualifies when it either carries no ``:`` at all (a wiki page
    name or relative path — Trac reserves ``:`` for resolvers, so page names
    never contain one), or carries a ``scheme:`` prefix drawn from
    :data:`TRACLINK_SCHEMES` or :data:`URL_SCHEMES` followed by a non-empty
    target.

    Everything else is prose that happens to sit inside brackets — sentinel
    markers like ``auto-pm:`` or ``[label](foo:bar)``. Callers must emit
    those verbatim rather than rewriting them into a broken link.

    Examples:
        >>> is_link_target("WikiPage")
        True
        >>> is_link_target("wiki:WikiPage")
        True
        >>> is_link_target("https://example.com")
        True
        >>> is_link_target("auto-pm:")
        False
        >>> is_link_target("foo:bar")
        False
    """
    if ":" not in candidate:
        return True
    match = SCHEME_RE.match(candidate)
    if match is None:
        return False
    scheme = match.group("scheme").lower()
    return scheme in TRACLINK_SCHEMES or scheme in URL_SCHEMES


@dataclass
class ConversionResult:
    """Result of format conversion with metadata and warnings.

    Attributes:
        text: Converted text output
        source_format: Format of input text ('markdown', 'tracwiki', or 'unknown')
        target_format: Format of output text ('markdown' or 'tracwiki')
        converted: True if conversion performed, False if pass-through (formats matched)
        warnings: List of warnings about lossy conversions or unsupported features
    """

    text: str
    source_format: str = "unknown"
    target_format: str = "unknown"
    converted: bool = False
    warnings: list[str] = field(default_factory=list)

    # Backward compatibility: tracwiki property returns text
    @property
    def tracwiki(self) -> str:
        """Backward compatibility property for old code expecting .tracwiki"""
        return self.text


def _strip_code_fences(text: str) -> str:
    """Return ``text`` with content inside fenced code blocks redacted.

    Markdown ``` … ``` and TracWiki {{{ … }}} blocks both shadow heading
    syntax in the surrounding prose — a doc that documents one format by
    showing examples of the OTHER must not have its detection inverted by
    those examples. Replaces fence interiors with empty lines so line
    numbering / regex anchors elsewhere remain stable.
    """
    out: list[str] = []
    in_md_fence = False
    in_tw_fence_depth = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        if not in_md_fence and in_tw_fence_depth == 0:
            if stripped.startswith("```"):
                in_md_fence = True
                out.append("")
                continue
            if stripped.startswith("{{{"):
                in_tw_fence_depth = 1 + stripped.count("{{{") - 1
                out.append("")
                continue
            out.append(line)
            continue
        if in_md_fence:
            if stripped.startswith("```"):
                in_md_fence = False
            out.append("")
            continue
        if in_tw_fence_depth > 0:
            in_tw_fence_depth += stripped.count("{{{")
            in_tw_fence_depth -= stripped.count("}}}")
            if in_tw_fence_depth < 0:
                in_tw_fence_depth = 0
            out.append("")
            continue
    return "\n".join(out)


def blank_code_fences(text: str) -> str:
    """Return ``text`` with fenced blocks replaced by spaces, character
    for character, so the result has the SAME LENGTH as the input.

    Same intent as :func:`_strip_code_fences`, but usable by a caller
    that scans the blanked copy and then indexes back into the original
    — ``match.start()``/``match.end()`` stay valid, which
    ``_check_unconfigured_intertrac_prefix`` needs for its bracketed-
    label handling (ticket #59). ``_strip_code_fences`` collapses fence
    lines to empty strings and so cannot be used for that.

    It also differs in one behaviour, deliberately: a fence opened and
    closed on the SAME line (``{{{ x }}}``) is closed here. In
    ``_strip_code_fences`` it is not, and everything after such a line
    reads as fence interior — harmless for that function's callers,
    which only ask "does this pattern appear anywhere outside a fence",
    but as a blanking function it would suppress every warning after
    such a line. That converts ticket #59's false positive into a false
    negative, the more dangerous sign, so it is fixed here rather than
    inherited.
    """
    out: list[str] = []
    in_md_fence = False
    in_tw_fence_depth = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        stripped = body.lstrip()
        blanked = " " * len(body) + newline

        if not in_md_fence and in_tw_fence_depth == 0:
            if stripped.startswith("```"):
                in_md_fence = True
                out.append(blanked)
                continue
            if stripped.startswith("{{{"):
                # Net depth on the opening line itself: `{{{ x }}}` on
                # one line opens and closes, leaving depth 0.
                depth = stripped.count("{{{") - stripped.count("}}}")
                in_tw_fence_depth = max(depth, 0)
                out.append(blanked)
                continue
            out.append(line)
            continue

        if in_md_fence:
            if stripped.startswith("```"):
                in_md_fence = False
            out.append(blanked)
            continue

        in_tw_fence_depth += stripped.count("{{{")
        in_tw_fence_depth -= stripped.count("}}}")
        if in_tw_fence_depth < 0:
            in_tw_fence_depth = 0
        out.append(blanked)
    return "".join(out)


#: An inline code span: a run of backticks, a body, and a matching run.
#: Deliberately NOT DOTALL -- a span is confined to one line here. A
#: stray unmatched backtick is common in prose, and letting a span run
#: across paragraphs would blank an arbitrary stretch of the document,
#: which is the dangerous direction: over-blanking silences real defects
#: while leaving every test superficially green (ticket #65, row 71).
_INLINE_CODE_SPAN_RE = re.compile(
    r"(?P<ticks>`+)(?:(?!(?P=ticks)).)+(?P=ticks)"
)


def blank_inline_code_spans(text: str) -> str:
    """Return ``text`` with inline code spans replaced by spaces,
    character for character, so the result has the SAME LENGTH as the
    input -- the companion to :func:`blank_code_fences` for the other
    way of marking content as literal.

    Markup inside backticks is quoted ON PURPOSE and Trac renders it as
    literal text, which is exactly the outcome
    ``tracwiki_markup_in_markdown`` exists to warn about -- so warning
    about it is self-defeating, and that false positive is what ticket
    #65 measured firing constantly once TracWiki authoring landed
    (#62).

    Run this AFTER ``blank_code_fences``: a fenced block's own
    delimiters are backticks, and blanking the fences first stops them
    being read as span delimiters.

    Newlines are preserved along with length, so any line-oriented
    logic downstream still sees the same shape.
    """

    def _blank(match: re.Match[str]) -> str:
        return "".join(
            "\n" if ch == "\n" else " " for ch in match.group(0)
        )

    return _INLINE_CODE_SPAN_RE.sub(_blank, text)


def detect_format_heuristic(text: str) -> str:
    """Heuristic format detection (fallback when capabilities unavailable).

    Priority:
    1. Check for unambiguous markers (TracWiki heading with trailing =, Markdown # without)
    2. Score ambiguous markers (count syntax elements)
    3. Default to 'tracwiki' if unclear

    Code-fenced content is redacted before scanning so a Markdown doc that
    embeds TracWiki examples (or vice-versa) is not misclassified by its
    own examples. Heading-based detection is also anchored to line start —
    the previous unanchored regex matched ``key = value = result`` style
    prose and inverted the verdict on otherwise-clean Markdown.

    Returns 'markdown' or 'tracwiki'.
    """
    scan_text = _strip_code_fences(text)

    # Check for unambiguous TracWiki markers
    # Heading with trailing equals at LINE START: = H1 = or == H2 ==
    if re.search(
        r"^={1,6}\s+.+?\s+={1,6}\s*$", scan_text, re.MULTILINE
    ):
        return "tracwiki"

    # Check for unambiguous Markdown markers
    # Heading without trailing equals: # H1 or ## H2
    if re.search(r"^#{1,6}\s+[^=]", scan_text, re.MULTILINE):
        return "markdown"

    # Score ambiguous markers (use original text — fence delimiters
    # themselves are signal, even when interiors are redacted).
    md_score = (
        text.count("**")  # Markdown bold
        + text.count("```")  # Markdown code fence
        + text.count("](")  # Markdown link
    )
    tw_score = (
        text.count("'''")  # TracWiki bold
        + text.count("{{{")  # TracWiki code block
        + text.count("[[")  # TracWiki macro/image
    )

    # If scores are equal or unclear, default to TracWiki
    return "markdown" if md_score > tw_score else "tracwiki"


async def auto_convert(
    text: str,
    config,
    target_format: str | None = None,
    source_format: str | None = None,
) -> ConversionResult:
    """Automatically convert text based on server capabilities and source format.

    If target_format specified, converts to that format.
    If target_format is None, uses server capabilities to determine target:
    - If server has markdown processor: prefer Markdown
    - If server has no markdown processor: use TracWiki

    If source_format is specified, the heuristic is skipped and the caller's
    declared format is honored. Callers that already know the source format
    (e.g. wiki_file_push handler with explicit ``format=`` arg) MUST pass it
    through so re-detection cannot invert the verdict on bait-laden inputs.

    Args:
        text: Text to convert
        config: Config with Trac server URL/credentials
        target_format: Optional 'markdown' or 'tracwiki' (None = auto-detect from server)
        source_format: Optional 'markdown' or 'tracwiki'. When provided, the
            content heuristic is skipped — use this when the caller already
            knows the source format (e.g. from a file extension), since the
            heuristic can be fooled by content that contains examples of the
            *other* format inside code blocks or prose.

    Returns:
        ConversionResult with converted text and metadata
    """
    from trac_mcp_server.converters.markdown_to_tracwiki import (
        convert_with_warnings as markdown_to_tracwiki,
    )
    from trac_mcp_server.converters.tracwiki_to_markdown import (
        tracwiki_to_markdown,
    )
    from trac_mcp_server.detection.capabilities import (
        get_server_capabilities,
    )

    # Determine target format if not specified
    if target_format is None:
        try:
            caps = await get_server_capabilities(config)
            target_format = (
                "markdown" if caps.markdown_processor else "tracwiki"
            )
        except Exception:
            # Capabilities detection failed, default to TracWiki
            target_format = "tracwiki"

    # Honor caller-supplied source_format; only re-detect when caller doesn't know.
    if source_format is None:
        source_format = detect_format_heuristic(text)

    # Convert if formats differ
    if source_format == target_format:
        # Pass-through - no conversion needed
        return ConversionResult(
            text=text,
            source_format=source_format,
            target_format=target_format,
            converted=False,
            warnings=[],
        )
    elif source_format == "markdown" and target_format == "tracwiki":
        return markdown_to_tracwiki(text)
    elif source_format == "tracwiki" and target_format == "markdown":
        return tracwiki_to_markdown(text)
    else:
        # Unknown format combination, pass through
        return ConversionResult(
            text=text,
            source_format=source_format,
            target_format=target_format,
            converted=False,
            warnings=[
                "Unknown format combination - text passed through unchanged"
            ],
        )


# =============================================================================
# Code-block indentation loss (ticket #68)
# =============================================================================


def _delimited_code_blocks(
    text: str, tracwiki_only: bool = False
) -> list[list[str]]:
    """Return each DELIMITED code block's body, as a list of raw lines.

    Recognises Markdown fences (``` / ~~~) and TracWiki ``{{{ }}}``
    blocks, whichever opens first, with ``{{{`` nesting tracked by
    depth. ``tracwiki_only`` scans for ``{{{ }}}`` alone -- what the
    CONVERTED side is written in, where a run of backticks is content
    rather than a delimiter.

    A four-space-indented Markdown code block is deliberately NOT a
    block here. It has no delimiters, and dropping its four-space
    marker is exactly what correct conversion does -- counting it would
    make `find_code_block_indentation_loss` report every one of them.

    An unterminated block is dropped rather than closed at EOF: its
    extent is unknown, and guessing it invents body lines that were
    never in a block.
    """
    blocks: list[list[str]] = []
    body: list[str] = []
    md_fence = ""  # the opening run (``` or ~~~), "" when not in one
    tw_depth = 0

    for raw in text.splitlines():
        stripped = raw.lstrip()

        if md_fence:
            if stripped.startswith(md_fence):
                blocks.append(body)
                body, md_fence = [], ""
            else:
                body.append(raw)
            continue

        if tw_depth:
            tw_depth += stripped.count("{{{")
            tw_depth -= stripped.count("}}}")
            if tw_depth <= 0:
                blocks.append(body)
                body, tw_depth = [], 0
            else:
                body.append(raw)
            continue

        if not tracwiki_only and (
            stripped.startswith("```") or stripped.startswith("~~~")
        ):
            md_fence = stripped[:3]
            continue

        if stripped.startswith("{{{"):
            # Net depth on the opening line: `{{{ x }}}` opens and
            # closes, leaving nothing for a body to be read into.
            depth = stripped.count("{{{") - stripped.count("}}}")
            if depth > 0:
                tw_depth = depth
            continue

    return blocks


def _relative_indent_profile(
    lines: list[str],
) -> list[tuple[int, str]]:
    """``(indent relative to the block's own minimum, content)`` for
    each non-blank line.

    Measuring RELATIVE to the block's minimum is what keeps a fenced
    block nested inside a list item silent: mistune dedents such a
    block by the list's own indent, legitimately and by the same amount
    on every line, so the profile is unchanged. The cost is a
    deliberate blind spot -- a UNIFORM dedent of a whole block is not
    reported. That is the safe direction: what makes a stripped code
    block syntactically invalid is losing its internal structure, and
    a uniform shift leaves that intact.
    """
    indents = [
        len(line) - len(line.lstrip()) for line in lines if line.strip()
    ]
    base = min(indents) if indents else 0
    return [
        (len(line) - len(line.lstrip()) - base, line.strip())
        for line in lines
        if line.strip()
    ]


def find_code_block_indentation_loss(
    source: str, tracwiki: str
) -> list[dict]:
    """Report code-block lines whose leading whitespace shrank in
    conversion (ticket #68).

    Feeding a TracWiki ``{{{ }}}`` block to the Markdown converter
    strips the indentation from its body -- the block is not recognised
    as a code construct, so ordinary paragraph handling eats it. The
    result is syntactically invalid code stored with an EMPTY warnings
    list and a render that looks entirely plausible; it is visible only
    in the stored bytes. Every other rule in the warning suite catches
    damage a reader can SEE.

    Args:
        source: What the caller wrote (Markdown-declared input only --
            a TracWiki-declared write is stored verbatim and cannot
            lose anything, so callers must not run this on one).
        tracwiki: The converted TracWiki that would be stored.

    Returns:
        One dict per damaged block -- ``{line, content, source_indent,
        converted_indent}`` for its FIRST shrunken line. Empty list
        means no loss detected, which on this check means the stored
        bytes are safe to write.

    Blocks are paired by their whitespace-stripped body content, not by
    position: a document holding both a four-space-indented Markdown
    block (not a block here) and a damaged ``{{{`` block (one) has a
    different block count on each side, and a positional rule skips it
    entirely -- a false negative on a real defect. Content-pairing
    requires an unambiguous match, so two IDENTICAL damaged blocks are
    a known, accepted residual: both stay silent rather than risk
    reporting against the wrong counterpart.
    """
    converted_profiles = [
        _relative_indent_profile(block)
        for block in _delimited_code_blocks(
            tracwiki, tracwiki_only=True
        )
    ]

    losses: list[dict] = []
    for block in _delimited_code_blocks(source):
        source_profile = _relative_indent_profile(block)
        if not source_profile:
            continue
        key = [content for _, content in source_profile]
        matches = [
            profile
            for profile in converted_profiles
            if [content for _, content in profile] == key
        ]
        if len(matches) != 1:
            continue  # absent or ambiguous -- stay silent
        converted_profile = matches[0]

        for index, (
            (source_indent, content),
            (converted_indent, _),
        ) in enumerate(
            # Equal length by construction: the two were paired
            # on their content keys, so strict= can only fire if
            # that pairing is ever loosened.
            zip(source_profile, converted_profile, strict=True)
        ):
            if converted_indent < source_indent:
                losses.append(
                    {
                        "line": index + 1,
                        "content": content,
                        "source_indent": source_indent,
                        "converted_indent": converted_indent,
                    }
                )
                break  # one report per block, not per line

    return losses


def describe_indentation_loss(loss: dict) -> str:
    """One-line message for a `find_code_block_indentation_loss` entry,
    shared by every surface that reports one as plain text."""
    return (
        f"Code block line {loss['line']} lost leading whitespace in "
        f"conversion ({loss['source_indent']} -> "
        f"{loss['converted_indent']} spaces): {loss['content']!r}. The "
        "stored code would be syntactically invalid -- a TracWiki "
        "{{{ }}} block in Markdown input is not recognised as a code "
        "construct, so its indentation is eaten. Push it as TracWiki, "
        "or write the block as a Markdown fence."
    )
