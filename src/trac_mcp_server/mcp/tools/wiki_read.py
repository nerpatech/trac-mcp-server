"""Read-only wiki tool handlers for MCP server.

This module implements wiki read operations: get, search, and recent_changes.
All tools use async handlers with run_sync() to bridge synchronous TracClient calls,
automatic Markdown conversion, and structured error responses.
"""

import asyncio
import base64
import difflib
import json
import time
import xmlrpc.client
from datetime import datetime, timedelta

import mcp.types as types

from ...core.async_utils import run_sync, run_sync_limited
from ...core.client import TracClient
from .errors import (
    build_error_response,
    format_timestamp,
    translate_xmlrpc_error,
)
from .registry import ToolSpec
from .source_format import reject_removed_conversion_args

# wiki_diff's default cap on the returned diff text (ticket #119).
_DIFF_MAX_CHARS = 50000

# Tool definitions for list_tools()
WIKI_READ_TOOLS = [
    types.Tool(
        name="wiki_get",
        description="Get wiki page content as stored: TracWiki, byte-for-byte. Returns full content with metadata (version, author, modified date).",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "Wiki page name to retrieve (required)",
                },
                "version": {
                    "type": "integer",
                    "description": "Specific version to retrieve (optional, defaults to latest)",
                    "minimum": 1,
                },
            },
            "required": ["page_name"],
        },
    ),
    types.Tool(
        name="wiki_search",
        description="Search wiki pages by content with relevance ranking. Returns snippets showing matched text, as stored: TracWiki, byte-for-byte.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string (required)",
                },
                "prefix": {
                    "type": "string",
                    "description": "Filter to pages starting with this prefix (namespace filter, optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results per page (default: 10, max: 50)",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                },
                "cursor": {
                    "type": "string",
                    "description": "Pagination cursor from previous response (optional)",
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="wiki_recent_changes",
        description="Get recently modified wiki pages. Returns pages sorted by modification date (newest first). Useful for finding stale or recently updated documentation.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "since_days": {
                    "type": "integer",
                    "description": "Return pages modified within this many days",
                    "default": 30,
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 20, max: 100)",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": [],
        },
    ),
    types.Tool(
        name="wiki_get_history",
        description="Get wiki page revision history newest-first. Returns list of revisions with version, author, lastModified, and comment (commit message). Useful for detecting prior edits or scanning attribution markers in change comments.",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "Wiki page name to retrieve history for (required)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum revisions to return, newest first. Omit for all revisions.",
                    "minimum": 1,
                },
            },
            "required": ["page_name"],
        },
    ),
    types.Tool(
        name="wiki_diff",
        description=(
            "Unified diff between two versions of a wiki page, computed on "
            "the stored TracWiki, together with the revisions in the range "
            "(version, author, time, change comment), oldest first. The "
            "start is from_version (default: the version before "
            "to_version; the empty page when to_version is 1) or "
            "from_author: the newest version by that author at or before "
            "to_version, i.e. everything since that author's last write "
            "(identical=true when the author wrote to_version itself). "
            "Returns the resolved versions, identical, added/removed line "
            "counts and the hunk count; a diff longer than max_chars is "
            "cut at a hunk boundary and flagged truncated, with the counts "
            "still covering the whole diff."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "Wiki page name to diff (required)",
                },
                "to_version": {
                    "type": "integer",
                    "description": "End version (default: latest)",
                    "minimum": 1,
                },
                "from_version": {
                    "type": "integer",
                    "description": "Start version, lower than to_version (default: to_version - 1). Not together with from_author.",
                    "minimum": 1,
                },
                "from_author": {
                    "type": "string",
                    "description": "Start at the newest version by this Trac username at or before to_version. An error if that author has none -- never a fall-back to version 1. Not together with from_version.",
                },
                "context": {
                    "type": "integer",
                    "description": "Unchanged lines around each change (default: 3)",
                    "default": 3,
                    "minimum": 0,
                },
                "max_chars": {
                    "type": "integer",
                    "description": f"Maximum length of the returned diff (default: {_DIFF_MAX_CHARS})",
                    "default": _DIFF_MAX_CHARS,
                    "minimum": 1000,
                },
            },
            "required": ["page_name"],
        },
    ),
]


