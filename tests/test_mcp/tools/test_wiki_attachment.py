"""Tests for wiki_attachment tool handlers.

Covers put / get / list / delete with mocked TracClient. A live
round-trip test marked ``@pytest.mark.live`` is also included; gate it
behind ``--run-live`` (see conftest.py).

Mirrors the shape of test_ticket_attachment.py modulo the divergences
required by the wiki XML-RPC API:
- wiki.putAttachmentEx (NOT wiki.putAttachment) is the underlying call.
- wiki.listAttachments returns list[str] paths, not list[tuple].
- Target wiki page must exist or putAttachmentEx raises ResourceNotFound.
- Rename-on-collision returns a path string; we extract the basename.
"""

import os
import xmlrpc.client
from unittest.mock import MagicMock

import mcp.types as types
import pytest

from trac_mcp_server.mcp.tools.errors import build_error_response
from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.mcp.tools.wiki_attachment import (
    WIKI_ATTACHMENT_SPECS,
)

# These tests exercise the server-side path forms (ticket #111).
pytestmark = pytest.mark.usefixtures("local_file_access")

_REGISTRY = ToolRegistry(WIKI_ATTACHMENT_SPECS)


async def handle_wiki_attachment_tool(name, arguments, client):
    """Test shim: dispatch via ToolRegistry, translating unknown-tool
    errors into the validation_error envelope the tests expect."""
    try:
        return await _REGISTRY.call_tool(name, arguments, client)
    except ValueError as e:
        return build_error_response(
            "validation_error",
            str(e),
            "Use list_tools to see available tools.",
        )


def _make_client() -> MagicMock:
    """Create a minimal mock TracClient with config attribute."""
    client = MagicMock()
    client.config = MagicMock()
    client.config.trac_url = "http://localhost/trac"
    client.config.username = "user"
    client.config.password = "pass"
    return client


# =============================================================================
# _handle_put
# =============================================================================


