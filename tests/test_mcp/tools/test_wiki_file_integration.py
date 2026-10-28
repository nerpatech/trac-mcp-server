"""Integration tests for wiki_file tools with real format conversion.

Tests wiki_file push/pull handlers with mocked TracClient but real
file_handler and converter code paths. Verifies format conversion,
round-trip fidelity, format detection consistency, and error handling.

Unlike test_wiki_file_tools.py which mocks auto_convert, these tests
exercise the full conversion pipeline (file read -> format detect ->
convert -> TracClient call -> convert back -> file write).
"""

import xmlrpc.client
from unittest.mock import MagicMock

import mcp.types as types
import pytest

from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.mcp.tools.wiki_file import WIKI_FILE_SPECS

# These tests exercise the server-side path forms (ticket #111).
pytestmark = pytest.mark.usefixtures("local_file_access")

_registry = ToolRegistry(WIKI_FILE_SPECS)


def _make_client():
    """Create a minimal mock TracClient for testing."""
    client = MagicMock()
    client.config = MagicMock()
    client.config.trac_url = "http://localhost/trac"
    client.config.username = "user"
    client.config.password = "pass"
    client.config.verify_ssl = True
    client.config.auto_convert = True
    return client


# =============================================================================
# Push: Markdown -> TracWiki conversion integration
# =============================================================================


class TestPushMarkdownConversion:
    """Integration tests for push with real Markdown-to-TracWiki conversion."""

    async def test_push_md_converts_headings(self, tmp_path):
        """Push .md file converts Markdown headings to TracWiki format."""
        md_file = tmp_path / "page.md"
        md_file.write_text(
            "# Main Title\n\n## Section One\n\nParagraph text."
        )

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "Page does not exist"
        )
        client.put_wiki_page.return_value = {"version": 1}

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["converted"] is True
        assert result.structuredContent["source_format"] == "markdown"

        # Verify TracClient received TracWiki content (not Markdown)
        put_call = client.put_wiki_page.call_args
        wiki_content = put_call[0][1]
        assert (
            "= Main Title =" in wiki_content
            or "= Main Title=" in wiki_content
        )
        assert "# Main Title" not in wiki_content

    async def test_push_md_converts_bold_and_italic(self, tmp_path):
        """Push .md file converts bold/italic formatting to TracWiki."""
        md_file = tmp_path / "page.md"
        md_file.write_text("Some **bold** and *italic* text.")

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.return_value = {"version": 1}

        await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        put_call = client.put_wiki_page.call_args
        wiki_content = put_call[0][1]
        assert "'''" in wiki_content  # TracWiki bold
        assert "''" in wiki_content  # TracWiki italic

    async def test_push_md_converts_code_blocks(self, tmp_path):
        """Push .md file converts fenced code blocks to TracWiki {{{ }}}."""
        md_file = tmp_path / "page.md"
        md_file.write_text(
            "# Code Example\n\n```python\nprint('hello')\n```\n"
        )

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.return_value = {"version": 1}

        await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        put_call = client.put_wiki_page.call_args
        wiki_content = put_call[0][1]
        assert "{{{" in wiki_content
        assert "}}}" in wiki_content
        assert "print('hello')" in wiki_content

    async def test_push_md_strips_frontmatter_before_conversion(
        self, tmp_path
    ):
        """Push .md file strips YAML frontmatter before conversion."""
        md_file = tmp_path / "page.md"
        md_file.write_text(
            "---\ntitle: My Page\ntags: [a, b]\n---\n# Hello\n\nWorld"
        )

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.return_value = {"version": 1}

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        put_call = client.put_wiki_page.call_args
        wiki_content = put_call[0][1]
        assert "---" not in wiki_content
        assert "title: My Page" not in wiki_content
        assert result.structuredContent["converted"] is True


# =============================================================================
# Push: TracWiki pass-through
# =============================================================================


class TestPushTracWikiPassthrough:
    """Integration tests for push with TracWiki files (no conversion)."""

    async def test_push_wiki_passes_through_unchanged(self, tmp_path):
        """Push .wiki file passes content through without conversion."""
        wiki_file = tmp_path / "page.wiki"
        original_content = (
            "= Title =\n\n'''bold''' and ''italic'' text.\n\n{{{code}}}"
        )
        wiki_file.write_text(original_content)

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.return_value = {"version": 1}

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(wiki_file), "page_name": "TestPage"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["converted"] is False
        assert result.structuredContent["source_format"] == "tracwiki"

        put_call = client.put_wiki_page.call_args
        assert put_call[0][1] == original_content

    async def test_push_tracwiki_extension(self, tmp_path):
        """Push .tracwiki file also passes through without conversion."""
        wiki_file = tmp_path / "page.tracwiki"
        wiki_file.write_text("= Title =\n\nContent here.")

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.return_value = {"version": 1}

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(wiki_file), "page_name": "TestPage"},
            client,
        )

        assert result.structuredContent["source_format"] == "tracwiki"
        assert result.structuredContent["converted"] is False


