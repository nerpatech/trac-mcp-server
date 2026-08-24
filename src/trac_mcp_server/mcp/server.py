"""MCP Server for Trac integration.

This module implements the Model Context Protocol server that enables
AI agents to interact with Trac via standardized tools and resources.

Transport: stdio (default, for Claude Desktop/Code integration) or
streamable HTTP (``--transport http``, for shared/remote deployments).
Protocol: JSON-RPC 2.0 over MCP

Note: Resources capability enables wiki page access via MCP resource protocol.
"""

import argparse
import asyncio
import logging
import sys
from urllib.parse import parse_qs

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from pydantic_core import Url

from .. import __version__
from ..config_bootstrap import bootstrap_server_config
from ..core.async_utils import run_sync
from ..core.client import TracClient
from ..instances import (
    InstanceRegistry,
    UnknownInstanceError,
    load_declared_instances,
)
from ..logger import setup_logging
from ..version import check_version_consistency
from .http_app import run_http
from .lifespan import server_lifespan
from .oidc import OidcClientCache, extract_bearer_token
from .resources.wiki import (
    handle_list_wiki_resources,
    handle_read_wiki_resource,
)
from .tools import (
    ALL_SPECS,
    ToolRegistry,
    build_error_response,
    load_permissions_file,
)
from .tools.instances import set_instance_registry
from .tools.registry import ToolSpec, with_instance_param

logger = logging.getLogger(__name__)

# Initialize server instance
server = Server("trac-mcp-server", version=__version__)

# Global instance registry (initialized in lifespan)
_instances: InstanceRegistry | None = None

# Global registry instance (initialized in main)
_registry: ToolRegistry | None = None

# Per-user OIDC client cache (initialized in main() only when the http
# transport is configured with server_config.oidc_rpc_url). None means
# "OIDC per-user mode is not active" -- the normal InstanceRegistry path is
# used unconditionally in that case, same as before this mode existed.
_oidc_cache: OidcClientCache | None = None


# ---------------------------------------------------------------------------
# Ping tool (always available, no Trac permission required)
# ---------------------------------------------------------------------------


