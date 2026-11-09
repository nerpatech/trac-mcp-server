"""Tests for wiki_file tool handlers: detect_format, push, pull, and frontmatter stripping."""

import xmlrpc.client
from pathlib import Path
from unittest.mock import MagicMock, patch

import mcp.types as types
import pytest

from trac_mcp_server.converters.front_matter import split_front_matter
from trac_mcp_server.converters.html_to_markdown import (
    trac_html_to_markdown,
)
from trac_mcp_server.core.client import (
    WikiPageConflict,
    WikiPageNotModified,
)
from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.mcp.tools.wiki_file import (
    WIKI_FILE_SPECS,
    _strip_yaml_frontmatter,
)

# These tests exercise the server-side path forms (ticket #111).
pytestmark = pytest.mark.usefixtures("local_file_access")

_registry = ToolRegistry(WIKI_FILE_SPECS)

# The server has no tracrpc_comment conditional put (Trac
# /trac_mcp_server #129): put_wiki_page falls back to its client-side
# precheck, which is the path the stand-ins below were written for.
_NO_PLUGIN = xmlrpc.client.Fault(
    -32601, 'RPC method "wiki.putPageIfVersion" not found'
)

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
        assert "content is required" in result.content[0].text

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
    client.config.link_rewrites = ()
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
    async def test_push_concurrent_edit_is_a_version_conflict(
        self, mock_convert, tmp_path
    ):
        """A page edited between the push's read and its write is reported
        as version_conflict, not a generic error (ticket #120)."""
        md_file = tmp_path / "page.md"
        md_file.write_text("# Updated\nContent")

        mock_result = MagicMock()
        mock_result.text = "= Updated =\nContent"
        mock_result.converted = True
        mock_result.warnings = []
        mock_convert.return_value = mock_result

        client = _make_client()
        client.get_wiki_page_info.return_value = {"version": 3}
        client.put_wiki_page.side_effect = WikiPageConflict(
            "stale", "TestPage", 3, 4
        )

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        assert result.isError
        text = result.content[0].text
        assert "Error (version_conflict)" in text
        assert "Current version is 4" in text
        assert "you tried to update version 3" in text

    @patch("trac_mcp_server.mcp.tools.wiki_file.auto_convert")
    async def test_push_page_deleted_between_read_and_write(
        self, mock_convert, tmp_path
    ):
        """current_version None: the page vanished after it was read."""
        md_file = tmp_path / "page.md"
        md_file.write_text("# Updated\nContent")

        mock_result = MagicMock()
        mock_result.text = "= Updated =\nContent"
        mock_result.converted = True
        mock_result.warnings = []
        mock_convert.return_value = mock_result

        client = _make_client()
        client.get_wiki_page_info.return_value = {"version": 3}
        client.put_wiki_page.side_effect = WikiPageConflict(
            "gone", "TestPage", 3, None
        )

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md_file), "page_name": "TestPage"},
            client,
        )

        assert result.isError
        text = result.content[0].text
        assert "Error (version_conflict)" in text
        assert "Page 'TestPage' was deleted after it was read." in text
        assert "Push again to recreate it." in text
        assert "has been modified" not in text
        assert "wiki_get" not in text

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

    @patch("trac_mcp_server.mcp.tools.file_io.read_file_with_encoding")
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
        assert "content is required" in result.content[0].text
        client.put_wiki_page.assert_not_called()

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


class TestPushReadbackAfterWrite:
    """A fault after a successful putPage (Trac /trac_mcp_server #140).

    A real TracClient with only ``_rpc_request`` mocked: putPage returns
    True and the read-back getPageInfo that follows it faults. Everything
    between -- put_wiki_page's precheck and read-back, and the handler's
    existence probe -- runs for real.
    """

    @staticmethod
    def _client(mock_config, readback_fault):
        from trac_mcp_server.core.client import TracClient

        client = TracClient(mock_config)
        calls: list[tuple] = []
        page_info = {
            "name": "X",
            "version": 3,
            "author": "someone",
            "lastModified": 1234567890,
        }

        def _rpc(namespace, method, *args):
            calls.append((namespace, method, *args))
            if method == "putPageIfVersion":
                raise _NO_PLUGIN
            if method == "putPage":
                return True
            if method == "getPageInfo":
                wrote = any(c[1] == "putPage" for c in calls[:-1])
                if wrote:
                    raise readback_fault
                return dict(page_info)
            raise AssertionError(f"unexpected RPC {namespace}.{method}")

        return client, calls, _rpc

    async def _push(self, mock_config, readback_fault):
        client, calls, rpc = self._client(mock_config, readback_fault)
        with (
            patch.object(client, "_rpc_request", side_effect=rpc),
            patch(
                "trac_mcp_server.mcp.tools.wiki_file.gate_or_refuse",
                return_value=(None, []),
            ),
        ):
            result = await _registry.call_tool(
                "wiki_file_push",
                {"content": "= X =\nnew text\n", "page_name": "X"},
                client,
            )
        puts = [c for c in calls if c[1] == "putPage"]
        return result, puts

    async def test_not_found_readback_does_not_write_twice(
        self, mock_config
    ):
        """The page is deleted between putPage and its read-back: the
        read-back's "not found" fault must not reach the create fallback,
        which would post putPage again and recreate the deleted page."""
        result, puts = await self._push(
            mock_config, xmlrpc.client.Fault(1, "Page 'X' not found")
        )

        assert len(puts) == 1
        # With the existing page's version, i.e. the update -- not a
        # create with no version.
        assert puts[0][4]["version"] == 3
        assert not result.isError
        assert result.structuredContent["action"] == "updated"
        assert result.structuredContent["readback_failed"] is True

    async def test_other_readback_fault_reports_the_page_as_stored(
        self, mock_config
    ):
        """Any other read-back fault after a stored write must say the
        page was stored, not come back as an error (which means nothing
        was stored, and invites a retry)."""
        result, puts = await self._push(
            mock_config, xmlrpc.client.Fault(1, "Internal error")
        )

        assert len(puts) == 1
        assert not result.isError
        sc = result.structuredContent
        assert sc["readback_failed"] is True
        assert sc["version"] is None
        assert "Internal error" in sc["readback_error"]
        text = result.content[0].text
        assert "WAS stored" in text
        assert "Do not push again" in text

    async def test_clean_push_reports_no_readback_failure(self):
        client = _make_client()
        client.get_wiki_page_info.return_value = {"version": 3}
        client.put_wiki_page.return_value = {"version": 4}
        result = await _registry.call_tool(
            "wiki_file_push",
            {"content": "= X =\n", "page_name": "X"},
            client,
        )
        assert result.structuredContent["readback_failed"] is False
        assert "readback_error" not in result.structuredContent

    async def test_clean_push_reports_no_possible_overwrite(self):
        client = _make_client()
        client.get_wiki_page_info.return_value = {"version": 3}
        client.put_wiki_page.return_value = {
            "version": 4,
            "possible_overwrite": False,
        }
        result = await _registry.call_tool(
            "wiki_file_push",
            {"content": "= X =\n", "page_name": "X"},
            client,
        )
        assert result.structuredContent["possible_overwrite"] is False
        assert "intervening_versions" not in result.structuredContent

    async def test_probe_not_found_still_creates_once(self):
        """The fallback the fix narrows still works: a not-found fault
        from the existence probe creates the page, with one write."""
        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "Page X does not exist"
        )
        client.put_wiki_page.return_value = {"version": 1}
        result = await _registry.call_tool(
            "wiki_file_push",
            {"content": "= X =\n", "page_name": "X"},
            client,
        )
        assert result.structuredContent["action"] == "created"
        client.put_wiki_page.assert_called_once()
        assert client.put_wiki_page.call_args[0][3] is None

    async def test_probe_real_404_means_absent_and_creates(self):
        """TracXMLRPC's missing-page fault, Fault(404, 'Wiki page "X"
        does not exist'), is classified by its code (Trac #191)."""
        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            404, 'Wiki page "X" does not exist'
        )
        client.put_wiki_page.return_value = {"version": 1}
        result = await _registry.call_tool(
            "wiki_file_push",
            {"content": "= X =\n", "page_name": "X"},
            client,
        )
        assert result.structuredContent["action"] == "created"
        client.put_wiki_page.assert_called_once()

    async def test_probe_code_1_quoted_name_is_not_absence(self):
        """A code-1 fault is any TracError, and the name it quotes,
        'Not Found Notes', is no evidence that the page is missing:
        the probe's fault propagates (the dispatcher's server error)
        and nothing is written (Trac #191)."""
        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1,
            "'Invalid Wiki page name 'Not Found Notes'' while "
            "executing 'wiki.getPageInfo()'",
        )
        result = await _registry.call_tool(
            "wiki_file_push",
            {"content": "= X =\n", "page_name": "Not Found Notes"},
            client,
        )
        assert result.isError is True
        assert "server_error" in result.content[0].text
        assert "not_found" not in result.content[0].text
        client.put_wiki_page.assert_not_called()

    async def test_write_fault_is_not_retried_as_a_create(self):
        """A not-found fault raised by the write itself (not the probe)
        is an error, never a second write."""
        client = _make_client()
        client.get_wiki_page_info.return_value = {"version": 3}
        client.put_wiki_page.side_effect = xmlrpc.client.Fault(
            1, "Page X not found"
        )
        result = await _registry.call_tool(
            "wiki_file_push",
            {"content": "= X =\n", "page_name": "X"},
            client,
        )
        assert result.isError
        client.put_wiki_page.assert_called_once()


