# Unit tests for the extracted bootstrap_config() helper.
# Behavior parity for the lifespan integration is covered by tests/test_lifespan.py.

from unittest.mock import patch

import dotenv.main
import pytest

from trac_mcp_server.config_bootstrap import (
    bootstrap_config,
    bootstrap_server_config,
)

# The real find_dotenv, held before any test can patch the module attribute.
_REAL_FIND_DOTENV = dotenv.main.find_dotenv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_TRAC_URL = "https://trac.example.com/trac"
_VALID_USERNAME = "testuser"
_VALID_PASSWORD = "testpass"


def _set_valid_env(monkeypatch):
    """Set required Trac env vars to valid values."""
    monkeypatch.setenv("TRAC_URL", _VALID_TRAC_URL)
    monkeypatch.setenv("TRAC_USERNAME", _VALID_USERNAME)
    monkeypatch.setenv("TRAC_PASSWORD", _VALID_PASSWORD)


def _clear_trac_env(monkeypatch):
    """Remove all Trac credential env vars so no fallback is available."""
    for var in (
        "TRAC_URL",
        "TRAC_USERNAME",
        "TRAC_PASSWORD",
        "TRAC_INSECURE",
        "TRAC_DEBUG",
        "TRAC_MCP_CONFIG",
        "TRAC_ASSIST_CONFIG",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# 1. env-only path
# ---------------------------------------------------------------------------


def test_env_only_returns_config_and_sources(monkeypatch):
    """bootstrap_config(None) with env vars set returns Config + correct sources."""
    _set_valid_env(monkeypatch)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        config, sources = bootstrap_config(None)

    assert config.trac_url == _VALID_TRAC_URL
    assert config.username == _VALID_USERNAME
    assert config.password == _VALID_PASSWORD
    assert sources == ["environment variables"]


# ---------------------------------------------------------------------------
# 2. YAML config file path
# ---------------------------------------------------------------------------


def test_yaml_only_returns_yaml_source_label(monkeypatch, tmp_path):
    """When a YAML config file is found, its path appears first in sources."""
    # Write a minimal YAML config under tmp_path
    config_dir = tmp_path / ".trac_mcp"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text(
        "trac:\n"
        f"  url: {_VALID_TRAC_URL}\n"
        f"  username: {_VALID_USERNAME}\n"
        # password must come from env — YAML-only can't provide it in isolation
        # because load_config still requires it
    )

    # Provide password via env only (env-only for password)
    monkeypatch.setenv("TRAC_PASSWORD", _VALID_PASSWORD)
    # Clear URL/USERNAME from env so they come from YAML only
    monkeypatch.delenv("TRAC_URL", raising=False)
    monkeypatch.delenv("TRAC_USERNAME", raising=False)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[config_file],
        ),
        patch(
            "trac_mcp_server.config_bootstrap.load_hierarchical_config",
            return_value={
                "trac": {
                    "url": _VALID_TRAC_URL,
                    "username": _VALID_USERNAME,
                }
            },
        ),
    ):
        config, sources = bootstrap_config(None)

    assert sources[0] == f"config file: {config_file}"
    assert "environment variables" in sources
    assert config.trac_url == _VALID_TRAC_URL


# ---------------------------------------------------------------------------
# 2b. oidc_rpc_url from YAML relaxes the credential requirement (#269, L19)
# ---------------------------------------------------------------------------

_OIDC_RPC_URL = "https://trac.example.com/trac-api/login/xmlrpc"


