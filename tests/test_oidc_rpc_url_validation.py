"""Startup validation of TRAC_MCP_OIDC_RPC_URL: every request forwards a
user's access token to this URL, and an OIDC-only deployment has no startup
self-test that would notice a bad value."""

import pytest

from trac_mcp_server.config import validate_server_config
from trac_mcp_server.config_bootstrap import bootstrap_server_config
from trac_mcp_server.config_schema import ServerConfig


@pytest.fixture(autouse=True)
def _clean_server_env(monkeypatch):
    """bootstrap_server_config() reads the real environment: a token set on the
    host would hit the mutual exclusion with OIDC before anything tested here."""
    for var in (
        "TRAC_MCP_TRANSPORT",
        "TRAC_MCP_HOST",
        "TRAC_MCP_PORT",
        "TRAC_MCP_PATH",
        "TRAC_MCP_AUTH_TOKEN",
        "TRAC_MCP_OIDC_RPC_URL",
        "TRAC_MCP_OIDC_ALLOW_INSECURE_HTTP",
        "TRAC_IDENTITIES",
    ):
        monkeypatch.delenv(var, raising=False)


def _config(url, **kwargs) -> ServerConfig:
    return ServerConfig(
        transport="http", host="0.0.0.0", oidc_rpc_url=url, **kwargs
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://trac.example.com/trac-api/login/xmlrpc",
        "http://localhost:8081/trac-api/login/xmlrpc",
        "http://127.0.0.1/trac-api/login/xmlrpc",
        "http://[::1]:8081/trac-api/login/xmlrpc",
    ],
)
def test_https_and_loopback_are_accepted(url):
    validate_server_config(_config(url))


@pytest.mark.parametrize(
    "url",
    ["http://trac.example.com/trac-api", "http://10.0.0.5/trac-api"],
)
def test_plain_http_is_refused(url):
    with pytest.raises(ValueError, match="unencrypted"):
        validate_server_config(_config(url))


def test_plain_http_is_accepted_with_the_explicit_opt_out():
    validate_server_config(
        _config(
            "http://trac.internal/trac-api",
            oidc_allow_insecure_http=True,
        )
    )


@pytest.mark.parametrize(
    "url",
    [
        "trac.example.com/trac-api",
        "ftp://trac.example.com/trac-api",
        "https://",
        "https:///trac-api",
        "https://trac.example.com:port/trac-api",
    ],
)
@pytest.mark.parametrize("opt_out", [False, True])
def test_malformed_urls_are_refused_with_or_without_the_opt_out(
    url, opt_out
):
    with pytest.raises(
        ValueError, match="Invalid TRAC_MCP_OIDC_RPC_URL"
    ):
        validate_server_config(
            _config(url, oidc_allow_insecure_http=opt_out)
        )


def test_refused_outside_the_http_transport():
    """load_config() relaxes the credential check for this variable, and only
    the http transport reads per-request tokens: a stdio server would start
    and then fail every call."""
    with pytest.raises(ValueError, match="transport is 'stdio'"):
        validate_server_config(
            ServerConfig(
                transport="stdio",
                oidc_rpc_url="https://trac.example.com/trac-api",
            )
        )


@pytest.mark.parametrize(
    "value, expected",
    [("true", True), ("1", True), ("false", False), ("", False)],
)
def test_opt_out_is_read_from_the_environment(
    monkeypatch, value, expected
):
    monkeypatch.setenv("TRAC_MCP_TRANSPORT", "http")
    monkeypatch.setenv(
        "TRAC_MCP_OIDC_RPC_URL", "https://trac.example.com/trac-api"
    )
    monkeypatch.setenv("TRAC_MCP_OIDC_ALLOW_INSECURE_HTTP", value)
    server_config = bootstrap_server_config({})
    assert server_config.oidc_allow_insecure_http is expected


def test_opt_out_is_off_by_default(monkeypatch):
    monkeypatch.setenv("TRAC_MCP_TRANSPORT", "http")
    monkeypatch.setenv(
        "TRAC_MCP_OIDC_RPC_URL", "http://trac.example.com/trac-api"
    )
    with pytest.raises(ValueError, match="unencrypted"):
        bootstrap_server_config({})
