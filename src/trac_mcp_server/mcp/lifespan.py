"""Lifespan management for MCP server startup and shutdown."""

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ..config_bootstrap import bootstrap_config
from ..core.async_utils import init_semaphore, run_sync
from ..core.client import TracClient
from ..instances import InstanceRegistry, load_declared_instances
from ..logger import startup_note

logger = logging.getLogger(__name__)


def _stderr_print(msg: str) -> None:
    """Print message to stderr for user feedback (safe in MCP mode)."""
    print(msg, file=sys.stderr, flush=True)


def _note(
    message: str,
    *args: object,
    level: int = logging.INFO,
    printed: str | list[str] | None = None,
) -> None:
    """Log a startup line; print it on stderr only where the logger does
    not (stdio with a log file), via this module's ``_stderr_print``."""
    startup_note(
        logger,
        message,
        *args,
        level=level,
        printed=printed,
        print_fn=_stderr_print,
    )


@asynccontextmanager
async def server_lifespan(
    config_overrides: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """
    Manage server startup and shutdown lifecycle.

    On startup:
    - Load .env file (so values are available for env var lookups and YAML interpolation)
    - Load YAML config file if present (as fallback values)
    - Merge all sources via load_config(): CLI > env vars > .env > YAML > defaults
    - Create TracClient and validate connection
    - Fail fast if Trac is unreachable

    On shutdown:
    - Log shutdown message
    - Cleanup (minimal for XML-RPC stateless client)

    Args:
        config_overrides: Optional dict with config values from CLI (url, username, password, insecure)

    Yields:
        Dict with 'client' key containing the initialized TracClient

    Raises:
        RuntimeError: If configuration is invalid or Trac connection fails.
    """
    _note(
        "MCP server starting...", printed="Trac MCP Server starting..."
    )

    # Load configuration with unified precedence:
    # CLI args > env vars (.env loaded first) > YAML config > defaults
    try:
        overrides = config_overrides or {}
        config, sources = bootstrap_config(overrides)
        source_desc = ", ".join(sources) if sources else "defaults"
        _note(
            "Configuration loaded from: %s",
            source_desc,
            printed=f"  Configuration loaded from: {source_desc}",
        )
        _note(
            "Trac URL: %s",
            config.trac_url,
            printed=f"  Trac URL: {config.trac_url}",
        )
    except ValueError as e:
        _note(
            "Configuration error: %s. Ensure TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD are set.",
            e,
            level=logging.ERROR,
            printed=[
                f"ERROR: Configuration error: {e}",
                "  Ensure TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD are set.",
            ],
        )
        raise RuntimeError(
            f"Configuration error: {e}. Ensure TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD are set."
        ) from e

    # Validate Trac connection. Skipped for an OIDC-only deployment: there
    # is no shared service-account identity to authenticate a self-test
    # with (see Config.oidc_only) -- connectivity and auth are validated
    # per-request instead, against each caller's own forwarded token.
    client: TracClient | None = None
    if config.oidc_only:
        _note(
            "OIDC-only deployment (TRAC_MCP_OIDC_RPC_URL set, no shared "
            "Trac credentials): skipping startup connectivity self-test.",
            printed=(
                "  OIDC per-user mode: skipping startup connectivity check "
                "(no shared credentials configured)."
            ),
        )
        init_semaphore(config.max_parallel_requests)
        _note(
            "Parallel requests: %d",
            config.max_parallel_requests,
            printed=f"  Parallel requests: {config.max_parallel_requests}",
        )
    else:
        _note(
            "Validating Trac connection...",
            printed="  Validating Trac connection...",
        )
        try:
            client = TracClient(config)
            version = await run_sync(client.validate_connection)
            _note(
                "Successfully connected to Trac API version %s",
                version,
                printed=f"  Connected to Trac API version {version}",
            )
            init_semaphore(config.max_parallel_requests)
            _note(
                "Parallel requests: %d",
                config.max_parallel_requests,
                printed=(
                    f"  Parallel requests: {config.max_parallel_requests}"
                ),
            )
        except Exception as e:
            _note(
                "Failed to connect to Trac: %s. Check TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD.",
                e,
                level=logging.ERROR,
                printed=[
                    "ERROR: Trac connection failed.",
                    f"  {e}",
                    "  Check TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD.",
                ],
            )
            raise RuntimeError(
                f"Trac connection failed: {e}. Check TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD."
            ) from e

    instances = InstanceRegistry(config, load_declared_instances())
    if client is not None:
        instances.seed_default(client)
    declared_names = instances.declared_names()
    if declared_names:
        names = ", ".join(declared_names)
        _note(
            "Configured instances: default, %s",
            names,
            printed=f"  Configured instances: default, {names}",
        )
    else:
        _note(
            "Configured instances: default only",
            printed="  Configured instances: default only",
        )
    _note("Server ready. Waiting for MCP client connection...")

    # Server is ready - yield client, instance registry, and the resolved
    # Config (callers wire OIDC per-user auth off the latter -- see
    # mcp/oidc.py -- since it carries oidc_only/trac_url/proxy settings
    # that a per-token Config is cloned from).
    yield {"client": client, "instances": instances, "config": config}

    # Shutdown
    _note(
        "MCP server shutting down",
        printed="Trac MCP Server shutting down.",
    )