def _bootstrap_oidc(
    monkeypatch, tmp_path, *, env_url, yaml_url, server=True
):
    """bootstrap_config() with a Trac URL, no TRAC_USERNAME/TRAC_PASSWORD,
    and the OIDC RPC URL in the environment, the YAML ``server`` section,
    both or neither. ``server`` is the daemon's call (lifespan.py); False
    is the CLI's (cli/convert.py), which has no OIDC path."""
    _clear_trac_env(monkeypatch)
    monkeypatch.delenv("TRAC_MCP_OIDC_RPC_URL", raising=False)
    monkeypatch.setenv("TRAC_URL", _VALID_TRAC_URL)
    if env_url:
        monkeypatch.setenv("TRAC_MCP_OIDC_RPC_URL", _OIDC_RPC_URL)
    raw: dict = {}
    if yaml_url:
        raw = {"server": {"oidc_rpc_url": _OIDC_RPC_URL}}
    config_file = tmp_path / "config.yaml"
    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[config_file] if raw else [],
        ),
        patch(
            "trac_mcp_server.config_bootstrap.load_hierarchical_config",
            return_value=raw,
        ),
    ):
        return bootstrap_config(None, oidc_rpc_url_from_yaml=server)


def test_oidc_rpc_url_only_in_yaml_is_oidc_only(monkeypatch, tmp_path):
    """Trac /trac_mcp_server #269 (L19): bootstrap_server_config() resolves
    the URL from the env var or YAML ``server.oidc_rpc_url``; the server's
    bootstrap_config() read only the env var, so a YAML-only deployment
    still demanded TRAC_USERNAME/TRAC_PASSWORD and was not ``oidc_only``."""
    config, _ = _bootstrap_oidc(
        monkeypatch, tmp_path, env_url=False, yaml_url=True
    )
    assert config.oidc_only is True
    assert config.username == ""
    assert config.password == ""


def test_cli_bootstrap_ignores_a_yaml_only_oidc_rpc_url(
    monkeypatch, tmp_path
):
    """The CLI (cli/convert.py) has no OIDC path: a YAML-only
    ``server.oidc_rpc_url`` must not relax its credential requirement, as
    before #269."""
    with pytest.raises(ValueError, match="Trac username not found"):
        _bootstrap_oidc(
            monkeypatch,
            tmp_path,
            env_url=False,
            yaml_url=True,
            server=False,
        )


def test_cli_bootstrap_still_honours_the_env_var(monkeypatch, tmp_path):
    """What the CLI did before #269: the env var relaxed the check."""
    config, _ = _bootstrap_oidc(
        monkeypatch,
        tmp_path,
        env_url=True,
        yaml_url=False,
        server=False,
    )
    assert config.oidc_only is True


def test_oidc_rpc_url_only_in_env_is_oidc_only(monkeypatch, tmp_path):
    config, _ = _bootstrap_oidc(
        monkeypatch, tmp_path, env_url=True, yaml_url=False
    )
    assert config.oidc_only is True


def test_no_oidc_rpc_url_still_requires_credentials(
    monkeypatch, tmp_path
):
    with pytest.raises(ValueError, match="username not found"):
        _bootstrap_oidc(
            monkeypatch, tmp_path, env_url=False, yaml_url=False
        )


def test_oidc_rpc_url_in_yaml_keeps_real_credentials_selftest(
    monkeypatch, tmp_path
):
    """The URL beside real credentials leaves ``oidc_only`` false."""
    monkeypatch.setenv("TRAC_USERNAME", _VALID_USERNAME)
    monkeypatch.setenv("TRAC_PASSWORD", _VALID_PASSWORD)
    monkeypatch.delenv("TRAC_MCP_OIDC_RPC_URL", raising=False)
    monkeypatch.setenv("TRAC_URL", _VALID_TRAC_URL)
    raw = {"server": {"oidc_rpc_url": _OIDC_RPC_URL}}
    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[tmp_path / "config.yaml"],
        ),
        patch(
            "trac_mcp_server.config_bootstrap.load_hierarchical_config",
            return_value=raw,
        ),
    ):
        config, _ = bootstrap_config(None, oidc_rpc_url_from_yaml=True)
    assert config.oidc_only is False
    assert config.username == _VALID_USERNAME


# ---------------------------------------------------------------------------
# 3. CLI overrides win over env
# ---------------------------------------------------------------------------


