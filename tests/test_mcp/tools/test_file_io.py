"""Tests for the file tools' inline forms and the file_access gate (ticket #111).

The gate's contract: with ``file_access`` off -- the module default, and
what ``main()`` sets for the http transport -- no path-taking tool reads
or writes the server's filesystem, and none of them reaches Trac either.
The inline forms (``content`` / ``content_base64`` in, ``content`` /
``content_base64`` out) work in both modes.
"""

import base64
import xmlrpc.client
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import mcp.types as types
import pytest

from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.mcp.tools.file_io import (
    get_file_access,
    get_max_inline_bytes,
    set_file_access,
)
from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.mcp.tools.ticket_attachment import (
    TICKET_ATTACHMENT_SPECS,
)
from trac_mcp_server.mcp.tools.wiki_attachment import (
    WIKI_ATTACHMENT_SPECS,
)
from trac_mcp_server.mcp.tools.wiki_file import WIKI_FILE_SPECS

_registry = ToolRegistry(
    WIKI_FILE_SPECS + TICKET_ATTACHMENT_SPECS + WIKI_ATTACHMENT_SPECS
)


@pytest.fixture(autouse=True)
def _restore_file_access():
    """Every test here starts from, and leaves, the fail-closed default."""
    set_file_access("off")
    yield
    set_file_access("off")


def _make_client():
    client = MagicMock()
    client.config = MagicMock()
    client.config.trac_url = "http://localhost/trac"
    # Falsy info: push takes its create path, pull reports version 1.
    client.get_wiki_page_info.return_value = {}
    client.put_wiki_page.return_value = {"version": 1}
    client.get_wiki_page.return_value = "= Title =\n"
    client.get_ticket_attachment.return_value = b"bytes"
    client.get_wiki_attachment.return_value = b"bytes"
    return client


async def _call(name, args, client=None):
    client = client or _make_client()
    with patch(
        "trac_mcp_server.mcp.tools.wiki_file.gate_or_refuse",
        return_value=(None, []),
    ):
        result = await _registry.call_tool(name, args, client)
    assert isinstance(result, types.CallToolResult)
    return result, client


# ---------------------------------------------------------------------------
# The gate: every path argument is refused while file_access is off
# ---------------------------------------------------------------------------

_INPUT_CASES = [
    ("wiki_file_push", {"page_name": "P"}),
    ("wiki_file_detect_format", {}),
    ("ticket_attachment_put", {"ticket_id": 1}),
    ("wiki_attachment_put", {"page_name": "P"}),
]
_OUTPUT_CASES = [
    ("wiki_file_pull", {"page_name": "P"}, "file_path"),
    (
        "ticket_attachment_get",
        {"ticket_id": 1, "filename": "a"},
        "output_path",
    ),
    (
        "wiki_attachment_get",
        {"page_name": "P", "filename": "a"},
        "output_path",
    ),
]


class TestGateOff:
    def test_default_is_off(self):
        assert get_file_access() == "off"

    @pytest.mark.parametrize("name,args", _INPUT_CASES)
    async def test_input_path_refused_and_file_unread(
        self, name, args, tmp_path
    ):
        secret = tmp_path / "secret.md"
        secret.write_text("= Secret =\n")
        result, client = await _call(
            name, {**args, "file_path": str(secret)}
        )
        assert result.isError is True
        text = result.content[0].text
        assert "permission_denied" in text
        assert "file_access is 'off'" in text
        assert "trac-mcp" in text
        # Nothing reached Trac, and nothing of the file came back.
        assert client.method_calls == []
        assert "Secret" not in text

    @pytest.mark.parametrize("name,args,key", _OUTPUT_CASES)
    async def test_output_path_refused_and_nothing_written(
        self, name, args, key, tmp_path
    ):
        target = tmp_path / "out.bin"
        result, client = await _call(name, {**args, key: str(target)})
        assert result.isError is True
        assert "permission_denied" in result.content[0].text
        assert not target.exists()
        assert client.method_calls == []

    @pytest.mark.parametrize("name,args,key", _OUTPUT_CASES)
    async def test_output_path_allowed_when_local(
        self, name, args, key, tmp_path
    ):
        """The same call succeeds once the operator sets local -- so the
        refusal above is the gate, not some other failure."""
        set_file_access("local")
        target = tmp_path / "out.bin"
        result, _ = await _call(name, {**args, key: str(target)})
        assert result.isError is not True, result.content[0].text
        assert target.exists()


class TestSetFileAccess:
    def test_rejects_unknown_mode(self):
        with pytest.raises(ValueError, match="file_access"):
            set_file_access("on")  # type: ignore[arg-type]

    def test_sets_cap(self):
        set_file_access("local", 123)
        assert get_file_access() == "local"
        assert get_max_inline_bytes() == 123


# ---------------------------------------------------------------------------
# Inline forms
# ---------------------------------------------------------------------------