class TestPut:
    """Tests for wiki_attachment_put handler."""

    async def test_put_uses_putattachmentex_not_putattachment(
        self, tmp_path
    ):
        """The handler MUST call put_wiki_attachment (which wraps
        wiki.putAttachmentEx), forwarding description + replace.

        Pitfall 2: wiki.putAttachment drops the description and forces
        replace=True. wiki.putAttachmentEx is the correct method.
        """
        attachment_file = tmp_path / "diagram.png"
        payload = b"\x89PNG\r\n\x1a\nfake-pixels"
        attachment_file.write_bytes(payload)

        client = _make_client()
        # putAttachmentEx returns the stored filename (basename), not a path.
        client.put_wiki_attachment.return_value = "diagram.png"

        result = await handle_wiki_attachment_tool(
            "wiki_attachment_put",
            {
                "page_name": "WikiStart",
                "file_path": str(attachment_file),
                "description": "architecture diagram",
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is None or result.isError is False

        # Lock the underlying call site uses put_wiki_attachment
        # (which delegates to wiki.putAttachmentEx with separate
        # pagename + filename, NOT a single "page/file" path like the
        # bare wiki.putAttachment).
        client.put_wiki_attachment.assert_called_once()
        call_args = client.put_wiki_attachment.call_args
        (
            page_name_arg,
            filename_arg,
            description_arg,
            binary_arg,
            replace_arg,
        ) = call_args[0]
        assert page_name_arg == "WikiStart"
        assert filename_arg == "diagram.png"
        assert description_arg == "architecture diagram"
        assert replace_arg is False
        assert isinstance(binary_arg, xmlrpc.client.Binary)
        assert binary_arg.data == payload

        assert result.structuredContent["page_name"] == "WikiStart"
        assert (
            result.structuredContent["requested_filename"]
            == "diagram.png"
        )
        assert (
            result.structuredContent["attached_filename"]
            == "diagram.png"
        )
        assert result.structuredContent["renamed_on_collision"] is False
        assert result.structuredContent["bytes_uploaded"] == len(
            payload
        )
        assert result.structuredContent["replace"] is False

    async def test_put_filename_override(self, tmp_path):
        """Explicit filename overrides the basename of file_path."""
        attachment_file = tmp_path / "local-name.txt"
        attachment_file.write_bytes(b"abc")

        client = _make_client()
        client.put_wiki_attachment.return_value = "remote-name.txt"

        await handle_wiki_attachment_tool(
            "wiki_attachment_put",
            {
                "page_name": "WikiStart",
                "file_path": str(attachment_file),
                "filename": "remote-name.txt",
            },
            client,
        )

        call_args = client.put_wiki_attachment.call_args
        # signature: (page_name, filename, description, data, replace)
        assert call_args[0][0] == "WikiStart"
        assert call_args[0][1] == "remote-name.txt"

    async def test_put_replace_true(self, tmp_path):
        """replace=True is forwarded to TracClient."""
        attachment_file = tmp_path / "data.txt"
        attachment_file.write_bytes(b"x")

        client = _make_client()
        client.put_wiki_attachment.return_value = "data.txt"

        await handle_wiki_attachment_tool(
            "wiki_attachment_put",
            {
                "page_name": "Page",
                "file_path": str(attachment_file),
                "replace": True,
            },
            client,
        )

        call_args = client.put_wiki_attachment.call_args
        # signature: (page_name, filename, description, data, replace)
        assert call_args[0][4] is True

    async def test_put_renamed_on_collision(self, tmp_path):
        """When Trac renames the file (replace=False, name clash), surface
        BOTH requested + stored names and set renamed_on_collision=True.

        Pitfall 5: callers MUST be able to detect that the attachment
        was stored under a different name than they asked for.
        """
        attachment_file = tmp_path / "diagram.png"
        attachment_file.write_bytes(b"x")

        client = _make_client()
        # Server returns a renamed filename to avoid collision with an
        # existing attachment of the same name. Some Trac versions
        # return a "page/filename" path; the handler must extract the
        # basename for the rename comparison.
        client.put_wiki_attachment.return_value = (
            "WikiStart/diagram.2.png"
        )

        result = await handle_wiki_attachment_tool(
            "wiki_attachment_put",
            {
                "page_name": "WikiStart",
                "file_path": str(attachment_file),
                "filename": "diagram.png",
                "replace": False,
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is None or result.isError is False
        assert (
            result.structuredContent["requested_filename"]
            == "diagram.png"
        )
        assert (
            result.structuredContent["attached_filename"]
            == "diagram.2.png"
        )
        assert result.structuredContent["renamed_on_collision"] is True
        assert result.structuredContent["replace"] is False

    async def test_put_missing_page_name(self, tmp_path):
        attachment_file = tmp_path / "x.txt"
        attachment_file.write_bytes(b"x")

        client = _make_client()
        result = await handle_wiki_attachment_tool(
            "wiki_attachment_put",
            {"file_path": str(attachment_file)},
            client,
        )
        assert result.isError is True
        assert "page_name is required" in result.content[0].text

    async def test_put_missing_file_path(self):
        client = _make_client()
        result = await handle_wiki_attachment_tool(
            "wiki_attachment_put",
            {"page_name": "WikiStart"},
            client,
        )
        assert result.isError is True
        assert "content_base64 is required" in result.content[0].text
        client.put_wiki_attachment.assert_not_called()

    async def test_put_relative_path_rejected(self):
        """validate_file_path rejects non-absolute paths."""
        client = _make_client()
        result = await handle_wiki_attachment_tool(
            "wiki_attachment_put",
            {
                "page_name": "WikiStart",
                "file_path": "relative/path.txt",
            },
            client,
        )
        assert result.isError is True
        assert "validation_error" in result.content[0].text

    async def test_put_nonexistent_file_rejected(self, tmp_path):
        client = _make_client()
        result = await handle_wiki_attachment_tool(
            "wiki_attachment_put",
            {
                "page_name": "WikiStart",
                "file_path": str(tmp_path / "no_such_file.bin"),
            },
            client,
        )
        assert result.isError is True
        assert "validation_error" in result.content[0].text

    async def test_put_page_not_found_translated(self, tmp_path):
        """Pitfall 7: target wiki page must exist; ResourceNotFound from
        Trac is translated to a structured not_found error."""
        attachment_file = tmp_path / "x.txt"
        attachment_file.write_bytes(b"x")

        client = _make_client()
        client.put_wiki_attachment.side_effect = xmlrpc.client.Fault(
            1, "Wiki page 'NoSuchPage' does not exist"
        )

        result = await handle_wiki_attachment_tool(
            "wiki_attachment_put",
            {
                "page_name": "NoSuchPage",
                "file_path": str(attachment_file),
            },
            client,
        )
        assert result.isError is True
        assert "not_found" in result.content[0].text

    async def test_put_permission_denied_translated(self, tmp_path):
        """WIKI_MODIFY denied → permission_denied error response."""
        attachment_file = tmp_path / "x.txt"
        attachment_file.write_bytes(b"x")

        client = _make_client()
        client.put_wiki_attachment.side_effect = xmlrpc.client.Fault(
            403, "WIKI_MODIFY permission denied"
        )

        result = await handle_wiki_attachment_tool(
            "wiki_attachment_put",
            {
                "page_name": "WikiStart",
                "file_path": str(attachment_file),
            },
            client,
        )
        assert result.isError is True
        assert "permission_denied" in result.content[0].text


# =============================================================================
# _handle_get
# =============================================================================


class TestGet:
    """Tests for wiki_attachment_get handler."""

    async def test_get_writes_bytes_to_output_path(self, tmp_path):
        """Bytes returned by TracClient are written verbatim to output_path."""
        out_file = tmp_path / "downloaded.bin"
        payload = b"\x00\x01binary-data\xff\xfe"

        client = _make_client()
        client.get_wiki_attachment.return_value = payload

        result = await handle_wiki_attachment_tool(
            "wiki_attachment_get",
            {
                "page_name": "WikiStart",
                "filename": "diagram.png",
                "output_path": str(out_file),
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is None or result.isError is False
        assert out_file.read_bytes() == payload
        assert result.structuredContent["bytes_written"] == len(payload)
        assert result.structuredContent["filename"] == "diagram.png"
        assert result.structuredContent["page_name"] == "WikiStart"

        client.get_wiki_attachment.assert_called_once_with(
            "WikiStart/diagram.png"
        )

    async def test_get_handles_xmlrpc_binary_payload(self, tmp_path):
        """If TracClient returns xmlrpc.client.Binary, payload is unwrapped."""
        out_file = tmp_path / "out.bin"
        payload = b"hello"

        client = _make_client()
        client.get_wiki_attachment.return_value = xmlrpc.client.Binary(
            payload
        )

        await handle_wiki_attachment_tool(
            "wiki_attachment_get",
            {
                "page_name": "Page",
                "filename": "x",
                "output_path": str(out_file),
            },
            client,
        )

        assert out_file.read_bytes() == payload

    async def test_get_missing_page_name(self, tmp_path):
        out_file = tmp_path / "out.bin"
        client = _make_client()
        result = await handle_wiki_attachment_tool(
            "wiki_attachment_get",
            {"filename": "x", "output_path": str(out_file)},
            client,
        )
        assert result.isError is True
        assert "page_name is required" in result.content[0].text

    async def test_get_missing_filename(self, tmp_path):
        out_file = tmp_path / "out.bin"
        client = _make_client()
        result = await handle_wiki_attachment_tool(
            "wiki_attachment_get",
            {"page_name": "WikiStart", "output_path": str(out_file)},
            client,
        )
        assert result.isError is True
        assert "filename is required" in result.content[0].text

    async def test_get_missing_output_path(self):
        """No output_path: the bytes come back inline, base64 (ticket
        #111)."""
        client = _make_client()
        client.get_wiki_attachment.return_value = b"\x00\x01abc"
        result = await handle_wiki_attachment_tool(
            "wiki_attachment_get",
            {"page_name": "WikiStart", "filename": "x"},
            client,
        )
        assert result.isError is not True
        assert result.structuredContent["content_base64"] == "AAFhYmM="
        assert result.structuredContent["bytes"] == 5
        assert "output_path" not in result.structuredContent

    async def test_get_invalid_output_path(self):
        """Output path with nonexistent parent dir is rejected."""
        client = _make_client()
        result = await handle_wiki_attachment_tool(
            "wiki_attachment_get",
            {
                "page_name": "WikiStart",
                "filename": "x",
                "output_path": "/nonexistent/dir/out.bin",
            },
            client,
        )
        assert result.isError is True
        assert "validation_error" in result.content[0].text

    async def test_get_xmlrpc_fault_translated(self, tmp_path):
        out_file = tmp_path / "out.bin"
        client = _make_client()
        client.get_wiki_attachment.side_effect = xmlrpc.client.Fault(
            1, "Attachment 'missing.bin' does not exist"
        )

        result = await handle_wiki_attachment_tool(
            "wiki_attachment_get",
            {
                "page_name": "WikiStart",
                "filename": "missing.bin",
                "output_path": str(out_file),
            },
            client,
        )
        assert result.isError is True
        assert "not_found" in result.content[0].text
        assert not out_file.exists()


# =============================================================================
# _handle_list
# =============================================================================


class TestList:
    """Tests for wiki_attachment_list handler."""

    async def test_list_returns_list_of_str_paths(self):
        """Pitfall 3: wiki.listAttachments returns list[str] paths,
        NOT list[tuple] like the ticket equivalent. Lock that shape."""
        client = _make_client()
        client.list_wiki_attachments.return_value = [
            "WikiStart/diagram.png",
            "WikiStart/notes.txt",
        ]

        result = await handle_wiki_attachment_tool(
            "wiki_attachment_list",
            {"page_name": "WikiStart"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["count"] == 2
        attachments = result.structuredContent["attachments"]
        # Must be list[str] not list[dict]/list[tuple]
        assert all(isinstance(a, str) for a in attachments)
        assert attachments == [
            "WikiStart/diagram.png",
            "WikiStart/notes.txt",
        ]

        client.list_wiki_attachments.assert_called_once_with(
            "WikiStart"
        )

    async def test_list_empty(self):
        client = _make_client()
        client.list_wiki_attachments.return_value = []

        result = await handle_wiki_attachment_tool(
            "wiki_attachment_list",
            {"page_name": "WikiStart"},
            client,
        )
        assert result.structuredContent["count"] == 0
        assert "no attachments" in result.content[0].text

    async def test_list_missing_page_name(self):
        client = _make_client()
        result = await handle_wiki_attachment_tool(
            "wiki_attachment_list", {}, client
        )
        assert result.isError is True
        assert "page_name is required" in result.content[0].text


# =============================================================================
# _handle_delete
# =============================================================================


class TestDelete:
    """Tests for wiki_attachment_delete handler."""

    async def test_delete_calls_client_with_path(self):
        client = _make_client()
        client.delete_wiki_attachment.return_value = True

        result = await handle_wiki_attachment_tool(
            "wiki_attachment_delete",
            {"page_name": "WikiStart", "filename": "diagram.png"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is None or result.isError is False
        client.delete_wiki_attachment.assert_called_once_with(
            "WikiStart/diagram.png"
        )
        assert result.structuredContent["page_name"] == "WikiStart"
        assert result.structuredContent["filename"] == "diagram.png"
        assert (
            result.structuredContent["page_path"]
            == "WikiStart/diagram.png"
        )

    async def test_delete_missing_page_name(self):
        client = _make_client()
        result = await handle_wiki_attachment_tool(
            "wiki_attachment_delete", {"filename": "x"}, client
        )
        assert result.isError is True
        assert "page_name is required" in result.content[0].text

    async def test_delete_missing_filename(self):
        client = _make_client()
        result = await handle_wiki_attachment_tool(
            "wiki_attachment_delete",
            {"page_name": "WikiStart"},
            client,
        )
        assert result.isError is True
        assert "filename is required" in result.content[0].text

    async def test_delete_permission_denied(self):
        """Permission errors get a WIKI_DELETE-specific corrective action."""
        client = _make_client()
        client.delete_wiki_attachment.side_effect = xmlrpc.client.Fault(
            403, "Permission denied"
        )

        result = await handle_wiki_attachment_tool(
            "wiki_attachment_delete",
            {"page_name": "WikiStart", "filename": "x"},
            client,
        )
        assert result.isError is True
        assert "permission_denied" in result.content[0].text
        assert "WIKI_DELETE" in result.content[0].text


# =============================================================================
# Unknown tool
# =============================================================================


async def test_unknown_tool_name():
    client = _make_client()
    result = await handle_wiki_attachment_tool(
        "wiki_attachment_unknown", {}, client
    )
    assert result.isError is True
    assert "validation_error" in result.content[0].text


# =============================================================================
# Live round-trip (push -> list -> get -> delete)
# =============================================================================


@pytest.mark.live
async def test_live_round_trip(tmp_path):
    """End-to-end: push a fixture file, list, get, verify bytes, delete.

    Requires --run-live and TRAC_URL/USERNAME/PASSWORD env vars pointing
    at a live Trac instance with a wiki page the test user can attach to.
    """
    from trac_mcp_server.config import Config
    from trac_mcp_server.core.client import TracClient

    page_name = os.environ.get("TRAC_TEST_WIKI_PAGE", "WikiStart")

    # Use the same env-driven config the server uses
    config = Config(
        trac_url=os.environ["TRAC_URL"],
        username=os.environ["TRAC_USERNAME"],
        password=os.environ["TRAC_PASSWORD"],
        insecure=os.environ.get("TRAC_INSECURE", "").lower()
        in ("1", "true", "yes"),
    )
    client = TracClient(config)

    fixture = tmp_path / "live-wiki-fixture.bin"
    payload = b"trac-mcp-server live wiki attachment test \x00\x01\x02"
    fixture.write_bytes(payload)

    # Push
    push_result = await handle_wiki_attachment_tool(
        "wiki_attachment_put",
        {
            "page_name": page_name,
            "file_path": str(fixture),
            "description": "live round-trip fixture",
            "replace": True,
        },
        client,
    )
    assert push_result.isError is None or push_result.isError is False

    # List — confirm it appears
    list_result = await handle_wiki_attachment_tool(
        "wiki_attachment_list", {"page_name": page_name}, client
    )
    paths = list_result.structuredContent["attachments"]
    assert f"{page_name}/live-wiki-fixture.bin" in paths

    # Get — confirm byte-identical retrieval
    out_file = tmp_path / "downloaded.bin"
    get_result = await handle_wiki_attachment_tool(
        "wiki_attachment_get",
        {
            "page_name": page_name,
            "filename": "live-wiki-fixture.bin",
            "output_path": str(out_file),
        },
        client,
    )
    assert get_result.isError is None or get_result.isError is False
    assert out_file.read_bytes() == payload

    # Delete — clean up
    del_result = await handle_wiki_attachment_tool(
        "wiki_attachment_delete",
        {"page_name": page_name, "filename": "live-wiki-fixture.bin"},
        client,
    )
    assert del_result.isError is None or del_result.isError is False