def test_cli_overrides_win_over_env(monkeypatch):
    """CLI overrides take precedence over env vars; 'CLI arguments' in sources."""
    # Set env vars to different values
    monkeypatch.setenv("TRAC_URL", "https://env.example.com")
    monkeypatch.setenv("TRAC_USERNAME", "env_user")
    monkeypatch.setenv("TRAC_PASSWORD", "env_pass")

    cli_overrides = {
        "url": "https://override.example.com",
        "username": "cli_user",
        "password": "cli_pass",
    }

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        config, sources = bootstrap_config(cli_overrides)

    assert config.trac_url == "https://override.example.com"
    assert config.username == "cli_user"
    assert "CLI arguments" in sources


# ---------------------------------------------------------------------------
# 4. Missing credentials → ValueError
# ---------------------------------------------------------------------------


def test_missing_credentials_raises_valueerror(monkeypatch, tmp_path):
    """bootstrap_config raises ValueError when required fields are absent."""
    _clear_trac_env(monkeypatch)
    # Change CWD to tmp_path so no real config file is found
    monkeypatch.chdir(tmp_path)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        with pytest.raises(ValueError):
            bootstrap_config(None)


# ---------------------------------------------------------------------------
# 5. Empty dict is treated the same as None (no 'CLI arguments' label)
# ---------------------------------------------------------------------------


def test_empty_cli_overrides_dict_does_not_add_source_label(
    monkeypatch,
):
    """Passing {} is identical to None — 'CLI arguments' must NOT appear in sources."""
    _set_valid_env(monkeypatch)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        _, sources = bootstrap_config({})

    assert "CLI arguments" not in sources
    assert "environment variables" in sources


# ---------------------------------------------------------------------------
# 6. Source list ordering: YAML file first, env vars last
# ---------------------------------------------------------------------------


def test_sources_list_ordering(monkeypatch, tmp_path):
    """With YAML + env (no CLI), sources order must be [config file, env vars]."""
    _set_valid_env(monkeypatch)
    fake_config_path = tmp_path / "config.yaml"

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[fake_config_path],
        ),
        patch(
            "trac_mcp_server.config_bootstrap.load_hierarchical_config",
            return_value={
                "trac": {
                    "url": _VALID_TRAC_URL,
                    "username": _VALID_USERNAME,
                    "password": _VALID_PASSWORD,
                }
            },
        ),
    ):
        _, sources = bootstrap_config(None)

    assert sources == [
        f"config file: {fake_config_path}",
        "environment variables",
    ]


# ---------------------------------------------------------------------------
# 7. Helper does not write to stdout or stderr
# ---------------------------------------------------------------------------


def test_helper_does_not_write_to_stderr_or_stdout(monkeypatch, capsys):
    """bootstrap_config() must produce no stdout/stderr output (data only)."""
    _set_valid_env(monkeypatch)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        bootstrap_config(None)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ---------------------------------------------------------------------------
# bootstrap_server_config() tests
# ---------------------------------------------------------------------------


def _clear_server_env(monkeypatch):
    """Remove all TRAC_MCP_* server env vars so no fallback is available."""
    for var in (
        "TRAC_MCP_TRANSPORT",
        "TRAC_MCP_HOST",
        "TRAC_MCP_PORT",
        "TRAC_MCP_PATH",
        "TRAC_MCP_AUTH_TOKEN",
        "TRAC_MCP_OIDC_RPC_URL",
        "TRAC_MCP_READ_ONLY",
        "TRAC_MCP_DISABLED_TOOLS",
        "TRAC_MCP_ALLOWED_HOSTS",
        "TRAC_MCP_ALLOWED_ORIGINS",
        "TRAC_MCP_FILE_ACCESS",
        "TRAC_MCP_MAX_INLINE_BYTES",
    ):
        monkeypatch.delenv(var, raising=False)


