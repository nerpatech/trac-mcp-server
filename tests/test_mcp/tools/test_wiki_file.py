"""Tests for wiki_file tool handlers: detect_format, push, pull, and frontmatter stripping."""

import xmlrpc.client
from unittest.mock import MagicMock, patch

import mcp.types as types
import pytest

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
