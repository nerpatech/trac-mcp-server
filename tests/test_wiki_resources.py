"""
Tests for wiki resource handlers.
"""

import asyncio
import unittest
import xmlrpc.client
from unittest.mock import MagicMock, patch

from pydantic_core import Url

from trac_mcp_server.config import Config
from trac_mcp_server.mcp.resources.wiki import (
    WIKI_RESOURCES,
    _format_page_tree,
    _parse_query_params,
    handle_list_wiki_resources,
    handle_read_wiki_resource,
)


class TestWikiResourceDefinitions(unittest.TestCase):
    """Test wiki resource definitions."""

    def test_wiki_resources_has_two_entries(self):
        """Test WIKI_RESOURCES has exactly 2 resources."""
        self.assertEqual(len(WIKI_RESOURCES), 2)

    def test_wiki_page_resource_uri(self):
        """Test wiki page template resource URI."""
        page_resource = WIKI_RESOURCES[0]
        # URI is AnyUrl so compare string representation
        self.assertIn("trac://wiki/", str(page_resource.uri))
        self.assertIn("page_name", str(page_resource.uri))
        self.assertEqual(page_resource.name, "Wiki Page")
        self.assertEqual(page_resource.mimeType, "text/plain")

    def test_wiki_index_resource_uri(self):
        """Test wiki index resource URI."""
        index_resource = WIKI_RESOURCES[1]
        # URI is AnyUrl so compare string representation
        self.assertEqual(str(index_resource.uri), "trac://wiki/_index")
        self.assertEqual(index_resource.name, "Wiki Page Index")
        self.assertEqual(index_resource.mimeType, "text/plain")


class TestHandleListWikiResources(unittest.TestCase):
    """Test handle_list_wiki_resources function."""

    def test_returns_wiki_resources(self):
        """Test list handler returns WIKI_RESOURCES."""
        result = asyncio.run(handle_list_wiki_resources())
        self.assertEqual(result, WIKI_RESOURCES)
        self.assertEqual(len(result), 2)


class TestFormatPageTree(unittest.TestCase):
    """Test page tree formatting."""

    def test_empty_list(self):
        """Test empty page list returns empty string."""
        result = _format_page_tree([])
        self.assertEqual(result, "")

    def test_flat_pages(self):
        """Test flat page list formatting."""
        pages = ["PageTwo", "WikiStart"]
        result = _format_page_tree(pages)
        # Should be sorted alphabetically
        self.assertIn("PageTwo", result)
        self.assertIn("WikiStart", result)
        # First entry should not have connector
        lines = result.split("\n")
        self.assertEqual(lines[0], "PageTwo")
        self.assertEqual(lines[1], "WikiStart")

    def test_hierarchical_pages(self):
        """Test hierarchical page formatting with tree connectors."""
        pages = ["Dev/Setup", "Dev/Testing", "WikiStart"]
        result = _format_page_tree(pages)
        # Should show Dev as parent with nested children
        self.assertIn("Dev", result)
        self.assertIn("Setup", result)
        self.assertIn("Testing", result)
        self.assertIn("WikiStart", result)
        # Check tree connectors
        self.assertIn("|-- ", result)
        self.assertIn("`-- ", result)

    def test_deep_hierarchy(self):
        """Test deeply nested page hierarchy."""
        pages = [
            "API/Reference/Methods",
            "API/Reference/Types",
            "API/Overview",
        ]
        result = _format_page_tree(pages)
        self.assertIn("API", result)
        self.assertIn("Reference", result)
        self.assertIn("Methods", result)
        self.assertIn("Types", result)
        self.assertIn("Overview", result)

    def test_single_page(self):
        """Test single page formatting."""
        pages = ["WikiStart"]
        result = _format_page_tree(pages)
        self.assertEqual(result, "WikiStart")


class TestParseQueryParams(unittest.TestCase):
    """Test query parameter parsing."""

    def test_empty_string(self):
        """Test empty query string returns empty dict."""
        result = _parse_query_params("")
        self.assertEqual(result, {})

    def test_single_param(self):
        """Test single parameter parsing."""
        result = _parse_query_params("format=tracwiki")
        self.assertEqual(result, {"format": "tracwiki"})

    def test_multiple_params(self):
        """Test multiple parameter parsing."""
        result = _parse_query_params("format=tracwiki&version=5")
        self.assertEqual(result, {"format": "tracwiki", "version": "5"})

    def test_url_encoded_value(self):
        """Test URL-encoded parameter values."""
        result = _parse_query_params("page=Test%20Page")
        self.assertEqual(result, {"page": "Test Page"})


