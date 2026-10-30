"""Simplified configuration for standalone MCP server.

Reads Trac connection settings from CLI args, environment variables,
.env files, and YAML config file fallbacks.

Precedence (highest to lowest):
    CLI args > Environment variables > .env file > YAML config > Built-in defaults

Environment variables:
    TRAC_URL: Trac instance URL (required)
    TRAC_USERNAME: Trac username (required, unless TRAC_MCP_OIDC_RPC_URL is set)
    TRAC_PASSWORD: Trac password (required, unless TRAC_MCP_OIDC_RPC_URL is set)
    TRAC_INSECURE: Skip SSL verification (optional, default: false)
    TRAC_MAX_PARALLEL_REQUESTS: Max parallel XML-RPC requests (optional, default: 5)
    TRAC_MAX_BATCH_SIZE: Max items per batch operation (optional, default: 500)
    TRAC_RPC_TIMEOUT: Read timeout in seconds for XML-RPC requests (optional, default: 60)
    TRAC_MCP_OIDC_RPC_URL: Full XML-RPC URL of an OIDC-protected Trac endpoint
        (optional). When set, TRAC_USERNAME/TRAC_PASSWORD are not required --
        this deployment mode never uses a shared service-account identity;
        see mcp/oidc.py and docs/reference/http-transport.md.
    TRAC_ALLOWED_INSTANCES: Comma-separated instances the default identity may
        reach, each a ``/path`` on the TRAC_URL host or an absolute http(s) URL
        (optional, default: unrestricted; ticket #110)
"""

import ipaddress
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .config_schema import ServerConfig

logger = logging.getLogger(__name__)


@dataclass
class Config:
    trac_url: str
    username: str
    password: str
    insecure: bool = False
    debug: bool = False
    max_parallel_requests: int = 5
    max_batch_size: int = 500
    rpc_timeout: int = 60
    # OIDC per-user auth (HTTP transport only; see mcp/oidc.py). bearer_token
    # and rpc_url_override are set per-request on a *synthesized* Config for
    # one caller's own token -- never on the shared default Config. oidc_only
    # marks the shared default Config as loaded without a service-account
    # identity (TRAC_MCP_OIDC_RPC_URL was set instead), which relaxes
    # username/password validation and skips the startup connectivity
    # self-test, since there are no credentials to test with.
    bearer_token: str | None = None
    rpc_url_override: str | None = None
    oidc_only: bool = False
    # Ticket #64 ruling 5. The write-time link gate can refuse writes,
    # and it runs on a daemon every project on this host writes
    # through, so a gate misbehaving in production needs an answer
    # cheaper than a revert and a redeploy. Default on: this exists to
    # be switched off in an emergency, not to be opted into.
    write_gate: bool = True
    # Ticket #110: the instances this Config's caller may reach, as
    # absolute URLs with no trailing slash. None = unrestricted. Carried on
    # every Config ``InstanceRegistry.resolve()`` returns so the probes in
    # preview/targets.py, which see only the client, can enforce the same
    # scope as the resolve() choke point.
    allowed_instances: frozenset[str] | None = None


def validate_config(config: Config) -> None:
    """Validate configuration values and raise ValueError if invalid.

    Args:
        config: Config instance to validate.

    Raises:
        ValueError: If URL format is invalid or credentials are empty.
    """
    # Normalize URL: strip whitespace
    config.trac_url = config.trac_url.strip()

    if not config.trac_url.startswith(("http://", "https://")):
        raise ValueError(
            f"Invalid Trac URL '{config.trac_url}': must start with http:// or https://"
        )

    parsed = urlparse(config.trac_url)
    if not parsed.hostname:
        raise ValueError(
            f"Invalid Trac URL '{config.trac_url}': URL must include a hostname"
        )

    # Strip trailing slash after validation (safe now that scheme/host are verified)
    config.trac_url = config.trac_url.removesuffix("/")

    # oidc_only deployments have no service-account identity by design --
    # see the Config.oidc_only docstring comment. username/password are
    # meaningless there and stay empty.
    if not config.oidc_only:
        if not config.username.strip():
            raise ValueError(
                "Trac username cannot be empty. Set TRAC_USERNAME environment variable."
            )

        if not config.password.strip():
            raise ValueError(
                "Trac password cannot be empty. Set TRAC_PASSWORD environment variable."
            )

    if config.insecure:
        logger.warning(
            "WARNING: SSL verification disabled (insecure=True). Use only for development."
        )