async def _handle_ping(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ping tool -- test Trac connectivity."""
    try:
        version = await run_sync(client.validate_connection)
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=f"Trac MCP server connected successfully. API version: {version}",
                )
            ]
        )
    except Exception as e:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=f"Trac connection failed: {e}. Check TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD.",
                )
            ],
            isError=True,
        )


PING_SPEC = ToolSpec(
    tool=types.Tool(
        name="ping",
        description="Test Trac MCP server connectivity and return API version",
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    permissions=frozenset(),
    handler=_handle_ping,
)


# ---------------------------------------------------------------------------
# Global accessors (same pattern as _trac_client)
# ---------------------------------------------------------------------------


def get_instances() -> InstanceRegistry:
    """Get the global InstanceRegistry instance.

    Returns:
        InstanceRegistry instance

    Raises:
        RuntimeError: If the registry is not initialized
    """
    if _instances is None:
        raise RuntimeError(
            "InstanceRegistry not initialized. Server lifespan not started."
        )
    return _instances


def set_instances(instances: InstanceRegistry | None) -> None:
    """Set the global InstanceRegistry instance.

    Args:
        instances: InstanceRegistry instance to set, or None to clear
    """
    global _instances
    _instances = instances


def get_registry() -> ToolRegistry:
    """Get the global ToolRegistry instance.

    Returns:
        ToolRegistry instance

    Raises:
        RuntimeError: If registry is not initialized
    """
    if _registry is None:
        raise RuntimeError("ToolRegistry not initialized.")
    return _registry


def set_registry(registry: ToolRegistry | None) -> None:
    """Set the global ToolRegistry instance.

    Args:
        registry: ToolRegistry instance to set, or None to clear
    """
    global _registry
    _registry = registry


def set_oidc_cache(cache: OidcClientCache | None) -> None:
    """Set the global OIDC per-user client cache.

    Args:
        cache: OidcClientCache to set, or None to disable per-user OIDC
            auth (the default -- see module docstring in mcp/oidc.py).
    """
    global _oidc_cache
    _oidc_cache = cache


async def _resolve_client(instance: str | None) -> TracClient:
    """Resolve the TracClient for the current tool/resource call.

    When OIDC per-user auth is active (``_oidc_cache`` set), this is the
    *only* path a call can use to reach Trac: it reads the caller's own
    token from the current request and never touches the InstanceRegistry
    or any shared identity. Otherwise, behavior is unchanged from before
    this mode existed: resolve via the InstanceRegistry.

    Raises:
        ValueError: If ``instance`` is used together with OIDC per-user
            auth (unsupported -- see module docstring in mcp/oidc.py), or
            if the caller's Authorization header is missing/malformed.
            Both are translated into a normal tool-error response by
            existing callers, exactly like an UnknownInstanceError from
            the InstanceRegistry path.
    """
    if _oidc_cache is not None:
        if instance not in (None, "default"):
            raise ValueError(
                "The 'instance' argument is not supported when OIDC "
                "per-user authentication is configured -- this deployment "
                "serves only its single configured Trac endpoint."
            )
        try:
            request = server.request_context.request
        except LookupError:
            request = None
        auth_header = (
            request.headers.get("authorization") if request else None
        )
        return _oidc_cache.get_client(extract_bearer_token(auth_header))

    return get_instances().get_client(instance)


# ---------------------------------------------------------------------------
# MCP protocol handlers
# ---------------------------------------------------------------------------


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List available Trac tools.

    Returns all registered (and permitted) tools from the ToolRegistry.
    """
    return get_registry().list_tools()


@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    """List available Trac resources.

    Returns wiki resources for reading wiki pages via URI templates.
    """
    return await handle_list_wiki_resources()


@server.read_resource()  # type: ignore[arg-type]  # MCP Url type mismatch
async def handle_read_resource(uri: Url) -> str:
    """Read a Trac resource by URI.

    Supports:
    - trac://wiki/{page_name} - Read wiki page (Markdown by default)
    - trac://wiki/{page_name}?format=tracwiki - Read raw TracWiki
    - trac://wiki/{page_name}?version=N - Read historical version
    - trac://wiki/{page_name}?instance=/project - Read from another instance
    - trac://wiki/_index - List all wiki pages with tree structure

    Args:
        uri: Resource URI (e.g., trac://wiki/WikiStart)

    Returns:
        Resource content as formatted text

    Raises:
        ValueError: If URI scheme or path is not recognized
    """
    # Validate URI scheme
    if uri.scheme != "trac":
        raise ValueError(f"Unsupported URI scheme: {uri.scheme}")

    # Route by resource type (host portion of URI)
    if uri.host == "wiki":
        instance = _instance_from_query(uri.query)
        try:
            client = await _resolve_client(instance)
        except ValueError as e:
            error_type = (
                "unknown_instance"
                if isinstance(e, UnknownInstanceError)
                else "validation_error"
            )
            return f"Error ({error_type}): {e}\n\nAction: Call list_instances to see what is reachable."
        return await handle_read_wiki_resource(uri, client)

    raise ValueError(f"Unknown resource type: {uri.host}")


def _instance_from_query(query: str | None) -> str | None:
    """Extract the ``instance`` query param from a resource URI, if present."""
    params = parse_qs(query or "")
    values = params.get("instance")
    return values[0] if values else None


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> types.CallToolResult:
    """Handle tool execution via ToolRegistry dispatch.

    Pops the optional ``instance`` argument before dispatch so no handler
    ever sees an unexpected key, and resolves it against the InstanceRegistry
    to pick which Trac project's client to use.

    Args:
        name: The name of the tool to execute.
        arguments: Tool arguments (optional).

    Returns:
        CallToolResult with tool output content and optional isError flag.
    """
    args = dict(arguments or {})
    instance = args.pop("instance", None)
    try:
        client = await _resolve_client(instance)
    except ValueError as e:
        error_type = (
            "unknown_instance"
            if isinstance(e, UnknownInstanceError)
            else "validation_error"
        )
        corrective_action = (
            "Set the 'Authorization: Bearer <token>' header to your own "
            "Trac OIDC access token."
            if _oidc_cache is not None
            else "Call list_instances to see what is reachable."
        )
        return build_error_response(
            error_type,
            str(e),
            corrective_action,
        )

    try:
        return await get_registry().call_tool(name, args, client)
    except ValueError as e:
        # Unknown or filtered-out tool name
        return build_error_response(
            "unknown_tool",
            str(e),
            "Use list_tools to see available tools.",
        )


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


async def main(config_overrides: dict | None = None):
    """Run the MCP server with the configured transport.

    Resolves the server (transport) config, sets up logging appropriately
    for that transport, validates Trac connection via lifespan manager, and
    starts serving -- stdio (default) or streamable HTTP.

    Args:
        config_overrides: Optional dict with config values to override (url,
            username, password, insecure, log_file, transport, host, port,
            path, allow_unauthenticated)
    """
    # Extract log file override if provided
    log_file = (
        config_overrides.get("log_file") if config_overrides else None
    )

    try:
        server_config = bootstrap_server_config(config_overrides)
    except ValueError as e:
        sys.stderr.write(f"ERROR: Server configuration error: {e}\n")
        raise RuntimeError(f"Server configuration error: {e}") from e

    # Setup logging: MCP mode for stdio (file only, never stdout, since
    # stdio_server() uses stdout for JSON-RPC); stderr+file for http.
    # CRITICAL: This must be called BEFORE stdio_server context
    # to prevent any stdout contamination during protocol negotiation
    setup_logging(
        mode="mcp" if server_config.transport == "stdio" else "http",
        log_file=log_file,
    )

    # Version check (non-blocking warning for stale binaries)
    is_consistent, message = check_version_consistency()
    if not is_consistent:
        logger.warning(message)
        sys.stderr.write(f"\u26a0\ufe0f  Warning: {message}\n")
    else:
        logger.info(message)

    # Build ToolRegistry with optional permission filtering
    permissions_file = (
        config_overrides.get("permissions_file")
        if config_overrides
        else None
    )
    allowed_permissions = None
    if permissions_file:
        allowed_permissions = load_permissions_file(permissions_file)
        logger.info(
            "Loaded %d permissions from %s",
            len(allowed_permissions),
            permissions_file,
        )

    # Declared-instance names are needed for the `instance` schema
    # description; this is a cheap, side-effect-free re-parse of the same
    # YAML the lifespan will load momentarily (no live connection required).
    declared_names = sorted(load_declared_instances())
    all_specs = with_instance_param(
        [PING_SPEC] + ALL_SPECS, declared_names
    )
    registry = ToolRegistry(
        all_specs,
        allowed_permissions,
        read_only=server_config.read_only,
    )
    logger.info(
        "Registered %d tools (of %d total)",
        registry.tool_count(),
        len(all_specs),
    )

    if permissions_file:
        print(
            f"Permissions file: {permissions_file} "
            f"({registry.tool_count()} of {len(all_specs)} tools enabled)",
            file=sys.stderr,
        )

    if server_config.read_only:
        logger.info(
            "Read-only mode active: %d of %d tools exposed.",
            registry.tool_count(),
            len(all_specs),
        )
        print(
            f"Read-only mode: {registry.tool_count()} of "
            f"{len(all_specs)} tools enabled",
            file=sys.stderr,
        )

    set_registry(registry)

    # Use lifespan manager for startup validation with config overrides.
    # NOTE: We call set_instances() directly here rather than in the lifespan
    # to avoid the Python __main__ module duplication bug. When this file
    # is run via `python -m trac_mcp_server.mcp.server`, the module is
    # loaded as __main__, but `from . import server` in lifespan.py would
    # re-import it as trac_mcp_server.mcp.server (a separate copy),
    # causing set_instances() to modify the wrong module's _instances.
    async with server_lifespan(
        config_overrides=config_overrides
    ) as ctx:
        set_instances(ctx["instances"])
        set_instance_registry(ctx["instances"])
        oidc_rpc_url = (
            server_config.oidc_rpc_url
            if server_config.transport == "http"
            else None
        )
        if oidc_rpc_url:
            logger.info(
                "OIDC per-user auth active: requests must carry their own "
                "'Authorization: Bearer <token>'; no shared identity is "
                "used for tool calls."
            )
            set_oidc_cache(OidcClientCache(ctx["config"], oidc_rpc_url))
        try:
            if server_config.transport == "stdio":
                await _run_stdio()
            else:
                await run_http(server, server_config)
        finally:
            set_instances(None)
            set_instance_registry(None)
            set_registry(None)
            set_oidc_cache(None)


async def _run_stdio() -> None:
    """Serve the MCP protocol over stdio JSON-RPC."""
    async with mcp.server.stdio.stdio_server() as (
        read_stream,
        write_stream,
    ):
        init_options = InitializationOptions(
            server_name="trac-mcp-server",
            server_version=__version__,
            capabilities=server.get_capabilities(
                notification_options=NotificationOptions(),
                experimental_capabilities={},
            ),
        )
        await server.run(read_stream, write_stream, init_options)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``trac-mcp-server`` CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Trac MCP Server - Model Context Protocol server for Trac integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default config (from .env or config.yaml)
  trac-mcp-server

  # Override Trac URL
  trac-mcp-server --url https://trac.example.com

  # Override all connection settings
  trac-mcp-server --url https://trac.example.com --username admin --password secret

  # Use with insecure SSL (development only)
  trac-mcp-server --url http://localhost:8000 --insecure

  # Custom log file location
  trac-mcp-server --log-file /var/log/trac-mcp-server.log

  # Restrict tools by Trac permissions
  trac-mcp-server --permissions-file /etc/trac-mcp/read-only.permissions

  # Serve over streamable HTTP instead of stdio
  TRAC_MCP_AUTH_TOKEN=devtoken trac-mcp-server --transport http --port 8080

Note: The default stdio transport uses stdout for JSON-RPC communication with
MCP clients -- all user-facing messages are written to stderr, and stdin/stdout
must not be piped manually. This does not apply to --transport http.
        """,
    )

    parser.add_argument(
        "--url",
        help="Override Trac instance URL (takes precedence over TRAC_URL env var and config files)",
    )
    parser.add_argument(
        "--username",
        help="Override Trac username (takes precedence over TRAC_USERNAME env var and config files)",
    )
    parser.add_argument(
        "--password",
        help="Override Trac password (takes precedence over TRAC_PASSWORD env var and config files)"
        " (visible in process list -- prefer TRAC_PASSWORD env var for security)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip SSL certificate verification (use only for development)",
    )
    parser.add_argument(
        "--log-file",
        default="/tmp/trac-mcp-server.log",
        help="Log file path (default: /tmp/trac-mcp-server.log)",
    )
    parser.add_argument(
        "--permissions-file",
        help="Path to permissions file restricting available tools. "
        "Format: one Trac permission per line (e.g., TICKET_VIEW), # for comments. "
        "If not specified, all tools are available.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        help="MCP transport to serve (default: stdio; also settable via "
        "TRAC_MCP_TRANSPORT env var or config.yaml server.transport)",
    )
    parser.add_argument(
        "--host",
        help="Bind host for --transport http (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Bind port for --transport http (default: 8080)",
    )
    parser.add_argument(
        "--path",
        help="URL path the MCP endpoint is mounted at for --transport http "
        "(default: /mcp)",
    )
    parser.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="Allow --transport http to bind a non-loopback host without an "
        "auth token. Dangerous -- exposes Trac credentials to the network. "
        "Prefer setting TRAC_MCP_AUTH_TOKEN instead.",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Expose only read-only tools (view/search/list/get), "
        "regardless of transport. Also settable via TRAC_MCP_READ_ONLY "
        "env var or config.yaml server.read_only. Combinable with "
        "--permissions-file.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"trac-mcp-server version {__version__}",
    )

    return parser


def run() -> None:
    """Entry point that handles errors gracefully and parses CLI arguments."""
    args = build_parser().parse_args()

    # Build config overrides dict from CLI args
    config_overrides = {}
    if args.url:
        config_overrides["url"] = args.url
    if args.username:
        config_overrides["username"] = args.username
    if args.password:
        config_overrides["password"] = args.password
    if args.insecure:
        config_overrides["insecure"] = True
    if args.log_file:
        config_overrides["log_file"] = args.log_file
    if args.permissions_file:
        config_overrides["permissions_file"] = args.permissions_file
    if args.transport:
        config_overrides["transport"] = args.transport
    if args.host:
        config_overrides["host"] = args.host
    if args.port is not None:
        config_overrides["port"] = args.port
    if args.path:
        config_overrides["path"] = args.path
    if args.allow_unauthenticated:
        config_overrides["allow_unauthenticated"] = True
    if args.read_only:
        config_overrides["read_only"] = True

    # Log config overrides to stderr (before stdio transport starts)
    if config_overrides:
        override_keys = [
            k for k in config_overrides.keys() if k != "password"
        ]
        print(
            f"Config overrides from CLI: {', '.join(override_keys)}",
            file=sys.stderr,
        )

    try:
        asyncio.run(
            main(
                config_overrides=config_overrides
                if config_overrides
                else None
            )
        )
    except RuntimeError:
        # Error already printed to stderr by lifespan manager
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    run()
