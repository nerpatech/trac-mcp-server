"""Multi-instance resolution for the Trac MCP server.

Resolves an optional per-tool ``instance`` argument to a validated
:class:`~trac_mcp_server.config.Config`, so a single running server can talk
to multiple Trac projects: named instances declared in config, plus ad-hoc
addressing of any project on the *same host* as the default instance.

Ad-hoc cross-host addressing is intentionally rejected -- it would let an
agent-supplied hostname receive the operator's configured credentials.

Ticket #110: a caller may additionally be scoped to an allowlist of
instances (``instances:`` on a ``TRAC_IDENTITIES`` entry, or
``TRAC_ALLOWED_INSTANCES`` for the default identity), enforced on the
*resolved* URL in :meth:`InstanceRegistry.resolve`.
"""

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .config import (
    Config,
    normalize_allowed_instances,
    parse_allowed_instances,
    validate_config,
)
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


@dataclass(frozen=True, slots=True)
class Identity:
    """A per-caller Trac identity declared in a ``TRAC_IDENTITIES`` file.

    ``name`` is the identity's own key in the file (for error messages and
    the hermetic test's ``whoami``-style assertions), not a Trac username
    by itself -- ``username``/``password`` are what actually reach Trac.
    """

    name: str
    username: str
    password: str
    # Ticket #110: the instances this identity may reach, as written in the
    # file (each a ``/path`` on the default host or an absolute URL).
    # None = unrestricted; an empty tuple is never constructed, since
    # load_identities() rejects an empty list.
    instances: tuple[str, ...] | None = None


class UnknownInstanceError(ValueError):
    """Raised when an ``instance`` argument cannot be resolved to a Config."""


class InstanceNotAllowedError(ValueError):
    """Raised when a resolved instance is outside the caller's allowlist.

    Ticket #110. Distinct from :class:`UnknownInstanceError`: the instance
    exists (or may), the caller is simply not scoped to it. Subclasses
    ``ValueError`` so the existing dispatch sites' ``except ValueError``
    catches it; they map it to the ``instance_not_allowed`` error type.
    """


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


def load_identities() -> dict[str, Identity]:
    """Load per-caller identities from the file named by ``TRAC_IDENTITIES``.

    Ticket #102: a shared HTTP daemon resolves a bearer token to a Trac
    username/password, replacing a one-process-per-identity deployment.
    File shape (YAML or JSON, same as ``TRAC_INSTANCES``)::

        identities:
          alice:
            token: ${ALICE_MCP_TOKEN}
            username: ${ALICE_TRAC_USER}
            password: ${ALICE_TRAC_PASSWORD}
            instances:            # optional (ticket #110)
              - /trac_test
              - https://trac.example.org/other

    ``instances`` scopes the identity to those instances (a list, or a
    comma-separated string such as ``${VAR}`` interpolation yields); absent
    means unrestricted, present-but-empty is an error.

    ``${VAR}`` interpolation and the bare ``{name: {...}}`` shape are the
    same machinery ``TRAC_INSTANCES`` uses
    (``config_loader.load_instances_file``), so tokens and passwords can
    stay in ``.env`` rather than in the identities file itself.

    Loaded once at process startup -- unlike declared instances, there is
    no mtime-triggered reload here. Changing identities needs a restart.

    Returns:
        Dict of bearer token -> Identity, keyed by token (not name) so the
        auth middleware can look one up directly. Empty when
        ``TRAC_IDENTITIES`` is unset -- the common case, where a single
        process serves a single operator's identity exactly as before
        this ticket.

    Raises:
        ValueError: An entry is missing a token/username/password, two
            entries claim the same token, or an ``instances`` list is
            empty or holds an entry that is neither a ``/path`` nor an
            http(s) URL.
    """
    env_path = os.environ.get("TRAC_IDENTITIES")
    if not env_path:
        return {}

    data = load_instances_file(env_path) or {}
    entries = (
        data.get("identities", data) if isinstance(data, dict) else {}
    )

    result: dict[str, Identity] = {}
    for name, entry in entries.items():
        entry = entry or {}
        token = entry.get("token")
        username = entry.get("username")
        password = entry.get("password")
        if not token or not str(token).strip():
            raise ValueError(
                f"Identity '{name}' in TRAC_IDENTITIES has no token."
            )
        if not username or not str(username).strip():
            raise ValueError(
                f"Identity '{name}' in TRAC_IDENTITIES has no username."
            )
        if not password or not str(password).strip():
            raise ValueError(
                f"Identity '{name}' in TRAC_IDENTITIES has no password."
            )
        if token in result:
            raise ValueError(
                f"Identity '{name}' in TRAC_IDENTITIES reuses a token "
                f"already claimed by '{result[token].name}'. Tokens must "
                "be unique."
            )
        result[token] = Identity(
            name=name,
            username=username,
            password=password,
            instances=_parse_identity_instances(name, entry),
        )

    return result


