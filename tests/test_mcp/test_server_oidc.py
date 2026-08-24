"""Tests for OIDC per-user auth wiring in mcp/server.py -- _resolve_client()
and its effect on handle_call_tool()/handle_read_resource().

These run outside a real ASGI request, so server.request_context.request
raises LookupError exactly like a stdio call would -- that's the same
"no token available" case as a genuinely missing header, and it must fail
the same way: no fallback to any shared identity. The "header present"
path is covered end-to-end in test_http_app.py::TestOidcTokenRequired plus
the Config/TracClient wiring unit tests in test_oidc.py.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from pydantic_core import Url

from trac_mcp_server.config import Config
from trac_mcp_server.mcp.oidc import OidcClientCache
from trac_mcp_server.mcp.server import (
    PING_SPEC,
    handle_call_tool,
    handle_read_resource,
    set_instances,
    set_oidc_cache,
    set_registry,
)
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _clean_globals():
    """Every test starts and ends with no OIDC cache / registry installed,
    regardless of what a failing assertion left behind."""
    set_registry(ToolRegistry([PING_SPEC] + ALL_SPECS))
    set_oidc_cache(None)
    yield
    set_oidc_cache(None)
    set_registry(None)
    set_instances(None)


def _oidc_cache() -> OidcClientCache:
    base_config = Config(
        trac_url="https://trac.example.com/trac",
        username="service-account",
        password="service-secret",
    )
    return OidcClientCache(
        base_config, "https://trac.example.com/trac-api/login/xmlrpc"
    )


class TestResolveClientOidcMode:
    """handle_call_tool()'s client resolution when _oidc_cache is set."""

    def test_no_request_context_is_treated_as_missing_token(self):
        """Outside an ASGI request (as happens here, and as stdio always
        is), there is no header to read -- this must fail exactly like a
        missing header would, never fall back to get_instances()."""
        set_oidc_cache(_oidc_cache())

        with patch(
            "trac_mcp_server.mcp.server.get_instances"
        ) as mock_get_instances:
            result = asyncio.run(handle_call_tool("ping", {}))

        mock_get_instances.assert_not_called()
        assert result.isError
        assert "oidc" in result.content[0].text.lower()

    def test_instance_argument_rejected_in_oidc_mode(self):
        """Multi-instance addressing is out of scope for OIDC per-user
        auth (see mcp/oidc.py) -- must fail loudly, not silently ignore
        the argument and hit the wrong (or a misleadingly "right") URL."""
        set_oidc_cache(_oidc_cache())

        with patch(
            "trac_mcp_server.mcp.server.get_instances"
        ) as mock_get_instances:
            result = asyncio.run(
                handle_call_tool("ping", {"instance": "/other-project"})
            )

        mock_get_instances.assert_not_called()
        assert result.isError
        assert "instance" in result.content[0].text.lower()

    def test_instance_default_is_allowed_in_oidc_mode(self):
        """instance='default' is equivalent to omitting it -- must not be
        rejected by the same guard as a real cross-project instance."""
        set_oidc_cache(_oidc_cache())

        result = asyncio.run(
            handle_call_tool("ping", {"instance": "default"})
        )

        # Still fails (no request context to read a token from), but for
        # the "missing token" reason, not "instance not supported".
        assert result.isError
        assert "instance" not in result.content[0].text.lower()

    def test_oidc_cache_none_uses_normal_instance_registry(self):
        """Regression guard: when OIDC mode isn't configured, behavior is
        completely unchanged from before this feature existed."""
        mock_client = MagicMock()
        mock_registry = MagicMock()

        async def fake_call_tool(name, args, client):
            import mcp.types as types

            assert client is mock_client
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="ok")]
            )

        mock_registry.call_tool = MagicMock(side_effect=fake_call_tool)
        set_registry(mock_registry)

        with patch(
            "trac_mcp_server.mcp.server.get_instances"
        ) as mock_get_instances:
            mock_get_instances.return_value.get_client.return_value = (
                mock_client
            )
            result = asyncio.run(handle_call_tool("ping", {}))

        assert not result.isError


class TestResolveClientOidcModeWikiResource:
    """handle_read_resource() goes through the same _resolve_client()."""

    def test_wiki_resource_rejects_missing_token_in_oidc_mode(self):
        set_oidc_cache(_oidc_cache())

        with patch(
            "trac_mcp_server.mcp.server.get_instances"
        ) as mock_get_instances:
            result = asyncio.run(
                handle_read_resource(Url("trac://wiki/WikiStart"))
            )

        mock_get_instances.assert_not_called()
        assert "oidc" in result.lower() or "token" in result.lower()
