"""
Integration tests for capability detection system.

Tests CapabilityDetector, processor testing, caching, and detection orchestration.
Uses mocking to avoid live Trac dependency.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlparse

import pytest

from trac_mcp_server.detection.capabilities import CapabilityDetector
from trac_mcp_server.detection.processor_utils import (
    check_processor_available,
)


class TestCapabilityDetector:
    """Tests for CapabilityDetector initialization and basic operations."""

    def test_capability_detector_init(self):
        """Test CapabilityDetector instantiation with config."""
        # Mock client and config
        mock_client = Mock()
        mock_config = Mock()
        mock_config.project_config_dir = "/test/path/.trac_mcp"

        # Initialize detector
        detector = CapabilityDetector(mock_client, mock_config)

        # Verify attributes
        assert detector.trac_client == mock_client
        assert detector.config == mock_config
        assert detector.cache_path == Path(
            "/test/path/.trac_mcp/capabilities.json"
        )

    def test_capability_detector_init_no_cache_dir(self):
        """Test CapabilityDetector when no project config dir available."""
        mock_client = Mock()
        mock_config = Mock()
        mock_config.project_config_dir = None

        detector = CapabilityDetector(mock_client, mock_config)

        assert detector.cache_path is None


class TestCachePersistence:
    """Tests for cache loading, saving, and expiry logic."""

    def test_cache_save_and_load(self, tmp_path):
        """Test cache persistence to file system."""
        # Setup
        mock_client = Mock()
        mock_config = Mock()
        cache_dir = tmp_path / ".trac_mcp"
        cache_dir.mkdir()
        mock_config.project_config_dir = str(cache_dir)

        detector = CapabilityDetector(mock_client, mock_config)

        # Create capabilities data
        capabilities = {
            "trac_version": "1.4",
            "xmlrpc_available": True,
            "wiki_processors": ["markdown", "rst"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Save cache
        detector._save_cache(capabilities)

        # Verify file exists
        cache_file = cache_dir / "capabilities.json"
        assert cache_file.exists()

        # Load cache
        loaded = detector._load_cache()
        assert loaded is not None
        assert loaded["trac_version"] == "1.4"
        assert loaded["xmlrpc_available"] is True
        assert loaded["wiki_processors"] == ["markdown", "rst"]

    def test_cache_expiry(self, tmp_path):
        """Test 24-hour cache expiry logic."""
        # Setup
        mock_client = Mock()
        mock_config = Mock()
        cache_dir = tmp_path / ".trac_mcp"
        cache_dir.mkdir()
        mock_config.project_config_dir = str(cache_dir)

        detector = CapabilityDetector(mock_client, mock_config)

        # Create expired cache (25 hours old)
        old_timestamp = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat()
        capabilities = {
            "trac_version": "1.4",
            "timestamp": old_timestamp,
        }
        detector._save_cache(capabilities)

        # Load cache - should return None (expired)
        loaded = detector._load_cache()
        assert loaded is None

    def test_cache_valid_within_expiry(self, tmp_path):
        """Test cache is valid within 24-hour window."""
        # Setup
        mock_client = Mock()
        mock_config = Mock()
        cache_dir = tmp_path / ".trac_mcp"
        cache_dir.mkdir()
        mock_config.project_config_dir = str(cache_dir)

        detector = CapabilityDetector(mock_client, mock_config)

        # Create fresh cache (1 hour old)
        recent_timestamp = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        capabilities = {
            "trac_version": "1.4",
            "timestamp": recent_timestamp,
        }
        detector._save_cache(capabilities)

        # Load cache - should return data (not expired)
        loaded = detector._load_cache()
        assert loaded is not None
        assert loaded["trac_version"] == "1.4"


class TestXMLRPCDetection:
    """Tests for XML-RPC based detection method."""

    def test_xmlrpc_detection_success(self):
        """Test _detect_via_xmlrpc with successful method listing."""
        # Mock client
        mock_client = Mock()
        mock_client.list_methods.return_value = [
            "system.listMethods",
            "system.getAPIVersion",
            "wiki.getAllPages",
            "wiki.getPage",
            "ticket.query",
            "ticket.get",
        ]

        mock_config = Mock()
        mock_config.project_config_dir = None

        detector = CapabilityDetector(mock_client, mock_config)

        # Call detection
        result = detector._detect_via_xmlrpc()

        # Verify results
        assert result["xmlrpc_available"] is True
        assert result["has_wiki_rpc"] is True
        assert result["has_ticket_rpc"] is True
        assert len(result["available_methods"]) == 6

    def test_xmlrpc_detection_failure(self):
        """Test _detect_via_xmlrpc when XML-RPC unavailable."""
        # Mock client that raises exception
        mock_client = Mock()
        mock_client.list_methods.side_effect = Exception(
            "Connection refused"
        )

        mock_config = Mock()
        mock_config.project_config_dir = None

        detector = CapabilityDetector(mock_client, mock_config)

        # Call detection
        result = detector._detect_via_xmlrpc()

        # Verify failure result
        assert result["xmlrpc_available"] is False


class TestWebScraping:
    """Tests for web scraping detection method."""

    MOCK_ABOUT_HTML = (
        b"<html><body>"
        b'<div id="info">'
        b"<h1>Trac 1.6.1</h1>"
        b"<h2>Installed Plugins</h2>"
        b"<dl>"
        b"<dt>MarkdownMacro</dt><dd>0.12.0</dd>"
        b"<dt>XmlRpcPlugin</dt><dd>1.1.9</dd>"
        b"</dl>"
        b"</div>"
        b"</body></html>"
    )

    @patch("trac_mcp_server.detection.web_scraper.requests.get")
    def test_web_scraping_success(self, mock_get):
        """Test scrape_about_page with mocked HTML response."""
        from trac_mcp_server.detection.web_scraper import (
            scrape_about_page,
        )

        mock_response = Mock()
        mock_response.content = self.MOCK_ABOUT_HTML
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = scrape_about_page(
            "https://trac.example.com", ("user", "pass")
        )

        assert result["trac_version"] == "1.6.1"
        assert "MarkdownMacro" in result["plugins"]
        assert result["plugins"]["MarkdownMacro"] == "0.12.0"
        assert "XmlRpcPlugin" in result["plugins"]
        assert result["plugins"]["XmlRpcPlugin"] == "1.1.9"
        assert result["markdown_processor"] == "MarkdownMacro"
        mock_get.assert_called_once_with(
            "https://trac.example.com/about",
            auth=("user", "pass"),
            timeout=10,
        )

    @patch("trac_mcp_server.detection.web_scraper.requests.get")
    def test_web_scraping_failure(self, mock_get):
        """Test scrape_about_page when request fails."""
        import requests as req

        from trac_mcp_server.detection.web_scraper import (
            scrape_about_page,
        )

        mock_get.side_effect = req.RequestException(
            "Connection refused"
        )

        result = scrape_about_page(
            "https://trac.example.com", ("user", "pass")
        )

        assert result == {}

    @patch("trac_mcp_server.detection.web_scraper.requests.get")
    def test_web_scraping_permission_denied(self, mock_get):
        """Test scrape_about_page returns empty dict on 403 Forbidden."""
        import requests as req

        from trac_mcp_server.detection.web_scraper import (
            scrape_about_page,
        )

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = req.HTTPError(
            response=mock_response
        )
        mock_get.return_value = mock_response

        result = scrape_about_page(
            "https://trac.example.com", ("user", "pass")
        )

        assert result == {}


class TestProjectIndexScraping:
    """Tests for scrape_project_index -- multi-project host root page."""

    # Shape verified live against a real multi-project Trac host root.
    MOCK_INDEX_HTML = (
        b"<html><body>"
        b"<h1>Available Projects</h1>"
        b"<ul>"
        b'<li><a href="/bcs" title="BCS project">BCS</a></li>'
        b'<li><a href="/trac_mcp_server" title="Trac MCP Server">'
        b"Trac MCP Server</a></li>"
        b"</ul>"
        b"</body></html>"
    )

    @patch("trac_mcp_server.detection.web_scraper.requests.get")
    def test_scrape_success(self, mock_get):
        from trac_mcp_server.detection.web_scraper import (
            scrape_project_index,
        )

        mock_response = Mock()
        mock_response.content = self.MOCK_INDEX_HTML
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = scrape_project_index(
            "http://192.168.10.4:8000", ("user", "pass")
        )

        assert len(result) == 2
        bcs = next(e for e in result if e["path"] == "/bcs")
        assert bcs["title"] == "BCS"
        assert bcs["description"] == "BCS project"
        assert bcs["url"] == "http://192.168.10.4:8000/bcs"
        mock_get.assert_called_once_with(
            "http://192.168.10.4:8000",
            auth=("user", "pass"),
            timeout=10,
        )

    @patch("trac_mcp_server.detection.web_scraper.requests.get")
    def test_scrape_without_auth(self, mock_get):
        """The host root is typically unauthenticated (auth_tuple optional)."""
        from trac_mcp_server.detection.web_scraper import (
            scrape_project_index,
        )

        mock_response = Mock()
        mock_response.content = self.MOCK_INDEX_HTML
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = scrape_project_index("http://192.168.10.4:8000")

        assert len(result) == 2
        mock_get.assert_called_once_with(
            "http://192.168.10.4:8000", auth=None, timeout=10
        )

    @patch("trac_mcp_server.detection.web_scraper.requests.get")
    def test_scrape_connection_failure_returns_empty_list(
        self, mock_get
    ):
        import requests as req

        from trac_mcp_server.detection.web_scraper import (
            scrape_project_index,
        )

        mock_get.side_effect = req.RequestException(
            "Connection refused"
        )

        result = scrape_project_index("http://192.168.10.4:8000")

        assert result == []

    @patch("trac_mcp_server.detection.web_scraper.requests.get")
    def test_scrape_http_error_returns_empty_list(self, mock_get):
        import requests as req

        from trac_mcp_server.detection.web_scraper import (
            scrape_project_index,
        )

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = req.HTTPError(
            response=mock_response
        )
        mock_get.return_value = mock_response

        result = scrape_project_index("http://192.168.10.4:8000")

        assert result == []

    @patch("trac_mcp_server.detection.web_scraper.requests.get")
    def test_scrape_ignores_links_without_href(self, mock_get):
        from trac_mcp_server.detection.web_scraper import (
            scrape_project_index,
        )

        mock_response = Mock()
        mock_response.content = (
            b"<html><body><ul><li><a>No href</a></li>"
            b'<li><a href="/bcs">BCS</a></li></ul></body></html>'
        )
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = scrape_project_index("http://192.168.10.4:8000")

        assert len(result) == 1
        assert result[0]["path"] == "/bcs"


@pytest.mark.live
def test_live_scrape_project_index():
    """Live: scrape the real multi-project Trac host's project index.

    Requires --run-live and TRAC_URL/USERNAME/PASSWORD env vars. Derives
    the host root from TRAC_URL (e.g. http://host:8000/trac_mcp_server ->
    http://host:8000) and expects the configured project to appear among
    the discovered projects.
    """
    from trac_mcp_server.detection.web_scraper import (
        scrape_project_index,
    )

    trac_url = os.environ["TRAC_URL"]
    parsed = urlparse(trac_url)
    host_root = f"{parsed.scheme}://{parsed.netloc}"
    auth = (os.environ["TRAC_USERNAME"], os.environ["TRAC_PASSWORD"])

    results = scrape_project_index(host_root, auth)

    assert len(results) > 0
    assert any(
        parsed.path.rstrip("/") == entry["path"] for entry in results
    )


class TestProcessorTesting:
    """Tests for processor availability testing."""

    def test_processor_test_success(self):
        """Test check_processor_available with successful processor."""
        # Mock client
        mock_client = Mock()
        mock_client.put_wiki_page.return_value = True
        mock_client.get_wiki_page_html.return_value = (
            "<div>Test content</div>"
        )
        mock_client.delete_wiki_page.return_value = True

        # Test processor
        result = check_processor_available(mock_client, "markdown")

        # Verify success
        assert result is True
        assert mock_client.put_wiki_page.called
        assert mock_client.get_wiki_page_html.called
        assert mock_client.delete_wiki_page.called

    def test_processor_test_failure_error_in_html(self):
        """Test check_processor_available when processor returns error."""
        # Mock client
        mock_client = Mock()
        mock_client.put_wiki_page.return_value = True
        mock_client.get_wiki_page_html.return_value = (
            "<div class='system-message'>Processor not found</div>"
        )
        mock_client.delete_wiki_page.return_value = True

        # Test processor
        result = check_processor_available(
            mock_client, "invalid_processor"
        )

        # Verify failure
        assert result is False
        assert (
            mock_client.delete_wiki_page.called
        )  # Cleanup still happens

    def test_processor_test_failure_exception(self):
        """Test check_processor_available when exception occurs."""
        # Mock client that raises exception
        mock_client = Mock()
        mock_client.put_wiki_page.side_effect = Exception(
            "Permission denied"
        )

        # Test processor
        result = check_processor_available(mock_client, "markdown")

        # Verify failure
        assert result is False


class TestDetectionOrchestration:
    """Tests for fallback chain and detection orchestration."""

    def test_detect_all_xmlrpc_primary(self, tmp_path):
        """Test detect_all uses XML-RPC as primary method."""
        # Mock client with XML-RPC available
        mock_client = Mock()
        mock_client.list_methods.return_value = [
            "wiki.getAllPages",
            "ticket.query",
        ]

        mock_config = Mock()
        cache_dir = tmp_path / ".trac_mcp"
        cache_dir.mkdir()
        mock_config.project_config_dir = str(cache_dir)
        mock_config.trac_url = "https://trac.example.com"
        mock_config.username = "user"
        mock_config.password = "pass"

        detector = CapabilityDetector(mock_client, mock_config)

        # Mock _detect_via_web and _detect_via_probing to prevent actual calls
        with patch.object(detector, "_detect_via_web", return_value={}):
            with patch.object(
                detector, "_detect_via_probing", return_value={}
            ):
                # Detect
                result = detector.detect_all(force_refresh=True)

                # Verify XML-RPC was used
                assert result["detection_method"] == "xmlrpc"
                assert result["xmlrpc_available"] is True

    def test_detect_all_fallback_to_probing(self, tmp_path):
        """Test detect_all falls back to probing when XML-RPC and web fail."""
        # Mock client with XML-RPC unavailable
        mock_client = Mock()
        mock_client.list_methods.side_effect = Exception(
            "Not available"
        )
        mock_client.put_wiki_page.return_value = True
        mock_client.get_wiki_page_html.return_value = "<div>Test</div>"
        mock_client.delete_wiki_page.return_value = True

        mock_config = Mock()
        cache_dir = tmp_path / ".trac_mcp"
        cache_dir.mkdir()
        mock_config.project_config_dir = str(cache_dir)
        mock_config.trac_url = "https://trac.example.com"
        mock_config.username = "user"
        mock_config.password = "pass"

        detector = CapabilityDetector(mock_client, mock_config)

        # Mock web scraping to fail
        with patch(
            "trac_mcp_server.detection.web_scraper.requests.get"
        ) as mock_get:
            mock_get.side_effect = Exception("Failed")

            # Detect
            result = detector.detect_all(force_refresh=True)

            # Verify probing was used
            assert "probing" in result["detection_method"]
            assert (
                len(result["wiki_processors"]) > 0
            )  # Found some processors

    def test_detect_all_uses_cache(self, tmp_path):
        """Test detect_all uses cache when available and fresh."""
        # Setup cache
        mock_client = Mock()
        mock_config = Mock()
        cache_dir = tmp_path / ".trac_mcp"
        cache_dir.mkdir()
        mock_config.project_config_dir = str(cache_dir)

        detector = CapabilityDetector(mock_client, mock_config)

        # Pre-populate cache
        cached_data = {
            "trac_version": "1.4",
            "xmlrpc_available": True,
            "wiki_processors": ["cached_processor"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        detector._save_cache(cached_data)

        # Detect without force refresh
        result = detector.detect_all(force_refresh=False)

        # Verify cache was used (client methods not called)
        assert not mock_client.list_methods.called
        assert result["wiki_processors"] == ["cached_processor"]