def parse_allowed_instances(
    raw: object, source: str
) -> tuple[str, ...]:
    """Parse one instance allowlist as configured (ticket #110).

    The single parser for both sources -- ``TRAC_ALLOWED_INSTANCES`` and
    an identity's ``instances:`` key -- so the two cannot drift. ``raw``
    is a comma-separated string (all an env var, or a ``${VAR}``
    interpolation, can be) or a list.

    Args:
        raw: The configured value. ``None`` is the caller's business
            (it means unrestricted) and is not accepted here.
        source: Names the setting in error messages, e.g.
            ``"TRAC_ALLOWED_INSTANCES"``.

    Returns:
        The stripped, non-empty entries, in order.

    Raises:
        ValueError: ``raw`` is neither a string nor a list (a YAML
            mapping must not be read as its keys); it has no non-empty
            entries -- an empty list is a misconfiguration, never "deny
            everything"; or an entry is neither a ``/path`` nor an
            absolute http(s) URL.
    """
    if isinstance(raw, str):
        items: list = raw.split(",")
    elif isinstance(raw, list | tuple):
        items = list(raw)
    else:
        raise ValueError(
            f"{source} must be a list or a comma-separated string of "
            f"instances, not {type(raw).__name__}."
        )
    entries = tuple(
        s for s in (str(item).strip() for item in items) if s
    )
    if not entries:
        raise ValueError(
            f"{source} is set but lists no instances. Leave it unset "
            "for an unrestricted caller, or list the instances it may "
            "reach (/path or absolute URL)."
        )
    for entry in entries:
        if not entry.startswith(("/", "http://", "https://")):
            raise ValueError(
                f"{source} has instance '{entry}': must be a /path on "
                "the default host or an absolute http(s) URL."
            )
    return entries


def normalize_allowed_instances(
    entries: "list[str] | tuple[str, ...]", base_url: str
) -> frozenset[str]:
    """Turn parsed allowlist entries into absolute URLs with no trailing
    slash (ticket #110).

    A ``/path`` entry means that path on ``base_url``'s host; an
    ``http(s)://`` entry is kept as written. Nothing else is
    normalised -- no dot-segment resolution, no lowercasing of the path --
    because the allowlist is compared exactly: a non-canonical spelling
    must be refused, never rewritten into an allowed one. ``entries``
    must come from :func:`parse_allowed_instances`, which validated them.
    """
    parsed = urlparse(base_url.strip())
    host_root = f"{parsed.scheme}://{parsed.netloc}"
    return frozenset(
        (host_root + entry if entry.startswith("/") else entry).rstrip(
            "/"
        )
        for entry in entries
    )


