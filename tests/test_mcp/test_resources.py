"""Tests for MCP server resource handlers.

Verifies resource routing and error handling in the MCP server,
separate from the wiki resource module unit tests.
"""

import asyncio
from unittest.mock import patch

import pytest
from pydantic_core import Url

from trac_mcp_server.mcp import server as server_module
from trac_mcp_server.mcp.server import (
    PING_SPEC,
    handle_list_resources,
    handle_read_resource,
    set_registry,
)
from trac_mcp_server.mcp.tools import ALL_SPECS, ToolRegistry


@pytest.fixture(autouse=True)
def _unfiltered_registry():
    """Wiki resources are served when wiki_get is: serve every tool.

    Filtered registries are covered by test_resource_permissions.py.
    """
    previous = server_module._registry
    set_registry(ToolRegistry([PING_SPEC] + ALL_SPECS))
    try:
        yield
    finally:
        set_registry(previous)


# ---------------------------------------------------------------------------
# handle_list_resources tests
# ---------------------------------------------------------------------------


class TestListResources:
    """Test handle_list_resources MCP handler."""

    def test_returns_wiki_resources(self):
        """list_resources returns the index only; the page is a template."""
        result = asyncio.run(handle_list_resources())
        assert len(result) == 1

    def test_page_template_is_not_a_resource(self):
        """list_resources does not list trac://wiki/{page_name}."""
        result = asyncio.run(handle_list_resources())

        assert not any("page_name" in str(r.uri) for r in result)

    def test_has_index_resource(self):
        """list_resources includes wiki index resource."""
        result = asyncio.run(handle_list_resources())

        index_resource = next(
            (r for r in result if "_index" in str(r.uri)), None
        )
        assert index_resource is not None
        assert str(index_resource.uri) == "trac://wiki/_index"
        assert index_resource.name == "Wiki Page Index"


# ---------------------------------------------------------------------------
# handle_read_resource routing tests
# ---------------------------------------------------------------------------


class TestReadResourceRouting:
    """Test handle_read_resource URI routing."""

    @patch("trac_mcp_server.mcp.server.handle_read_wiki_resource")
    @patch("trac_mcp_server.mcp.server.get_instances")
    def test_routes_wiki_uri_to_wiki_handler(
        self, mock_get_instances, mock_wiki_handler
    ):
        """trac://wiki/* URIs route to wiki handler."""
        mock_wiki_handler.return_value = "Wiki content"
        mock_client = (
            mock_get_instances.return_value.get_client.return_value
        )

        uri = Url("trac://wiki/WikiStart")
        result = asyncio.run(handle_read_resource(uri))

        mock_wiki_handler.assert_called_once_with(uri, mock_client)
        assert result == "Wiki content"

    @patch("trac_mcp_server.mcp.server.handle_read_wiki_resource")
    @patch("trac_mcp_server.mcp.server.get_instances")
    def test_routes_wiki_index_uri(
        self, mock_get_instances, mock_wiki_handler
    ):
        """trac://wiki/_index routes to wiki handler."""
        mock_wiki_handler.return_value = "# Wiki Pages\n..."
        mock_client = (
            mock_get_instances.return_value.get_client.return_value
        )

        uri = Url("trac://wiki/_index")
        result = asyncio.run(handle_read_resource(uri))

        mock_wiki_handler.assert_called_once_with(uri, mock_client)
        assert "Wiki Pages" in result

    @patch("trac_mcp_server.mcp.server.handle_read_wiki_resource")
    @patch("trac_mcp_server.mcp.server.get_instances")
    def test_routes_hierarchical_wiki_uri(
        self, mock_get_instances, mock_wiki_handler
    ):
        """trac://wiki/Dev/Setup routes to wiki handler."""
        mock_wiki_handler.return_value = "Setup content"

        uri = Url("trac://wiki/Dev/Setup")
        asyncio.run(handle_read_resource(uri))

        mock_wiki_handler.assert_called_once()
        call_uri = mock_wiki_handler.call_args[0][0]
        assert str(call_uri) == "trac://wiki/Dev/Setup"


# ---------------------------------------------------------------------------
# handle_read_resource error tests
# ---------------------------------------------------------------------------


class TestReadResourceErrors:
    """Test handle_read_resource error handling."""

    def test_invalid_uri_scheme_raises_error(self):
        """Non-trac URI scheme raises ValueError."""
        uri = Url("http://example.com/wiki/Page")

        with pytest.raises(ValueError, match="Unsupported URI scheme"):
            asyncio.run(handle_read_resource(uri))

    def test_https_scheme_raises_error(self):
        """https:// URI scheme raises ValueError."""
        uri = Url("https://example.com/path")

        with pytest.raises(ValueError, match="Unsupported URI scheme"):
            asyncio.run(handle_read_resource(uri))

    def test_unknown_resource_type_raises_error(self):
        """Unknown resource type (trac://unknown/*) raises ValueError."""
        uri = Url("trac://unknown/path")

        with pytest.raises(ValueError, match="Unknown resource type"):
            asyncio.run(handle_read_resource(uri))

    def test_ticket_resource_type_not_yet_supported(self):
        """trac://ticket/* raises unknown resource type."""
        uri = Url("trac://ticket/123")

        with pytest.raises(ValueError, match="Unknown resource type"):
            asyncio.run(handle_read_resource(uri))
