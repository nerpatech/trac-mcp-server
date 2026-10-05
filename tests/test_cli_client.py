"""Tests for the trac-mcp CLI (ticket #111).

Two halves. Endpoint resolution is pure and tested directly. The tool
calls are tested against the real substrate (Rules/testing/
RealSubstrateNotMocks): the production MCP server -- its real
ToolRegistry, dispatch, identity and instance annotation -- served over
real uvicorn on an ephemeral port, driven by the CLI's real ``main()``
through the SDK's real streamable-HTTP client. Only Trac itself is an
in-memory fake, so the bytes the CLI read from disk can be compared with
the bytes "Trac" stored, and back.
"""

import base64
import json
import xmlrpc.client
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_mcp.test_http_identity import _free_port, _running_app
from trac_mcp_server.cli import client as cli
from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.mcp import server as server_module
from trac_mcp_server.mcp.tools.file_io import set_file_access

# ---------------------------------------------------------------------------
# Endpoint resolution
# ---------------------------------------------------------------------------


def _ns(**kw):
    base = {
        "url": None,
        "server": cli.DEFAULT_SERVER,
        "token_env": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _write_mcp_json(directory: Path, servers: dict) -> Path:
    path = directory / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


_HTTP_ENTRY = {
    "type": "http",
    "url": "http://192.0.2.1:8091/mcp",
    "headers": {"Authorization": "Bearer ${TOKEN_A}"},
}


class TestExpandVars:
    def test_expands_set_variable(self):
        assert cli.expand_vars("a${X}b", {"X": "1"}) == "a1b"

    def test_default_used_when_unset(self):
        assert cli.expand_vars("${X:-d}", {}) == "d"

    def test_unset_without_default_names_variable_only(self):
        with pytest.raises(cli.CliError, match="TOKEN_A is not set"):
            cli.expand_vars("Bearer ${TOKEN_A}", {})


class TestResolveEndpoint:
    def test_mcp_json_entry_and_its_own_token(self, tmp_path):
        _write_mcp_json(tmp_path, {"trac-http": _HTTP_ENTRY})
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        url, token, where = cli.resolve_endpoint(
            _ns(),
            sub,
            {"TOKEN_A": "tok-a", "TRAC_MCP_AUTH_TOKEN": "other"},
        )
        assert url == "http://192.0.2.1:8091/mcp"
        assert token == "tok-a"
        assert "tok-a" not in where
        assert ".mcp.json" in where

    def test_token_env_overrides_entry_header(self, tmp_path):
        _write_mcp_json(tmp_path, {"trac-http": _HTTP_ENTRY})
        _, token, _ = cli.resolve_endpoint(
            _ns(token_env="TOKEN_B"), tmp_path, {"TOKEN_B": "tok-b"}
        )
        assert token == "tok-b"

    def test_url_flag_wins(self, tmp_path):
        _write_mcp_json(tmp_path, {"trac-http": _HTTP_ENTRY})
        url, token, where = cli.resolve_endpoint(
            _ns(url="http://x/mcp"),
            tmp_path,
            {"TRAC_MCP_AUTH_TOKEN": "t"},
        )
        assert (url, token, where) == ("http://x/mcp", "t", "--url")

    def test_env_fallback_without_mcp_json(self, tmp_path):
        url, token, _ = cli.resolve_endpoint(
            _ns(),
            tmp_path,
            {
                "TRAC_MCP_URL": "http://e/mcp",
                "TRAC_MCP_AUTH_TOKEN": "t",
            },
        )
        assert (url, token) == ("http://e/mcp", "t")

    def test_stdio_entry_refused(self, tmp_path):
        _write_mcp_json(tmp_path, {"trac-http": {"command": "x"}})
        with pytest.raises(cli.CliError, match="no url"):
            cli.resolve_endpoint(_ns(), tmp_path, {})

    def test_named_server_missing(self, tmp_path):
        _write_mcp_json(tmp_path, {"trac-http": _HTTP_ENTRY})
        with pytest.raises(cli.CliError, match="no server 'other'"):
            cli.resolve_endpoint(_ns(server="other"), tmp_path, {})

    def test_nothing_to_connect_to(self, tmp_path):
        with pytest.raises(
            cli.CliError, match="no server to connect to"
        ):
            cli.resolve_endpoint(_ns(), tmp_path, {})


def test_detect_format_runs_locally(tmp_path, capsys):
    f = tmp_path / "x.md"
    f.write_text("anything")
    assert (
        cli.main(["detect-format", str(f)], cwd=tmp_path, environ={})
        == 0
    )
    assert "Format: markdown" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Real substrate: production server over uvicorn, fake Trac behind it
# ---------------------------------------------------------------------------


class FakeTrac:
    """In-memory Trac with exactly the client methods the file tools use."""

    def __init__(self):
        # write_gate off: the link gate would render through real Trac.
        self.config = SimpleNamespace(
            trac_url="http://fake/trac_test", write_gate=False
        )
        self.pages: dict[str, list[str]] = {}
        self.attachments: dict[tuple, bytes] = {}

    def get_wiki_page_info(self, name, version=None):
        return (
            {"version": len(self.pages[name])}
            if name in self.pages
            else {}
        )

    def put_wiki_page(self, name, content, comment, version):
        self.pages.setdefault(name, []).append(content)
        return {"version": len(self.pages[name])}

    def get_wiki_page(self, name, version=None):
        history = self.pages[name]
        return history[-1] if version is None else history[version - 1]

    def put_ticket_attachment(
        self, ticket_id, filename, desc, data, replace
    ):
        self.attachments[("ticket", ticket_id, filename)] = data.data
        return filename

    def get_ticket_attachment(self, ticket_id, filename):
        return xmlrpc.client.Binary(
            self.attachments[("ticket", ticket_id, filename)]
        )

    def put_wiki_attachment(self, page, filename, desc, data, replace):
        self.attachments[("wiki", page, filename)] = data.data
        return f"{page}/{filename}"

    def get_wiki_attachment(self, path):
        page, filename = path.rsplit("/", 1)
        return xmlrpc.client.Binary(
            self.attachments[("wiki", page, filename)]
        )


class _FakeInstances:
    def __init__(self, trac):
        self.trac = trac
        self.identities_seen = []

    def get_client(self, instance, identity=None):
        self.identities_seen.append(identity)
        return self.trac


_TOKEN = "cli-test-token"


@contextmanager
def _served(trac: FakeTrac, mode: str = "off"):
    """The production server globals, wired as main() wires them, behind
    real uvicorn, with ``trac`` as every instance. Yields the MCP
    endpoint URL."""
    with _served_instances(_FakeInstances(trac), mode) as url:
        yield url


@contextmanager
def _served_instances(instances, mode: str = "off"):
    """As :func:`_served`, over any instance registry -- a fake, or the
    real one for the live test."""
    from trac_mcp_server.mcp.server import PING_SPEC
    from trac_mcp_server.mcp.tools import ALL_SPECS, ToolRegistry
    from trac_mcp_server.mcp.tools.registry import (
        with_instance_param,
        with_page_alias,
        with_strict_schema,
    )

    specs = with_strict_schema(
        with_instance_param(
            with_page_alias([PING_SPEC] + ALL_SPECS), []
        )
    )
    server_module.set_registry(ToolRegistry(specs))
    server_module.set_instances(instances)
    set_file_access(mode)
    config = ServerConfig(
        transport="http",
        host="127.0.0.1",
        port=_free_port(),
        auth_token=_TOKEN,
    )
    try:
        with _running_app(server_module.server, config) as base_url:
            yield f"{base_url}/mcp"
    finally:
        server_module.set_registry(None)
        server_module.set_instances(None)
        set_file_access("off")


def _cli(url, *argv, token=_TOKEN):
    env = {"TRAC_MCP_AUTH_TOKEN": token} if token else {}
    return cli.main(["--url", url, *argv], cwd=Path("/"), environ=env)


class TestRealSubstrate:
    def test_wiki_push_pull_roundtrip_is_byte_exact(
        self, tmp_path, capsys
    ):
        # Quotes, backslashes, tabs, non-ASCII and a TracWiki code block:
        # everything an inline JSON body used to mangle.
        body = (
            '= Title =\n"quoted" \\backslash\\ \\n literal\n\tTab — em dash\n'
            "{{{\n  indented code\n}}}\n"
        )
        src = tmp_path / "page.wiki"
        src.write_bytes(body.encode("utf-8"))
        trac = FakeTrac()
        with _served(trac) as url:
            assert _cli(url, "wiki-push", "P", str(src)) == cli.EXIT_OK
            assert trac.pages["P"] == [body]
            out = tmp_path / "out.wiki"
            rc = _cli(
                url, "wiki-pull", "P", str(out), "--format", "tracwiki"
            )
            assert rc == cli.EXIT_OK
        assert out.read_bytes() == src.read_bytes()
        printed = capsys.readouterr().out
        assert (
            "Updated wiki page 'P'" in printed or "Created" in printed
        )
        assert "[instance:" in printed

    def test_markdown_file_converted_on_push(self, tmp_path):
        src = tmp_path / "notes.md"
        src.write_text("# Notes\n\nSome **bold**.\n")
        trac = FakeTrac()
        with _served(trac) as url:
            assert _cli(url, "wiki-push", "P", str(src)) == cli.EXIT_OK
        assert "= Notes =" in trac.pages["P"][0]
        assert "'''bold'''" in trac.pages["P"][0]

    @pytest.mark.parametrize(
        "target", [["--ticket", "7"], ["--page", "WikiStart"]]
    )
    def test_attachment_roundtrip_is_byte_exact(self, tmp_path, target):
        data = bytes(range(256)) * 64
        src = tmp_path / "blob.bin"
        src.write_bytes(data)
        out = tmp_path / "back.bin"
        trac = FakeTrac()
        with _served(trac) as url:
            assert (
                _cli(url, "attach-put", *target, str(src))
                == cli.EXIT_OK
            )
            rc = _cli(url, "attach-get", *target, "blob.bin", str(out))
            assert rc == cli.EXIT_OK
        assert out.read_bytes() == data

    def test_bad_token_is_a_connect_error(self, tmp_path, capsys):
        src = tmp_path / "a.wiki"
        src.write_text("= A =\n")
        trac = FakeTrac()
        with _served(trac) as url:
            rc = _cli(url, "wiki-push", "P", str(src), token="wrong")
        assert rc == cli.EXIT_CONNECT_ERROR
        assert trac.pages == {}
        assert "wrong" not in capsys.readouterr().err

    def test_tool_error_is_reported_with_exit_1(self, tmp_path, capsys):
        trac = FakeTrac()
        with _served(trac) as url:
            rc = _cli(
                url,
                "attach-get",
                "--ticket",
                "1",
                "missing.bin",
                str(tmp_path / "x"),
            )
        assert rc == cli.EXIT_TOOL_ERROR
        assert "Error" in capsys.readouterr().err

    def test_off_server_still_refuses_a_raw_path(self, tmp_path):
        """The gate holds over the real transport too: a client that
        bypasses the CLI and sends a path gets a refusal."""
        import asyncio

        secret = tmp_path / "secret.wiki"
        secret.write_text("= Secret =\n")
        trac = FakeTrac()
        with _served(trac) as url:
            result = asyncio.run(
                cli.call_tool(
                    url,
                    _TOKEN,
                    "wiki_file_push",
                    {"page_name": "P", "file_path": str(secret)},
                )
            )
        assert result.isError is True
        assert "file_access is 'off'" in result.content[0].text
        assert trac.pages == {}


def test_inline_payloads_never_carry_a_path(tmp_path, monkeypatch):
    """What the CLI sends is the bytes, never the local path -- the path
    would mean nothing on the server, and with file_access off it would be
    refused."""
    sent = []

    async def _record(url, token, name, arguments):
        sent.append((name, arguments))
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="ok")],
            structuredContent={},
        )

    monkeypatch.setattr(cli, "call_tool", _record)
    f = tmp_path / "a.bin"
    f.write_bytes(b"\x00\xff")
    env = {"TRAC_MCP_AUTH_TOKEN": "t"}
    cli.main(
        [
            "--url",
            "http://x/mcp",
            "attach-put",
            "--ticket",
            "1",
            str(f),
        ],
        cwd=tmp_path,
        environ=env,
    )
    cli.main(
        ["--url", "http://x/mcp", "wiki-push", "P", str(f)],
        cwd=tmp_path,
        environ=env,
    )
    for _name, arguments in sent:
        assert "file_path" not in arguments
        assert str(tmp_path) not in json.dumps(arguments)
    assert (
        sent[0][1]["content_base64"]
        == base64.b64encode(b"\x00\xff").decode()
    )