def encode_cursor(offset: int, total: int) -> str:
    """Encode pagination cursor.

    Args:
        offset: Current offset into results
        total: Total number of results

    Returns:
        Base64-encoded cursor string
    """
    cursor_data = {"offset": offset, "total": total}
    cursor_json = json.dumps(cursor_data)
    cursor_bytes = cursor_json.encode("utf-8")
    return base64.b64encode(cursor_bytes).decode("utf-8")


def decode_cursor(cursor: str) -> tuple[int, int]:
    """Decode pagination cursor.

    Args:
        cursor: Base64-encoded cursor string

    Returns:
        Tuple of (offset, total)

    Raises:
        ValueError: If cursor is invalid
    """
    try:
        cursor_bytes = base64.b64decode(cursor.encode("utf-8"))
        cursor_json = cursor_bytes.decode("utf-8")
        cursor_data = json.loads(cursor_json)
        return cursor_data["offset"], cursor_data["total"]
    except (KeyError, json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"Invalid cursor: {e}") from e


async def _handle_get(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_get."""
    page_name = args.get("page_name")
    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )

    format_error = reject_removed_conversion_args(args)
    if format_error is not None:
        return format_error

    version = args.get("version")

    # Get page content and info in parallel (bounded by semaphore)
    content, info = await asyncio.gather(
        run_sync_limited(client.get_wiki_page, page_name, version),
        run_sync_limited(client.get_wiki_page_info, page_name, version),
    )

    # Returned as stored, so a read-edit-write round trip is byte-exact
    # (ticket #69).
    content_output = content

    # Extract metadata
    page_version = info.get("version", 1)
    author = info.get("author", "unknown")
    modified = info.get("lastModified", "")
    modified_str = format_timestamp(modified)

    # Format response with metadata header
    response_lines = [
        f"# {page_name}",
        f"Version: {page_version} | Author: {author} | Modified: {modified_str}",
        "----",
        "",
        content_output,
    ]

    # Build structured JSON
    wiki_json = {
        "name": page_name,
        "content": content_output,
        "version": page_version,
        "author": author,
        "lastModified": modified_str,
    }

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent=wiki_json,
    )


async def _handle_search(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_search."""
    query = args.get("query")
    if not query:
        return build_error_response(
            "validation_error",
            "query is required",
            "Provide query parameter.",
        )

    prefix = args.get("prefix")
    limit = args.get("limit", 10)
    cursor = args.get("cursor")

    format_error = reject_removed_conversion_args(args)
    if format_error is not None:
        return format_error

    # Ensure limit is within bounds
    limit = min(max(1, limit), 50)

    # Decode cursor or start at offset 0
    if cursor:
        try:
            offset, total = decode_cursor(cursor)
        except ValueError as e:
            return build_error_response(
                "validation_error",
                str(e),
                "Provide valid cursor from previous response.",
            )
    else:
        offset = 0

    # Search wiki pages
    results = await run_sync(client.search_wiki_pages_by_content, query)

    if not results:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text="No wiki pages found matching query.",
                )
            ]
        )

    # Apply prefix filter if provided
    if prefix:
        results = [
            r for r in results if r.get("name", "").startswith(prefix)
        ]

        if not results:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"No wiki pages found matching query with prefix '{prefix}'.",
                    )
                ]
            )

    # Calculate pagination
    total = len(results)
    results_page = results[offset : offset + limit]

    if not results_page:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=f"No more results (offset {offset} exceeds {total} total results).",
                )
            ]
        )

    # Format results with snippets
    formatted = []
    for result in results_page:
        name = result.get("name", "")
        snippet = result.get("snippet", "")
        formatted.append(f"**{name}**\n  ...{snippet}...")

    # Build response
    response_lines = [f"Found {total} wiki pages"]

    if total > limit:
        showing_start = offset + 1
        showing_end = offset + len(results_page)
        response_lines[0] = (
            f"Found {total} wiki pages "
            f"(showing {showing_start}-{showing_end})"
        )

    response_lines[0] += ":"
    response_lines.append("")
    response_lines.extend(formatted)

    # Add next cursor if there are more results
    if offset + limit < total:
        next_cursor = encode_cursor(offset + limit, total)
        response_lines.append("")
        response_lines.append(
            f"Use cursor '{next_cursor}' to get next page."
        )

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ]
    )