def _parse_identity_instances(
    name: str, entry: dict
) -> tuple[str, ...] | None:
    """The ``instances`` key of one identities entry (ticket #110):
    None when absent (unrestricted), else parsed by the same
    :func:`~trac_mcp_server.config.parse_allowed_instances` that reads
    ``TRAC_ALLOWED_INSTANCES``."""
    raw = entry.get("instances")
    if raw is None:
        return None
    return parse_allowed_instances(
        raw, f"Identity '{name}' in TRAC_IDENTITIES: 'instances'"
    )


def caller_identity() -> "Identity | None":
    """The bearer-token identity of the in-flight MCP request, if any.

    Reads the mcp SDK's own per-message contextvar directly
    (``mcp.server.lowlevel.server.request_ctx`` -- the exact thing
    ``Server.request_context`` wraps) rather than going through a
    particular ``Server`` instance, so both ``mcp/server.py``'s tool
    dispatch and ``mcp/tools/instances.py``'s ``list_instances`` handler
    -- which never sees a ``Server`` object, only a ``TracClient`` -- can
    call this the same way without a circular import between the two.

    Returns ``None``:
    - outside a request (``request_ctx.get()`` raises ``LookupError`` --
      e.g. a unit test calling a handler directly);
    - on the ``stdio`` transport, where ``.request`` is always ``None``
      (there is no HTTP request to carry a scope);
    - over http with no bearer identity (the legacy static token, or no
      auth configured at all) -- the caller then gets the default
      instance's own configured credentials, exactly as before this
      ticket.

    NOTE: relies on the mcp SDK setting ``request_context.request`` to
    the Starlette ``Request`` handling the in-flight message (see
    ``streamable_http.py``'s three
    ``ServerMessageMetadata(request_context=request)`` call sites).
    Re-verify this on any ``mcp`` version bump.
    """
    from mcp.server.lowlevel.server import request_ctx

    try:
        context = request_ctx.get()
    except LookupError:
        return None
    request = context.request
    if request is None:
        return None
    return request.scope.get("trac_identity")


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

    Ticket #102: every entry point above also takes an optional caller
    ``identity`` (resolved from the HTTP bearer token). An identity's
    credentials replace *inherited* default credentials -- for the
    default instance itself, for a declared instance that does not set
    its own username/password, and for ad-hoc same-host addressing.
    A declared instance's *explicit* username/password always wins over
    the caller's identity, so one identity's password can never reach a
    host another declared instance deliberately points credentials at.
    """

    def __init__(
        self, default_config: Config, declared: dict[str, InstanceSpec]
    ):
        self._default = default_config
        self._declared = declared
        # Keyed by (url, username, password, allowlist) -- see
        # get_client() (#102, #110).
        self._clients: dict[
            tuple[str, str, str, frozenset[str] | None], TracClient
        ] = {}
        self._lock = threading.Lock()
        self._sources = _instance_source_paths()
        self._mtimes = self._snapshot_mtimes()

    def _snapshot_mtimes(self) -> dict[Path, float]:
        return {
            p: p.stat().st_mtime for p in self._sources if p.exists()
        }

    def _reload_if_changed(self) -> None:
        """Pick up edits to the instances file without a server restart."""
        with self._lock:
            current = self._snapshot_mtimes()
            if current != self._mtimes:
                self._declared = load_declared_instances()
                self._sources = _instance_source_paths()
                self._mtimes = self._snapshot_mtimes()

    def _configured_names(self) -> str:
        return ", ".join(["default"] + sorted(self._declared))

    def allowed_urls(
        self, identity: Identity | None = None
    ) -> frozenset[str] | None:
        """The caller's instance allowlist as absolute URLs, or None.

        Ticket #110. An identity with ``instances:`` is scoped to those;
        an identity without is unrestricted and NOT bound by
        ``TRAC_ALLOWED_INSTANCES``, which scopes the default credentials
        only. With no identity, the default Config's own list applies.
        """
        if identity is None:
            return self._default.allowed_instances
        if identity.instances is None:
            return None
        return normalize_allowed_instances(
            identity.instances, self._default.trac_url
        )

    def _display_instance(self, url: str) -> str:
        """``/path`` for a same-host URL, the full URL otherwise."""
        if _same_host(url, self._default.trac_url):
            return urlparse(url).path or "/"
        return url

    def _enforce_allowlist(
        self,
        name: str | None,
        config: Config,
        allowed: frozenset[str] | None,
    ) -> Config:
        """Refuse ``config`` unless its resolved URL is in ``allowed``.

        Ticket #110: the one place the allowlist is checked, on the
        RESOLVED url after every branch of ``resolve()`` -- so neither a
        declared name nor a full URL routes around it. Exact match.
        """
        if allowed is not None and config.trac_url not in allowed:
            shown = ", ".join(
                sorted(self._display_instance(u) for u in allowed)
            )
            raise InstanceNotAllowedError(
                f"Instance '{name if name is not None else 'default'}' "
                f"(resolved to {config.trac_url}) is not in this "
                f"caller's allowed instances: {shown}. Call "
                "list_instances to see what is reachable."
            )
        return config

    def resolve(
        self, name: str | None, identity: Identity | None = None
    ) -> Config:
        allowed = self.allowed_urls(identity)
        return self._enforce_allowlist(
            name,
            self._resolve_unenforced(name, identity, allowed),
            allowed,
        )

    def _resolve_unenforced(
        self,
        name: str | None,
        identity: Identity | None,
        allowed: frozenset[str] | None,
    ) -> Config:
        """Resolve ``name`` to a Config without the allowlist check.

        Private: ``describe()`` classifies links against every configured
        instance and must not be scoped by (or raise on) a caller's list.
        """
        self._reload_if_changed()

        if name is None or name == "default":
            if identity is None:
                return self._default
            # Gap 1 (comment:2 review): an identity replaces the default's
            # own credentials too, not only an instance that inherits from
            # it -- resolve(None) with an identity is the most common call
            # shape, so missing it here would defeat the point.
            return self._synthesize(
                self._default.trac_url,
                identity.username,
                identity.password,
                allowed=allowed,
            )

        spec = self._declared.get(name)
        if spec is not None:
            return self._synthesize(
                *self._declared_credentials(spec, identity),
                spec.insecure,
                allowed=allowed,
            )

        if name.startswith("/"):
            url = _host_root(self._default.trac_url) + name
            return self._synthesize(
                *self._adhoc_credentials(url, identity), allowed=allowed
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
            return self._synthesize(
                *self._adhoc_credentials(name, identity),
                allowed=allowed,
            )

        raise UnknownInstanceError(
            f"Unknown instance '{name}'. "
            f"Configured instances: {self._configured_names()}"
        )

    @staticmethod
    def _declared_credentials(
        spec: InstanceSpec, identity: Identity | None
    ) -> tuple[str, str | None, str | None]:
        """(url, username, password) for a declared instance.

        Gap 3 (CONFIRMED by the operator): a declared instance's own
        explicit username wins over the caller's identity -- checked the
        same way ``describe()`` already distinguishes "explicit" from
        "inherited" credentials. Only the inherited case is replaced by
        the identity, so one identity's password can never reach a host
        another declared instance deliberately points credentials at.
        """
        if spec.username:
            return spec.url, spec.username, spec.password
        if identity is not None:
            return spec.url, identity.username, identity.password
        return spec.url, spec.username, spec.password

    @staticmethod
    def _adhoc_credentials(
        url: str, identity: Identity | None
    ) -> tuple[str, str | None, str | None]:
        """(url, username, password) for ad-hoc path/same-host addressing.

        No declared instance is involved, so there is nothing "explicit"
        to protect -- the identity's credentials simply take the place of
        the default's, exactly like the default-instance case above.
        """
        if identity is not None:
            return url, identity.username, identity.password
        return url, None, None

    def _synthesize(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        insecure: bool | None = None,
        allowed: frozenset[str] | None = None,
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
            rpc_timeout=self._default.rpc_timeout,
            # Ticket #110: the caller's scope rides on the Config so the
            # probes, which see only the client, enforce it too.
            allowed_instances=allowed,
        )
        validate_config(config)
        return config

    def get_client(
        self, name: str | None, identity: Identity | None = None
    ) -> TracClient:
        """Return a TracClient for ``name``/``identity``, caching by
        resolved (url, username, password, allowlist).

        The cache key includes the password, not just the URL and
        username (ticket #102): two identities sharing a username but not
        a password would otherwise have the second reuse the first's
        already-authenticated client. It includes the allowlist too
        (ticket #110): the client's Config carries the scope its probes
        enforce, so two callers with the same credentials but different
        scopes must not share one.
        """
        config = self.resolve(name, identity)
        key = (
            config.trac_url,
            config.username,
            config.password,
            config.allowed_instances,
        )
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = TracClient(config)
                self._clients[key] = client
            return client

    def seed_default(self, client: TracClient) -> None:
        """Install the lifespan-built client under the default key.

        Reuses startup's already-validated connection instead of
        constructing a duplicate TracClient for the default instance.
        """
        key = (
            self._default.trac_url,
            self._default.username,
            self._default.password,
            self._default.allowed_instances,
        )
        with self._lock:
            self._clients[key] = client

    def describe(self) -> list[dict]:
        """Describe configured instances. Never includes passwords.

        Unscoped on purpose (ticket #110): ``local_intertrac_bases()``
        uses it to classify links, which needs every configured instance
        whatever the caller may reach. Hence the unenforced resolve --
        a ``TRAC_ALLOWED_INSTANCES`` list must not make this raise.
        """
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
                    "url": self._resolve_unenforced(
                        name, None, None
                    ).trac_url,
                    "is_default": False,
                    "credentials": "explicit"
                    if spec.username
                    else "inherited",
                }
            )
        return result

    def declared_names(self) -> list[str]:
        return sorted(self._declared)
