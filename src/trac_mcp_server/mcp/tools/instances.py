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
