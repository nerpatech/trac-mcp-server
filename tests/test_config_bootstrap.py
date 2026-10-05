# Unit tests for the extracted bootstrap_config() helper.
# Behavior parity for the lifespan integration is covered by tests/test_lifespan.py.

from unittest.mock import patch

import pytest

from trac_mcp_server.config_bootstrap import (
    bootstrap_config,
    bootstrap_server_config,
)

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
