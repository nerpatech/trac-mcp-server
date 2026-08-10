"""Multi-instance resolution for the Trac MCP server.

Resolves an optional per-tool ``instance`` argument to a validated
:class:`~trac_mcp_server.config.Config`, so a single running server can talk
to multiple Trac projects: named instances declared in config, plus ad-hoc
addressing of any project on the *same host* as the default instance.

Ad-hoc cross-host addressing is intentionally rejected -- it would let an
agent-supplied hostname receive the operator's configured credentials.
"""

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .config import Config, validate_config
from .config_loader import (
    discover_config_files,
    load_hierarchical_config,
    load_instances_file,
)
from .config_schema import build_config
from .core.client import TracClient


@dataclass(frozen=True, slots=True)
class InstanceSpec:
    """A declared instance entry, before defaults are merged in."""

    name: str
    url: str
    username: str | None = None
    password: str | None = None
    insecure: bool | None = None


class UnknownInstanceError(ValueError):
    """Raised when an ``instance`` argument cannot be resolved to a Config."""


def _host_root(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _same_host(url: str, other_url: str) -> bool:
    a, b = urlparse(url), urlparse(other_url)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


def _instance_source_paths() -> list[Path]:
    """Files whose mtimes gate a declared-instances reload."""
    paths = list(discover_config_files())
    env_path = os.environ.get("TRAC_INSTANCES")
    if env_path:
        paths.append(Path(env_path).expanduser())
    return paths


def load_declared_instances() -> dict[str, InstanceSpec]:
    """Merge declared instances from YAML config and ``TRAC_INSTANCES``.

    ``TRAC_INSTANCES`` wins on name collision, consistent with the existing
    env-over-YAML precedence used for the primary connection settings.

    Returns:
        Dict of instance name -> InstanceSpec. Empty when nothing is declared.
    """
    declared: dict[str, InstanceSpec] = {}

    config_files = discover_config_files()
    if config_files:
        raw = load_hierarchical_config()
        unified = build_config(raw)
        for name, inst in unified.instances.items():
            declared[name] = InstanceSpec(
                name=name,
                url=inst.url,
                username=inst.username,
                password=inst.password,
                insecure=inst.insecure,
            )

    env_path = os.environ.get("TRAC_INSTANCES")
    if env_path:
        data = load_instances_file(env_path) or {}
        entries = (
            data.get("instances", data)
            if isinstance(data, dict)
            else {}
        )
        for name, inst in entries.items():
            declared[name] = InstanceSpec(
                name=name,
                url=inst["url"],
                username=inst.get("username"),
                password=inst.get("password"),
                insecure=inst.get("insecure"),
            )

    return declared


class InstanceRegistry:
    """Resolves an optional ``instance`` argument to a Config, with caching.

    Resolution order for ``resolve(name)``:
        1. ``None`` or ``"default"`` -> the default Config as-is.
        2. ``name`` in declared instances -> merged over the default
           (unset username/password/insecure inherit; a relative URL is
           joined onto the default host).
        3. ``name`` looks like a path (``/bcs``) or an absolute http(s) URL
           whose scheme+host equals the default's -> synthesized Config
           using the default's credentials.
        4. Otherwise -> :class:`UnknownInstanceError` (this includes
           cross-host URLs, rejected by design).
    """

    def __init__(
        self, default_config: Config, declared: dict[str, InstanceSpec]
    ):
        self._default = default_config
        self._declared = declared
        self._clients: dict[str, TracClient] = {}
        self._lock = threading.Lock()
        self._sources = _instance_source_paths()
        self._mtimes = self._snapshot_mtimes()

    def _snapshot_mtimes(self) -> dict[Path, float]:
        return {
            p: p.stat().st_mtime for p in self._sources if p.exists()
        }

    def _reload_if_changed(self) -> None:
        """Pick up edits to the instances file without a server restart."""
        current = self._snapshot_mtimes()
        if current != self._mtimes:
            self._declared = load_declared_instances()
            self._sources = _instance_source_paths()
            self._mtimes = self._snapshot_mtimes()

    def _configured_names(self) -> str:
        return ", ".join(["default"] + sorted(self._declared))

    def resolve(self, name: str | None) -> Config:
        self._reload_if_changed()

        if name is None or name == "default":
            return self._default

        spec = self._declared.get(name)
        if spec is not None:
            return self._synthesize(
                spec.url, spec.username, spec.password, spec.insecure
            )

        if name.startswith("/"):
            return self._synthesize(
                _host_root(self._default.trac_url) + name
            )

        if name.startswith(("http://", "https://")):
            if not _same_host(name, self._default.trac_url):
                raise UnknownInstanceError(
                    f"Instance '{name}' is on a different host than the "
                    f"default instance ({_host_root(self._default.trac_url)}). "
                    "Cross-host ad-hoc addressing is rejected by design so "
                    "configured credentials are never sent to another host. "
                    f"Configured instances: {self._configured_names()}"
                )
            return self._synthesize(name)

        raise UnknownInstanceError(
            f"Unknown instance '{name}'. "
            f"Configured instances: {self._configured_names()}"
        )

    def _synthesize(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        insecure: bool | None = None,
    ) -> Config:
        if not url.startswith(("http://", "https://")):
            url = _host_root(self._default.trac_url) + (
                url if url.startswith("/") else f"/{url}"
            )
        config = Config(
            trac_url=url,
            username=username or self._default.username,
            password=password or self._default.password,
            insecure=(
                insecure
                if insecure is not None
                else self._default.insecure
            ),
            debug=self._default.debug,
            max_parallel_requests=self._default.max_parallel_requests,
            max_batch_size=self._default.max_batch_size,
        )
        validate_config(config)
        return config

    def get_client(self, name: str | None) -> TracClient:
        """Return a TracClient for ``name``, caching by resolved URL."""
        config = self.resolve(name)
        with self._lock:
            client = self._clients.get(config.trac_url)
            if client is None:
                client = TracClient(config)
                self._clients[config.trac_url] = client
            return client

    def seed_default(self, client: TracClient) -> None:
        """Install the lifespan-built client under the default key.

        Reuses startup's already-validated connection instead of
        constructing a duplicate TracClient for the default instance.
        """
        with self._lock:
            self._clients[self._default.trac_url] = client

    def describe(self) -> list[dict]:
        """Describe configured instances. Never includes passwords."""
        result = [
            {
                "name": "default",
                "url": self._default.trac_url,
                "is_default": True,
                "credentials": "explicit",
            }
        ]
        for name in sorted(self._declared):
            spec = self._declared[name]
            result.append(
                {
                    "name": name,
                    "url": self.resolve(name).trac_url,
                    "is_default": False,
                    "credentials": "explicit"
                    if spec.username
                    else "inherited",
                }
            )
        return result

    def declared_names(self) -> list[str]:
        return sorted(self._declared)