class TestURIParsing(unittest.TestCase):
    """Test URI parsing in handle_read_wiki_resource."""

    def setUp(self):
        """Set up test client."""
        self.client = MagicMock()
        self.client.config = Config(
            trac_url="https://example.com/trac",
            username="testuser",
            password="testpass",
        )

    @patch("trac_mcp_server.mcp.resources.wiki.run_sync_limited")
    def test_simple_page_name(self, mock_run_sync_limited):
        """Test simple page name extraction from URI."""
        mock_run_sync_limited.side_effect = [
            "= WikiStart =\nWelcome!",  # get_wiki_page
            {
                "author": "admin",
                "version": 1,
                "lastModified": 1234567890,
            },  # get_wiki_page_info
        ]

        uri = Url("trac://wiki/WikiStart")
        asyncio.run(handle_read_wiki_resource(uri, self.client))

        # Verify get_wiki_page was called with correct page name
        call_args = mock_run_sync_limited.call_args_list[0]
        self.assertEqual(call_args[0][1], "WikiStart")

    @patch("trac_mcp_server.mcp.resources.wiki.run_sync_limited")
    def test_hierarchical_page_name(self, mock_run_sync_limited):
        """Test hierarchical page name: trac://wiki/Dev/Setup -> Dev/Setup."""
        mock_run_sync_limited.side_effect = [
            "Setup instructions",
            {
                "author": "admin",
                "version": 1,
                "lastModified": 1234567890,
            },
        ]

        uri = Url("trac://wiki/Dev/Setup")
        asyncio.run(handle_read_wiki_resource(uri, self.client))

        call_args = mock_run_sync_limited.call_args_list[0]
        self.assertEqual(call_args[0][1], "Dev/Setup")

    @patch("trac_mcp_server.mcp.resources.wiki.run_sync_limited")
    def test_url_encoded_page_name(self, mock_run_sync_limited):
        """Test URL-encoded page name: trac://wiki/Test%20Page -> Test Page."""
        mock_run_sync_limited.side_effect = [
            "Test page content",
            {
                "author": "admin",
                "version": 1,
                "lastModified": 1234567890,
            },
        ]

        uri = Url("trac://wiki/Test%20Page")
        asyncio.run(handle_read_wiki_resource(uri, self.client))

        call_args = mock_run_sync_limited.call_args_list[0]
        self.assertEqual(call_args[0][1], "Test Page")

    @patch("trac_mcp_server.mcp.resources.wiki.run_sync_limited")
    def test_format_parameter(self, mock_run_sync_limited):
        """Test format=tracwiki returns raw content without conversion."""
        mock_run_sync_limited.side_effect = [
            "= Raw TracWiki =\n'''Bold'''",  # get_wiki_page
            {
                "author": "admin",
                "version": 1,
                "lastModified": 1234567890,
            },
        ]

        uri = Url("trac://wiki/Page?format=tracwiki")
        result = asyncio.run(
            handle_read_wiki_resource(uri, self.client)
        )

        # Should contain raw TracWiki syntax
        self.assertIn("= Raw TracWiki =", result)
        self.assertIn("'''Bold'''", result)

    @patch("trac_mcp_server.mcp.resources.wiki.run_sync_limited")
    def test_version_parameter(self, mock_run_sync_limited):
        """Test version=5 passes version to client."""
        mock_run_sync_limited.side_effect = [
            "Old content",
            {
                "author": "admin",
                "version": 5,
                "lastModified": 1234567890,
            },
        ]

        uri = Url("trac://wiki/Page?version=5")
        asyncio.run(handle_read_wiki_resource(uri, self.client))

        # Verify version was passed to both calls
        get_page_call = mock_run_sync_limited.call_args_list[0]
        get_info_call = mock_run_sync_limited.call_args_list[1]
        self.assertEqual(get_page_call[0][2], 5)  # version arg
        self.assertEqual(get_info_call[0][2], 5)  # version arg

    @patch("trac_mcp_server.mcp.resources.wiki.run_sync_limited")
    def test_combined_params(self, mock_run_sync_limited):
        """Test combined format and version parameters."""
        mock_run_sync_limited.side_effect = [
            "'''Raw version 3'''",
            {
                "author": "admin",
                "version": 3,
                "lastModified": 1234567890,
            },
        ]

        uri = Url("trac://wiki/Page?format=tracwiki&version=3")
        result = asyncio.run(
            handle_read_wiki_resource(uri, self.client)
        )

        # Should have raw TracWiki
        self.assertIn("'''Raw version 3'''", result)
        # Should request version 3
        get_page_call = mock_run_sync_limited.call_args_list[0]
        self.assertEqual(get_page_call[0][2], 3)


class TestIndexResource(unittest.TestCase):
    """Test _index resource handler."""

    def setUp(self):
        """Set up test client."""
        self.client = MagicMock()
        self.client.config = Config(
            trac_url="https://example.com/trac",
            username="testuser",
            password="testpass",
        )

    @patch("trac_mcp_server.mcp.resources.wiki.run_sync")
    def test_index_returns_page_tree(self, mock_run_sync):
        """Test _index path returns hierarchical page tree."""
        mock_run_sync.return_value = [
            "Dev/Setup",
            "Dev/Testing",
            "WikiStart",
        ]

        uri = Url("trac://wiki/_index")
        result = asyncio.run(
            handle_read_wiki_resource(uri, self.client)
        )

        self.assertIn("# Wiki Pages", result)
        self.assertIn("Dev", result)
        self.assertIn("WikiStart", result)


