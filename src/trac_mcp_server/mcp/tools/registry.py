"""ToolSpec and ToolRegistry for permission-based tool filtering.

This module provides a centralized registry for MCP tools that supports
filtering based on Trac permissions, enabling operators to restrict which
tools are exposed to AI agents.

Key concepts:
- ToolSpec: Immutable dataclass linking a Tool definition, required permissions,
  and an async handler with standardized signature (client, args) -> CallToolResult.
- ToolRegistry: Filters specs by allowed permissions at construction time,
  then provides list_tools() and call_tool() dispatch with error translation.
- load_permissions_file: Reads a simple text file of Trac permission names.
"""

import logging
import xmlrpc.client
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mcp.types as types

from ...core.client import TracClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Immutable specification for a single MCP tool.

    Attributes:
        tool: The MCP Tool definition (name, description, inputSchema).
        permissions: Trac permissions required to use this tool.
            Empty frozenset means the tool is always available (no permission needed).
        handler: Async handler with signature (client, args) -> CallToolResult.
    """

    tool: types.Tool
    permissions: frozenset[str]
    handler: Callable[
        [TracClient, dict], Awaitable[types.CallToolResult]
    ]


def _is_read_only_tool(spec: ToolSpec) -> bool:
    """True if a spec's own Tool.annotations declare it read-only.

    Every ToolSpec in this codebase sets readOnlyHint explicitly (True for
    view/search/list/get, False for create/update/delete) -- reusing that
    existing, already-accurate signal means read-only mode needs no
    separate classification to maintain. A tool with no annotations, or an
    unset/falsy readOnlyHint, is treated as NOT read-only: the safe default
    when the signal is missing is to exclude, not to accidentally admit a
    write tool.
    """
    annotations = spec.tool.annotations
    return bool(annotations and annotations.readOnlyHint)


class ToolRegistry:
    """Registry of ToolSpecs with optional permission- and read-only-based
    filtering.

    If allowed_permissions is None, all specs pass that filter (backward
    compat). Otherwise, a spec passes it only if:
    - its permissions set is empty (always available), or
    - its permissions are a subset of allowed_permissions.

    If read_only is True, a spec passes only if its own Tool.annotations
    say it's read-only (see _is_read_only_tool) -- independent of the
    permissions filter above, so both can be combined. A tool excluded
    this way isn't just hidden from list_tools(): calling it by name still
    dispatches through call_tool(), which raises the same "unknown tool"
    ValueError as a permissions-filtered-out tool, so there's no separate
    code path to keep this from being bypassed.
    """

    def __init__(
        self,
        specs: list[ToolSpec],
        allowed_permissions: frozenset[str] | None = None,
        read_only: bool = False,
    ):
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            if read_only and not _is_read_only_tool(spec):
                continue
            if (
                allowed_permissions is not None
                and spec.permissions
                and not spec.permissions <= allowed_permissions
            ):
                continue
            self._specs[spec.tool.name] = spec

    def list_tools(self) -> list[types.Tool]:
        """Return list of types.Tool for all registered (permitted) specs."""
        return [spec.tool for spec in self._specs.values()]

    def tool_count(self) -> int:
        """Return number of registered tools."""
        return len(self._specs)

    async def call_tool(
        self,
        name: str,
        arguments: dict | None,
        client: TracClient,
    ) -> types.CallToolResult:
        """Dispatch tool call to registered handler.

        Provides centralized error handling for XML-RPC faults, validation
        errors, and unexpected exceptions, translating them into structured
        CallToolResult responses with corrective actions.

        Args:
            name: Tool name to invoke.
            arguments: Tool arguments (may be None).
            client: TracClient instance.

        Returns:
            CallToolResult from the handler.

        Raises:
            ValueError: If tool name is not registered (unknown or filtered out).
        """
        from .errors import build_error_response, translate_xmlrpc_error

        spec = self._specs.get(name)
        if spec is None:
            raise ValueError(f"Unknown tool: {name}")
        args = arguments or {}
        try:
            return await spec.handler(client, args)
        except xmlrpc.client.Fault as e:
            domain = _domain_from_tool_name(name)
            entity_name = _entity_name_from_args(name, args)
            logger.warning(
                "XML-RPC fault in %s: %s", name, e.faultString
            )
            return translate_xmlrpc_error(e, domain, entity_name)
        except ValueError as e:
            return build_error_response(
                "validation_error",
                str(e),
                "Check parameter values and retry.",
            )
        except Exception as e:
            logger.exception("Unexpected error in tool %s", name)
            return build_error_response(
                "server_error",
                str(e),
                "Contact Trac administrator or retry later.",
            )


def _entity_name_from_args(name: str, args: dict) -> str | None:
    """Extract entity name from tool arguments for contextual error messages.

    Maps tool name prefixes to the relevant argument key so that
    ``translate_xmlrpc_error`` can produce messages like
    "find pages similar to 'MyPage'" instead of generic ones.
    """
    if name.startswith("wiki_"):
        return args.get("page_name")
    if name.startswith("milestone_"):
        return args.get("name")
    return None


def _domain_from_tool_name(name: str) -> str:
    """Derive error domain from tool name for corrective action messages.

    Maps tool names like 'ticket_search', 'wiki_get', 'milestone_list'
    to their error domain ('ticket', 'wiki', 'milestone').
    Falls back to 'ticket' for unrecognized patterns.
    """
    if name.startswith("wiki_"):
        return "wiki"
    if name.startswith("milestone_"):
        return "milestone"
    if name.startswith("ticket_"):
        return "ticket"
    return "ticket"


def with_instance_param(
    specs: list[ToolSpec], names: list[str]
) -> list[ToolSpec]:
    """Return new specs with an optional ``instance`` argument added.

    Applied unconditionally to every tool spec -- hiding the parameter
    behind config would defeat the point of making other Trac projects
    discoverable. Additive and optional: existing ``required`` lists are
    left untouched, so callers that never pass ``instance`` see identical
    behavior to before.

    Args:
        specs: Tool specs to augment.
        names: Configured (declared) instance names, for the description.

    Returns:
        New list of ToolSpec with the same permissions/handler but an
        updated inputSchema.
    """
    if names:
        description = (
            "Optional. Route this call to another configured Trac instance "
            f"instead of the default. Configured instances: {', '.join(names)}. "
            "Any other project on the same Trac host as the default instance "
            "is also reachable ad-hoc via its path, e.g. '/project'."
        )
    else:
        description = (
            "Optional. Route this call to another project on the same Trac "
            "host as the default instance, addressed by path, e.g. "
            "'/project'. No named instances are configured."
        )

    result = []
    for spec in specs:
        schema: dict[str, Any] = dict(
            spec.tool.inputSchema
            or {"type": "object", "properties": {}, "required": []}
        )
        properties: dict[str, Any] = dict(
            schema.get("properties") or {}
        )
        properties["instance"] = {
            "type": "string",
            "description": description,
        }
        schema["properties"] = properties
        new_tool = spec.tool.model_copy(update={"inputSchema": schema})
        result.append(
            ToolSpec(
                tool=new_tool,
                permissions=spec.permissions,
                handler=spec.handler,
            )
        )
    return result


def load_permissions_file(path: str | Path) -> frozenset[str]:
    """Load permissions from a text file.

    Format: one permission per line, ``#`` for comments, blank lines ignored.

    Example file::

        # Read-only permissions
        TICKET_VIEW
        WIKI_VIEW
        MILESTONE_VIEW

    Args:
        path: Path to the permissions file.

    Returns:
        Frozenset of permission strings.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file contains invalid permissions or is empty.
    """
    path = Path(path)
    permissions: set[str] = set()
    for line_num, line in enumerate(path.read_text().splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Validate: Trac permissions are UPPER_SNAKE_CASE
        if (
            not stripped.replace("_", "").isalpha()
            or not stripped.isupper()
        ):
            raise ValueError(
                f"Invalid permission '{stripped}' at line {line_num} in {path}. "
                "Expected UPPER_SNAKE_CASE (e.g., TICKET_VIEW)."
            )
        permissions.add(stripped)
    if not permissions:
        raise ValueError(
            f"No permissions found in {path}. File must contain at least one permission."
        )
    return frozenset(permissions)