def _is_loopback_host(host: str) -> bool:
    """Return True if ``host`` only ever resolves to the local machine."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_server_config(server_config: "ServerConfig") -> None:
    """Validate MCP server (transport) configuration.

    Refuses to bind a non-loopback host for the http transport unless an
    auth token is configured, OIDC per-user auth is configured, or the
    operator explicitly opts out -- otherwise "add HTTP" silently becomes
    "expose the operator's Trac credentials to the network".

    Also refuses ``auth_token`` and ``oidc_rpc_url`` together: both are
    read off the same ``Authorization`` header for different purposes (see
    mcp/oidc.py), so combining them isn't a stronger gate, just an
    always-losing static-token comparison against a per-user token no
    caller could ever satisfy.

    Also validates ``server_config.identities`` (ticket #102) against the
    rest of the server config -- checks that need the auth_token and
    transport together, so they belong here rather than in
    ``instances.load_identities()``, which only sees the identities file
    in isolation.

    Args:
        server_config: ServerConfig instance to validate.

    Raises:
        ValueError: If auth_token or identities are combined with
            oidc_rpc_url, if an unauthenticated http transport would bind
            a non-loopback host, or an identities config is invalid (see
            below).
    """
    if server_config.identities:
        if server_config.transport != "http":
            raise ValueError(
                "TRAC_IDENTITIES is set but transport is "
                f"'{server_config.transport}'. Per-caller identity "
                "resolution reads a bearer token off each HTTP request; "
                "stdio has no per-request signal to read one from, so an "
                "identities file there could never be honoured. Use "
                "--transport http, or drop TRAC_IDENTITIES."
            )
        if (
            server_config.auth_token
            and server_config.auth_token in server_config.identities
        ):
            raise ValueError(
                "An identity token in TRAC_IDENTITIES collides with "
                "TRAC_MCP_AUTH_TOKEN. Every token must be distinct, or a "
                "caller presenting the shared value would be ambiguous "
                "between the static token and that identity."
            )
        if (
            server_config.allow_unauthenticated
            and not server_config.auth_token
        ):
            raise ValueError(
                "TRAC_IDENTITIES is set together with "
                "allow_unauthenticated and no TRAC_MCP_AUTH_TOKEN. "
                "Declaring per-caller identities and then opting the "
                "endpoint out of authentication is contradictory: no "
                "caller could ever present a token for one of them to "
                "be resolved. Set TRAC_MCP_AUTH_TOKEN, or drop "
                "allow_unauthenticated/TRAC_IDENTITIES."
            )

    if server_config.transport != "http":
        return

    if server_config.auth_token and server_config.oidc_rpc_url:
        raise ValueError(
            "TRAC_MCP_AUTH_TOKEN and TRAC_MCP_OIDC_RPC_URL cannot both be "
            "set: OIDC per-user auth reads the caller's own token from the "
            "same Authorization header a static bearer token would occupy, "
            "so every request would fail the static-token comparison. "
            "Remove one of the two."
        )

    if server_config.identities and server_config.oidc_rpc_url:
        raise ValueError(
            "TRAC_IDENTITIES and TRAC_MCP_OIDC_RPC_URL cannot both be "
            "set: both resolve the caller from the same Authorization "
            "header, one as a static identity token, the other as the "
            "caller's own OIDC access token. Remove one of the two."
        )

    # Identities alone (no static auth_token) still gate every request --
    # BearerAuthMiddleware is open only when there is neither a token nor
    # any identities (ticket #102) -- so they count as authentication for
    # bind safety exactly like auth_token does.
    if (
        server_config.auth_token
        or server_config.oidc_rpc_url
        or server_config.allow_unauthenticated
        or server_config.identities
    ):
        return

    if _is_loopback_host(server_config.host):
        return

    raise ValueError(
        f"Refusing to bind non-loopback host '{server_config.host}' for the "
        "http transport without authentication. Set TRAC_MCP_AUTH_TOKEN "
        "(or the server.auth_token config value), or pass "
        "--allow-unauthenticated to explicitly opt out."
    )


def load_config(
    url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    insecure: bool = False,
    debug: bool = False,
    yaml_fallbacks: dict | None = None,
) -> Config:
    """Load configuration with unified precedence.

    Resolution order for each field (highest to lowest):
        CLI arg > env var / .env > yaml_fallbacks > built-in default

    The caller is responsible for calling ``load_dotenv()`` before this
    function so that .env values are available via ``os.getenv()``.

    Args:
        url: Override Trac URL (takes precedence over env var and YAML).
        username: Override username (takes precedence over env var and YAML).
        password: Override password (takes precedence over env var and YAML).
        insecure: Skip SSL verification (CLI flag).
        debug: Enable debug logging (CLI flag).
        yaml_fallbacks: Dict of values from YAML config file ``trac`` section.
            Used as fallback when CLI arg and env var are both unset.

    Returns:
        Validated Config instance.

    Raises:
        ValueError: If required config (URL, username, password) is missing
            after checking all sources.
    """
    fb = yaml_fallbacks or {}

    # TRAC_MCP_OIDC_RPC_URL relaxes the TRAC_USERNAME/TRAC_PASSWORD
    # requirement below -- an OIDC-only deployment (see Config.oidc_only)
    # has no shared service-account identity by design; every request
    # carries its own token instead (mcp/oidc.py). Read directly via
    # os.getenv rather than threading ServerConfig through this call:
    # TRAC_MCP_OIDC_RPC_URL is only ever meaningful for the http transport,
    # so an operator running stdio while it happens to be set just gets a
    # (harmless) relaxed check. Config.oidc_only itself is computed further
    # down, once we know whether real credentials ended up configured
    # anyway -- so setting TRAC_MCP_OIDC_RPC_URL *alongside* a real
    # TRAC_USERNAME/TRAC_PASSWORD keeps the startup self-test enabled.
    oidc_rpc_configured = bool(os.getenv("TRAC_MCP_OIDC_RPC_URL"))

    # --- String fields: CLI > env > YAML > error ---

    trac_url = url or os.getenv("TRAC_URL") or fb.get("url")
    if not trac_url:
        raise ValueError(
            "Trac URL not found. Set TRAC_URL environment variable, "
            "pass --url CLI argument, or add 'url' to config.yaml."
        )
    trac_url = trac_url.strip()

    trac_username = (
        username or os.getenv("TRAC_USERNAME") or fb.get("username")
    )
    if not trac_username:
        if oidc_rpc_configured:
            trac_username = ""
        else:
            raise ValueError(
                "Trac username not found. Set TRAC_USERNAME environment variable, "
                "pass --username CLI argument, or add 'username' to config.yaml."
            )
    else:
        trac_username = trac_username.strip()

    trac_password = (
        password or os.getenv("TRAC_PASSWORD") or fb.get("password")
    )
    if not trac_password:
        if oidc_rpc_configured:
            trac_password = ""
        else:
            raise ValueError(
                "Trac password not found. Set TRAC_PASSWORD environment variable, "
                "pass --password CLI argument, or add 'password' to config.yaml."
            )
    else:
        trac_password = trac_password.strip()

    # True only when TRAC_MCP_OIDC_RPC_URL is configured AND no real
    # credentials ended up available -- setting it alongside a working
    # TRAC_USERNAME/TRAC_PASSWORD keeps the shared-identity self-test in
    # mcp/lifespan.py enabled; tool-call gating in that mode is governed
    # separately, by ServerConfig.oidc_rpc_url in mcp/server.py.
    oidc_only = (
        oidc_rpc_configured and not trac_username and not trac_password
    )

    # --- Boolean fields: CLI > env > YAML > default ---

    def get_bool_env(key: str) -> bool | None:
        """Return True/False from env var, or None if unset."""
        val = os.getenv(key)
        if val is None:
            return None
        return val.lower() in ("true", "1", "yes", "on")

    if insecure:
        final_insecure = True
    else:
        env_insecure = get_bool_env("TRAC_INSECURE")
        if env_insecure is not None:
            final_insecure = env_insecure
        else:
            final_insecure = bool(fb.get("insecure", False))

    if debug:
        final_debug = True
    else:
        env_debug = get_bool_env("TRAC_DEBUG")
        if env_debug is not None:
            final_debug = env_debug
        else:
            final_debug = bool(fb.get("debug", False))

    # --- Numeric fields: env > YAML > default ---
    # (No CLI args for numeric fields currently)

    max_parallel_raw = os.getenv("TRAC_MAX_PARALLEL_REQUESTS")
    if max_parallel_raw is not None:
        try:
            final_max_parallel = int(max_parallel_raw)
        except ValueError:
            raise ValueError(
                f"Invalid TRAC_MAX_PARALLEL_REQUESTS '{max_parallel_raw}': must be a number between 1 and 100"
            ) from None
        if not (1 <= final_max_parallel <= 100):
            raise ValueError(
                f"Invalid TRAC_MAX_PARALLEL_REQUESTS '{max_parallel_raw}': must be a number between 1 and 100"
            )
    elif "max_parallel_requests" in fb:
        final_max_parallel = int(fb["max_parallel_requests"])
    else:
        final_max_parallel = 5

    max_batch_raw = os.getenv("TRAC_MAX_BATCH_SIZE")
    if max_batch_raw is not None:
        try:
            final_max_batch = int(max_batch_raw)
        except ValueError:
            raise ValueError(
                f"Invalid TRAC_MAX_BATCH_SIZE '{max_batch_raw}': must be a number between 1 and 10000"
            ) from None
        if not (1 <= final_max_batch <= 10000):
            raise ValueError(
                f"Invalid TRAC_MAX_BATCH_SIZE '{max_batch_raw}': must be a number between 1 and 10000"
            )
    elif "max_batch_size" in fb:
        final_max_batch = int(fb["max_batch_size"])
    else:
        final_max_batch = 500

    rpc_timeout_raw = os.getenv("TRAC_RPC_TIMEOUT")
    if rpc_timeout_raw is not None:
        try:
            final_rpc_timeout = int(rpc_timeout_raw)
        except ValueError:
            raise ValueError(
                f"Invalid TRAC_RPC_TIMEOUT '{rpc_timeout_raw}': must be a number between 5 and 300"
            ) from None
        if not (5 <= final_rpc_timeout <= 300):
            raise ValueError(
                f"Invalid TRAC_RPC_TIMEOUT '{rpc_timeout_raw}': must be a number between 5 and 300"
            )
    elif "rpc_timeout" in fb:
        final_rpc_timeout = int(fb["rpc_timeout"])
    else:
        final_rpc_timeout = 60

    # Off only on an explicit falsey value, so a typo'd or empty
    # TRAC_WRITE_GATE leaves the gate ON. A kill switch that fails open
    # on a malformed value disables the check exactly when nobody meant
    # to, and silently.
    write_gate_raw = os.getenv("TRAC_WRITE_GATE")
    if write_gate_raw is not None:
        final_write_gate = write_gate_raw.strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
    elif "write_gate" in fb:
        final_write_gate = bool(fb["write_gate"])
    else:
        final_write_gate = True

    # Ticket #110: env only, no YAML key. Unset is unrestricted; set but
    # empty is an error, never "deny all" -- an empty list that silently
    # locked everything out (or, worse, was read as "no list") is a
    # misconfiguration the operator must hear about at startup.
    allowed_raw = os.getenv("TRAC_ALLOWED_INSTANCES")
    allowed_entries = (
        parse_allowed_instances(allowed_raw, "TRAC_ALLOWED_INSTANCES")
        if allowed_raw is not None
        else None
    )

    config = Config(
        trac_url=trac_url,
        username=trac_username,
        password=trac_password,
        insecure=final_insecure,
        debug=final_debug,
        max_parallel_requests=final_max_parallel,
        max_batch_size=final_max_batch,
        rpc_timeout=final_rpc_timeout,
        oidc_only=oidc_only,
        write_gate=final_write_gate,
    )

    validate_config(config)

    if allowed_entries is not None:
        config.allowed_instances = normalize_allowed_instances(
            allowed_entries, config.trac_url
        )
        # Fail closed at startup: a default instance outside its own
        # allowlist would refuse every default-instance call.
        if config.trac_url not in config.allowed_instances:
            raise ValueError(
                f"Default instance {config.trac_url} is not in "
                "TRAC_ALLOWED_INSTANCES "
                f"({', '.join(sorted(config.allowed_instances))}). Add it "
                "to the list, or point TRAC_URL at an allowed instance."
            )

    return config