# ---------------------------------------------------------------------------
# Live: the same path end to end against real Trac (/trac_test)
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_cli_roundtrip_through_real_server(tmp_path):
    """CLI -> real server stack (file_access off) -> real /trac_test, and
    back: a page body and a page attachment both round-trip byte-exact.
    The write gate is live too, so the body carries no links."""
    import asyncio
    import os
    import uuid

    from trac_mcp_server.config import Config
    from trac_mcp_server.instances import InstanceRegistry

    default_config = Config(
        trac_url=os.environ["TRAC_URL"],
        username=os.environ["TRAC_USERNAME"],
        password=os.environ["TRAC_PASSWORD"],
        insecure=os.environ.get("TRAC_INSECURE", "").lower()
        in ("1", "true", "yes"),
    )
    page = f"Sandbox/Ticket111Cli{uuid.uuid4().hex[:8]}"
    body = (
        "= Ticket 111 live CLI scratch =\n"
        '"quoted" \\backslash\\ \\n literal\n\tTab \u2014 em dash\n'
        "{{{\n  indented code\n}}}\n"
    )
    src = tmp_path / "page.wiki"
    src.write_bytes(body.encode("utf-8"))
    blob = tmp_path / "blob.bin"
    blob.write_bytes(bytes(range(256)) * 16)
    instance = ["--instance", "/trac_test"]

    with _served_instances(InstanceRegistry(default_config, {})) as url:
        try:
            assert (
                _cli(url, *instance, "wiki-push", page, str(src))
                == cli.EXIT_OK
            )
            out = tmp_path / "back.wiki"
            assert (
                _cli(
                    url,
                    *instance,
                    "wiki-pull",
                    page,
                    str(out),
                    "--format",
                    "tracwiki",
                )
                == cli.EXIT_OK
            )
            assert out.read_bytes() == src.read_bytes()

            assert (
                _cli(
                    url,
                    *instance,
                    "attach-put",
                    "--page",
                    page,
                    str(blob),
                )
                == cli.EXIT_OK
            )
            back = tmp_path / "back.bin"
            assert (
                _cli(
                    url,
                    *instance,
                    "attach-get",
                    "--page",
                    page,
                    "blob.bin",
                    str(back),
                )
                == cli.EXIT_OK
            )
            assert back.read_bytes() == blob.read_bytes()
        finally:
            asyncio.run(
                cli.call_tool(
                    url,
                    _TOKEN,
                    "wiki_delete",
                    {"page_name": page, "instance": "/trac_test"},
                )
            )
