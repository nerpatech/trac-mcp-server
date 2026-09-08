"""``list_instances`` MCP tool -- discover reachable Trac instances.

Surfaces both the configured (named + default) instances and, optionally,
other projects visible on the same Trac host via ``scrape_project_index``.
"""

import logging
from urllib.parse import urlparse

import mcp.types as types

from ...core.client import TracClient
from ...detection.web_scraper import scrape_project_index
from ...instances import InstanceRegistry
from .registry import ToolSpec

logger = logging.getLogger(__name__)

INSTANCE_TOOLS = [
    types.Tool(
        name="list_instances",
        description=(
            "List Trac instances reachable from this server: named instances "
            "from configuration, and (when discover=true) other projects "
            "visible on the same Trac host's project index. Use the returned "
            "path (e.g. '/project') as the 'instance' argument on any other "
            "tool to target that project."
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
                "discover": {
                    "type": "boolean",
                    "description": (
                        "Scrape the Trac host's project index for other "
                        "reachable projects (default: true)."
                    ),
                    "default": True,
                },
            },
            "required": [],
        },
    )
]

# Module-level accessor mirroring server.py's get_client()/set_client()
# pattern. A direct `tools -> server` import would be circular, since
# server.py imports INSTANCE_SPECS from this module.
_registry_ref: InstanceRegistry | None = None


def set_instance_registry(registry: InstanceRegistry | None) -> None:
    """Install the InstanceRegistry the list_instances handler reads from."""
    global _registry_ref
    _registry_ref = registry


def get_instance_registry() -> InstanceRegistry | None:
    """The installed InstanceRegistry, or ``None`` before startup wires
    one in. Ticket #86's other tool modules read it through here rather
    than through ``server.py`` for the same reason ``_registry_ref``
    exists at all: a direct ``tools -> server`` import would be
    circular, since ``server.py`` imports tool specs from this package."""
    return _registry_ref


def local_intertrac_bases() -> frozenset[str]:
    """InterTrac dispatcher bases -- one per configured instance -- that
    are ours, for ``preview.checks``'s ``missing_intertrac_realm`` check
    (ticket #86). One helper rather than three call sites each reading
    the registry and stripping trailing slashes their own way -- the
    project's own "not a check re-implemented per handler" rule
    (``write_gate.py``'s module docstring) applies just as much to the
    piece that FEEDS a check as to the check itself.

    Empty before the registry is wired in (mirrors ``describe()``'s own
    None guard in ``_handle_list_instances``) -- the check simply does
    not fire, which is the status quo this ticket is about, not a new
    failure mode.
    """
    registry = _registry_ref
    if registry is None:
        return frozenset()
    return frozenset(
        entry["url"].rstrip("/") for entry in registry.describe()
    )


def _host_root(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _handle_list_instances(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle list_instances tool -- describe configured + discovered instances."""
    registry = _registry_ref
    if registry is None:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text="Error (server_error): Instance registry not initialized.",
                )
            ],
            isError=True,
        )

    discover = args.get("discover", True)
    configured = registry.describe()
    default_config = registry.resolve(None)

    structured: dict = {
        "configured": configured,
        "default": default_config.trac_url,
    }

    lines = [
        f"Default instance: {default_config.trac_url}",
        "",
        "Configured:",
    ]
    for entry in configured:
        marker = " (default)" if entry["is_default"] else ""
        lines.append(f"  {entry['name']}: {entry['url']}{marker}")

    if discover:
        host_root = _host_root(default_config.trac_url)
        discovered = scrape_project_index(
            host_root,
            (default_config.username, default_config.password),
        )
        if discovered:
            configured_urls = {
                entry["url"].rstrip("/") for entry in configured
            }
            for entry in discovered:
                entry["configured"] = (
                    entry["url"].rstrip("/") in configured_urls
                )
            structured["discovered"] = discovered

            lines.append("")
            lines.append("Discovered on host:")
            for entry in discovered:
                flag = " [configured]" if entry["configured"] else ""
                lines.append(
                    f"  {entry['path']}: {entry['title']}{flag}"
                )

    return types.CallToolResult(
        content=[types.TextContent(type="text", text="\n".join(lines))],
        structuredContent=structured,
    )


INSTANCE_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=INSTANCE_TOOLS[0],
        permissions=frozenset(),
        handler=_handle_list_instances,
    ),
]
