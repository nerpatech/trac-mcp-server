"""Wiki resource handlers for MCP server.

This module implements MCP resource handlers for exposing Trac wiki pages
as read-only resources via URI templates. Agents can discover pages via
the index resource and read individual pages by version. Content is
returned as stored -- TracWiki, byte-for-byte (ticket #69).
"""

import asyncio
import difflib
import xmlrpc.client
from typing import Any
from urllib.parse import unquote

import mcp.types as types
from pydantic_core import Url

from ...core.async_utils import run_sync, run_sync_limited
from ...core.client import TracClient
from ..tools.errors import format_timestamp

# Resource definitions for list_resources()
WIKI_RESOURCES = [
    types.Resource(
        uri="trac://wiki/{page_name}",  # type: ignore[arg-type]  # MCP AnyUrl/Url type mismatch
        name="Wiki Page",
        description=(
            "Read a wiki page by name, as stored: TracWiki, "
            "byte-for-byte. "
            "Query param: version=N (specific version). "
            "Example: trac://wiki/WikiStart?version=5"
        ),
        mimeType="text/plain",
    ),
    types.Resource(
        uri="trac://wiki/_index",  # type: ignore[arg-type]  # MCP AnyUrl/Url type mismatch
        name="Wiki Page Index",
        description=(
            "List all wiki pages in a hierarchical tree structure. "
            "Use this to discover available pages before reading them."
        ),
        mimeType="text/plain",
    ),
]


async def handle_list_wiki_resources() -> list[types.Resource]:
    """List available wiki resources.

    Returns:
        List of Resource objects describing wiki page templates and index.
    """
    return WIKI_RESOURCES


async def handle_read_wiki_resource(
    uri: Url, client: TracClient
) -> str:
    """Read a wiki resource by URI.

    Args:
        uri: Resource URI (e.g., trac://wiki/WikiStart, trac://wiki/_index)
        client: Pre-configured TracClient instance

    Returns:
        Formatted page content with metadata, or error message.

    URI Formats:
        - trac://wiki/_index - List all pages as hierarchical tree
        - trac://wiki/{page_name} - Read specific page
        - trac://wiki/{page_name}?version=5 - Specific version

    Content is returned as stored. ``?format=markdown`` was removed
    with the rest of the Markdown read path (ticket #69) and is
    reported as an error rather than ignored, so a stale caller is
    told instead of quietly handed something else.
    """
    # Parse URI path - remove trac://wiki/ prefix
    path = str(uri)
    if path.startswith("trac://wiki/"):
        path = path[len("trac://wiki/") :]

    # Split path and query string
    if "?" in path:
        path, query_string = path.split("?", 1)
    else:
        query_string = ""

    # URL-decode the page name
    page_name = unquote(path)

    # Parse query parameters
    params = _parse_query_params(query_string)
    output_format = params.get("format")
    if output_format is not None and output_format != "tracwiki":
        return (
            f"Error (validation_error): format={output_format!r} is no "
            "longer supported -- wiki resources return the stored "
            "TracWiki byte-for-byte.\n\nHint: drop the format query "
            "parameter. To convert the result to Markdown, use the "
            "trac-convert binary."
        )
    version_str = params.get("version")
    version = int(version_str) if version_str else None

    # Handle special _index path
    if page_name == "_index":
        return await _build_page_index(client)

    # Read page
    try:
        # Fetch content and metadata in parallel (bounded by semaphore)
        content, info = await asyncio.gather(
            run_sync_limited(client.get_wiki_page, page_name, version),
            run_sync_limited(
                client.get_wiki_page_info, page_name, version
            ),
        )

        # Format response with metadata
        return _format_page_response(page_name, content, info)

    except xmlrpc.client.Fault as e:
        fault_str = e.faultString.lower()

        if "not found" in fault_str or e.faultCode == 1:
            return await _build_not_found_error(client, page_name)
        elif "version" in fault_str:
            return f"Error (invalid_version): Version {version} not found for page '{page_name}'.\n\nHint: Use trac://wiki/{page_name} to see the latest version."
        else:
            return f"Error (server_error): {e.faultString}"

    except Exception as e:
        return f"Error (server_error): {str(e)}"