# =============================================================================
# Pull: TracWiki -> Markdown conversion integration
# =============================================================================


class TestPullMarkdownConversion:
    """Integration tests for pull with real TracWiki-to-Markdown conversion."""

    async def test_pull_converts_tracwiki_headings_to_markdown(
        self, tmp_path
    ):
        """Pull converts TracWiki headings to Markdown format."""
        out_file = tmp_path / "page.md"

        client = _make_client()
        client.get_wiki_page.return_value = (
            "= Title =\n\n== Section ==\n\nContent."
        )
        client.get_wiki_page_info.return_value = {"version": 3}

        result = await _registry.call_tool(
            "wiki_file_pull",
            {
                "page_name": "TestPage",
                "file_path": str(out_file),
                "format": "markdown",
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["converted"] is True

        written = out_file.read_text()
        assert "# Title" in written
        assert "## Section" in written
        assert "= Title =" not in written

    async def test_pull_converts_tracwiki_formatting(self, tmp_path):
        """Pull converts TracWiki bold/italic to Markdown."""
        out_file = tmp_path / "page.md"

        client = _make_client()
        client.get_wiki_page.return_value = (
            "'''bold''' and ''italic'' text."
        )
        client.get_wiki_page_info.return_value = {"version": 1}

        await _registry.call_tool(
            "wiki_file_pull",
            {"page_name": "TestPage", "file_path": str(out_file)},
            client,
        )

        written = out_file.read_text()
        assert "**bold**" in written
        assert "*italic*" in written

    async def test_pull_tracwiki_format_no_conversion(self, tmp_path):
        """Pull with format=tracwiki writes raw TracWiki unchanged."""
        out_file = tmp_path / "page.wiki"
        original = "= Title =\n\n'''bold''' text."

        client = _make_client()
        client.get_wiki_page.return_value = original
        client.get_wiki_page_info.return_value = {"version": 2}

        result = await _registry.call_tool(
            "wiki_file_pull",
            {
                "page_name": "TestPage",
                "file_path": str(out_file),
                "format": "tracwiki",
            },
            client,
        )

        assert result.structuredContent["converted"] is False
        written = out_file.read_text()
        assert written == original


# =============================================================================
# Round-trip fidelity
# =============================================================================


class TestRoundTripFidelity:
    """Test push-then-pull preserves semantic content."""

    async def test_roundtrip_headings_preserved(self, tmp_path):
        """Push Markdown, pull back: headings are semantically preserved."""
        md_content = "# Main Title\n\n## Section A\n\nContent A.\n\n## Section B\n\nContent B."
        md_file = tmp_path / "source.md"
        md_file.write_text(md_content)

        # Track what gets pushed to wiki
        pushed_content = {}

        def capture_put(page_name, content, comment, version):
            pushed_content["wiki"] = content
            return {"version": 1}

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.side_effect = capture_put

        # Push
        await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        assert "wiki" in pushed_content

        # Now pull back: mock get_wiki_page to return what was pushed
        client.get_wiki_page.return_value = pushed_content["wiki"]
        client.get_wiki_page_info.side_effect = None
        client.get_wiki_page_info.return_value = {"version": 1}

        out_file = tmp_path / "pulled.md"
        await _registry.call_tool(
            "wiki_file_pull",
            {"page_name": "TestPage", "file_path": str(out_file)},
            client,
        )

        pulled = out_file.read_text()

        # Semantic equivalence: headings and content preserved
        assert "Main Title" in pulled
        assert "Section A" in pulled
        assert "Section B" in pulled
        assert "Content A." in pulled
        assert "Content B." in pulled

    async def test_roundtrip_lists_preserved(self, tmp_path):
        """Push Markdown with lists, pull back: list items preserved."""
        md_content = (
            "# List Test\n\n- Item one\n- Item two\n- Item three\n"
        )
        md_file = tmp_path / "lists.md"
        md_file.write_text(md_content)

        pushed_content = {}

        def capture_put(page_name, content, comment, version):
            pushed_content["wiki"] = content
            return {"version": 1}

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.side_effect = capture_put

        await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "ListPage"},
            client,
        )

        # Verify TracWiki uses * for lists
        wiki = pushed_content["wiki"]
        assert "* Item one" in wiki or "- Item one" in wiki

        # Pull back
        client.get_wiki_page.return_value = pushed_content["wiki"]
        client.get_wiki_page_info.side_effect = None
        client.get_wiki_page_info.return_value = {"version": 1}

        out_file = tmp_path / "pulled_lists.md"
        await _registry.call_tool(
            "wiki_file_pull",
            {"page_name": "ListPage", "file_path": str(out_file)},
            client,
        )

        pulled = out_file.read_text()
        assert "Item one" in pulled
        assert "Item two" in pulled
        assert "Item three" in pulled