class TestErrorResponses(unittest.TestCase):
    """Test error response handling."""

    def setUp(self):
        """Set up test client."""
        self.client = MagicMock()
        self.client.config = Config(
            trac_url="https://example.com/trac",
            username="testuser",
            password="testpass",
        )

    def test_not_found_error_with_suggestions(self):
        """Test page not found includes similar page suggestions."""

        # Configure client methods to raise/return appropriate values
        self.client.get_wiki_page.side_effect = xmlrpc.client.Fault(
            1, "Page not found"
        )
        self.client.list_wiki_pages.return_value = [
            "WikiStart",
            "WikiHelp",
            "WikiSyntax",
        ]

        # Create async wrapper that calls client methods
        async def mock_run(func, *args, **kwargs):
            return func(*args, **kwargs)

        with (
            patch(
                "trac_mcp_server.mcp.resources.wiki.run_sync_limited",
                side_effect=mock_run,
            ),
            patch(
                "trac_mcp_server.mcp.resources.wiki.run_sync",
                side_effect=mock_run,
            ),
        ):
            uri = Url("trac://wiki/WikiStrt")  # Typo
            result = asyncio.run(
                handle_read_wiki_resource(uri, self.client)
            )

        self.assertIn("Error (not_found):", result)
        self.assertIn("WikiStrt", result)
        self.assertIn("Similar pages:", result)

    def test_not_found_error_basic(self):
        """Test page not found error without suggestions available."""

        # Configure client methods
        self.client.get_wiki_page.side_effect = xmlrpc.client.Fault(
            1, "Page not found"
        )
        self.client.list_wiki_pages.return_value = [
            "TotallyDifferent",
            "UnrelatedPage",
        ]  # No matches

        async def mock_run(func, *args, **kwargs):
            return func(*args, **kwargs)

        with (
            patch(
                "trac_mcp_server.mcp.resources.wiki.run_sync_limited",
                side_effect=mock_run,
            ),
            patch(
                "trac_mcp_server.mcp.resources.wiki.run_sync",
                side_effect=mock_run,
            ),
        ):
            uri = Url("trac://wiki/XYZABC123")
            result = asyncio.run(
                handle_read_wiki_resource(uri, self.client)
            )

        self.assertIn("Error (not_found):", result)
        self.assertIn("XYZABC123", result)


class TestResponseFormatting(unittest.TestCase):
    """Test response formatting."""

    def setUp(self):
        """Set up test client."""
        self.client = MagicMock()
        self.client.config = Config(
            trac_url="https://example.com/trac",
            username="testuser",
            password="testpass",
        )

    @patch("trac_mcp_server.mcp.resources.wiki.run_sync_limited")
    def test_response_includes_metadata(self, mock_run_sync_limited):
        """Test response includes author, version, and modified date."""
        mock_run_sync_limited.side_effect = [
            "Page content here",
            {
                "author": "alice",
                "version": 7,
                "lastModified": 1706828400,
            },
        ]

        uri = Url("trac://wiki/TestPage")
        result = asyncio.run(
            handle_read_wiki_resource(uri, self.client)
        )

        self.assertIn("# TestPage", result)
        self.assertIn("**Author:** alice", result)
        self.assertIn("**Version:** 7", result)
        self.assertIn("**Last Modified:**", result)
        self.assertIn("---", result)  # Separator
        self.assertIn("Page content here", result)

    @patch("trac_mcp_server.mcp.resources.wiki.run_sync_limited")
    def test_stored_tracwiki_is_returned_unconverted(
        self, mock_run_sync_limited
    ):
        """The resource returns stored bytes, with no query parameter
        needed to ask for them (ticket #69)."""
        stored = "= Heading =\n'''Bold''' and ''italic''"
        mock_run_sync_limited.side_effect = [
            stored,
            {
                "author": "admin",
                "version": 1,
                "lastModified": 1234567890,
            },
        ]

        uri = Url("trac://wiki/TestPage")
        result = asyncio.run(
            handle_read_wiki_resource(uri, self.client)
        )

        self.assertIn(stored, result)

    @patch("trac_mcp_server.mcp.resources.wiki.run_sync_limited")
    def test_format_markdown_query_param_is_rejected(
        self, mock_run_sync_limited
    ):
        """A stale caller asking for Markdown is told, rather than
        handed TracWiki while believing otherwise (ticket #69)."""
        uri = Url("trac://wiki/TestPage?format=markdown")
        result = asyncio.run(
            handle_read_wiki_resource(uri, self.client)
        )

        self.assertIn("validation_error", result)
        self.assertIn("trac-convert", result)
        mock_run_sync_limited.assert_not_called()


if __name__ == "__main__":
    unittest.main()