async def _handle_recent_changes(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_recent_changes."""
    since_days = args.get("since_days", 30)
    limit = args.get("limit", 20)

    # Ensure parameters are within bounds
    since_days = max(1, since_days)
    limit = min(max(1, limit), 100)

    # Calculate timestamp cutoff
    since_ts = int(time.time()) - int(
        timedelta(days=since_days).total_seconds()
    )

    # Get recent changes
    changes = await run_sync(client.get_recent_wiki_changes, since_ts)

    if not changes:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=f"No wiki pages modified in the last {since_days} days.",
                )
            ],
            structuredContent={"pages": [], "since_days": since_days},
        )

    # Sort by lastModified descending (most recent first)
    changes.sort(key=lambda x: x.get("lastModified", 0), reverse=True)

    # Limit results
    total = len(changes)
    changes = changes[:limit]

    # Format response
    response_lines = [f"Wiki pages modified in last {since_days} days:"]
    if total > limit:
        response_lines[0] += f" (showing {limit} of {total})"
    response_lines.append("")

    pages_json = []
    for change in changes:
        page_name = change.get("name", "Unknown")
        author = change.get("author", "unknown")
        last_modified = change.get("lastModified", 0)
        page_version = change.get("version", 1)

        # Format timestamp
        match last_modified:
            case xmlrpc.client.DateTime() as dt_val:
                dt = datetime.fromtimestamp(
                    time.mktime(dt_val.timetuple())
                )
                modified_str = dt.strftime("%Y-%m-%d %H:%M")
            case int() | float() as ts:
                dt = datetime.fromtimestamp(ts)
                modified_str = dt.strftime("%Y-%m-%d %H:%M")
            case _:
                modified_str = str(last_modified)

        response_lines.append(
            f"- {page_name} (modified: {modified_str} by {author})"
        )

        # Build JSON object
        pages_json.append(
            {
                "name": page_name,
                "author": author,
                "lastModified": modified_str,
                "version": page_version,
            }
        )

    if total > limit:
        response_lines.append("")
        response_lines.append(
            "Use limit parameter to see more (up to 100)."
        )

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent={
            "pages": pages_json,
            "since_days": since_days,
        },
    )


def _revision_entry(version: int, info: dict) -> dict:
    """One revision as wiki_get_history and wiki_diff both report it."""
    return {
        "version": version,
        "author": info.get("author", "unknown"),
        "lastModified": format_timestamp(info.get("lastModified", "")),
        "comment": info.get("comment", "") or "",
    }


def _revision_line(rev: dict) -> str:
    comment_str = rev["comment"] or "(no comment)"
    return f"- v{rev['version']} by {rev['author']} at {rev['lastModified']}: {comment_str}"


async def _handle_get_history(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_get_history.

    Walks the page's version history newest-first, fetching per-version
    metadata via ``client.get_wiki_page_info(page_name, version)``. The
    ``comment`` field (Trac XmlRpcPlugin post trac-hacks #1864) is
    preserved for attribution-marker scanning by auto-pm's edit workflow.
    """
    page_name = args.get("page_name")
    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )

    limit = args.get("limit")

    # Fetch current version to determine the walk range
    try:
        current_info = await run_sync_limited(
            client.get_wiki_page_info, page_name
        )
    except xmlrpc.client.Fault as err:
        return translate_xmlrpc_error(err, "wiki", page_name)

    current_version = current_info.get("version", 1)
    if not isinstance(current_version, int) or current_version < 1:
        current_version = 1

    # Range newest-first: current, current-1, ..., 1
    versions = list(range(current_version, 0, -1))
    if limit is not None and limit > 0:
        versions = versions[:limit]

    revisions: list[dict] = []
    for v in versions:
        try:
            info = await run_sync_limited(
                client.get_wiki_page_info, page_name, v
            )
        except xmlrpc.client.Fault:
            # Skip revisions we can't fetch (permissions, gaps) but
            # keep going — partial history is better than none.
            continue
        # A deleted version answers 0, not a fault (#119).
        if not isinstance(info, dict):
            continue

        revisions.append(_revision_entry(v, info))

    # Format human-readable text output
    if revisions:
        response_lines = [f"# {page_name} history", ""]
        response_lines.extend(map(_revision_line, revisions))
    else:
        response_lines = [
            f"# {page_name} history",
            "",
            "(no revisions found)",
        ]

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text="\n".join(response_lines)
            )
        ],
        structuredContent={
            "page_name": page_name,
            "revisions": revisions,
        },
    )