def test_server_config_defaults_with_no_sources(monkeypatch):
    """No CLI/env/YAML -> ServerConfig defaults (stdio, 127.0.0.1, 8080, /mcp)."""
    _clear_server_env(monkeypatch)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        server_config = bootstrap_server_config(None)

    assert server_config.transport == "stdio"
    assert server_config.host == "127.0.0.1"
    assert server_config.port == 8080
    assert server_config.path == "/mcp"
    assert server_config.auth_token is None
    assert server_config.oidc_rpc_url is None


def test_env_vars_populate_server_config(monkeypatch):
    """TRAC_MCP_* env vars populate ServerConfig fields."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_TRANSPORT", "http")
    monkeypatch.setenv("TRAC_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("TRAC_MCP_PORT", "9090")
    monkeypatch.setenv("TRAC_MCP_PATH", "/api/mcp")
    monkeypatch.setenv("TRAC_MCP_AUTH_TOKEN", "envtoken")

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        server_config = bootstrap_server_config(None)

    assert server_config.transport == "http"
    assert server_config.host == "0.0.0.0"
    assert server_config.port == 9090
    assert server_config.path == "/api/mcp"
    assert server_config.auth_token == "envtoken"


def test_env_var_populates_oidc_rpc_url(monkeypatch):
    """TRAC_MCP_OIDC_RPC_URL populates ServerConfig.oidc_rpc_url."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_TRANSPORT", "http")
    monkeypatch.setenv(
        "TRAC_MCP_OIDC_RPC_URL",
        "https://trac.example.com/trac-api/login/xmlrpc",
    )

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        server_config = bootstrap_server_config(None)

    assert (
        server_config.oidc_rpc_url
        == "https://trac.example.com/trac-api/login/xmlrpc"
    )


def test_cli_overrides_win_over_env_for_server_config(monkeypatch):
    """CLI overrides take precedence over TRAC_MCP_* env vars."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("TRAC_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("TRAC_MCP_PORT", "1111")

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        server_config = bootstrap_server_config(
            {
                "transport": "http",
                "host": "127.0.0.1",
                "port": 9999,
                "allow_unauthenticated": True,
            }
        )

    assert server_config.transport == "http"
    assert server_config.host == "127.0.0.1"
    assert server_config.port == 9999
    assert server_config.allow_unauthenticated is True


def test_yaml_server_section_used_as_fallback(monkeypatch):
    """YAML server: section values are used when CLI/env are unset."""
    _clear_server_env(monkeypatch)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=["/fake/config.yaml"],
        ),
        patch(
            "trac_mcp_server.config_bootstrap.load_hierarchical_config",
            return_value={
                "server": {
                    "transport": "http",
                    "host": "127.0.0.1",
                    "port": 8765,
                    "path": "/mcp",
                }
            },
        ),
    ):
        server_config = bootstrap_server_config(None)

    assert server_config.transport == "http"
    assert server_config.port == 8765


def test_auth_token_not_accepted_from_cli_overrides(monkeypatch):
    """auth_token is intentionally not a recognized cli_overrides key --
    it must come from TRAC_MCP_AUTH_TOKEN or YAML, never the CLI, so it
    never appears in the process list."""
    _clear_server_env(monkeypatch)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        server_config = bootstrap_server_config(
            {"auth_token": "should-be-ignored"}
        )

    assert server_config.auth_token is None


def test_invalid_transport_raises(monkeypatch):
    """An unrecognized transport value raises ValueError."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_TRANSPORT", "sse")

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        with pytest.raises(ValueError, match="Invalid transport"):
            bootstrap_server_config(None)


def test_invalid_port_raises(monkeypatch):
    """A non-numeric TRAC_MCP_PORT raises ValueError."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_PORT", "not-a-number")

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        with pytest.raises(ValueError, match="Invalid port"):
            bootstrap_server_config(None)


def test_out_of_range_port_raises(monkeypatch):
    """An out-of-range TRAC_MCP_PORT raises ValueError."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_PORT", "99999")

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        with pytest.raises(ValueError, match="Invalid port"):
            bootstrap_server_config(None)