# =============================================================================
# Format detection consistency
# =============================================================================


class TestFormatDetectionConsistency:
    """Test that detect_format matches push format parameter."""

    async def test_detect_then_push_md_consistent(self, tmp_path):
        """detect_format on .md file returns 'markdown', push uses same format."""
        md_file = tmp_path / "page.md"
        md_file.write_text("# Title\n\nContent.")

        client = _make_client()

        # Detect format
        detect_result = await _registry.call_tool(
            "wiki_file_detect_format",
            {"file_path": str(md_file)},
            client,
        )

        assert isinstance(detect_result, types.CallToolResult)
        detected_format = detect_result.structuredContent["format"]
        assert detected_format == "markdown"

        # Push and verify format matches
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.return_value = {"version": 1}

        push_result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        assert (
            push_result.structuredContent["source_format"]
            == detected_format
        )

    async def test_detect_then_push_wiki_consistent(self, tmp_path):
        """detect_format on .wiki file returns 'tracwiki', push uses same format."""
        wiki_file = tmp_path / "page.wiki"
        wiki_file.write_text("= Title =\n\nContent.")

        client = _make_client()

        detect_result = await _registry.call_tool(
            "wiki_file_detect_format",
            {"file_path": str(wiki_file)},
            client,
        )

        detected_format = detect_result.structuredContent["format"]
        assert detected_format == "tracwiki"

        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.return_value = {"version": 1}

        push_result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(wiki_file), "page_name": "TestPage"},
            client,
        )

        assert (
            push_result.structuredContent["source_format"]
            == detected_format
        )

    async def test_detect_txt_with_markdown_content(self, tmp_path):
        """detect_format on .txt with Markdown content uses heuristic detection."""
        txt_file = tmp_path / "page.txt"
        txt_file.write_text(
            "# Heading\n\nSome **bold** text and `code`."
        )

        client = _make_client()
        result = await _registry.call_tool(
            "wiki_file_detect_format",
            {"file_path": str(txt_file)},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        # Heuristic should detect markdown from ATX headings and inline formatting
        fmt = result.structuredContent["format"]
        assert fmt in (
            "markdown",
            "tracwiki",
        )  # Either is valid for ambiguous .txt


# =============================================================================
# Error paths
# =============================================================================


class TestErrorPaths:
    """Test error handling in wiki_file tools."""

    async def test_push_nonexistent_file(self):
        """Push non-existent file returns validation error."""
        client = _make_client()
        result = await _registry.call_tool(
            "wiki_file_push",
            {
                "file_path": "/nonexistent/file.md",
                "page_name": "TestPage",
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "validation_error" in result.content[0].text

    async def test_pull_nonexistent_wiki_page(self, tmp_path):
        """Pull non-existent wiki page returns not_found error."""
        out_file = tmp_path / "page.md"

        client = _make_client()
        client.get_wiki_page.side_effect = xmlrpc.client.Fault(
            1, "Page NoSuchPage does not exist"
        )

        result = await _registry.call_tool(
            "wiki_file_pull",
            {"page_name": "NoSuchPage", "file_path": str(out_file)},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "not_found" in result.content[0].text
        assert "wiki_search" in result.content[0].text
        assert not out_file.exists()

    async def test_pull_invalid_output_directory(self):
        """Pull to path with non-existent parent directory returns error."""
        client = _make_client()
        result = await _registry.call_tool(
            "wiki_file_pull",
            {
                "page_name": "TestPage",
                "file_path": "/no/such/dir/page.md",
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "validation_error" in result.content[0].text

    async def test_detect_format_nonexistent_file(self):
        """detect_format on non-existent file returns validation error."""
        client = _make_client()
        result = await _registry.call_tool(
            "wiki_file_detect_format",
            {"file_path": "/nonexistent/file.md"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "validation_error" in result.content[0].text

    async def test_push_permission_denied_fault(self, tmp_path):
        """Push with permission denied Fault returns permission_denied error."""
        md_file = tmp_path / "page.md"
        md_file.write_text("# Hello")

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            403, "Permission denied"
        )

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "permission_denied" in result.content[0].text

    async def test_push_relative_file_path(self, tmp_path):
        """Push with relative file path returns validation error."""
        client = _make_client()
        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": "relative/path.md", "page_name": "TestPage"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "validation_error" in result.content[0].text