def _parse_query_params(query_string: str) -> dict[str, str]:
    """Parse query string into parameter dict.

    Args:
        query_string: URL query string (without leading ?)

    Returns:
        Dict of parameter name -> value
    """
    if not query_string:
        return {}

    params = {}
    for part in query_string.split("&"):
        if "=" in part:
            key, value = part.split("=", 1)
            params[unquote(key)] = unquote(value)
    return params


async def _build_page_index(client: TracClient) -> str:
    """Build hierarchical page index.

    Args:
        client: TracClient instance

    Returns:
        Formatted tree structure of all wiki pages
    """
    pages = await run_sync(client.list_wiki_pages)
    tree = _format_page_tree(pages)
    return f"# Wiki Pages\n\n{tree}"


def _format_page_tree(pages: list[str]) -> str:
    """Format page list as hierarchical tree with box-drawing characters.

    Args:
        pages: List of page names (e.g., ["WikiStart", "Dev/Setup", "Dev/Testing"])

    Returns:
        Tree structure string with box-drawing connectors

    Example output:
        Dev
        |-- Setup
        `-- Testing
        WikiStart
    """
    if not pages:
        return ""

    # Build nested dict structure
    tree: dict[str, Any] = {}
    for page in sorted(pages):
        parts = page.split("/")
        current = tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]

    # Format tree recursively
    lines: list[str] = []
    _format_tree_node(tree, "", lines, is_last=True, is_root=True)
    return "\n".join(lines)


def _format_tree_node(
    node: dict[str, Any],
    prefix: str,
    lines: list[str],
    is_last: bool,
    is_root: bool = False,
) -> None:
    """Recursively format tree node with box-drawing characters.

    Args:
        node: Dict representing tree node (children as nested dicts)
        prefix: Current line prefix for indentation
        lines: Output lines list (mutated)
        is_last: Whether this is the last sibling
        is_root: Whether this is the root node
    """
    keys = sorted(node.keys())

    for i, key in enumerate(keys):
        is_last_child = i == len(keys) - 1

        if is_root:
            # Root level - no connector
            lines.append(key)
            child_prefix = ""
        else:
            # Nested level - add connector
            connector = "`-- " if is_last_child else "|-- "
            lines.append(f"{prefix}{connector}{key}")
            # Continuation prefix for children
            child_prefix = prefix + (
                "    " if is_last_child else "|   "
            )

        # Recurse into children
        children = node[key]
        if children:
            _format_tree_node(
                children, child_prefix, lines, is_last_child
            )


def _format_page_response(
    page_name: str, content: str, info: dict[str, Any]
) -> str:
    """Format page response with metadata header.

    Args:
        page_name: Wiki page name
        content: Page content (already converted to Markdown if needed)
        info: Page metadata dict with author, version, lastModified

    Returns:
        Formatted response string
    """
    author = info.get("author", "unknown")
    version = info.get("version", "unknown")
    last_modified = info.get("lastModified")

    # Format timestamp
    modified_str = (
        format_timestamp(last_modified) if last_modified else "unknown"
    )

    return f"""# {page_name}

**Author:** {author}
**Version:** {version}
**Last Modified:** {modified_str}

---

{content}"""


async def _build_not_found_error(
    client: TracClient, page_name: str
) -> str:
    """Build not found error with similar page suggestions.

    Args:
        client: TracClient instance for fetching page list
        page_name: Requested page name that wasn't found

    Returns:
        Formatted error string with suggestions
    """
    try:
        all_pages = await run_sync(client.list_wiki_pages)
        similar = difflib.get_close_matches(
            page_name, all_pages, n=5, cutoff=0.6
        )

        if similar:
            suggestions = ", ".join(similar)
            return f"Error (not_found): Page '{page_name}' not found.\n\nSimilar pages: {suggestions}"
        else:
            return f"Error (not_found): Page '{page_name}' not found.\n\nUse trac://wiki/_index to see available pages."

    except Exception:
        # If we can't get suggestions, return basic error
        return f"Error (not_found): Page '{page_name}' not found."