def test_http_non_loopback_no_token_raises(monkeypatch):
    """Bind-safety validation runs at the end of bootstrap_server_config()."""
    _clear_server_env(monkeypatch)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        with pytest.raises(
            ValueError, match="Refusing to bind non-loopback host"
        ):
            bootstrap_server_config(
                {"transport": "http", "host": "0.0.0.0"}
            )


# ---------------------------------------------------------------------------
# file_access / max_inline_bytes (ticket #111)
# ---------------------------------------------------------------------------


def _bootstrap_server(overrides=None, yaml_server=None):
    files = ["/fake/config.yaml"] if yaml_server else []
    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=files,
        ),
        patch(
            "trac_mcp_server.config_bootstrap.load_hierarchical_config",
            return_value={"server": yaml_server or {}},
        ),
    ):
        return bootstrap_server_config(overrides)


@pytest.mark.parametrize(
    "transport,expected", [("stdio", "local"), ("http", "off")]
)
def test_file_access_defaults_by_transport(
    monkeypatch, transport, expected
):
    """An http server does not share its caller's filesystem, so path
    arguments default off there; stdio runs on the caller's machine."""
    _clear_server_env(monkeypatch)
    config = _bootstrap_server({"transport": transport})
    assert config.file_access == expected
    assert config.max_inline_bytes == 5 * 1024 * 1024


def test_file_access_env_overrides_transport_default(monkeypatch):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_FILE_ACCESS", "local")
    assert (
        _bootstrap_server({"transport": "http"}).file_access == "local"
    )


def test_file_access_cli_beats_env(monkeypatch):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_FILE_ACCESS", "local")
    config = _bootstrap_server(
        {"transport": "stdio", "file_access": "off"}
    )
    assert config.file_access == "off"


def test_file_access_yaml_fallback(monkeypatch):
    _clear_server_env(monkeypatch)
    config = _bootstrap_server(
        yaml_server={"transport": "http", "file_access": "local"}
    )
    assert config.file_access == "local"


def test_file_access_invalid_value_rejected(monkeypatch):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_FILE_ACCESS", "on")
    with pytest.raises(ValueError, match="file_access"):
        _bootstrap_server({"transport": "http"})


def test_max_inline_bytes_from_env_and_yaml(monkeypatch):
    _clear_server_env(monkeypatch)
    config = _bootstrap_server(yaml_server={"max_inline_bytes": 1000})
    assert config.max_inline_bytes == 1000
    monkeypatch.setenv("TRAC_MCP_MAX_INLINE_BYTES", "2000")
    config = _bootstrap_server(yaml_server={"max_inline_bytes": 1000})
    assert config.max_inline_bytes == 2000


@pytest.mark.parametrize("raw", ["lots", "0", "-5"])
def test_max_inline_bytes_invalid_rejected(monkeypatch, raw):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_MAX_INLINE_BYTES", raw)
    with pytest.raises(ValueError):
        _bootstrap_server()


@pytest.mark.parametrize(
    "line,expected",
    [
        ("file_access: off", "off"),
        ("file_access: on", "local"),
        ('file_access: "off"', "off"),
        ("file_access: local", "local"),
    ],
)
def test_file_access_survives_real_yaml_parsing(
    monkeypatch, line, expected
):
    """Through the real loader, not a dict: YAML 1.1 reads a bare off/on
    as a boolean, and the documented `file_access: off` must still start
    the server (review finding on #111)."""
    import yaml

    from trac_mcp_server.config_loader import ConfigLoader

    _clear_server_env(monkeypatch)
    server = yaml.load(
        f"transport: http\n{line}\n", Loader=ConfigLoader
    )
    assert _bootstrap_server(yaml_server=server).file_access == expected


# --- read_only: CLI > env > YAML > default (False) ---