class TestPushNotModified:
    """An identical push stores nothing and says so (Trac
    /trac_mcp_server #174): a success-shaped "unchanged" result."""

    async def test_identical_push_is_an_unchanged_success(self):
        client = _make_client()
        client.get_wiki_page_info.return_value = {"version": 3}
        client.put_wiki_page.side_effect = WikiPageNotModified(
            "Page not modified (content identical)", "X", 3
        )
        result = await _registry.call_tool(
            "wiki_file_push",
            {"content": "= X =\n", "page_name": "X"},
            client,
        )

        assert not result.isError
        assert result.content[0].text.splitlines()[0] == (
            "Wiki page 'X' unchanged (version 3): content identical "
            "to the stored page. Trac stored nothing."
        )
        sc = result.structuredContent
        assert sc["action"] == "unchanged"
        assert sc["version"] == 3
        assert sc["page_name"] == "X"
        assert sc["readback_failed"] is False
        assert sc["possible_overwrite"] is False
        assert sc["warnings"] == []

    async def test_unchanged_reports_the_reread_current_version(self):
        """Identical content stored as v4 by someone else after the
        probe saw v3: version is the re-read 4 (ticket #174 review)."""
        client = _make_client()
        client.get_wiki_page_info.return_value = {"version": 3}
        client.put_wiki_page.side_effect = WikiPageNotModified(
            "Page not modified (content identical)", "X", 3, 4
        )
        result = await _registry.call_tool(
            "wiki_file_push",
            {"content": "= X =\n", "page_name": "X"},
            client,
        )

        assert not result.isError
        assert result.content[0].text.splitlines()[0] == (
            "Wiki page 'X' unchanged (version 4): content identical "
            "to the stored page. Trac stored nothing."
        )
        assert result.structuredContent["action"] == "unchanged"
        assert result.structuredContent["version"] == 4

    async def test_unchanged_create_path_reports_the_reread_version(
        self,
    ):
        """The probe found no page (version None), yet the write was a
        no-op: someone created it identically in between. The re-read
        version is reported."""
        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "Page not found"
        )
        client.put_wiki_page.side_effect = WikiPageNotModified(
            "Page not modified (content identical)", "X", None, 1
        )
        result = await _registry.call_tool(
            "wiki_file_push",
            {"content": "= X =\n", "page_name": "X"},
            client,
        )

        assert not result.isError
        assert result.content[0].text.splitlines()[0] == (
            "Wiki page 'X' unchanged (version 1): content identical "
            "to the stored page. Trac stored nothing."
        )
        assert result.structuredContent["action"] == "unchanged"
        assert result.structuredContent["version"] == 1

    async def test_unchanged_with_no_known_version(self):
        client = _make_client()
        client.get_wiki_page_info.side_effect = xmlrpc.client.Fault(
            1, "Page not found"
        )
        client.put_wiki_page.side_effect = WikiPageNotModified(
            "Page not modified (content identical)", "X", None, None
        )
        result = await _registry.call_tool(
            "wiki_file_push",
            {"content": "= X =\n", "page_name": "X"},
            client,
        )

        assert not result.isError
        assert result.content[0].text.splitlines()[0] == (
            "Wiki page 'X' unchanged (version unknown): content "
            "identical to the stored page. Trac stored nothing."
        )
        assert result.structuredContent["version"] is None

    async def test_unchanged_keeps_warnings_and_gate_lines(self):
        client = _make_client()
        client.get_wiki_page_info.return_value = {"version": 3}
        client.put_wiki_page.side_effect = WikiPageNotModified(
            "Page not modified (content identical)", "X", 3
        )
        gate_note = "Link gate: OFF (test note)"
        with (
            patch(
                "trac_mcp_server.mcp.tools.wiki_file.gate_or_refuse",
                return_value=(None, [gate_note]),
            ),
            patch(
                "trac_mcp_server.mcp.tools.wiki_file.auto_convert"
            ) as mock_convert,
        ):
            conversion = MagicMock()
            conversion.text = "= X =\n"
            conversion.converted = True
            conversion.warnings = ["a conversion warning"]
            mock_convert.return_value = conversion
            result = await _registry.call_tool(
                "wiki_file_push",
                {
                    "content": "# X\n",
                    "filename": "x.md",
                    "page_name": "X",
                },
                client,
            )

        assert not result.isError
        text = result.content[0].text
        assert text.splitlines()[0].startswith(
            "Wiki page 'X' unchanged (version 3)"
        )
        assert "Conversion warnings:" in text
        assert "- a conversion warning" in text
        assert gate_note in text.splitlines()
        sc = result.structuredContent
        assert sc["action"] == "unchanged"
        assert sc["warnings"] == ["a conversion warning"]
        assert sc["gate_notes"] == [gate_note]

    async def test_real_client_plugin_not_modified_fault(
        self, mock_config
    ):
        """End to end through a real TracClient: the plugin's "Page not
        modified" fault comes back as the unchanged result."""
        from trac_mcp_server.core.client import TracClient

        client = TracClient(mock_config)

        def _rpc(namespace, method, *args):
            if method == "getPageInfo":
                return {"name": "X", "version": 3}
            if method == "putPageIfVersion":
                raise xmlrpc.client.Fault(
                    1,
                    "'Page not modified' while executing "
                    "'wiki.putPageIfVersion()'",
                )
            raise AssertionError(f"unexpected RPC {namespace}.{method}")

        with (
            patch.object(client, "_rpc_request", side_effect=_rpc),
            patch(
                "trac_mcp_server.mcp.tools.wiki_file.gate_or_refuse",
                return_value=(None, []),
            ),
        ):
            result = await _registry.call_tool(
                "wiki_file_push",
                {"content": "= X =\n", "page_name": "X"},
                client,
            )

        assert not result.isError
        assert result.structuredContent["action"] == "unchanged"
        assert result.structuredContent["version"] == 3