def _diff_lines(text: str) -> list[str]:
    """Split on ``\\n`` only, keeping the newline.

    Not ``str.splitlines()``: that also splits on ``\\x0c``, ``\\u2028``
    and friends, and would report changes on lines the stored text does
    not have.
    """
    lines = [line + "\n" for line in text.split("\n")]
    lines[-1] = lines[-1][:-1]
    return lines if lines[-1] else lines[:-1]


def _hunk_range(start: int, stop: int) -> str:
    """A unified-diff hunk range, as ``difflib._format_range_unified``."""
    length = stop - start
    if length == 1:
        return str(start + 1)
    return f"{start + 1 if length else start},{length}"


# Past this many inserted+deleted lines, _MyersMatcher stops searching for
# a minimal diff and replaces everything between the common prefix and
# suffix: still a correct diff, and its trace stays O(cap^2). wiki_diff
# reports that as minimal=false, since its counts then over-state the edit.
_MYERS_MAX_EDITS = 2000

# Versions whose metadata wiki_diff fetches concurrently while walking back
# for a start version (from_author, or past a deleted version).
_INFO_WINDOW = 16


class _MyersMatcher(difflib.SequenceMatcher):
    """``SequenceMatcher`` whose matching blocks come from Myers' O(ND)
    diff, the algorithm behind git's default.

    ``difflib.unified_diff`` hard-codes ``autojunk=True``, which on a
    200-line page treats any line making up 1% of it -- the blank line, a
    table rule -- as junk, and merged 200 separate edits into one hunk.
    ``autojunk=False`` fixes that but is quadratic in a repeated line's
    count: 33 s for a 2,200-line page of 200 edits. Myers' cost grows
    with the number of edits instead. Overriding ``get_matching_blocks``
    keeps difflib's own ``get_opcodes``/``get_grouped_opcodes``.
    """

    # False once get_matching_blocks has hit _MYERS_MAX_EDITS.
    minimal = True

    def get_matching_blocks(self):
        a, b = self.a, self.b
        prefix = 0
        while (
            prefix < len(a)
            and prefix < len(b)
            and a[prefix] == b[prefix]
        ):
            prefix += 1
        suffix = 0
        while (
            suffix < len(a) - prefix
            and suffix < len(b) - prefix
            and a[-1 - suffix] == b[-1 - suffix]
        ):
            suffix += 1
        x, y = a[prefix : len(a) - suffix], b[prefix : len(b) - suffix]

        pairs = self._myers_pairs(x, y)
        if pairs is None:
            self.minimal = False
            pairs = []
        blocks = [difflib.Match(0, 0, prefix)] if prefix else []
        for i, j in pairs:
            last = blocks[-1] if blocks else None
            if (
                last is not None
                and last.a + last.size == prefix + i
                and last.b + last.size == prefix + j
            ):
                blocks[-1] = last._replace(size=last.size + 1)
            else:
                blocks.append(difflib.Match(prefix + i, prefix + j, 1))
        if suffix:
            last = blocks[-1] if blocks else None
            start_a, start_b = len(a) - suffix, len(b) - suffix
            if (
                last is not None
                and last.a + last.size == start_a
                and last.b + last.size == start_b
            ):
                blocks[-1] = last._replace(size=last.size + suffix)
            else:
                blocks.append(difflib.Match(start_a, start_b, suffix))
        blocks.append(difflib.Match(len(a), len(b), 0))
        return blocks

    @staticmethod
    def _myers_pairs(
        x: list[str], y: list[str]
    ) -> list[tuple[int, int]] | None:
        """Matched (i, j) line pairs of a shortest edit script, in order,
        or None when it needs more than _MYERS_MAX_EDITS edits."""
        n, m = len(x), len(y)
        if not n or not m:
            return []
        offset = n + m + 1
        v = [0] * (2 * offset + 1)
        # trace[d] holds v[-d..d+1] as it stood before step d.
        trace: list[list[int]] = []
        for d in range(min(n + m, _MYERS_MAX_EDITS) + 1):
            trace.append(v[offset - d : offset + d + 2])
            for k in range(-d, d + 1, 2):
                if k == -d or (
                    k != d and v[offset + k - 1] < v[offset + k + 1]
                ):
                    i = v[offset + k + 1]
                else:
                    i = v[offset + k - 1] + 1
                j = i - k
                while i < n and j < m and x[i] == y[j]:
                    i += 1
                    j += 1
                v[offset + k] = i
                if i >= n and j >= m:
                    return _MyersMatcher._backtrack(trace, n, m)
        return None

    @staticmethod
    def _backtrack(
        trace: list[list[int]], n: int, m: int
    ) -> list[tuple[int, int]]:
        pairs = []
        i, j = n, m
        for d in range(len(trace) - 1, -1, -1):
            row = trace[d]

            def at(k: int, row=row, d=d) -> int:
                return row[k + d]

            k = i - j
            if k == -d or (k != d and at(k - 1) < at(k + 1)):
                prev_k = k + 1
            else:
                prev_k = k - 1
            prev_i = at(prev_k)
            prev_j = prev_i - prev_k
            while i > prev_i and j > prev_j:
                i -= 1
                j -= 1
                pairs.append((i, j))
            if d:
                i, j = prev_i, prev_j
        pairs.reverse()
        return pairs