def test_read_only_defaults_to_false(monkeypatch):
    _clear_server_env(monkeypatch)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        server_config = bootstrap_server_config(None)

    assert server_config.read_only is False


@pytest.mark.parametrize(
    "value", ["true", "1", "yes", "on", "TRUE", "True"]
)
def test_read_only_env_truthy_values(monkeypatch, value):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_READ_ONLY", value)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        server_config = bootstrap_server_config(None)

    assert server_config.read_only is True


@pytest.mark.parametrize(
    "value", ["false", "0", "no", "off", "OFF", " no "]
)
def test_read_only_env_falsy_values(monkeypatch, value):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_READ_ONLY", value)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        server_config = bootstrap_server_config(None)

    assert server_config.read_only is False


def test_read_only_cli_override_wins_over_env(monkeypatch):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_READ_ONLY", "false")

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=[],
        ),
    ):
        server_config = bootstrap_server_config({"read_only": True})

    assert server_config.read_only is True


def test_read_only_falls_back_to_yaml_when_env_unset(monkeypatch):
    _clear_server_env(monkeypatch)

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=["/fake/config.yaml"],
        ),
        patch(
            "trac_mcp_server.config_bootstrap.load_hierarchical_config",
            return_value={"server": {"read_only": True}},
        ),
    ):
        server_config = bootstrap_server_config(None)

    assert server_config.read_only is True


def test_read_only_env_overrides_yaml(monkeypatch):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_READ_ONLY", "false")

    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=["/fake/config.yaml"],
        ),
        patch(
            "trac_mcp_server.config_bootstrap.load_hierarchical_config",
            return_value={"server": {"read_only": True}},
        ),
    ):
        server_config = bootstrap_server_config(None)

    assert server_config.read_only is False


# --- disabled_tools: env (CSV) > YAML > default ([]) ---


def _bootstrap_with_yaml(yaml_data):
    with (
        patch("trac_mcp_server.config_bootstrap.load_dotenv"),
        patch(
            "trac_mcp_server.config_bootstrap.discover_config_files",
            return_value=["/fake/config.yaml"] if yaml_data else [],
        ),
        patch(
            "trac_mcp_server.config_bootstrap.load_hierarchical_config",
            return_value=yaml_data or {},
        ),
    ):
        return bootstrap_server_config(None)


def test_disabled_tools_defaults_to_empty(monkeypatch):
    _clear_server_env(monkeypatch)
    assert _bootstrap_with_yaml(None).disabled_tools == []


def test_disabled_tools_env_csv(monkeypatch):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv(
        "TRAC_MCP_DISABLED_TOOLS",
        " ticket_render_check, ticket_comment_history ,",
    )
    assert _bootstrap_with_yaml(None).disabled_tools == [
        "ticket_render_check",
        "ticket_comment_history",
    ]


def test_disabled_tools_from_yaml(monkeypatch):
    _clear_server_env(monkeypatch)
    cfg = _bootstrap_with_yaml(
        {"server": {"disabled_tools": ["ticket_render_check"]}}
    )
    assert cfg.disabled_tools == ["ticket_render_check"]


def test_disabled_tools_env_overrides_yaml(monkeypatch):
    """A set value replaces the YAML list, it does not extend it."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_DISABLED_TOOLS", "ticket_delete")
    cfg = _bootstrap_with_yaml(
        {"server": {"disabled_tools": ["ticket_render_check"]}}
    )
    assert cfg.disabled_tools == ["ticket_delete"]


@pytest.mark.parametrize("value", ["", "  ", " , "])
def test_disabled_tools_empty_env_keeps_yaml(monkeypatch, value):
    """An empty value counts as unset, so a compose file's
    ``${TRAC_MCP_DISABLED_TOOLS:-}`` does not bring the tools back."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_DISABLED_TOOLS", value)
    cfg = _bootstrap_with_yaml(
        {
            "server": {
                "disabled_tools": ["ticket_delete", "ticket_update"]
            }
        }
    )
    assert cfg.disabled_tools == ["ticket_delete", "ticket_update"]


