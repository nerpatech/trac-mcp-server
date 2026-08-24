"""Shared config-bootstrap helper. Returns (Config, sources) with no side
effects on stderr/stdout. Callers own their own logging and error translation.

This module contains the single public function ``bootstrap_config()`` which
encapsulates the six-step configuration-loading sequence previously inlined in
``mcp/lifespan.py``. Both the MCP server lifespan and the ``trac-convert`` CLI
call this helper to resolve the layered config precedence without duplicating
logic.
"""

import os
from typing import Any

from dotenv import load_dotenv

from .config import Config, load_config, validate_server_config
from .config_loader import (
    discover_config_files,
    load_hierarchical_config,
)
from .config_schema import ServerConfig, build_config


def bootstrap_config(
    cli_overrides: dict[str, Any] | None = None,
) -> tuple[Config, list[str]]:
    """Load configuration with CLI > env > YAML > defaults precedence.

    Performs the six-step bootstrap sequence:

    1. ``load_dotenv()`` — loads ``.env`` file so env vars are available
       before any reads (idempotent; safe to call multiple times).
    2. ``discover_config_files()`` — finds YAML config files in precedence
       order; if any exist, loads and merges them via
       ``load_hierarchical_config()`` / ``build_config()``, then extracts
       non-None trac-section values as ``yaml_fallbacks``.
    3. ``load_config(...)`` — resolves final values with precedence:
       CLI > env var / .env > YAML fallbacks > built-in defaults.
    4. Builds a ``sources`` list describing which layers contributed.

    Accepted ``cli_overrides`` keys:
        - ``url`` (str | None): Trac URL override.
        - ``username`` (str | None): Trac username override.
        - ``password`` (str | None): Trac password override.
        - ``insecure`` (bool): Skip SSL verification (default: False).
        - ``debug`` (bool): Enable debug logging (default: False).

    Args:
        cli_overrides: Optional dict with CLI-sourced values. Keys missing
            from the dict fall through to env vars / YAML / defaults.
            Pass ``None`` or ``{}`` when no CLI overrides are present;
            both are treated identically (no "CLI arguments" source label).

    Returns:
        A two-tuple ``(config, sources)`` where:
        - ``config``: A validated :class:`~trac_mcp_server.config.Config`
          instance ready for use with :class:`TracClient`.
        - ``sources``: A list of human-readable source labels in the order
          they contributed (e.g. ``["config file: /path/to/config.yaml",
          "CLI arguments", "environment variables"]``).

    Raises:
        ValueError: If required fields (``url``, ``username``, ``password``)
            are missing after checking all sources. Callers must translate:
            ``RuntimeError`` for the MCP server lifespan, ``EXIT_TRAC`` (exit
            code 4) for the CLI. Do NOT catch this error here.
    """
    # Step 1: Load .env early so ${VAR} interpolation in YAML can use them
    load_dotenv()

    # Step 2: Load YAML config if present, extract trac section as fallbacks
    yaml_fallbacks: dict[str, Any] | None = None
    sources: list[str] = []
    config_files = discover_config_files()

    if config_files:
        raw = load_hierarchical_config()
        unified = build_config(raw)
        yaml_fallbacks = {
            k: v
            for k, v in unified.trac.model_dump().items()
            if v is not None
        }
        sources.append(f"config file: {config_files[0]}")

    # Step 3: Single call to load_config with all sources merged
    overrides = cli_overrides or {}
    config = load_config(
        url=overrides.get("url"),
        username=overrides.get("username"),
        password=overrides.get("password"),
        insecure=overrides.get("insecure", False),
        debug=overrides.get("debug", False),
        yaml_fallbacks=yaml_fallbacks,
    )

    # Step 4-6: Build source labels
    if overrides:
        sources.append("CLI arguments")
    sources.append("environment variables")

    return config, sources


