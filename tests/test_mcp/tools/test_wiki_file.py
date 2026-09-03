"""Tests for wiki_file tool handlers: detect_format, push, pull, and frontmatter stripping."""

import xmlrpc.client
from unittest.mock import MagicMock, patch

import mcp.types as types

from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.mcp.tools.wiki_file import (
    WIKI_FILE_SPECS,
    _strip_yaml_frontmatter,
)

_registry = ToolRegistry(WIKI_FILE_SPECS)

# =============================================================================
# _strip_yaml_frontmatter
# =============================================================================


class TestStripYamlFrontmatter:
    """Tests for _strip_yaml_frontmatter helper."""

    def test_strips_frontmatter(self):
        content = (
            "---\ntitle: Test\ntags: [a, b]\n---\n# Hello\nBody text"
        )
        assert _strip_yaml_frontmatter(content) == "# Hello\nBody text"

    def test_no_frontmatter_unchanged(self):
        content = "# Hello\nBody text"
        assert _strip_yaml_frontmatter(content) == content

    def test_incomplete_frontmatter_unchanged(self):
        content = "---\ntitle: Test\n# No closing dashes"
        assert _strip_yaml_frontmatter(content) == content

    def test_empty_content(self):
        assert _strip_yaml_frontmatter("") == ""

    def test_frontmatter_only(self):
        content = "---\ntitle: Test\n---\n"
        assert _strip_yaml_frontmatter(content) == ""

    def test_frontmatter_with_blank_lines_after(self):
        content = "---\nx: 1\n---\n\n\n# Title"
        assert _strip_yaml_frontmatter(content) == "# Title"


# =============================================================================
# _handle_detect_format (via ToolRegistry.call_tool)
# =============================================================================


class TestDetectFormat:
    """Tests for wiki_file_detect_format handler."""

    async def test_md_file(self, tmp_path):
        md_file = tmp_path / "page.md"
        md_file.write_text("# Hello\n\nSome **bold** text.")
        client = MagicMock()
        result = await _registry.call_tool(
            "wiki_file_detect_format",
            {"file_path": str(md_file)},
            client,
        )
        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["format"] == "markdown"
        assert result.structuredContent["encoding"] == "utf-8"
        assert result.structuredContent["size_bytes"] > 0

    async def test_wiki_file(self, tmp_path):
        wiki_file = tmp_path / "page.wiki"
        wiki_file.write_text("= Title =\n\n'''bold''' text.")
        client = MagicMock()
        result = await _registry.call_tool(
            "wiki_file_detect_format",
            {"file_path": str(wiki_file)},
            client,
        )
        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["format"] == "tracwiki"

    async def test_txt_with_tracwiki_content(self, tmp_path):
        txt_file = tmp_path / "page.txt"
        txt_file.write_text("= Heading =\n\n'''bold''' and {{{code}}}")
        client = MagicMock()
        result = await _registry.call_tool(
            "wiki_file_detect_format",
            {"file_path": str(txt_file)},
            client,
        )
        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["format"] == "tracwiki"

    async def test_missing_file_path(self):
        client = MagicMock()
        result = await _registry.call_tool(
            "wiki_file_detect_format", {}, client
        )
        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "file_path is required" in result.content[0].text

    async def test_nonexistent_file(self, tmp_path):
        client = MagicMock()
        result = await _registry.call_tool(
            "wiki_file_detect_format",
            {"file_path": str(tmp_path / "no_such_file.md")},
            client,
        )
        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "validation_error" in result.content[0].text


# =============================================================================
# _handle_push (via ToolRegistry.call_tool)
# =============================================================================


def _make_client():
    """Create a minimal mock TracClient with config attribute."""
    client = MagicMock()
    client.config = MagicMock()
    client.config.trac_url = "http://localhost/trac"
    client.config.username = "user"
    client.config.password = "pass"
    client.config.verify_ssl = True
    client.config.auto_convert = True
    return client