def test_disabled_tools_yaml_names_normalised(monkeypatch):
    """YAML entries are stripped and blanks dropped, like env entries."""
    _clear_server_env(monkeypatch)
    cfg = _bootstrap_with_yaml(
        {"server": {"disabled_tools": [" ticket_delete", "", "  "]}}
    )
    assert cfg.disabled_tools == ["ticket_delete"]


@pytest.mark.parametrize("value", ["", "  "])
def test_read_only_empty_env_keeps_yaml(monkeypatch, value):
    """An empty value counts as unset; it must not override YAML
    ``read_only: true`` with False and expose every write tool."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_READ_ONLY", value)
    cfg = _bootstrap_with_yaml({"server": {"read_only": True}})
    assert cfg.read_only is True


def test_read_only_env_value_is_stripped(monkeypatch):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_READ_ONLY", "true ")
    assert _bootstrap_with_yaml(None).read_only is True


@pytest.mark.parametrize("value", ["y", "random", "enabled"])
def test_read_only_unrecognised_env_value_refused(monkeypatch, value):
    """A value that is neither true nor false is refused at startup
    instead of being taken as false."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_READ_ONLY", value)
    with pytest.raises(ValueError, match="Invalid TRAC_MCP_READ_ONLY"):
        _bootstrap_with_yaml({"server": {"read_only": True}})


def test_env_vars_populate_allowed_hosts_and_origins(monkeypatch):
    """TRAC_MCP_ALLOWED_HOSTS/ORIGINS are comma-separated lists -- needed
    whenever a client reaches this server by a name other than what it's
    bound to, e.g. a docker-compose service name."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv(
        "TRAC_MCP_ALLOWED_HOSTS", "trac-mcp:8080, trac-mcp:*"
    )
    monkeypatch.setenv(
        "TRAC_MCP_ALLOWED_ORIGINS", "http://trac-mcp:8080"
    )

    config = _bootstrap_server()

    assert config.allowed_hosts == ["trac-mcp:8080", "trac-mcp:*"]
    assert config.allowed_origins == ["http://trac-mcp:8080"]


def test_allowed_hosts_env_blank_entries_dropped(monkeypatch):
    """A trailing comma or stray whitespace must not produce an empty-
    string allow-list entry that could never match a real Host header."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_ALLOWED_HOSTS", "trac-mcp:8080,, ")

    assert _bootstrap_server().allowed_hosts == ["trac-mcp:8080"]


def test_allowed_hosts_env_replaces_yaml(monkeypatch):
    """A set value replaces the YAML list (env > YAML), it does not extend it."""
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_ALLOWED_HOSTS", "from-env:8080")

    config = _bootstrap_server(
        yaml_server={"allowed_hosts": ["from-yaml:8080"]}
    )

    assert config.allowed_hosts == ["from-env:8080"]


@pytest.mark.parametrize("value", [None, "", "  ", " , "])
@pytest.mark.parametrize(
    "env_key,yaml_key",
    [
        ("TRAC_MCP_ALLOWED_HOSTS", "allowed_hosts"),
        ("TRAC_MCP_ALLOWED_ORIGINS", "allowed_origins"),
    ],
)
def test_allowed_lists_unset_or_empty_env_keeps_yaml(
    monkeypatch, env_key, yaml_key, value
):
    """Unset, empty or blank counts as unset, so a compose file's
    ``${TRAC_MCP_ALLOWED_HOSTS:-}`` does not wipe the YAML list."""
    _clear_server_env(monkeypatch)
    if value is not None:
        monkeypatch.setenv(env_key, value)

    config = _bootstrap_server(
        yaml_server={yaml_key: ["from-yaml:8080"]}
    )

    assert getattr(config, yaml_key) == ["from-yaml:8080"]


