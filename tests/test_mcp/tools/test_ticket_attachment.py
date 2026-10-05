"""Tests for ticket_attachment tool handlers.

Covers put / get / list / delete with mocked TracClient. A live
round-trip test marked ``@pytest.mark.live`` is also included; gate it
behind ``--run-live`` (see conftest.py).
"""

import os
import xmlrpc.client
from unittest.mock import MagicMock

import mcp.types as types
import pytest

from trac_mcp_server.mcp.tools.errors import build_error_response
from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.mcp.tools.ticket_attachment import (
    TICKET_ATTACHMENT_SPECS,
)

# These tests exercise the server-side path forms (ticket #111).
pytestmark = pytest.mark.usefixtures("local_file_access")

_REGISTRY = ToolRegistry(TICKET_ATTACHMENT_SPECS)


async def handle_ticket_attachment_tool(name, arguments, client):
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
    """Tests for ticket_attachment_put handler."""

    async def test_put_reads_bytes_and_wraps_in_binary(self, tmp_path):
        """File bytes are read from disk and wrapped in xmlrpc.client.Binary."""
        attachment_file = tmp_path / "report.bin"
        payload = b"\x00\x01\x02hello\xffworld"
        attachment_file.write_bytes(payload)

        client = _make_client()
        client.put_ticket_attachment.return_value = "report.bin"

        result = await handle_ticket_attachment_tool(
            "ticket_attachment_put",
            {
                "ticket_id": 42,
                "file_path": str(attachment_file),
                "description": "benchmark verdict",
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is None or result.isError is False

        # Verify the bytes flowed through Binary unchanged
        client.put_ticket_attachment.assert_called_once()
        call_args = client.put_ticket_attachment.call_args
        (
            ticket_id_arg,
            filename_arg,
            description_arg,
            binary_arg,
            replace_arg,
        ) = call_args[0]
        assert ticket_id_arg == 42
        assert filename_arg == "report.bin"
        assert description_arg == "benchmark verdict"
        assert isinstance(binary_arg, xmlrpc.client.Binary)
        assert binary_arg.data == payload
        assert replace_arg is False

        assert result.structuredContent["ticket_id"] == 42
        assert (
            result.structuredContent["requested_filename"]
            == "report.bin"
        )
        assert (
            result.structuredContent["attached_filename"]
            == "report.bin"
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
        client.put_ticket_attachment.return_value = "remote-name.txt"

        await handle_ticket_attachment_tool(
            "ticket_attachment_put",
            {
                "ticket_id": 1,
                "file_path": str(attachment_file),
                "filename": "remote-name.txt",
            },
            client,
        )

        call_args = client.put_ticket_attachment.call_args
        assert call_args[0][1] == "remote-name.txt"

    async def test_put_replace_true(self, tmp_path):
        """replace=True is forwarded to TracClient."""
        attachment_file = tmp_path / "data.txt"
        attachment_file.write_bytes(b"x")

        client = _make_client()
        client.put_ticket_attachment.return_value = "data.txt"

        await handle_ticket_attachment_tool(
            "ticket_attachment_put",
            {
                "ticket_id": 5,
                "file_path": str(attachment_file),
                "replace": True,
            },
            client,
        )

        call_args = client.put_ticket_attachment.call_args
        assert call_args[0][4] is True

    async def test_put_rename_on_collision(self, tmp_path):
        """When Trac renames the file (replace=False, name clash), surface
        BOTH requested + stored names and set renamed_on_collision=True.

        Mirrors RESEARCH Pitfall 5: callers MUST be able to detect that the
        attachment was stored under a different name than they asked for.
        """
        attachment_file = tmp_path / "report.bin"
        attachment_file.write_bytes(b"x")

        client = _make_client()
        # Server returns a renamed filename to avoid collision with an
        # existing attachment of the same name.
        client.put_ticket_attachment.return_value = "report.2.bin"

        result = await handle_ticket_attachment_tool(
            "ticket_attachment_put",
            {
                "ticket_id": 7,
                "file_path": str(attachment_file),
                "filename": "report.bin",
                "replace": False,
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is None or result.isError is False
        assert (
            result.structuredContent["requested_filename"]
            == "report.bin"
        )
        assert (
            result.structuredContent["attached_filename"]
            == "report.2.bin"
        )
        assert result.structuredContent["renamed_on_collision"] is True
        assert result.structuredContent["replace"] is False

    async def test_put_missing_ticket_id(self, tmp_path):
        attachment_file = tmp_path / "x.txt"
        attachment_file.write_bytes(b"x")

        client = _make_client()
        result = await handle_ticket_attachment_tool(
            "ticket_attachment_put",
            {"file_path": str(attachment_file)},
            client,
        )
        assert result.isError is True
        assert "ticket_id is required" in result.content[0].text

    async def test_put_missing_file_path(self):
        """Neither file_path nor content_base64: the inline form is the
        one asked for (ticket #111)."""
        client = _make_client()
        result = await handle_ticket_attachment_tool(
            "ticket_attachment_put", {"ticket_id": 1}, client
        )
        assert result.isError is True
        assert "content_base64 is required" in result.content[0].text
        client.put_ticket_attachment.assert_not_called()

    async def test_put_relative_path_rejected(self):
        """validate_file_path rejects non-absolute paths."""
        client = _make_client()
        result = await handle_ticket_attachment_tool(
            "ticket_attachment_put",
            {"ticket_id": 1, "file_path": "relative/path.txt"},
            client,
        )
        assert result.isError is True
        assert "validation_error" in result.content[0].text

    async def test_put_nonexistent_file_rejected(self, tmp_path):
        client = _make_client()
        result = await handle_ticket_attachment_tool(
            "ticket_attachment_put",
            {
                "ticket_id": 1,
                "file_path": str(tmp_path / "no_such_file.bin"),
            },
            client,
        )
        assert result.isError is True
        assert "validation_error" in result.content[0].text

    async def test_put_xmlrpc_fault_translated(self, tmp_path):
        """XML-RPC fault is converted to structured error."""
        attachment_file = tmp_path / "x.txt"
        attachment_file.write_bytes(b"x")

        client = _make_client()
        client.put_ticket_attachment.side_effect = xmlrpc.client.Fault(
            1, "Ticket 999 does not exist"
        )

        result = await handle_ticket_attachment_tool(
            "ticket_attachment_put",
            {"ticket_id": 999, "file_path": str(attachment_file)},
            client,
        )
        assert result.isError is True
        assert "not_found" in result.content[0].text


# =============================================================================
# _handle_get
# =============================================================================


class TestGet:
    """Tests for ticket_attachment_get handler."""

    async def test_get_writes_bytes_to_output_path(self, tmp_path):
        """Bytes returned by TracClient are written verbatim to output_path."""
        out_file = tmp_path / "downloaded.bin"
        payload = b"\x00\x01binary-data\xff\xfe"

        client = _make_client()
        client.get_ticket_attachment.return_value = payload

        result = await handle_ticket_attachment_tool(
            "ticket_attachment_get",
            {
                "ticket_id": 7,
                "filename": "report.bin",
                "output_path": str(out_file),
            },
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is None or result.isError is False
        assert out_file.read_bytes() == payload
        assert result.structuredContent["bytes_written"] == len(payload)
        assert result.structuredContent["filename"] == "report.bin"

        client.get_ticket_attachment.assert_called_once_with(
            7, "report.bin"
        )

    async def test_get_handles_xmlrpc_binary_payload(self, tmp_path):
        """If TracClient returns xmlrpc.client.Binary, payload is unwrapped."""
        out_file = tmp_path / "out.bin"
        payload = b"hello"

        client = _make_client()
        client.get_ticket_attachment.return_value = (
            xmlrpc.client.Binary(payload)
        )

        await handle_ticket_attachment_tool(
            "ticket_attachment_get",
            {
                "ticket_id": 1,
                "filename": "x",
                "output_path": str(out_file),
            },
            client,
        )

        assert out_file.read_bytes() == payload

    async def test_get_missing_ticket_id(self, tmp_path):
        out_file = tmp_path / "out.bin"
        client = _make_client()
        result = await handle_ticket_attachment_tool(
            "ticket_attachment_get",
            {"filename": "x", "output_path": str(out_file)},
            client,
        )
        assert result.isError is True
        assert "ticket_id is required" in result.content[0].text

    async def test_get_missing_filename(self, tmp_path):
        out_file = tmp_path / "out.bin"
        client = _make_client()
        result = await handle_ticket_attachment_tool(
            "ticket_attachment_get",
            {"ticket_id": 1, "output_path": str(out_file)},
            client,
        )
        assert result.isError is True
        assert "filename is required" in result.content[0].text

    async def test_get_missing_output_path(self):
        """No output_path: the bytes come back inline, base64 (ticket
        #111)."""
        client = _make_client()
        client.get_ticket_attachment.return_value = b"\x00\x01abc"
        result = await handle_ticket_attachment_tool(
            "ticket_attachment_get",
            {"ticket_id": 1, "filename": "x"},
            client,
        )
        assert result.isError is not True
        assert result.structuredContent["content_base64"] == "AAFhYmM="
        assert result.structuredContent["bytes"] == 5
        assert "output_path" not in result.structuredContent

    async def test_get_invalid_output_path(self):
        """Output path with nonexistent parent dir is rejected."""
        client = _make_client()
        result = await handle_ticket_attachment_tool(
            "ticket_attachment_get",
            {
                "ticket_id": 1,
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
        client.get_ticket_attachment.side_effect = xmlrpc.client.Fault(
            1, "Attachment 'missing.bin' does not exist"
        )

        result = await handle_ticket_attachment_tool(
            "ticket_attachment_get",
            {
                "ticket_id": 1,
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
    """Tests for ticket_attachment_list handler."""

    async def test_list_returns_structured_attachments(self):
        client = _make_client()
        client.list_ticket_attachments.return_value = [
            (
                "report.bin",
                "verdict",
                1024,
                "2024-01-01T00:00:00",
                "alice",
            ),
            ("notes.txt", "", 64, "2024-01-02T00:00:00", "bob"),
        ]

        result = await handle_ticket_attachment_tool(
            "ticket_attachment_list", {"ticket_id": 42}, client
        )

        assert isinstance(result, types.CallToolResult)
        assert result.structuredContent["count"] == 2
        attachments = result.structuredContent["attachments"]
        assert attachments[0]["filename"] == "report.bin"
        assert attachments[0]["size"] == 1024
        assert attachments[0]["author"] == "alice"
        assert attachments[1]["filename"] == "notes.txt"

    async def test_list_empty(self):
        client = _make_client()
        client.list_ticket_attachments.return_value = []

        result = await handle_ticket_attachment_tool(
            "ticket_attachment_list", {"ticket_id": 42}, client
        )
        assert result.structuredContent["count"] == 0
        assert "no attachments" in result.content[0].text

    async def test_list_missing_ticket_id(self):
        client = _make_client()
        result = await handle_ticket_attachment_tool(
            "ticket_attachment_list", {}, client
        )
        assert result.isError is True
        assert "ticket_id is required" in result.content[0].text


# =============================================================================
# _handle_delete
# =============================================================================


class TestDelete:
    """Tests for ticket_attachment_delete handler."""

    async def test_delete_calls_client(self):
        client = _make_client()
        client.delete_ticket_attachment.return_value = True

        result = await handle_ticket_attachment_tool(
            "ticket_attachment_delete",
            {"ticket_id": 42, "filename": "report.bin"},
            client,
        )

        assert isinstance(result, types.CallToolResult)
        assert result.isError is None or result.isError is False
        client.delete_ticket_attachment.assert_called_once_with(
            42, "report.bin"
        )
        assert result.structuredContent["ticket_id"] == 42
        assert result.structuredContent["filename"] == "report.bin"

    async def test_delete_missing_ticket_id(self):
        client = _make_client()
        result = await handle_ticket_attachment_tool(
            "ticket_attachment_delete", {"filename": "x"}, client
        )
        assert result.isError is True
        assert "ticket_id is required" in result.content[0].text

    async def test_delete_missing_filename(self):
        client = _make_client()
        result = await handle_ticket_attachment_tool(
            "ticket_attachment_delete", {"ticket_id": 1}, client
        )
        assert result.isError is True
        assert "filename is required" in result.content[0].text

    async def test_delete_permission_denied(self):
        """Permission errors get a TICKET_ADMIN-specific corrective action."""
        client = _make_client()
        client.delete_ticket_attachment.side_effect = (
            xmlrpc.client.Fault(403, "Permission denied")
        )

        result = await handle_ticket_attachment_tool(
            "ticket_attachment_delete",
            {"ticket_id": 42, "filename": "x"},
            client,
        )
        assert result.isError is True
        assert "permission_denied" in result.content[0].text
        assert "TICKET_ADMIN" in result.content[0].text


# =============================================================================
# Unknown tool
# =============================================================================


async def test_unknown_tool_name():
    client = _make_client()
    result = await handle_ticket_attachment_tool(
        "ticket_attachment_unknown", {}, client
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
    at a live Trac instance with at least one ticket the test user can
    attach to.
    """
    from trac_mcp_server.config import Config
    from trac_mcp_server.core.client import TracClient

    ticket_id_env = os.environ.get("TRAC_TEST_TICKET_ID")
    if not ticket_id_env:
        pytest.skip(
            "TRAC_TEST_TICKET_ID env var required for live attachment test"
        )
    ticket_id = int(ticket_id_env)

    # Use the same env-driven config the server uses
    config = Config(
        trac_url=os.environ["TRAC_URL"],
        username=os.environ["TRAC_USERNAME"],
        password=os.environ["TRAC_PASSWORD"],
        insecure=os.environ.get("TRAC_INSECURE", "").lower()
        in ("1", "true", "yes"),
    )
    client = TracClient(config)

    fixture = tmp_path / "live-fixture.bin"
    payload = b"trac-mcp-server live attachment test \x00\x01\x02"
    fixture.write_bytes(payload)

    # Push
    push_result = await handle_ticket_attachment_tool(
        "ticket_attachment_put",
        {
            "ticket_id": ticket_id,
            "file_path": str(fixture),
            "description": "live round-trip fixture",
            "replace": True,
        },
        client,
    )
    assert push_result.isError is None or push_result.isError is False

    # List — confirm it appears
    list_result = await handle_ticket_attachment_tool(
        "ticket_attachment_list", {"ticket_id": ticket_id}, client
    )
    filenames = [
        a["filename"]
        for a in list_result.structuredContent["attachments"]
    ]
    assert "live-fixture.bin" in filenames

    # Get — confirm byte-identical retrieval
    out_file = tmp_path / "downloaded.bin"
    get_result = await handle_ticket_attachment_tool(
        "ticket_attachment_get",
        {
            "ticket_id": ticket_id,
            "filename": "live-fixture.bin",
            "output_path": str(out_file),
        },
        client,
    )
    assert get_result.isError is None or get_result.isError is False
    assert out_file.read_bytes() == payload

    # Delete — clean up
    del_result = await handle_ticket_attachment_tool(
        "ticket_attachment_delete",
        {"ticket_id": ticket_id, "filename": "live-fixture.bin"},
        client,
    )
    assert del_result.isError is None or del_result.isError is False
