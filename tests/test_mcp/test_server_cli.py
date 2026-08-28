"""Tests for trac-mcp-server CLI argument parsing (mcp/server.py).

Closes the gap recorded in docs/project/codebase/CONCERNS.md: "What's not
tested: CLI argument parsing, config override building, stdio transport
setup, version display."
"""

import argparse

import pytest

from trac_mcp_server import __version__
from trac_mcp_server.mcp.server import build_parser, server

# ---------------------------------------------------------------------------
# build_parser() basics
# ---------------------------------------------------------------------------


def test_build_parser_returns_parser():
    """build_parser() returns an ArgumentParser."""
    parser = build_parser()
    assert isinstance(parser, argparse.ArgumentParser)


def test_version_flag_prints_package_version(capsys):
    """--version exits 0 and prints the package version."""
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out


def test_no_args_all_transport_flags_default_to_none():
    """With no CLI flags, transport-related args are None (fall through to
    TRAC_MCP_* env / YAML / ServerConfig defaults in bootstrap_server_config)."""
    args = build_parser().parse_args([])
    assert args.transport is None
    assert args.host is None
    assert args.port is None
    assert args.path is None
    assert args.allow_unauthenticated is False


# ---------------------------------------------------------------------------
# --transport
# ---------------------------------------------------------------------------


def test_transport_accepts_stdio_and_http():
    parser = build_parser()
    assert (
        parser.parse_args(["--transport", "stdio"]).transport == "stdio"
    )
    assert (
        parser.parse_args(["--transport", "http"]).transport == "http"
    )


def test_transport_rejects_unknown_value(capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--transport", "sse"])
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err


# ---------------------------------------------------------------------------
# --host / --port / --path
# ---------------------------------------------------------------------------


def test_host_flag_parsed():
    args = build_parser().parse_args(["--host", "0.0.0.0"])
    assert args.host == "0.0.0.0"


def test_port_flag_parsed_as_int():
    args = build_parser().parse_args(["--port", "9090"])
    assert args.port == 9090
    assert isinstance(args.port, int)


def test_port_flag_rejects_non_numeric():
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--port", "not-a-number"])
    assert excinfo.value.code == 2


def test_path_flag_parsed():
    args = build_parser().parse_args(["--path", "/api/mcp"])
    assert args.path == "/api/mcp"


# ---------------------------------------------------------------------------
# --allow-unauthenticated
# ---------------------------------------------------------------------------


def test_allow_unauthenticated_flag_is_store_true():
    args = build_parser().parse_args(["--allow-unauthenticated"])
    assert args.allow_unauthenticated is True


# ---------------------------------------------------------------------------
# --read-only
# ---------------------------------------------------------------------------


def test_read_only_flag_is_store_true():
    args = build_parser().parse_args(["--read-only"])
    assert args.read_only is True


def test_read_only_flag_defaults_to_false():
    args = build_parser().parse_args([])
    assert args.read_only is False


# ---------------------------------------------------------------------------
# No --auth-token flag exists (auth_token must never be CLI-visible)
# ---------------------------------------------------------------------------


def test_no_auth_token_flag_exists():
    """--auth-token would leak the secret into the process list -- it must
    not exist as a CLI flag. Env var / YAML config only."""
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--auth-token", "secret"])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# Existing Trac connection flags still work (unregressed)
# ---------------------------------------------------------------------------


def test_existing_trac_flags_still_parsed():
    args = build_parser().parse_args(
        [
            "--url",
            "https://trac.example.com",
            "--username",
            "admin",
            "--password",
            "secret",
            "--insecure",
        ]
    )
    assert args.url == "https://trac.example.com"
    assert args.username == "admin"
    assert args.password == "secret"
    assert args.insecure is True


# ---------------------------------------------------------------------------
# Server version regression guard
# ---------------------------------------------------------------------------


def test_server_version_matches_project_version():
    """Server("trac-mcp-server", version=__version__) -- without this,
    StreamableHTTPSessionManager's create_initialization_options() falls
    back to reporting the mcp SDK version instead of the project version."""
    assert server.version == __version__
