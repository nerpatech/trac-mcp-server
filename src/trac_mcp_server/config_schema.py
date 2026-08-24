"""Unified configuration schema for trac_mcp_server.

Defines Pydantic models for the unified config structure with dedicated
sections for Trac connection and logging. Includes adapter function for
backward compatibility with existing Config dataclass.

Usage:
    from trac_mcp_server.config_schema import (
        UnifiedConfig, build_config, to_legacy_config,
    )

    raw = load_hierarchical_config()
    unified = build_config(raw)
    legacy = to_legacy_config(unified, cli_overrides={"url": "https://..."})
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Section models
# ---------------------------------------------------------------------------


class TracConfig(BaseModel):
    """Trac server connection settings.

    All fields are optional to support zero-config: env vars and CLI args
    can supply them at runtime instead.
    """

    url: str | None = Field(default=None, description="Trac server URL")
    username: str | None = Field(
        default=None, description="Trac username"
    )
    password: str | None = Field(
        default=None, description="Trac password"
    )
    insecure: bool = Field(
        default=False,
        description="Disable SSL verification (development only)",
    )
    debug: bool = Field(default=False, description="Enable debug mode")
    max_parallel_requests: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Maximum concurrent requests to Trac instance (1-100)",
    )
    max_batch_size: int = Field(
        default=500,
        ge=1,
        le=10000,
        description="Maximum items per batch operation (1-10000)",
    )
    rpc_timeout: int = Field(
        default=60,
        ge=5,
        le=300,
        description="Read timeout in seconds for XML-RPC requests (5-300)",
    )

    model_config = {"frozen": True}


class InstanceConfig(BaseModel):
    """A named additional Trac instance.

    Unset ``username``/``password``/``insecure`` inherit from the default
    ``trac`` section at resolution time (see ``instances.InstanceRegistry``).
    """

    url: str = Field(
        description="Trac instance URL (absolute, or a path resolved against the default host)"
    )
    username: str | None = Field(
        default=None,
        description="Trac username (inherits default if unset)",
    )
    password: str | None = Field(
        default=None,
        description="Trac password (inherits default if unset)",
    )
    insecure: bool | None = Field(
        default=None,
        description="Disable SSL verification (inherits default if unset)",
    )

    model_config = {"frozen": True}


class ServerConfig(BaseModel):
    """MCP server process settings: transport, bind address, and auth.

    Distinct from ``TracConfig`` -- these govern how the server process
    itself is exposed to clients, not how it talks to Trac.
    """

    transport: Literal["stdio", "http"] = Field(
        default="stdio", description="MCP transport to serve"
    )
    host: str = Field(
        default="127.0.0.1",
        description="Bind host for the http transport",
    )
    port: int = Field(
        default=8080,
        ge=1,
        le=65535,
        description="Bind port for the http transport (1-65535)",
    )
    path: str = Field(
        default="/mcp",
        description="URL path the MCP endpoint is mounted at",
    )
    auth_token: str | None = Field(
        default=None,
        description=(
            "Static bearer token required for http transport requests. "
            "Mutually exclusive with oidc_rpc_url -- both are read off the "
            "same Authorization header for different purposes."
        ),
    )
    oidc_rpc_url: str | None = Field(
        default=None,
        description=(
            "Full XML-RPC URL of an OIDC-protected Trac endpoint (e.g. "
            "https://trac.example.com/trac-api/login/xmlrpc). When set, "
            "every http-transport request must carry its own "
            "'Authorization: Bearer <token>' header -- the caller's own "
            "OIDC access token, typically attached by an MCP client's own "
            "OAuth flow per server (e.g. LibreChat's oauth: config) -- "
            "forwarded verbatim to this URL. There is no fallback to a "
            "shared service-account identity: a request without a bearer "
            "token is rejected outright."
        ),
    )
    allow_unauthenticated: bool = Field(
        default=False,
        description="Allow binding a non-loopback host without an auth_token",
    )
    allowed_hosts: list[str] = Field(
        default_factory=list,
        description="Extra Host header values to accept, beyond the loopback/host:port defaults",
    )
    allowed_origins: list[str] = Field(
        default_factory=list,
        description="Extra Origin header values to accept for DNS-rebinding protection",
    )

    model_config = {"frozen": True}


class LoggingConfig(BaseModel):
    """Logging configuration.

    Attributes:
        level: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        file: Optional log file path.
    """

    level: str = Field(default="INFO", description="Log level")
    file: str | None = Field(default=None, description="Log file path")

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Top-level unified config
# ---------------------------------------------------------------------------


class UnifiedConfig(BaseModel):
    """Top-level unified configuration.

    Aggregates all config sections. Every section has sensible defaults,
    so ``UnifiedConfig()`` (zero-config) is always valid.
    """

    trac: TracConfig = Field(default_factory=TracConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    instances: dict[str, InstanceConfig] = Field(default_factory=dict)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def build_config(raw_data: dict) -> UnifiedConfig:
    """Construct a ``UnifiedConfig`` from the raw dict returned by
    ``load_hierarchical_config()``.

    Handles missing sections gracefully — anything absent gets defaults.

    Args:
        raw_data: Merged configuration dictionary.

    Returns:
        Validated ``UnifiedConfig`` instance.
    """
    if not raw_data:
        return UnifiedConfig()

    return UnifiedConfig(**raw_data)


# ---------------------------------------------------------------------------
# Adapter: UnifiedConfig -> legacy Config dataclass
# ---------------------------------------------------------------------------


def to_legacy_config(
    unified: UnifiedConfig,
    cli_overrides: dict | None = None,
) -> Config:
    """Convert a ``UnifiedConfig`` into the existing ``Config`` dataclass,
    applying CLI overrides on top.

    The precedence applied here is:
        CLI override > unified config value > None

    CLI overrides dict keys: url, username, password, insecure, debug.

    Args:
        unified: The unified config produced by ``build_config()``.
        cli_overrides: Optional dict of CLI argument values.

    Returns:
        Legacy ``Config`` dataclass instance (NOT validated — caller
        should run ``validate_config()`` separately if needed).
    """
    # Import here to avoid circular imports (config.py imports config_schema)
    from .config import Config

    overrides = cli_overrides or {}

    return Config(
        trac_url=overrides.get("url") or unified.trac.url or "",
        username=overrides.get("username")
        or unified.trac.username
        or "",
        password=overrides.get("password")
        or unified.trac.password
        or "",
        insecure=overrides.get("insecure", False)
        or unified.trac.insecure,
        debug=overrides.get("debug", False) or unified.trac.debug,
        max_parallel_requests=unified.trac.max_parallel_requests,
        max_batch_size=unified.trac.max_batch_size,
        rpc_timeout=unified.trac.rpc_timeout,
    )
