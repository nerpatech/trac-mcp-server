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

logger = logging.getLogger(__name__)


def _stderr_print(msg: str) -> None:
    """Print message to stderr for user feedback (safe in MCP mode)."""
    print(msg, file=sys.stderr, flush=True)


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
    logger.info("MCP server starting...")
    _stderr_print("Trac MCP Server starting...")

    # Load configuration with unified precedence:
    # CLI args > env vars (.env loaded first) > YAML config > defaults
    try:
        overrides = config_overrides or {}
        config, sources = bootstrap_config(overrides)
        source_desc = ", ".join(sources) if sources else "defaults"
        logger.info("Configuration loaded from: %s", source_desc)
        _stderr_print(f"  Configuration loaded from: {source_desc}")
        logger.info("Trac URL: %s", config.trac_url)
        _stderr_print(f"  Trac URL: {config.trac_url}")
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        _stderr_print(f"ERROR: Configuration error: {e}")
        _stderr_print(
            "  Ensure TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD are set."
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
        logger.info(
            "OIDC-only deployment (TRAC_MCP_OIDC_RPC_URL set, no shared "
            "Trac credentials): skipping startup connectivity self-test."
        )
        _stderr_print(
            "  OIDC per-user mode: skipping startup connectivity check "
            "(no shared credentials configured)."
        )
        init_semaphore(config.max_parallel_requests)
        _stderr_print(
            f"  Parallel requests: {config.max_parallel_requests}"
        )
    else:
        logger.info("Validating Trac connection...")
        _stderr_print("  Validating Trac connection...")
        try:
            client = TracClient(config)
            version = await run_sync(client.validate_connection)
            logger.info(
                "Successfully connected to Trac API version %s", version
            )
            _stderr_print(f"  Connected to Trac API version {version}")
            init_semaphore(config.max_parallel_requests)
            _stderr_print(
                f"  Parallel requests: {config.max_parallel_requests}"
            )
        except Exception as e:
            logger.error("Failed to connect to Trac: %s", e)
            _stderr_print("ERROR: Trac connection failed.")
            _stderr_print(f"  {e}")
            _stderr_print(
                "  Check TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD."
            )
            raise RuntimeError(
                f"Trac connection failed: {e}. Check TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD."
            ) from e

    instances = InstanceRegistry(config, load_declared_instances())
    if client is not None:
        instances.seed_default(client)
    declared_names = instances.declared_names()
    if declared_names:
        _stderr_print(
            f"  Configured instances: default, {', '.join(declared_names)}"
        )
    else:
        _stderr_print("  Configured instances: default only")
    _stderr_print("Server ready. Waiting for MCP client connection...")

    # Server is ready - yield client, instance registry, and the resolved
    # Config (callers wire OIDC per-user auth off the latter -- see
    # mcp/oidc.py -- since it carries oidc_only/trac_url/proxy settings
    # that a per-token Config is cloned from).
    yield {"client": client, "instances": instances, "config": config}

    # Shutdown
    logger.info("MCP server shutting down")
    _stderr_print("Trac MCP Server shutting down.")