def bootstrap_server_config(
    cli_overrides: dict[str, Any] | None = None,
) -> ServerConfig:
    """Load MCP server (transport) configuration.

    Mirrors ``bootstrap_config()``'s bootstrap sequence -- ``.env``, then
    the YAML ``server:`` section as fallbacks -- but resolves the process
    settings that govern *how the server is exposed* (transport, bind
    address, auth) rather than how it talks to Trac. Precedence per field:
    CLI > env var (``TRAC_MCP_*``) > YAML ``server:`` section > default.

    Accepted ``cli_overrides`` keys: ``transport``, ``host``, ``port``,
    ``path``, ``allow_unauthenticated``. ``auth_token`` is intentionally
    not accepted here -- it must come from ``TRAC_MCP_AUTH_TOKEN`` or the
    YAML ``server:`` section, never a CLI flag, so it never appears in the
    process list.

    ``TRAC_MCP_ALLOWED_HOSTS``/``TRAC_MCP_ALLOWED_ORIGINS`` (comma-separated,
    e.g. ``trac-mcp:8080,trac-mcp:*``) extend the DNS-rebinding-protection
    allow-list beyond loopback and the configured bind address -- needed
    whenever a client reaches this server by a name other than what it's
    bound to, such as a docker-compose service name.

    Args:
        cli_overrides: Optional dict with CLI-sourced values.

    Returns:
        A validated :class:`~trac_mcp_server.config_schema.ServerConfig`.

    Raises:
        ValueError: If a numeric override is out of range, ``transport``
            is neither ``stdio`` nor ``http``, or an unauthenticated http
            transport would bind a non-loopback host.
    """
    load_dotenv()

    yaml_server: dict[str, Any] = {}
    config_files = discover_config_files()
    if config_files:
        raw = load_hierarchical_config()
        unified = build_config(raw)
        yaml_server = {
            k: v
            for k, v in unified.server.model_dump().items()
            if v is not None
        }

    overrides = cli_overrides or {}

    transport = (
        overrides.get("transport")
        or os.getenv("TRAC_MCP_TRANSPORT")
        or yaml_server.get("transport", "stdio")
    )
    if transport not in ("stdio", "http"):
        raise ValueError(
            f"Invalid transport '{transport}': must be 'stdio' or 'http'"
        )

    host = (
        overrides.get("host")
        or os.getenv("TRAC_MCP_HOST")
        or yaml_server.get("host", "127.0.0.1")
    )
    path = (
        overrides.get("path")
        or os.getenv("TRAC_MCP_PATH")
        or yaml_server.get("path", "/mcp")
    )

    port_override = overrides.get("port")
    port_raw = (
        port_override
        if port_override is not None
        else os.getenv("TRAC_MCP_PORT")
    )
    if port_raw is not None:
        try:
            port = int(port_raw)
        except ValueError:
            raise ValueError(
                f"Invalid port '{port_raw}': must be a number between 1 and 65535"
            ) from None
        if not (1 <= port <= 65535):
            raise ValueError(
                f"Invalid port '{port_raw}': must be a number between 1 and 65535"
            )
    else:
        port = int(yaml_server.get("port", 8080))

    auth_token = os.getenv("TRAC_MCP_AUTH_TOKEN") or yaml_server.get(
        "auth_token"
    )

    oidc_rpc_url = os.getenv(
        "TRAC_MCP_OIDC_RPC_URL"
    ) or yaml_server.get("oidc_rpc_url")

    allow_unauthenticated = bool(
        overrides.get("allow_unauthenticated", False)
    ) or bool(yaml_server.get("allow_unauthenticated", False))

    def _parse_csv_env(key: str) -> list[str] | None:
        """Comma-separated env var -> list, or None if unset.

        Blank entries (from a trailing comma or stray whitespace) are
        dropped rather than turning into an empty-string allow-list entry
        that could never match a real Host/Origin header.
        """
        raw = os.getenv(key)
        if raw is None:
            return None
        return [item.strip() for item in raw.split(",") if item.strip()]

    allowed_hosts_env = _parse_csv_env("TRAC_MCP_ALLOWED_HOSTS")
    allowed_hosts = (
        allowed_hosts_env
        if allowed_hosts_env is not None
        else list(yaml_server.get("allowed_hosts", []))
    )

    allowed_origins_env = _parse_csv_env("TRAC_MCP_ALLOWED_ORIGINS")
    allowed_origins = (
        allowed_origins_env
        if allowed_origins_env is not None
        else list(yaml_server.get("allowed_origins", []))
    )

    server_config = ServerConfig(
        transport=transport,
        host=host,
        port=port,
        path=path,
        auth_token=auth_token,
        oidc_rpc_url=oidc_rpc_url,
        allow_unauthenticated=allow_unauthenticated,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )

    validate_server_config(server_config)

    return server_config