class TestPushPossibleOverwrite:
    """wiki_file_push when the read-back version is not version + 1
    (Trac /trac_mcp_server #130).

    A real TracClient with only ``_rpc_request`` mocked. Before putPage,
    getPageInfo (the handler's existence probe and put_wiki_page's
    precheck) sees ``before`` -- ``None`` for a missing page; after it,
    the read-back sees ``after``.
    """

    @staticmethod
    async def _push(mock_config, before, after):
        from trac_mcp_server.core.client import TracClient

        client = TracClient(mock_config)
        calls: list[tuple] = []

        def _rpc(namespace, method, *args):
            calls.append((namespace, method, *args))
            if method == "putPageIfVersion":
                raise _NO_PLUGIN
            if method == "putPage":
                return True
            if method == "getPageInfo":
                if any(c[1] == "putPage" for c in calls[:-1]):
                    return {"name": "X", "version": after}
                if before is None:
                    raise xmlrpc.client.Fault(1, "Page 'X' not found")
                return {"name": "X", "version": before}
            raise AssertionError(f"unexpected RPC {namespace}.{method}")

        with (
            patch.object(client, "_rpc_request", side_effect=_rpc),
            patch(
                "trac_mcp_server.mcp.tools.wiki_file.gate_or_refuse",
                return_value=(None, []),
            ),
        ):
            result = await _registry.call_tool(
                "wiki_file_push",
                {"content": "= X =\nnew text\n", "page_name": "X"},
                client,
            )
        puts = [c for c in calls if c[1] == "putPage"]
        return result, puts

    async def test_skipped_version_is_flagged_and_warned(
        self, mock_config
    ):
        result, puts = await self._push(mock_config, before=3, after=5)

        assert len(puts) == 1
        assert puts[0][4]["version"] == 3
        # A success: the page was stored.
        assert not result.isError
        sc = result.structuredContent
        assert sc["action"] == "updated"
        assert sc["version"] == 5
        assert sc["readback_failed"] is False
        assert sc["possible_overwrite"] is True
        assert sc["intervening_versions"] == [4]
        text = result.content[0].text
        assert "possible overwrite" in text
        assert (
            "Version 4 was most likely written by someone else" in text
        )
        assert (
            "wiki_diff(page_name='X', from_version=3, to_version=4)"
            in text
        )

    async def test_uncontended_update_stays_quiet(self, mock_config):
        result, puts = await self._push(mock_config, before=3, after=4)

        assert len(puts) == 1
        sc = result.structuredContent
        assert sc["version"] == 4
        assert sc["possible_overwrite"] is False
        assert "intervening_versions" not in sc
        assert "overwrite" not in result.content[0].text.lower()

    async def test_create_is_never_flagged(self, mock_config):
        # Even a read-back version other than 1 (the page was written
        # again right after the create) says nothing about an overwrite.
        result, puts = await self._push(
            mock_config, before=None, after=2
        )

        assert len(puts) == 1
        assert "version" not in puts[0][4]
        sc = result.structuredContent
        assert sc["action"] == "created"
        assert sc["possible_overwrite"] is False
        assert "overwrite" not in result.content[0].text.lower()


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
        client.get_wiki_page_html.return_value = (
            '<h1 id="Hello">Hello</h1>\n'
            "<p>\n<strong>bold</strong> text.\n</p>\n"
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
        client.get_wiki_page_html.return_value = (
            '<h1 id="Hello">Hello</h1>\n'
        )
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
        client.get_wiki_page_html.return_value = (
            '<h1 id="OldVersion">Old Version</h1>\n'
        )
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
        client.get_wiki_page_html.assert_called_once_with("TestPage", 2)
        client.get_wiki_page_info.assert_called_once_with("TestPage", 2)

    @pytest.mark.parametrize("info", [{}, 0, None, {"version": 0}])
    async def test_pull_markdown_warns_when_version_cannot_be_read(
        self, tmp_path, info
    ):
        """No usable version from get_wiki_page_info: the pull still
        works, but the source and HTML reads were not pinned to one
        version, and the caller is told in ``warnings``."""
        out_file = tmp_path / "page.md"

        client = _make_client()
        client.get_wiki_page.return_value = "= Hello ="
        client.get_wiki_page_html.return_value = (
            '<h1 id="Hello">Hello</h1>\n'
        )
        client.get_wiki_page_info.return_value = info

        result = await _registry.call_tool(
            "wiki_file_pull",
            {"page_name": "TestPage", "file_path": str(out_file)},
            client,
        )

        assert not result.isError
        assert "# Hello" in out_file.read_text()
        warnings = result.structuredContent["warnings"]
        assert len(warnings) == 1
        assert "version could not be read" in warnings[0]
        assert "not pinned" in warnings[0]
        assert warnings[0] in result.content[0].text
        client.get_wiki_page.assert_called_once_with("TestPage", None)
        client.get_wiki_page_html.assert_called_once_with(
            "TestPage", None
        )

    async def test_pull_markdown_with_a_version_has_no_pin_warning(
        self, tmp_path
    ):
        client = _make_client()
        client.get_wiki_page.return_value = "= Hello ="
        client.get_wiki_page_html.return_value = (
            '<h1 id="Hello">Hello</h1>\n'
        )
        client.get_wiki_page_info.return_value = {"version": 4}

        result = await _registry.call_tool(
            "wiki_file_pull",
            {
                "page_name": "TestPage",
                "file_path": str(tmp_path / "page.md"),
            },
            client,
        )

        assert result.structuredContent["warnings"] == []

    async def test_pull_tracwiki_with_no_version_has_no_pin_warning(
        self, tmp_path
    ):
        """tracwiki reads no HTML, so there is nothing to pin."""
        client = _make_client()
        client.get_wiki_page.return_value = "= Hello ="
        client.get_wiki_page_info.return_value = {}

        result = await _registry.call_tool(
            "wiki_file_pull",
            {
                "page_name": "TestPage",
                "file_path": str(tmp_path / "page.wiki"),
                "format": "tracwiki",
            },
            client,
        )

        assert result.structuredContent["warnings"] == []

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

    async def test_pull_real_404_is_not_found(self, tmp_path):
        """TracXMLRPC's missing-page fault, Fault(404, 'Wiki page "X"
        does not exist'), is classified by its code (Trac #191)."""
        out_file = tmp_path / "page.md"

        client = _make_client()
        client.get_wiki_page.side_effect = xmlrpc.client.Fault(
            404, 'Wiki page "NoSuchPage" does not exist'
        )

        result = await _registry.call_tool(
            "wiki_file_pull",
            {"page_name": "NoSuchPage", "file_path": str(out_file)},
            client,
        )

        assert result.isError is True
        assert "Error (not_found)" in result.content[0].text
        assert not out_file.exists()

    async def test_pull_code_1_quoted_name_is_not_not_found(
        self, tmp_path
    ):
        """A code-1 fault is any TracError, and the name it quotes,
        'Not Found Notes', is no evidence that the page is missing: the
        fault propagates to the dispatcher, which reports a server
        error, not a not_found (Trac #191)."""
        out_file = tmp_path / "page.md"

        client = _make_client()
        client.get_wiki_page.side_effect = xmlrpc.client.Fault(
            1,
            "'Invalid Wiki page name 'Not Found Notes'' while "
            "executing 'wiki.getPage()'",
        )

        result = await _registry.call_tool(
            "wiki_file_pull",
            {
                "page_name": "Not Found Notes",
                "file_path": str(out_file),
            },
            client,
        )

        assert result.isError is True
        assert "server_error" in result.content[0].text
        assert "not_found" not in result.content[0].text
        assert not out_file.exists()

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


# =============================================================================
# wiki_file_pull through the HTML -> Markdown translator (Trac #210)
# =============================================================================

_FIXTURES = Path(__file__).parents[2] / "fixtures" / "trac_html"
# stock_TracCgi has two ``{{{#!python`` blocks and two translator
# warnings (a dropped table of contents among them).
_SLUG = "stock_TracCgi"


def _fixture_pair():
    return (
        (_FIXTURES / f"{_SLUG}.html").read_text(encoding="utf-8"),
        (_FIXTURES / f"{_SLUG}.tracwiki").read_text(encoding="utf-8"),
    )


def _fixture_client(version=7):
    """A fake client serving the #208 fixture's source and HTML.

    ``get_wiki_page_info`` reports ``version`` whatever was asked for,
    as Trac reports the latest version when none is asked for.
    """
    html, source = _fixture_pair()
    client = _make_client()
    client.get_wiki_page.return_value = source
    client.get_wiki_page_html.return_value = html
    client.get_wiki_page_info.return_value = {
        "version": version,
        "author": "admin",
    }
    return client


async def _pull(client, tmp_path, **extra):
    """Pull TestPage to a file; return the result and the file's text."""
    out = tmp_path / "out.md"
    result = await _registry.call_tool(
        "wiki_file_pull",
        {"page_name": "TestPage", "file_path": str(out), **extra},
        client,
    )
    return result, (out.read_text() if out.exists() else None)


class TestPullThroughTranslator:
    """format=markdown is Trac's rendered HTML, translated (Trac #210)."""

    async def test_output_is_the_translator_output(self, tmp_path):
        html, source = _fixture_pair()
        expected = trac_html_to_markdown(html, source)

        result, written = await _pull(_fixture_client(), tmp_path)

        assert not result.isError
        # Ticket #212: front matter, then the translator's output.
        assert split_front_matter(written)[1] == expected.text
        assert written.endswith("---\n\n" + expected.text)
        assert result.structuredContent["converted"] is True

    async def test_python_block_is_a_fenced_python_block(
        self, tmp_path
    ):
        """The language comes from the source, which the handler must
        pass to the translator: Trac's HTML does not name it."""
        _, written = await _pull(_fixture_client(), tmp_path)

        assert "```python\n" in written

    async def test_html_read_is_pinned_to_the_info_version_latest(
        self, tmp_path
    ):
        client = _fixture_client(version=7)

        await _pull(client, tmp_path)

        client.get_wiki_page_info.assert_called_once_with(
            "TestPage", None
        )
        client.get_wiki_page.assert_called_once_with("TestPage", 7)
        client.get_wiki_page_html.assert_called_once_with("TestPage", 7)

    async def test_html_read_is_pinned_to_the_info_version_asked(
        self, tmp_path
    ):
        client = _fixture_client(version=3)

        await _pull(client, tmp_path, version=3)

        client.get_wiki_page_info.assert_called_once_with("TestPage", 3)
        client.get_wiki_page.assert_called_once_with("TestPage", 3)
        client.get_wiki_page_html.assert_called_once_with("TestPage", 3)

    async def test_info_is_read_before_the_source_and_the_html(
        self, tmp_path
    ):
        client = _fixture_client()
        order = []
        for name in (
            "get_wiki_page_info",
            "get_wiki_page",
            "get_wiki_page_html",
        ):
            mock = getattr(client, name)
            mock.side_effect = (
                lambda *a, _n=name, _r=mock.return_value, **k: (
                    order.append(_n) or _r
                )
            )

        await _pull(client, tmp_path)

        assert order[0] == "get_wiki_page_info"
        assert set(order) == {
            "get_wiki_page_info",
            "get_wiki_page",
            "get_wiki_page_html",
        }

    async def test_warnings_reach_the_text_block_and_structured_content(
        self, tmp_path
    ):
        html, source = _fixture_pair()
        warnings = trac_html_to_markdown(html, source).warnings
        assert warnings  # the fixture is chosen for having some

        result, _ = await _pull(_fixture_client(), tmp_path)

        assert result.structuredContent["warnings"] == warnings
        text = result.content[0].text
        assert "Conversion warnings:" in text
        for warning in warnings:
            assert warning in text

    async def test_warnings_is_always_a_list(self, tmp_path):
        client = _make_client()
        client.get_wiki_page.return_value = "= Hello ="
        client.get_wiki_page_html.return_value = (
            '<h1 id="Hello">Hello</h1>\n'
        )
        client.get_wiki_page_info.return_value = {"version": 1}

        result, _ = await _pull(client, tmp_path)
        assert result.structuredContent["warnings"] == []
        assert "Conversion warnings:" not in result.content[0].text

        tracwiki, _ = await _pull(client, tmp_path, format="tracwiki")
        assert tracwiki.structuredContent["warnings"] == []

    async def test_tracwiki_never_reads_the_html(self, tmp_path):
        client = _fixture_client()

        result, written = await _pull(
            client, tmp_path, format="tracwiki"
        )

        client.get_wiki_page_html.assert_not_called()
        assert written == _fixture_pair()[1]
        assert result.structuredContent["converted"] is False

    @pytest.mark.parametrize(
        "failing",
        ["get_wiki_page_info", "get_wiki_page", "get_wiki_page_html"],
    )
    async def test_not_found_from_any_read_is_not_found(
        self, failing, tmp_path
    ):
        client = _fixture_client()
        getattr(client, failing).side_effect = xmlrpc.client.Fault(
            404, 'Wiki page "TestPage" does not exist'
        )

        result, _ = await _pull(client, tmp_path)

        assert result.isError is True
        assert "Error (not_found)" in result.content[0].text
        assert "TestPage" in result.content[0].text

    async def test_other_fault_from_the_html_read_is_not_not_found(
        self, tmp_path
    ):
        client = _fixture_client()
        client.get_wiki_page_html.side_effect = xmlrpc.client.Fault(
            403, "Permission denied"
        )

        result, _ = await _pull(client, tmp_path)

        assert result.isError is True
        assert "permission_denied" in result.content[0].text

    def test_tool_description_says_what_markdown_is(self, tmp_path):
        from trac_mcp_server.mcp.tools.wiki_file import WIKI_FILE_TOOLS

        tool = next(
            t for t in WIKI_FILE_TOOLS if t.name == "wiki_file_pull"
        )
        text = " ".join(tool.description.split())
        assert "rendered HTML" in text
        assert "one-way" in text
        assert "not meant to be pushed back" in text


# =============================================================================
# wiki_file_pull link rewriting (Trac #213)
# =============================================================================

_PREFIX = "http://localhost/trac/wiki/"
_RULE = {"from": _PREFIX, "to": "https://blog.example/"}
_LINKS_HTML = (
    f'<p><a href="{_PREFIX}Other">Other</a> '
    '<a href="#Heading">Heading</a> '
    '<img alt="i" src="http://localhost/trac/raw-attachment/wiki/P/i.png"/>'
    "</p>\n"
)


def _links_client(configured=()):
    client = _make_client()
    client.config.link_rewrites = configured
    client.get_wiki_page.return_value = "[wiki:Other Other]"
    client.get_wiki_page_html.return_value = _LINKS_HTML
    client.get_wiki_page_info.return_value = {"version": 2}
    return client


class TestPullLinkRewrites:
    async def test_no_map_anywhere_rewrites_nothing(self, tmp_path):
        result, text = await _pull(_links_client(), tmp_path)
        assert (
            result.structuredContent["link_rewrites_source"] == "none"
        )
        assert f"[Other]({_PREFIX}Other)" in text

    async def test_the_configured_map_applies_when_the_call_has_none(
        self, tmp_path
    ):
        client = _links_client(((_RULE["from"], _RULE["to"]),))
        result, text = await _pull(client, tmp_path)
        assert result.structuredContent["link_rewrites_source"] == (
            "config"
        )
        assert "[Other](https://blog.example/Other)" in text

    async def test_the_call_replaces_the_configured_map(self, tmp_path):
        client = _links_client(
            ((_RULE["from"], "https://cfg.example/"),)
        )
        result, text = await _pull(
            client, tmp_path, link_rewrites=[_RULE]
        )
        assert (
            result.structuredContent["link_rewrites_source"] == "call"
        )
        assert "[Other](https://blog.example/Other)" in text
        assert "cfg.example" not in text

    async def test_an_empty_call_list_disables_rewriting(
        self, tmp_path
    ):
        client = _links_client(((_RULE["from"], _RULE["to"]),))
        result, text = await _pull(client, tmp_path, link_rewrites=[])
        assert (
            result.structuredContent["link_rewrites_source"] == "call"
        )
        assert f"[Other]({_PREFIX}Other)" in text

    async def test_an_in_page_anchor_and_an_image_are_never_rewritten(
        self, tmp_path
    ):
        rules = [
            _RULE,
            {"from": "#", "to": "https://blog.example/#"},
            {"from": "http://localhost/trac/", "to": "/x/"},
        ]
        _, text = await _pull(
            _links_client(), tmp_path, link_rewrites=rules
        )
        assert "[Heading](#Heading)" in text
        assert (
            "![i](http://localhost/trac/raw-attachment/wiki/P/i.png)"
            in text
        )
        assert "[Other](https://blog.example/Other)" in text

    async def test_tracwiki_ignores_the_map_and_reports_none(
        self, tmp_path
    ):
        client = _links_client(((_RULE["from"], _RULE["to"]),))
        result, text = await _pull(
            client, tmp_path, format="tracwiki", link_rewrites=[_RULE]
        )
        assert (
            result.structuredContent["link_rewrites_source"] == "none"
        )
        assert text == "[wiki:Other Other]"

    @pytest.mark.parametrize(
        "bad",
        [
            [{"from": "", "to": "x"}],
            [_RULE, _RULE],
            [{"from": 1, "to": "x"}],
            [{"from": "a", "to": 2}],
            [{"from": "a"}],
            ["a=b"],
            "a=b",
        ],
    )
    async def test_a_bad_rule_is_a_validation_error_before_any_read(
        self, tmp_path, bad
    ):
        client = _links_client()
        result, text = await _pull(client, tmp_path, link_rewrites=bad)
        assert result.isError is True
        assert "validation_error" in result.content[0].text
        assert "link_rewrites" in result.content[0].text
        assert text is None
        client.get_wiki_page_html.assert_not_called()

    def test_the_input_schema_declares_link_rewrites(self):
        from trac_mcp_server.mcp.tools.wiki_file import WIKI_FILE_TOOLS

        tool = next(
            t for t in WIKI_FILE_TOOLS if t.name == "wiki_file_pull"
        )
        prop = tool.inputSchema["properties"]["link_rewrites"]
        assert prop["type"] == "array"
        assert prop["items"]["required"] == ["from", "to"]
        assert "link_rewrites_source" in tool.description


_HTML_FRAGMENT = (
    '<h1 id="Title">Title</h1>\n'
    '<p>\n<a class="wiki" href="http://localhost/trac/wiki/Other">'
    "Other</a> &amp; <strong>bold</strong>\n</p>\n"
)


def _html_client(info=None, configured=()):
    client = _make_client()
    client.config.link_rewrites = configured
    client.get_wiki_page.return_value = "SOURCE MUST NOT BE READ"
    client.get_wiki_page_html.return_value = _HTML_FRAGMENT
    client.get_wiki_page_info.return_value = (
        {"version": 6} if info is None else info
    )
    return client


class TestPullHtml:
    """format=html is Trac's own render, as a minimal document (#211)."""

    async def test_the_document_wraps_the_fragment_exactly(
        self, tmp_path
    ):
        result, text = await _pull(
            _html_client(), tmp_path, format="html"
        )

        assert not result.isError, result.content
        assert text == (
            "<!doctype html>\n"
            "<html>\n"
            "<head>\n"
            '<meta charset="utf-8">\n'
            "<title>TestPage</title>\n"
            "</head>\n"
            "<body>\n" + _HTML_FRAGMENT + "</body></html>\n"
        )
        sc = result.structuredContent
        assert sc["format"] == "html"
        assert sc["version"] == 6
        assert sc["converted"] is True
        assert sc["warnings"] == []

    async def test_trac_own_html_body_wrapper_is_unwrapped(
        self, tmp_path
    ):
        """Real getPageHTML answers ``<html><body>...</body></html>``
        itself (found on stage); the document has one of each."""
        inner = (
            '<h1 class="section" id="Stagecheck">Stage check</h1>\n'
            "<p>\nA link to "
            '<a class="wiki" '
            'href="http://trac.example.org/trac_test/wiki/WikiStart">'
            "the start page</a> and <em>italic</em> text.\n</p>\n"
        )
        client = _html_client()
        client.get_wiki_page_html.return_value = (
            "<html><body>" + inner + "</body></html>"
        )
        result, text = await _pull(client, tmp_path, format="html")

        assert not result.isError, result.content
        assert text == (
            "<!doctype html>\n<html>\n<head>\n"
            '<meta charset="utf-8">\n<title>TestPage</title>\n'
            "</head>\n<body>\n" + inner + "</body></html>\n"
        )
        assert text.count("<html") == 1
        assert text.count("<body") == 1

    @pytest.mark.parametrize(
        "wrapped",
        [
            "  \n<HTML><BODY>{inner}</BODY></HTML>\n  ",
            "<html>\n<body>\n{inner}</body>\n</html>",
            "<Html >\t<Body>{inner}</Body>\r\n</Html>\n",
        ],
    )
    async def test_the_wrapper_is_matched_ignoring_case_and_whitespace(
        self, tmp_path, wrapped
    ):
        inner = "<p>\ninner <b>kept</b>\n</p>\n"
        client = _html_client()
        client.get_wiki_page_html.return_value = wrapped.format(
            inner=inner
        )
        _, text = await _pull(client, tmp_path, format="html")

        assert text.lower().count("<html") == 1
        assert text.lower().count("<body") == 1
        body = text.split("<body>\n", 1)[1]
        assert body.endswith("</body></html>\n")
        assert "inner <b>kept</b>" in body
        assert text.count("</body>") == 1

    async def test_a_bare_fragment_passes_through(self, tmp_path):
        # Trac's wikiToHtml (ticket text) and older Tracs: no wrapper.
        result, text = await _pull(
            _html_client(), tmp_path, format="html"
        )
        assert text.count("<body") == 1
        assert "<body>\n" + _HTML_FRAGMENT + "</body></html>\n" in text

    @pytest.mark.parametrize(
        "slug",
        ["stock_TracCgi", "stock_WikiFormatting", "stock_TracLinks"],
    )
    async def test_a_real_getpagehtml_capture_is_unwrapped(
        self, tmp_path, slug
    ):
        raw = (_FIXTURES / f"{slug}.html").read_text(encoding="utf-8")
        assert raw.startswith("<html><body>")
        assert raw.rstrip().endswith("</body></html>")
        inner = raw.rstrip()[
            len("<html><body>") : -len("</body></html>")
        ]
        client = _html_client()
        client.get_wiki_page_html.return_value = raw
        result, text = await _pull(client, tmp_path, format="html")

        assert not result.isError, result.content
        assert text.count("<html") == 1
        assert text.count("<body") == 1
        assert text.endswith(inner + "</body></html>\n")
        assert "<body>\n" + inner in text

    @pytest.mark.parametrize(
        "prefix",
        [
            "",
            "\ufeff",
            "<!doctype html>\n",
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"\n'
            ' "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">\n',
            '<?xml version="1.0" encoding="utf-8"?>\n',
            '\ufeff<?xml version="1.0"?>\n<!doctype html>\n',
            '<!doctype html><?xml version="1.0"?>',
        ],
    )
    @pytest.mark.parametrize(
        ("html_open", "body_open"),
        [
            ("<html>", "<body>"),
            (
                '<html lang="en" xmlns="http://www.w3.org/1999/xhtml">',
                "<body>",
            ),
            ("<html>", '<body class="wiki" onload="x()">'),
            ("<HTML\n  lang=en>", "\n<BODY\tid=b >"),
        ],
    )
    async def test_the_wrapper_with_attributes_bom_or_prolog_is_unwrapped(
        self, tmp_path, prefix, html_open, body_open
    ):
        inner = '<h1 id="X">X</h1>\n<p>\nbody &amp; text\n</p>\n'
        client = _html_client()
        client.get_wiki_page_html.return_value = (
            prefix + html_open + body_open + inner + "</body></html>"
        )
        _, text = await _pull(client, tmp_path, format="html")

        assert text.lower().count("<html") == 1
        assert text.lower().count("<body") == 1
        assert text.count("<!doctype") == 1
        assert text.endswith("<body>\n" + inner + "</body></html>\n")
        assert "xmlns" not in text and "onload" not in text
        assert "\ufeff" not in text and "<?xml" not in text

    @pytest.mark.parametrize(
        "raw",
        [
            "<html><head><title>t</title></head><body>x</body></html>",
            "<html><body>x</body></html> trailing",
            "<body>x</body>",
            "<htmlx><body>x</body></htmlx>",
        ],
    )
    async def test_anything_else_passes_through_unchanged(
        self, tmp_path, raw
    ):
        client = _html_client()
        client.get_wiki_page_html.return_value = raw
        _, text = await _pull(client, tmp_path, format="html")
        assert text.endswith("<body>\n" + raw + "</body></html>\n")

    async def test_the_title_is_the_escaped_page_name(self, tmp_path):
        client = _html_client()
        out = tmp_path / "out.html"
        result = await _registry.call_tool(
            "wiki_file_pull",
            {
                "page_name": "A<b>&Page",
                "file_path": str(out),
                "format": "html",
            },
            client,
        )
        assert not result.isError, result.content
        text = out.read_text()
        assert "<title>A&lt;b&gt;&amp;Page</title>" in text
        assert "A<b>" not in text

    async def test_the_html_is_pinned_to_the_info_version(
        self, tmp_path
    ):
        client = _html_client({"version": 9})
        await _pull(client, tmp_path, format="html")
        client.get_wiki_page_info.assert_called_once_with(
            "TestPage", None
        )
        client.get_wiki_page_html.assert_called_once_with("TestPage", 9)

    async def test_a_requested_version_is_passed_to_the_info_read(
        self, tmp_path
    ):
        client = _html_client({"version": 2})
        result, _ = await _pull(
            client, tmp_path, format="html", version=2
        )
        client.get_wiki_page_info.assert_called_once_with("TestPage", 2)
        client.get_wiki_page_html.assert_called_once_with("TestPage", 2)
        assert result.structuredContent["version"] == 2

    @pytest.mark.parametrize("info", [{}, 0, None, {"version": 0}])
    async def test_an_unreadable_version_falls_back_and_warns(
        self, tmp_path, info
    ):
        client = _html_client(info)
        # None would be taken for "default" by the helper.
        client.get_wiki_page_info.return_value = info
        result, text = await _pull(client, tmp_path, format="html")

        assert not result.isError, result.content
        assert text.startswith("<!doctype html>\n")
        warnings = result.structuredContent["warnings"]
        assert len(warnings) == 1
        assert "version could not be read" in warnings[0]
        assert "not pinned" in warnings[0]
        assert warnings[0] in result.content[0].text
        client.get_wiki_page_html.assert_called_once_with(
            "TestPage", None
        )

    async def test_no_source_is_read(self, tmp_path):
        client = _html_client()
        await _pull(client, tmp_path, format="html")
        client.get_wiki_page.assert_not_called()

    async def test_link_rewrites_are_ignored_and_not_validated(
        self, tmp_path
    ):
        client = _html_client(
            configured=((_RULE["from"], _RULE["to"]),)
        )
        result, text = await _pull(
            client,
            tmp_path,
            format="html",
            link_rewrites=[{"from": "", "to": 1}],
        )
        assert not result.isError, result.content
        assert (
            result.structuredContent["link_rewrites_source"] == "none"
        )
        assert 'href="http://localhost/trac/wiki/Other"' in text

    @pytest.mark.parametrize("where", ["info", "html"])
    async def test_not_found_from_a_read_is_not_found(
        self, tmp_path, where
    ):
        client = _html_client()
        fault = xmlrpc.client.Fault(1, "Page TestPage does not exist")
        if where == "info":
            client.get_wiki_page_info.side_effect = fault
        else:
            client.get_wiki_page_html.side_effect = fault
        result, text = await _pull(client, tmp_path, format="html")
        assert result.isError is True
        assert "not_found" in result.content[0].text
        assert text is None

    def test_the_input_schema_offers_html_and_defaults_to_markdown(
        self,
    ):
        from trac_mcp_server.mcp.tools.wiki_file import WIKI_FILE_TOOLS

        tool = next(
            t for t in WIKI_FILE_TOOLS if t.name == "wiki_file_pull"
        )
        prop = tool.inputSchema["properties"]["format"]
        assert prop["enum"] == ["markdown", "html", "tracwiki"]
        assert prop["default"] == "markdown"
        assert 'format="html"' in tool.description
        for name in ("markdown", "html", "tracwiki"):
            assert name in prop["description"]

    async def test_the_default_format_is_still_markdown(self, tmp_path):
        result, text = await _pull(_html_client(), tmp_path)
        assert result.structuredContent["format"] == "markdown"
        assert not text.startswith("<!doctype")


class TestPullReportedVersion:
    """With a version asked for and no usable info, the version reported
    is the requested one, not 1; unpinned it is still 1 (Trac #211)."""

    @pytest.mark.parametrize("fmt", ["markdown", "html", "tracwiki"])
    @pytest.mark.parametrize("info", [{}, 0, None, {"version": 0}])
    async def test_the_requested_version_is_reported(
        self, tmp_path, fmt, info
    ):
        client = _html_client(info)
        client.get_wiki_page_info.return_value = info
        client.get_wiki_page.return_value = "= Hello ="
        result, _ = await _pull(client, tmp_path, format=fmt, version=4)

        assert not result.isError, result.content
        assert result.structuredContent["version"] == 4
        # Pinned by the request: nothing to warn about.
        assert result.structuredContent["warnings"] == []

    @pytest.mark.parametrize("fmt", ["markdown", "html", "tracwiki"])
    async def test_the_infos_version_wins_when_usable(
        self, tmp_path, fmt
    ):
        client = _html_client({"version": 9})
        client.get_wiki_page.return_value = "= Hello ="
        result, _ = await _pull(client, tmp_path, format=fmt, version=4)
        assert result.structuredContent["version"] == 9

    @pytest.mark.parametrize("fmt", ["markdown", "html", "tracwiki"])
    async def test_unpinned_and_unreadable_keeps_the_fallback_of_1(
        self, tmp_path, fmt
    ):
        client = _html_client({})
        client.get_wiki_page_info.return_value = {}
        client.get_wiki_page.return_value = "= Hello ="
        result, _ = await _pull(client, tmp_path, format=fmt)
        assert result.structuredContent["version"] == 1
        warned = result.structuredContent["warnings"]
        # tracwiki reads no HTML and has never warned.
        assert len(warned) == (0 if fmt == "tracwiki" else 1)


class TestPullWarningsHeading:
    @pytest.mark.parametrize(
        ("fmt", "heading"),
        [
            ("markdown", "Conversion warnings:"),
            ("html", "Warnings:"),
        ],
    )
    async def test_the_heading_names_what_the_warnings_are(
        self, tmp_path, fmt, heading
    ):
        client = _html_client({})
        client.get_wiki_page_info.return_value = {}
        client.get_wiki_page.return_value = "= Hello ="
        result, _ = await _pull(client, tmp_path, format=fmt)
        text = result.content[0].text
        assert f"\n\n{heading}\n- " in text
        if fmt == "html":
            assert "Conversion warnings" not in text


class TestPullInPageAnchors:
    """Trac renders ``[#Frag x]`` as ``<page URL>#Frag``; the pull writes
    it ``#Frag`` and never rewrites it (Trac #213)."""

    @staticmethod
    def _client(trac_url, page, html_name):
        client = _make_client()
        client.config.trac_url = trac_url
        client.get_wiki_page.return_value = "src"
        client.get_wiki_page_info.return_value = {"version": 2}
        base = trac_url.rstrip("/")
        client.get_wiki_page_html.return_value = (
            f'<p><a href="{base}/wiki/{html_name}#Linkrow">h</a> '
            f'<a href="{base}/wiki/WikiStart#Top">o</a></p>\n'
        )
        return client

    async def test_the_page_url_anchor_is_relative_and_unmapped(
        self, tmp_path
    ):
        client = self._client(
            "http://localhost/trac", "TestPage", "TestPage"
        )
        rules = [{"from": "http://localhost/trac/", "to": "https://b/"}]
        result, text = await _pull(
            client, tmp_path, link_rewrites=rules
        )
        assert "[h](#Linkrow)" in text
        assert "[o](https://b/wiki/WikiStart#Top)" in text

    async def test_the_document_url_is_the_routed_instances_wiki_url(
        self, tmp_path
    ):
        # A per-instance config: another base, with a trailing slash.
        seen = {}

        def spy(html, source=None, **kw):
            seen.update(kw)
            return trac_html_to_markdown(html, source, **kw)

        client = self._client(
            "http://other.example/trac_two/", "TestPage", "TestPage"
        )
        with patch(
            "trac_mcp_server.mcp.tools.wiki_file.trac_html_to_markdown",
            spy,
        ):
            _, text = await _pull(client, tmp_path)
        assert seen["document_url"] == (
            "http://other.example/trac_two/wiki/TestPage"
        )
        assert "[h](#Linkrow)" in text

    async def test_trac_url_host_may_differ_from_the_daemons(
        self, tmp_path
    ):
        # The daemon reaches Trac at a LAN address; Trac builds its
        # hrefs from its own public base URL. Only the path is compared.
        client = self._client(
            "http://10.0.0.5/auto_pm", "TestPage", "TestPage"
        )
        client.get_wiki_page_html.return_value = (
            '<p><a href="https://trac.example/auto_pm/wiki/TestPage'
            '#Sec">h</a> <a href="https://trac.example/auto_pm/wiki/'
            'Other#Sec">o</a></p>\n'
        )
        rules = [
            {
                "from": "https://trac.example/auto_pm/wiki/",
                "to": "https://b/",
            }
        ]
        _, text = await _pull(client, tmp_path, link_rewrites=rules)
        assert "[h](#Sec)" in text
        assert "[o](https://b/Other#Sec)" in text

    async def test_a_page_name_with_a_space_matches_trac_encoding(
        self, tmp_path
    ):
        client = self._client(
            "http://localhost/trac", "x", "Scratch/Page%20Two"
        )
        out = tmp_path / "out.md"
        result = await _registry.call_tool(
            "wiki_file_pull",
            {"page_name": "Scratch/Page Two", "file_path": str(out)},
            client,
        )
        assert not result.isError
        assert "[h](#Linkrow)" in out.read_text()


class TestPushThroughConditionalPut:
    """wiki_file_push where the server has wiki.putPageIfVersion (Trac
    /trac_mcp_server #129): the push's existing conflict handling covers
    the plugin's refusal."""

    @staticmethod
    async def _push(mock_config, put_outcome, after=4):
        from trac_mcp_server.core.client import TracClient

        client = TracClient(mock_config)
        calls: list[tuple] = []

        def _rpc(namespace, method, *args):
            calls.append((namespace, method, *args))
            if method == "putPageIfVersion":
                if isinstance(put_outcome, BaseException):
                    raise put_outcome
                return put_outcome
            if method == "getPageInfo":
                if any(c[1] == "putPageIfVersion" for c in calls[:-1]):
                    return {"name": "X", "version": after}
                return {"name": "X", "version": 3}
            raise AssertionError(f"unexpected RPC {namespace}.{method}")

        with (
            patch.object(client, "_rpc_request", side_effect=_rpc),
            patch(
                "trac_mcp_server.mcp.tools.wiki_file.gate_or_refuse",
                return_value=(None, []),
            ),
        ):
            result = await _registry.call_tool(
                "wiki_file_push",
                {"content": "= X =\nnew text\n", "page_name": "X"},
                client,
            )
        return result, [c[1] for c in calls]

    async def test_update_stores_through_the_plugin(self, mock_config):
        result, methods = await self._push(mock_config, True)

        assert not result.isError
        sc = result.structuredContent
        assert sc["action"] == "updated"
        assert sc["version"] == 4
        assert sc["possible_overwrite"] is False
        # Probe, conditional write, read-back: no putPage, no precheck.
        assert methods == [
            "getPageInfo",
            "putPageIfVersion",
            "getPageInfo",
        ]

    async def test_plugin_conflict_is_the_usual_version_conflict(
        self, mock_config
    ):
        result, methods = await self._push(
            mock_config,
            xmlrpc.client.Fault(
                409,
                "'[wiki-version-conflict current_version=5] ...' "
                "while executing 'wiki.putPageIfVersion()'",
            ),
        )

        assert result.isError
        text = result.content[0].text
        assert "Error (version_conflict)" in text
        assert (
            "Current version is 5, you tried to update version 3"
            in text
        )
        assert methods == ["getPageInfo", "putPageIfVersion"]


# =============================================================================
# Front matter on a Markdown pull, and the push refusal (Trac #212)
# =============================================================================

_FM_HTML = (
    '<h1 class="section" id="Hello">Hello Page'
    '<a class="anchor" href="#Hello" title="Link to this section"> '
    "\u00b6</a></h1>\n<p>\nBody text.\n</p>\n"
)
_FM_HTML_NO_H1 = '<h2 id="Sub">Sub</h2>\n<p>\nBody text.\n</p>\n'
_ISO_Z = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"


def _fm_client(html=_FM_HTML, info=None, trac_url="http://h:8000/proj"):
    client = _make_client()
    client.config.trac_url = trac_url
    client.get_wiki_page.return_value = "= Hello Page =\n\nBody text.\n"
    client.get_wiki_page_html.return_value = html
    client.get_wiki_page_info.return_value = (
        {"version": 7} if info is None else info
    )
    return client


async def _fm_pull(client, tmp_path, **extra):
    result, text = await _pull(client, tmp_path, **extra)
    assert not result.isError, result.content
    return text


def _fm_block(text):
    import yaml

    assert text.startswith("---\n")
    head, _, rest = text[4:].partition("\n---\n")
    return yaml.safe_load(head), rest


class TestPullFrontMatter:
    """A Markdown pull starts with front matter naming its page (#212)."""

    async def test_markdown_pull_starts_with_the_front_matter(
        self, tmp_path
    ):
        import re

        text = await _fm_pull(_fm_client(), tmp_path)

        meta, rest = _fm_block(text)
        assert list(meta) == [
            "title",
            "source",
            "trac_instance",
            "trac_page",
            "trac_version",
            "exported",
        ]
        assert meta["title"] == "Hello Page"
        assert meta["source"] == "http://h:8000/proj/wiki/TestPage"
        assert meta["trac_instance"] == "/proj"
        assert meta["trac_page"] == "TestPage"
        assert meta["trac_version"] == 7
        assert re.fullmatch(_ISO_Z, meta["exported"])
        # The raw text has the exported stamp as a quoted string.
        assert re.search(rf'^exported: "{_ISO_Z}"$', text, re.M)
        assert rest.startswith("\n# Hello Page")

    async def test_the_markdown_after_the_block_is_the_translator_output(
        self, tmp_path
    ):
        expected = trac_html_to_markdown(
            _FM_HTML, "= Hello Page =\n\nBody text.\n"
        )

        text = await _fm_pull(_fm_client(), tmp_path)

        assert text.endswith("---\n\n" + expected.text)

    async def test_title_falls_back_to_the_page_name(self, tmp_path):
        text = await _fm_pull(_fm_client(html=_FM_HTML_NO_H1), tmp_path)

        meta, _ = _fm_block(text)
        assert meta["title"] == "TestPage"

    async def test_unpinned_version_is_null(self, tmp_path):
        client = _fm_client(info={})

        result, text = await _pull(client, tmp_path)

        assert not result.isError
        assert "\ntrac_version: null\n" in text
        meta, _ = _fm_block(text)
        assert meta["trac_version"] is None

    async def test_asked_for_version_is_the_pinned_one(self, tmp_path):
        client = _fm_client(info={})

        result, text = await _pull(client, tmp_path, version=4)

        assert not result.isError
        meta, _ = _fm_block(text)
        assert meta["trac_version"] == 4

    @pytest.mark.parametrize("fmt", ["html", "tracwiki"])
    async def test_other_formats_have_none(self, tmp_path, fmt):
        client = _fm_client()

        result, text = await _pull(client, tmp_path, format=fmt)

        assert not result.isError
        assert "trac_page" not in text
        assert not text.startswith("---")

    def test_tool_description_says_so(self):
        pull = next(
            s.tool
            for s in WIKI_FILE_SPECS
            if s.tool.name == "wiki_file_pull"
        )
        assert "front matter" in pull.description
        assert "trac_page" in pull.description
        assert "force=true" in pull.description


_FM_EXPORT = (
    "---\n"
    'title: "Hello Page"\n'
    'source: "http://h:8000/proj/wiki/TestPage"\n'
    'trac_instance: "/proj"\n'
    'trac_page: "TestPage"\n'
    "trac_version: 7\n"
    'exported: "2026-11-09T12:00:00Z"\n'
    "---\n\n# Hello Page\n\nBody text.\n"
)


async def _fm_push(content, name="x.md", **extra):
    client = _make_client()
    client.get_wiki_page_info.return_value = {"version": 3}
    client.put_wiki_page.return_value = {
        "version": 4,
        "name": "TestPage",
    }
    args = {"content": content, "page_name": "TestPage", **extra}
    if name is not None:
        args["filename"] = name
    result = await _registry.call_tool("wiki_file_push", args, client)
    return client, result


class TestPushRefusesExportedMarkdown:
    """Markdown whose front matter carries trac_page is refused (#212)."""

    async def test_pulled_markdown_is_refused(self):
        client, result = await _fm_push(_FM_EXPORT)

        assert result.isError
        text = result.content[0].text
        assert "Error (validation_error)" in text
        assert "'TestPage'" in text
        assert "instance /proj" in text
        assert "version 7" in text
        assert "one-way" in text
        assert "wiki_get(page_name='TestPage')" in text
        assert "force=true" in text
        client.put_wiki_page.assert_not_called()
        client.get_wiki_page_info.assert_not_called()
        client.get_wiki_page.assert_not_called()

    async def test_missing_instance_and_version_read_unknown(self):
        content = '---\ntrac_page: "P"\n---\n\nBody\n'

        client, result = await _fm_push(content)

        assert result.isError
        text = result.content[0].text
        assert "instance unknown" in text
        assert "version unknown" in text
        client.put_wiki_page.assert_not_called()

    async def test_refused_whatever_the_format(self):
        client, result = await _fm_push(_FM_EXPORT, format="tracwiki")

        assert result.isError
        assert "Error (validation_error)" in result.content[0].text
        client.put_wiki_page.assert_not_called()

    async def test_refused_without_stripping(self):
        client, result = await _fm_push(
            _FM_EXPORT, strip_frontmatter=False
        )

        assert result.isError
        assert "Error (validation_error)" in result.content[0].text
        client.put_wiki_page.assert_not_called()

    async def test_refused_from_a_server_file_too(self, tmp_path):
        md = tmp_path / "x.md"
        md.write_text(_FM_EXPORT)
        client = _make_client()

        result = await _registry.call_tool(
            "wiki_file_push",
            {"file_path": str(md), "page_name": "TestPage"},
            client,
        )

        assert result.isError
        client.put_wiki_page.assert_not_called()

    async def test_force_pushes_it_without_the_front_matter(self):
        client, result = await _fm_push(_FM_EXPORT, force=True)

        assert not result.isError, result.content
        assert result.structuredContent["version"] == 4
        client.put_wiki_page.assert_called_once()
        stored = client.put_wiki_page.call_args[0][1]
        assert "trac_page" not in stored
        assert not stored.startswith("---")
        assert "Hello Page" in stored

    async def test_plain_markdown_pushes_as_before(self):
        client, result = await _fm_push("# Hello\n\nWorld\n")

        assert not result.isError, result.content
        client.put_wiki_page.assert_called_once()

    async def test_hugo_front_matter_is_stripped_not_refused(self):
        content = "---\ntitle: Post\ndate: 2026-01-01\n---\n\n# Hello\n\nWorld\n"

        client, result = await _fm_push(content)

        assert not result.isError, result.content
        stored = client.put_wiki_page.call_args[0][1]
        assert "date:" not in stored
        assert "Hello" in stored

    def test_schema_has_force_default_false(self):
        push = next(
            s.tool
            for s in WIKI_FILE_SPECS
            if s.tool.name == "wiki_file_push"
        )
        force = push.inputSchema["properties"]["force"]
        assert force["type"] == "boolean"
        assert force["default"] is False
        assert "trac_page" in force["description"]
        assert "force=true" in push.description


class TestStripYamlFrontmatterForms:
    """_strip_yaml_frontmatter is split_front_matter's remainder (#212)."""

    def test_crlf(self):
        assert _strip_yaml_frontmatter(
            "---\r\nx: 1\r\n---\r\n# T\r\n"
        ) == ("# T\r\n")

    def test_bom(self):
        assert (
            _strip_yaml_frontmatter("\ufeff---\nx: 1\n---\n# T")
            == "# T"
        )

    def test_closing_fence_at_end_of_text(self):
        assert _strip_yaml_frontmatter("---\nx: 1\n---") == ""