# A hunk: 0-based start lines in the old and new body, and its lines as
# (tag, text) -- tag " ", "-" or "+", text the full diff line(s), with
# git's "\ No newline at end of file" marker attached to the line it
# belongs to so that no cut can separate them.
_Hunk = tuple[int, int, list[tuple[str, str]]]


def _hunk_text(hunk: _Hunk) -> str:
    a_start, b_start, lines = hunk
    old = sum(1 for tag, _ in lines if tag != "+")
    new = sum(1 for tag, _ in lines if tag != "-")
    header = (
        f"@@ -{_hunk_range(a_start, a_start + old)} "
        f"+{_hunk_range(b_start, b_start + new)} @@\n"
    )
    return header + "".join(text for _, text in lines)


def _unified_hunks(
    from_text: str, to_text: str, n: int
) -> tuple[list[_Hunk], bool]:
    """Unified diff hunks of two bodies, and whether the diff is minimal
    (see _MYERS_MAX_EDITS)."""
    a, b = _diff_lines(from_text), _diff_lines(to_text)

    def line(tag: str, text: str) -> tuple[str, str]:
        if text.endswith("\n"):
            return tag, tag + text
        return tag, f"{tag}{text}\n\\ No newline at end of file\n"

    matcher = _MyersMatcher(None, a, b, autojunk=False)
    hunks = []
    for group in matcher.get_grouped_opcodes(n):
        lines = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines.extend(line(" ", text) for text in a[i1:i2])
                continue
            if tag in ("replace", "delete"):
                lines.extend(line("-", text) for text in a[i1:i2])
            if tag in ("replace", "insert"):
                lines.extend(line("+", text) for text in b[j1:j2])
        hunks.append((group[0][1], group[0][3], lines))
    return hunks, matcher.minimal