class TestPush:
    """Tests for wiki_file_push handler."""

    @patch("trac_mcp_server.mcp.tools.wiki_file.auto_convert")
    async def test_push_new_page(self, mock_convert, tmp_path):
        """Push a .md file to a new wiki page (page doesn't exist)."""
        md_file = tmp_path / "page.md"
        md_file.write_text("# Hello\nWorld")

        # auto_convert returns converted content
        mock_result = MagicMock()
        mock_result.text = "= Hello =\nWorld"
        mock_result.converted = True
        mock_result.warnings = []
        mock_convert.return_value = mock_result

        # TracClient: get_wiki_page_info raises Fault (not found)
        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "Page TestPage does not exist"
        )
        client.put_wiki_page.return_value = {
            "version": 1,
            "name": "TestPage",
        }

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["action"] == "created"
        assert result.structuredContent["version"] == 1
        assert result.structuredContent["converted"] is True

    @patch("trac_mcp_server.mcp.tools.wiki_file.auto_convert")
    async def test_push_update_existing(self, mock_convert, tmp_path):
        """Push a .md file updating an existing wiki page."""
        md_file = tmp_path / "page.md"
        md_file.write_text("# Updated\nContent")

        mock_result = MagicMock()
        mock_result.text = "= Updated =\nContent"
        mock_result.converted = True
        mock_result.warnings = []
        mock_convert.return_value = mock_result

        client = _make_client()
        client.get_wiki_page_info.return_value = {"version": 3}
        client.put_wiki_page.return_value = {
            "version": 4,
            "name": "TestPage",
        }

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["action"] == "updated"
        assert result.structuredContent["version"] == 4
        # Verify optimistic locking: put_wiki_page called with version=3
        client.put_wiki_page.assert_called_once()
        call_args = client.put_wiki_page.call_args
        assert call_args[0][3] == 3  # version argument

    @patch("trac_mcp_server.mcp.tools.wiki_file.auto_convert")
    async def test_push_strips_frontmatter(
        self, mock_convert, tmp_path
    ):
        """Frontmatter is stripped before conversion when strip_frontmatter=True."""
        md_file = tmp_path / "page.md"
        md_file.write_text("---\ntitle: Hello\n---\n# Hello\nWorld")

        mock_result = MagicMock()
        mock_result.text = "= Hello =\nWorld"
        mock_result.converted = True
        mock_result.warnings = []
        mock_convert.return_value = mock_result

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.return_value = {"version": 1}

        await _registry.call_tool(
            "wiki_file_push",
            {
                "file_path": str(md_file),
                "page_name": "TestPage",
                "strip_frontmatter": True,
            },
            client,
        )

        # auto_convert should have received content without frontmatter
        convert_call = mock_convert.call_args
        assert "---" not in convert_call[0][0]
        assert "# Hello" in convert_call[0][0]

    @patch("trac_mcp_server.mcp.tools.wiki_file.auto_convert")
    async def test_push_preserves_frontmatter(
        self, mock_convert, tmp_path
    ):
        """Frontmatter is preserved when strip_frontmatter=False."""
        md_file = tmp_path / "page.md"
        md_file.write_text("---\ntitle: Hello\n---\n# Hello")

        mock_result = MagicMock()
        mock_result.text = "converted"
        mock_result.converted = True
        mock_result.warnings = []
        mock_convert.return_value = mock_result

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.return_value = {"version": 1}

        await _registry.call_tool(
            "wiki_file_push",
            {
                "file_path": str(md_file),
                "page_name": "TestPage",
                "strip_frontmatter": False,
            },
            client,
        )

        # auto_convert should have received content WITH frontmatter
        convert_call = mock_convert.call_args
        assert "---" in convert_call[0][0]

    async def test_push_tracwiki_no_conversion(self, tmp_path):
        """Push a .wiki file passes content through without conversion."""
        wiki_file = tmp_path / "page.wiki"
        wiki_content = "= Title =\n\n'''bold'''"
        wiki_file.write_text(wiki_content)

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
        # put_wiki_page should receive the original content
        put_call = client.put_wiki_page.call_args
        assert put_call[0][1] == wiki_content

    @patch("trac_mcp_server.mcp.tools.wiki_file.auto_convert")
    async def test_push_new_page_info_returns_zero(
        self, mock_convert, tmp_path
    ):
        """Push creates page when get_wiki_page_info returns 0 instead of Fault.

        Some Trac instances return 0 (int) for non-existent pages rather than
        raising xmlrpc.client.Fault. The handler must treat falsy/non-dict
        returns as 'page not found' and create the page.
        """
        md_file = tmp_path / "page.md"
        md_file.write_text("# New Page\nContent here")

        mock_result = MagicMock()
        mock_result.text = "= New Page =\nContent here"
        mock_result.converted = True
        mock_result.warnings = []
        mock_convert.return_value = mock_result

        # TracClient: get_wiki_page_info returns 0 (the bug scenario)
        client = _make_client()
        client.get_wiki_page_info.return_value = 0
        client.put_wiki_page.return_value = {
            "version": 1,
            "name": "NewPage",
        }

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "NewPage"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["action"] == "created"
        assert result.structuredContent["version"] == 1
        # put_wiki_page should have been called with version=None (create mode)
        call_args = client.put_wiki_page.call_args
        assert call_args[0][3] is None  # version argument

    @patch(
        "trac_mcp_server.mcp.tools.wiki_file.read_file_with_encoding"
    )
    async def test_push_warns_on_non_utf8_detected_encoding(
        self, mock_read, tmp_path
    ):
        """ticket #48: a non-utf-8 detected encoding must surface as a
        warning in the tool result rather than staying silent -- the
        exact "nothing indicates a substitution occurred" complaint the
        ticket raised about wiki_file_push.
        """
        wiki_file = tmp_path / "page.wiki"
        wiki_file.write_text("= Title =\n'''bold'''")
        mock_read.return_value = ("= Title =\n'''bold'''", "cp1250")

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
        warnings = result.structuredContent["warnings"]
        assert any("cp1250" in w for w in warnings)
        assert any("UTF-8" in w for w in warnings)
        assert "cp1250" in result.content[0].text

    @patch("trac_mcp_server.mcp.tools.wiki_file.auto_convert")
    async def test_push_no_encoding_warning_for_utf8(
        self, mock_convert, tmp_path
    ):
        """No spurious warning for the ordinary UTF-8 case."""
        md_file = tmp_path / "page.md"
        md_file.write_text("# Hello\nWorld")

        mock_result = MagicMock()
        mock_result.text = "= Hello =\nWorld"
        mock_result.converted = True
        mock_result.warnings = []
        mock_convert.return_value = mock_result

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

        assert result.structuredContent["warnings"] == []

    async def test_push_missing_file_path(self):
        client = _make_client()
        result = await _registry.call_tool(
            "wiki_file_push", {"page_name": "TestPage"}, client
        )
        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "file_path is required" in result.content[0].text

    async def test_push_missing_page_name(self, tmp_path):
        md_file = tmp_path / "page.md"
        md_file.write_text("hello")
        client = _make_client()
        result = await _registry.call_tool(
            "wiki_file_push", {"file_path": str(md_file)}, client
        )
        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "page_name is required" in result.content[0].text

    # --- ticket #68: silent code-block indentation loss -----------------
    #
    # These deliberately do NOT patch auto_convert. The defect IS the
    # conversion, so a mocked converter could only assert that the plumbing
    # calls something -- the real one is what makes the seed a seed. No
    # network is reached: target_format is passed explicitly, so the
    # capability lookup that would need a server never runs.

    async def test_push_refuses_a_block_that_would_lose_indentation(
        self, tmp_path
    ):
        """Ticket #68, operator decision: refuse, do not warn.

        A `{{{ }}}` block in a Markdown-detected file has its body
        indentation eaten by paragraph handling, and every signal an
        author checks stays clean -- the call succeeds, the render looks
        plausible, `warnings` comes back empty. Warning on a path that
        then stores the damaged bytes anyway would leave the corruption
        in the page.

        Asserted on what reached the server, per the ticket's acceptance
        section: `put_wiki_page` must not be called at all. Asserting on
        the response alone would pass against a handler that reported the
        problem and stored the content regardless.
        """
        md_file = tmp_path / "page.md"
        md_file.write_text(
            "{{{#!python\ndef f(x):\n    return 1\n}}}\n"
        )

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "validation_error" in result.content[0].text
        assert "indentation" in result.content[0].text
        client.put_wiki_page.assert_not_called()

    async def test_push_stores_the_same_block_verbatim_as_tracwiki(
        self, tmp_path
    ):
        """The escape hatch the refusal's hint names, exercised.

        Same bytes, same tool, `format="tracwiki"` -- stored verbatim
        with its indentation intact. Paired with the refusal above
        because the point is the contrast: the check must gate on the
        declared format, not on the content, or it would make a
        legitimate TracWiki push impossible.
        """
        md_file = tmp_path / "page.md"
        content = "{{{#!python\ndef f(x):\n    return 1\n}}}\n"
        md_file.write_text(content)

        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "not found"
        )
        client.put_wiki_page.return_value = {"version": 1}

        result = await _registry.call_tool(
            "wiki_file_push",
            {
                "file_path": str(md_file),
                "page_name": "TestPage",
                "format": "tracwiki",
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is not True
        assert client.put_wiki_page.call_args[0][1] == content

    async def test_push_allows_a_markdown_fence_with_indentation(
        self, tmp_path
    ):
        """The recall gate at the write surface.

        The safe shape from the ticket's section 2 control rows. A check
        that refused this would make the tool useless for any file
        containing indented code, which is most of them -- and an
        over-firing check is what #57 and #59 record getting muted here.
        """
        md_file = tmp_path / "page.md"
        md_file.write_text("```python\ndef f(x):\n    return 1\n```\n")

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

        assert isinstance(result, types.CallToolResult)
        assert result.isError is not True
        assert "    return 1" in client.put_wiki_page.call_args[0][1]


# =============================================================================
# _handle_pull (via ToolRegistry.call_tool)
# =============================================================================


class TestPull:
    """Tests for wiki_file_pull handler."""

    async def test_pull_markdown_format(self, tmp_path):
        """Pull a wiki page to a .md file with TracWiki-to-Markdown conversion."""
        out_file = tmp_path / "page.md"

        client = _make_client()
        client.get_wiki_page.return_value = (
            "= Hello =\n\n'''bold''' text."
        )
        client.get_wiki_page_info.return_value = {
            "version": 5,
            "author": "admin",
        }

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
        assert result.structuredContent["page_name"] == "TestPage"
        assert result.structuredContent["format"] == "markdown"
        assert result.structuredContent["version"] == 5
        assert result.structuredContent["converted"] is True
        assert result.structuredContent["bytes_written"] > 0

        # Verify file was actually written with converted markdown content
        written = out_file.read_text()
        assert "# Hello" in written
        assert "**bold**" in written

    async def test_pull_tracwiki_format(self, tmp_path):
        """Pull a wiki page to a .wiki file without conversion."""
        out_file = tmp_path / "page.wiki"
        wiki_content = "= Title =\n\n'''bold''' and {{{code}}}"

        client = _make_client()
        client.get_wiki_page.return_value = wiki_content
        client.get_wiki_page_info.return_value = {
            "version": 3,
            "author": "admin",
        }

        result = await _registry.call_tool(
            "wiki_file_pull",
            {
                "page_name": "TestPage",
                "file_path": str(out_file),
                "format": "tracwiki",
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["format"] == "tracwiki"
        assert result.structuredContent["converted"] is False

        # Verify file was written with original TracWiki content unchanged
        written = out_file.read_text()
        assert written == wiki_content

    async def test_pull_default_format_is_markdown(self, tmp_path):
        """Default format is markdown when not specified."""
        out_file = tmp_path / "page.md"

        client = _make_client()
        client.get_wiki_page.return_value = "= Hello ="
        client.get_wiki_page_info.return_value = {"version": 1}

        result = await _registry.call_tool(
            "wiki_file_pull",
            {"page_name": "TestPage", "file_path": str(out_file)},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["format"] == "markdown"
        assert result.structuredContent["converted"] is True

    async def test_pull_specific_version(self, tmp_path):
        """Pull a specific version of a wiki page."""
        out_file = tmp_path / "page.md"

        client = _make_client()
        client.get_wiki_page.return_value = "= Old Version ="
        client.get_wiki_page_info.return_value = {
            "version": 2,
            "author": "admin",
        }

        result = await _registry.call_tool(
            "wiki_file_pull",
            {
                "page_name": "TestPage",
                "file_path": str(out_file),
                "version": 2,
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["version"] == 2

        # Verify get_wiki_page was called with version=2
        client.get_wiki_page.assert_called_once_with("TestPage", 2)
        client.get_wiki_page_info.assert_called_once_with("TestPage", 2)

    async def test_pull_nonexistent_page(self, tmp_path):
        """Pull a page that doesn't exist returns not_found error."""
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
        assert "NoSuchPage" in result.content[0].text
        assert "wiki_search" in result.content[0].text

        # File should not have been created
        assert not out_file.exists()

    async def test_pull_missing_page_name(self, tmp_path):
        """Missing page_name returns validation error."""
        out_file = tmp_path / "page.md"
        client = _make_client()
        result = await _registry.call_tool(
            "wiki_file_pull",
            {"file_path": str(out_file)},
            client,
        )
        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "page_name is required" in result.content[0].text

    async def test_pull_missing_file_path(self):
        """Missing file_path returns validation error."""
        client = _make_client()
        result = await _registry.call_tool(
            "wiki_file_pull",
            {"page_name": "TestPage"},
            client,
        )
        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "file_path is required" in result.content[0].text

    async def test_pull_invalid_output_path(self):
        """Invalid output path (parent doesn't exist) returns validation error."""
        client = _make_client()
        result = await _registry.call_tool(
            "wiki_file_pull",
            {
                "page_name": "TestPage",
                "file_path": "/nonexistent/dir/page.md",
            },
            client,
        )
        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "validation_error" in result.content[0].text

    async def test_pull_other_fault_reraises(self, tmp_path):
        """Non-not-found Fault is caught by the registry error handler."""
        out_file = tmp_path / "page.md"

        client = _make_client()
        client.get_wiki_page.side_effect = xmlrpc.client.Fault(
            403, "Permission denied"
        )

        result = await _registry.call_tool(
            "wiki_file_pull",
            {"page_name": "SecretPage", "file_path": str(out_file)},
            client,
        )

        # ToolRegistry classifies fault 403 as permission_denied
        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        assert "permission_denied" in result.content[0].text