class TestInlineText:
    async def test_push_content_with_md_filename_converts(self):
        result, client = await _call(
            "wiki_file_push",
            {
                "page_name": "P",
                "content": "# Title\n\nSome **bold** text.\n",
                "filename": "notes.md",
            },
        )
        assert result.isError is not True, result.content[0].text
        stored = client.put_wiki_page.call_args.args[1]
        assert "= Title =" in stored
        assert "'''bold'''" in stored
        assert result.structuredContent["source_format"] == "markdown"
        assert result.structuredContent["source"] == "inline content"
        assert result.structuredContent["file_path"] is None

    async def test_push_tracwiki_content_stored_verbatim(self):
        body = "= Title =\n{{{\n  indented\n}}}\n"
        result, client = await _call(
            "wiki_file_push",
            {"page_name": "P", "content": body, "format": "tracwiki"},
        )
        assert result.isError is not True
        assert client.put_wiki_page.call_args.args[1] == body

    async def test_push_without_filename_uses_heuristic(self):
        result, _ = await _call(
            "wiki_file_push",
            {"page_name": "P", "content": "= Title =\n'''bold'''\n"},
        )
        assert result.structuredContent["source_format"] == "tracwiki"

    async def test_push_both_forms_refused(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("x")
        result, client = await _call(
            "wiki_file_push",
            {"page_name": "P", "content": "x", "file_path": str(f)},
        )
        assert result.isError is True
        assert "not both" in result.content[0].text
        assert client.method_calls == []

    async def test_detect_format_inline(self):
        result, _ = await _call(
            "wiki_file_detect_format",
            {"content": "plain words", "filename": "x.md"},
        )
        assert result.structuredContent["format"] == "markdown"
        assert result.structuredContent["size_bytes"] == len(
            "plain words"
        )

    async def test_pull_inline_markdown(self):
        result, _ = await _call("wiki_file_pull", {"page_name": "P"})
        assert result.isError is not True
        assert result.structuredContent["content"].startswith("# Title")
        assert result.structuredContent["format"] == "markdown"


class TestInlineBinary:
    @pytest.mark.parametrize(
        "name,args,method",
        [
            (
                "ticket_attachment_put",
                {"ticket_id": 7},
                "put_ticket_attachment",
            ),
            (
                "wiki_attachment_put",
                {"page_name": "P"},
                "put_wiki_attachment",
            ),
        ],
    )
    async def test_put_decodes_base64_exactly(self, name, args, method):
        data = bytes(range(256))
        client = _make_client()
        getattr(client, method).return_value = "blob.bin"
        result, _ = await _call(
            name,
            {
                **args,
                "filename": "blob.bin",
                "content_base64": base64.b64encode(data).decode(),
            },
            client,
        )
        assert result.isError is not True, result.content[0].text
        sent = getattr(client, method).call_args.args[3]
        assert isinstance(sent, xmlrpc.client.Binary)
        assert sent.data == data
        assert result.structuredContent["bytes_uploaded"] == 256

    async def test_put_invalid_base64_refused(self):
        result, client = await _call(
            "ticket_attachment_put",
            {
                "ticket_id": 1,
                "filename": "a",
                "content_base64": "not base64!",
            },
        )
        assert result.isError is True
        assert "not valid base64" in result.content[0].text
        client.put_ticket_attachment.assert_not_called()

    async def test_put_inline_requires_filename(self):
        result, client = await _call(
            "wiki_attachment_put",
            {"page_name": "P", "content_base64": "eA=="},
        )
        assert result.isError is True
        assert "filename is required" in result.content[0].text
        client.put_wiki_attachment.assert_not_called()

    async def test_get_roundtrips_binary(self):
        data = bytes(range(256))
        client = _make_client()
        client.get_ticket_attachment.return_value = (
            xmlrpc.client.Binary(data)
        )
        result, _ = await _call(
            "ticket_attachment_get",
            {"ticket_id": 1, "filename": "a"},
            client,
        )
        assert (
            base64.b64decode(result.structuredContent["content_base64"])
            == data
        )


class TestInlineCap:
    async def test_get_over_cap_refused(self):
        set_file_access("off", 4)
        result, _ = await _call(
            "wiki_attachment_get", {"page_name": "P", "filename": "a"}
        )
        assert result.isError is True
        assert "inline limit of 4 bytes" in result.content[0].text

    async def test_pull_over_cap_refused(self):
        set_file_access("off", 4)
        result, _ = await _call(
            "wiki_file_pull", {"page_name": "P", "format": "tracwiki"}
        )
        assert result.isError is True
        assert "max_inline_bytes" in result.content[0].text

    async def test_at_cap_allowed(self):
        set_file_access("off", 5)
        result, _ = await _call(
            "ticket_attachment_get", {"ticket_id": 1, "filename": "a"}
        )
        assert result.isError is not True
        assert result.structuredContent["bytes"] == 5


# ---------------------------------------------------------------------------
# main() wiring: the mode in force while the server runs is the configured one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["off", "local"])
async def test_main_sets_file_access_while_serving(mode):
    from trac_mcp_server.mcp import server as server_module

    seen = {}

    async def _fake_run_http(_server, _config):
        seen["mode"] = get_file_access()
        seen["cap"] = get_max_inline_bytes()

    @asynccontextmanager
    async def _fake_lifespan(config_overrides=None):
        yield {"instances": MagicMock()}

    config = ServerConfig(
        transport="http", file_access=mode, max_inline_bytes=77
    )
    with (
        patch.object(
            server_module,
            "bootstrap_server_config",
            return_value=config,
        ),
        patch.object(server_module, "setup_logging"),
        patch.object(
            server_module,
            "check_version_consistency",
            return_value=(True, "ok"),
        ),
        patch.object(
            server_module, "load_declared_instances", return_value=[]
        ),
        patch.object(server_module, "server_lifespan", _fake_lifespan),
        patch.object(server_module, "run_http", _fake_run_http),
    ):
        await server_module.main()

    assert seen == {"mode": mode, "cap": 77}
    # And it is put back to the fail-closed default on the way out.
    assert get_file_access() == "off"