def _cap_diff(
    header: str, hunks: list[_Hunk], max_chars: int
) -> tuple[str, int]:
    """Join a diff, cut to ``max_chars`` at a hunk boundary.

    Returns (text, hunks_shown). A first hunk that alone exceeds the cap
    is cut at a line boundary instead, with its ``@@`` header recounted
    for the lines kept, so the text stays a diff ``patch`` accepts. When
    not even its first line fits, no hunk is shown.
    """
    if not hunks:
        return "", 0
    parts = [header]
    size = len(header)
    for shown, hunk in enumerate(hunks):
        text = _hunk_text(hunk)
        if size + len(text) <= max_chars:
            parts.append(text)
            size += len(text)
            continue
        if shown:
            return "".join(parts), shown
        a_start, b_start, lines = hunk
        # The full header is at least as long as any recounted one.
        budget = max_chars - size - len(text.split("\n", 1)[0]) - 1
        kept = []
        for tag, line in lines:
            budget -= len(line)
            if budget < 0:
                break
            kept.append((tag, line))
        if not kept:
            return header, 0
        return header + _hunk_text((a_start, b_start, kept)), 1
    return "".join(parts), len(hunks)


async def _handle_diff(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_diff (ticket #119).

    Two body fetches and one metadata fetch per version in the range, all
    server-side, so only the diff and the change comments reach the
    caller -- not two full bodies.
    """
    page_name = args.get("page_name")
    if not page_name:
        return build_error_response(
            "validation_error",
            "page_name is required",
            "Provide page_name parameter.",
        )

    from_version = args.get("from_version")
    from_author = args.get("from_author")
    to_version = args.get("to_version")
    context = args.get("context", 3)
    max_chars = args.get("max_chars", _DIFF_MAX_CHARS)

    if from_version is not None and from_author is not None:
        return build_error_response(
            "validation_error",
            "from_version and from_author are mutually exclusive",
            "Pass one start: from_version=N or from_author='username'.",
        )
    if from_author is not None and not from_author:
        return build_error_response(
            "validation_error",
            "from_author must be a non-empty Trac username",
            "Pass the username whose last write starts the range, or omit from_author.",
        )

    infos: dict[int, dict] = {}

    async def info_at(version: int) -> dict:
        if version not in infos:
            info = await run_sync_limited(
                client.get_wiki_page_info, page_name, version
            )
            # getPageInfoVersion answers a missing (deleted) version with
            # 0, not a fault (probed 2026-10-25).
            infos[version] = info if isinstance(info, dict) else {}
        return infos[version]

    async def revision_info(version: int) -> dict:
        try:
            return await info_at(version)
        except xmlrpc.client.Fault:
            # Skipped, as wiki_get_history does: a partial range is
            # better than no diff.
            return {}

    async def newest_at_or_below(start: int, wanted) -> int | None:
        """Newest existing version <= start whose info satisfies wanted."""
        for top in range(start, 0, -_INFO_WINDOW):
            window = range(top, max(top - _INFO_WINDOW, 0), -1)
            found = await asyncio.gather(*(info_at(v) for v in window))
            for version, info in zip(window, found, strict=True):
                if info and wanted(info):
                    return version
        return None

    try:
        latest_info = await run_sync_limited(
            client.get_wiki_page_info, page_name
        )
        if not isinstance(latest_info, dict):
            # Same answer for a missing page: 0, where getPage faults.
            return build_error_response(
                "not_found",
                f'Wiki page "{page_name}" does not exist',
                f"Use wiki_search to find pages similar to '{page_name}'.",
            )
        latest = latest_info.get("version", 1)

        if to_version is None:
            to_version = latest
        elif to_version > latest:
            return build_error_response(
                "not_found",
                f'Wiki page "{page_name}" has no version {to_version} (latest is {latest})',
                "Omit to_version for the latest, or list versions with wiki_get_history.",
            )

        if from_author is not None:
            from_version = await newest_at_or_below(
                to_version,
                lambda info: info.get("author") == from_author,
            )
            if from_version is None:
                return build_error_response(
                    "not_found",
                    f'Wiki page "{page_name}" has no version by {from_author!r} at or before version {to_version}',
                    "Check the author with wiki_get_history, or pass from_version instead.",
                )
        elif from_version is None:
            # The version before, skipping any deleted ones; none at all
            # (version 1) diffs against the empty page.
            from_version = await newest_at_or_below(
                to_version - 1, lambda info: True
            )
        elif from_version >= to_version:
            return build_error_response(
                "validation_error",
                f"from_version ({from_version}) must be lower than to_version ({to_version})",
                "Pass a from_version below to_version, or omit it for the version before.",
            )

        to_text = await run_sync_limited(
            client.get_wiki_page, page_name, to_version
        )
        if from_version is None:
            from_text = ""
        elif from_version == to_version:
            from_text = to_text
        else:
            from_text = await run_sync_limited(
                client.get_wiki_page, page_name, from_version
            )
    except xmlrpc.client.Fault as err:
        return translate_xmlrpc_error(err, "wiki", page_name)

    range_versions = range(
        1 if from_version is None else from_version + 1, to_version + 1
    )
    revision_infos = await asyncio.gather(
        *(revision_info(v) for v in range_versions)
    )
    revisions = [
        _revision_entry(v, info)
        for v, info in zip(range_versions, revision_infos, strict=True)
        if info
    ]

    # The Trac GUI may post CRLF where XML-RPC writes LF; diffing raw would
    # mark every line of a GUI-saved version as changed.
    line_endings_normalized = "\r\n" in from_text or "\r\n" in to_text
    from_label = (
        f"{page_name} (empty)"
        if from_version is None
        else f"{page_name}@v{from_version}"
    )
    # CPU-bound, so off the event loop the HTTP daemon shares.
    hunk_list, minimal = await run_sync(
        _unified_hunks,
        from_text.replace("\r\n", "\n"),
        to_text.replace("\r\n", "\n"),
        context,
    )
    header = f"--- {from_label}\n+++ {page_name}@v{to_version}\n"
    tags = [tag for _, _, lines in hunk_list for tag, _ in lines]
    added, removed = tags.count("+"), tags.count("-")
    hunks = len(hunk_list)
    diff_text, hunks_shown = _cap_diff(header, hunk_list, max_chars)
    truncated = bool(hunk_list) and len(diff_text) < len(header) + sum(
        len(_hunk_text(hunk)) for hunk in hunk_list
    )
    identical = from_text == to_text

    from_str = "empty" if from_version is None else f"v{from_version}"
    response_lines = [
        f"# {page_name} diff: {from_str} -> v{to_version}"
    ]
    if identical:
        response_lines.append("Identical: no change in this range.")
    elif not hunk_list:
        response_lines.append(
            "The bodies differ only in line endings (CRLF vs LF)."
        )
    else:
        response_lines.append(
            f"+{added} -{removed} lines in {hunks} hunk(s)"
        )
    if not minimal:
        response_lines.append(
            f"Not minimal: over {_MYERS_MAX_EDITS} edited lines, so one "
            "block replaces everything between the common start and end, "
            "and the counts over-state the change."
        )
    if revisions:
        response_lines.append("Revisions (oldest first):")
        response_lines.extend(map(_revision_line, revisions))
    response_lines.extend(["----", ""])
    text = "\n".join(response_lines) + diff_text
    if truncated:
        text += (
            f"\n[truncated: {hunks_shown} of {hunks} hunk(s) shown; "
            "raise max_chars or narrow the range]\n"
        )

    structured = {
        "page_name": page_name,
        "from_version": from_version,
        "to_version": to_version,
        "latest_version": latest,
        "identical": identical,
        "added": added,
        "removed": removed,
        "hunks": hunks,
        "hunks_shown": hunks_shown,
        "truncated": truncated,
        "minimal": minimal,
        "line_endings_normalized": line_endings_normalized,
        "diff": diff_text,
        "revisions": revisions,
    }
    if from_author is not None:
        structured["from_author"] = from_author

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
    )


# ToolSpec list for registry-based dispatch
WIKI_READ_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=WIKI_READ_TOOLS[0],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_get,
    ),
    ToolSpec(
        tool=WIKI_READ_TOOLS[1],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_search,
    ),
    ToolSpec(
        tool=WIKI_READ_TOOLS[2],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_recent_changes,
    ),
    ToolSpec(
        tool=WIKI_READ_TOOLS[3],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_get_history,
    ),
    ToolSpec(
        tool=WIKI_READ_TOOLS[4],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_diff,
    ),
]