def test_allowed_hosts_defaults_to_empty_list(monkeypatch):
    _clear_server_env(monkeypatch)

    config = _bootstrap_server()

    assert config.allowed_hosts == []
    assert config.allowed_origins == []


# ---------------------------------------------------------------------------
# .env is searched from the working directory (Trac /trac_mcp_server #276)
# ---------------------------------------------------------------------------


def _enable_real_dotenv(monkeypatch, tmp_path):
    """Isolated config sources, but with the real ``.env`` search live.

    The offline run disables ``.env`` (``PYTHON_DOTENV_DISABLED`` and, for
    a python-dotenv without that switch, a patched ``find_dotenv``); put
    both back so the lookup under test runs for real.
    """
    if getattr(_REAL_FIND_DOTENV, "__name__", "") == "<lambda>":
        pytest.skip("find_dotenv was already patched at import time")
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.setattr(dotenv.main, "find_dotenv", _REAL_FIND_DOTENV)
    monkeypatch.delenv("TRAC_IDENTITIES", raising=False)
    # setenv then delenv records "unset", so the variable load_dotenv
    # writes into os.environ is removed at teardown.
    monkeypatch.setenv("TRAC_MCP_PATH", "x")
    monkeypatch.delenv("TRAC_MCP_PATH")


def test_dotenv_is_found_in_the_working_directory(
    isolated_config, monkeypatch
):
    _clear_server_env(monkeypatch)
    _enable_real_dotenv(monkeypatch, isolated_config)
    (isolated_config / ".env").write_text(
        "TRAC_MCP_PATH=/from-cwd-dotenv\n"
    )

    assert bootstrap_server_config().path == "/from-cwd-dotenv"


def test_dotenv_is_found_in_a_parent_of_the_working_directory(
    isolated_config, monkeypatch
):
    _clear_server_env(monkeypatch)
    _enable_real_dotenv(monkeypatch, isolated_config)
    (isolated_config / ".env").write_text(
        "TRAC_MCP_PATH=/from-parent-dotenv\n"
    )
    sub = isolated_config / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)

    assert bootstrap_server_config().path == "/from-parent-dotenv"


def test_dotenv_disabled_switch_still_wins(
    isolated_config, monkeypatch
):
    _clear_server_env(monkeypatch)
    _enable_real_dotenv(monkeypatch, isolated_config)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    (isolated_config / ".env").write_text(
        "TRAC_MCP_PATH=/from-cwd-dotenv\n"
    )

    assert bootstrap_server_config().path == "/mcp"


# ---------------------------------------------------------------------------
# Empty / non-positive settings (Trac /trac_mcp_server #276)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_port_env_counts_as_unset(monkeypatch, raw):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_PORT", raw)
    assert _bootstrap_server().port == 8080


def test_empty_port_env_falls_back_to_yaml_port(monkeypatch):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_PORT", "")
    assert _bootstrap_server(yaml_server={"port": 9123}).port == 9123


@pytest.mark.parametrize("raw", ["", "  "])
def test_empty_inline_bytes_env_counts_as_unset(monkeypatch, raw):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_MAX_INLINE_BYTES", raw)
    assert _bootstrap_server().max_inline_bytes == 5 * 1024 * 1024
    assert (
        _bootstrap_server(
            yaml_server={"max_inline_bytes": 1000}
        ).max_inline_bytes
        == 1000
    )


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_non_positive_inline_bytes_env_names_the_setting(
    monkeypatch, raw
):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("TRAC_MCP_MAX_INLINE_BYTES", raw)
    with pytest.raises(
        ValueError,
        match="TRAC_MCP_MAX_INLINE_BYTES.*must be a positive number of bytes",
    ):
        _bootstrap_server()


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_inline_bytes_yaml_names_the_setting(
    monkeypatch, value
):
    _clear_server_env(monkeypatch)
    with pytest.raises(
        ValueError,
        match="max_inline_bytes",
    ):
        _bootstrap_server(yaml_server={"max_inline_bytes": value})
