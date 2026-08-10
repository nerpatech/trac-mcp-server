"""
Hierarchical configuration loader for trac_mcp_server.

Provides convention-based config file discovery, YAML !include support,
env var interpolation, and hierarchical merge with "project wins" semantics.

Usage:
    from trac_mcp_server.config_loader import load_hierarchical_config

    config = load_hierarchical_config()
"""

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Env var interpolation
# ---------------------------------------------------------------------------

# Matches ${VAR} and ${VAR:-default}
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+?)(?::-(.*?))?\}")


def interpolate_env_vars(value: str) -> str:
    """Replace ``${VAR}`` and ``${VAR:-default}`` patterns with env values.

    * ``${VAR}`` is replaced with ``os.environ.get(VAR, "")``.
    * ``${VAR:-default}`` uses *default* when VAR is unset or empty.
    * Literal ``${`` with no closing ``}`` is left untouched.
    """

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2)  # None when no :- clause
        env_val = os.environ.get(var_name)
        if env_val is not None and env_val != "":
            return env_val
        if default is not None:
            return default
        return ""

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _interpolate_recursive(obj: Any) -> Any:
    """Walk a nested dict/list and interpolate env vars in all strings."""
    if isinstance(obj, str):
        return interpolate_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _interpolate_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_recursive(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# 2. YAML !include support (dedicated SafeLoader subclass)
# ---------------------------------------------------------------------------


class ConfigLoader(yaml.SafeLoader):
    """YAML SafeLoader subclass with ``!include`` support.

    Uses a dedicated subclass so the global ``yaml.SafeLoader`` is never
    modified.  Tracks an *include stack* per-load to detect circular includes.
    """


def _include_constructor(
    loader: ConfigLoader, node: yaml.ScalarNode
) -> Any:
    """Handle ``!include path/to/file.yml`` directives."""
    include_path_str: str = loader.construct_scalar(node)

    # Resolve relative to the file that contains the !include
    if os.path.isabs(include_path_str):
        include_path = Path(include_path_str)
    else:
        # loader.name is the path of the file being parsed
        parent_dir = Path(loader.name).resolve().parent
        include_path = parent_dir / include_path_str

    include_path = include_path.resolve()

    # Circular include detection
    include_stack: list[Path] = getattr(loader, "_include_stack", [])
    if include_path in include_stack:
        chain = (
            " -> ".join(str(p) for p in include_stack)
            + f" -> {include_path}"
        )
        raise ValueError(f"Circular include detected: {chain}")

    if not include_path.exists():
        source_file = Path(loader.name).resolve()
        raise FileNotFoundError(
            f"Include file not found: {include_path} (referenced from {source_file})"
        )

    new_stack = include_stack + [include_path]
    return _load_yaml_with_includes(
        include_path, _include_stack=new_stack
    )


ConfigLoader.add_constructor("!include", _include_constructor)


def _load_yaml_with_includes(
    path: Path,
    *,
    _include_stack: list[Path] | None = None,
) -> Any:
    """Load a YAML file using the ``ConfigLoader`` (with ``!include``)."""
    path = path.resolve()
    if _include_stack is None:
        _include_stack = [path]

    with open(path, "r", encoding="utf-8") as fh:
        loader = ConfigLoader(fh)
        loader._include_stack = _include_stack  # type: ignore[attr-defined]
        try:
            return loader.get_single_data()
        finally:
            loader.dispose()


# ---------------------------------------------------------------------------
# 2a. TRAC_INSTANCES file loading
# ---------------------------------------------------------------------------


def load_instances_file(path: str | Path) -> Any:
    """Load and env-interpolate a ``TRAC_INSTANCES`` file (YAML or JSON).

    Valid JSON is valid YAML, so ``_load_yaml_with_includes`` parses both
    formats. Returns the raw parsed value -- callers handle both the
    ``{"instances": {...}}`` and bare ``{name: {...}}`` shapes.

    Args:
        path: Path to the instances file.

    Returns:
        The parsed (and env-interpolated) contents, or ``{}`` if the file
        is empty.
    """
    data = _load_yaml_with_includes(Path(path))
    return _interpolate_recursive(data) if data else {}


# ---------------------------------------------------------------------------
# 3. Convention-based file discovery
# ---------------------------------------------------------------------------


def discover_config_files() -> list[Path]:
    """Return existing config file paths in precedence order (highest first).

    Search order:
        1. ``TRAC_MCP_CONFIG`` env var (explicit single path).
           For backward compatibility, ``TRAC_ASSIST_CONFIG`` is also
           accepted with a deprecation warning.
        2. ``.trac_mcp/config.yml`` in CWD (project-level)
        3. ``.trac_mcp/config.yaml`` in CWD (alternate extension, backward compat)
        4. ``~/.config/trac_mcp/config.yml`` (XDG global)
        5. ``~/.trac_mcp/config.yaml`` (legacy global, backward compat)

    Only paths that exist on disk are returned.
    """
    candidates: list[Path] = []

    # 1. Env var override (prefer TRAC_MCP_CONFIG, fall back to legacy name)
    env_path = os.environ.get("TRAC_MCP_CONFIG")
    if not env_path:
        env_path = os.environ.get("TRAC_ASSIST_CONFIG")
        if env_path:
            logger.warning(
                "TRAC_ASSIST_CONFIG is deprecated; "
                "use TRAC_MCP_CONFIG instead"
            )
    if env_path:
        candidates.append(Path(env_path).expanduser().resolve())

    # 2-3. Project-level (CWD)
    cwd = Path.cwd()
    candidates.append(cwd / ".trac_mcp" / "config.yml")
    candidates.append(cwd / ".trac_mcp" / "config.yaml")

    # 4. XDG global
    candidates.append(
        Path.home() / ".config" / "trac_mcp" / "config.yml"
    )

    # 5. Legacy global
    candidates.append(Path.home() / ".trac_mcp" / "config.yaml")

    return [p for p in candidates if p.exists()]


# ---------------------------------------------------------------------------
# 3a. Config bootstrapping
# ---------------------------------------------------------------------------

_STARTER_CONFIG = """\
# trac-mcp-server configuration
# https://github.com/your-org/trac-mcp-server
#
# Trac connection settings can also be set via environment variables:
#   TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD, TRAC_INSECURE
#
# trac:
#   url: https://trac.example.com
#   username: admin
#   password: secret
#   insecure: false
#   max_parallel_requests: 5
#
# logging:
#   level: INFO
#   file: null
"""


def resolve_config_path() -> Path:
    """Return the single config file path that should be used.

    If config files already exist (per ``discover_config_files()``), return
    the highest-precedence one (first in the list).

    If no config files exist, return the default project-level path:
    ``CWD / .trac_mcp / config.yml``.

    This does NOT create the file -- use ``ensure_config()`` for that.

    Returns:
        Path to the active (or default) config file.
    """
    existing = discover_config_files()
    if existing:
        return existing[0]
    return Path.cwd() / ".trac_mcp" / "config.yml"


def ensure_config(target: Path | None = None) -> Path:
    """Ensure a config file exists, creating directory and starter file if needed.

    If a config file already exists (per ``discover_config_files()``),
    return its path without modification.

    If no config file exists, create the directory and write a starter
    config.yml with commented-out sections as a template.

    Args:
        target: Explicit path to create. If ``None``, uses
            ``resolve_config_path()`` (which defaults to
            ``CWD / .trac_mcp / config.yml``).

    Returns:
        Path to the config file (existing or newly created).
    """
    existing = discover_config_files()
    if existing:
        logger.debug("Config file already exists: %s", existing[0])
        return existing[0]

    config_path = target or resolve_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config_path.write_text(_STARTER_CONFIG, encoding="utf-8")
    logger.info("Created starter config: %s", config_path)

    return config_path


# ---------------------------------------------------------------------------
# 4. Hierarchical merge
# ---------------------------------------------------------------------------


def load_hierarchical_config() -> dict[str, Any]:
    """Load and merge all discovered config files.

    Merge strategy ("project wins"):
        Files are loaded from lowest precedence to highest.  Each file's
        top-level keys **replace** (not deep-merge) those from earlier files.

    After merging, env var interpolation is applied to all string values.

    Returns an empty dict when no config files exist (zero-config).
    """
    paths = discover_config_files()

    if not paths:
        logger.debug(
            "No config files found — using zero-config defaults"
        )
        return {}

    # Merge from lowest precedence (last) to highest (first)
    merged: dict[str, Any] = {}
    for path in reversed(paths):
        logger.debug("Loading config: %s", path)
        try:
            data = _load_yaml_with_includes(path)
        except Exception:
            logger.exception("Failed to load config file %s", path)
            raise

        if isinstance(data, dict):
            # Shallow merge — top-level keys from higher-precedence win
            merged.update(data)
        elif data is not None:
            logger.warning(
                "Config file %s has non-dict root (%s) — skipping",
                path,
                type(data).__name__,
            )

    # Apply env var interpolation after merge
    merged = _interpolate_recursive(merged)  # type: ignore[assignment]

    return merged
