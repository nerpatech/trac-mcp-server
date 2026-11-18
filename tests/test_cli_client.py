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
import os
import shlex
import sys
import time
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


def _helper_script(directory: Path, body: str) -> str:
    """A real ``headersHelper`` command: a Python script, run by the
    shell exactly as Claude Code runs the entry's string."""
    script = directory / "helper.py"
    script.write_text(body)
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"


_PRINT_TOKEN = (
    "import json\n"
    'print(json.dumps({"Authorization": "Bearer fake-helper-token"}))\n'
)


def _helper_entry(command: str, **extra) -> dict:
    return {
        "type": "http",
        "url": "http://192.0.2.1:8091/mcp",
        "headersHelper": command,
        **extra,
    }


class TestHeadersHelper:
    """Claude Code's ``headersHelper`` key (Trac /trac_mcp_server #254):
    a command that prints a JSON object of headers, run per connection.
    Real commands throughout, no subprocess mocking."""

    def test_helper_token_is_used_and_the_helper_sees_its_context(
        self, tmp_path
    ):
        project = tmp_path / "project"
        project.mkdir()
        seen = tmp_path / "seen.json"
        command = _helper_script(
            tmp_path,
            "import json, os\n"
            f"open({str(seen)!r}, 'w').write(json.dumps({{\n"
            '    "name": os.environ.get("CLAUDE_CODE_MCP_SERVER_NAME"),\n'
            '    "url": os.environ.get("CLAUDE_CODE_MCP_SERVER_URL"),\n'
            '    "cwd": os.getcwd(),\n'
            '    "path": os.environ.get("MARKER_FROM_CALLER"),\n'
            "}))\n" + _PRINT_TOKEN,
        )
        entry = _helper_entry(command)
        entry["url"] = "http://${HOST_X:-192.0.2.1}:8091/mcp"
        _write_mcp_json(project, {"trac-http": entry})
        sub = project / "a"
        sub.mkdir()
        url, token, where = cli.resolve_endpoint(
            _ns(), sub, {**os.environ, "MARKER_FROM_CALLER": "yes"}
        )
        assert url == "http://192.0.2.1:8091/mcp"
        assert token == "fake-helper-token"
        assert "fake-helper-token" not in where
        info = json.loads(seen.read_text())
        assert info["name"] == "trac-http"
        assert info["url"] == "http://192.0.2.1:8091/mcp"
        assert Path(info["cwd"]).resolve() == project.resolve()
        assert info["path"] == "yes"

    def test_helper_command_is_not_variable_expanded_by_the_cli(
        self, tmp_path
    ):
        # The shell expands it; the CLI's expand_vars would raise on an
        # unset ${VAR} here.
        command = "printf '%s' '{\"Authorization\": \"Bearer '\"${HTOK}\"'\"}'"
        _write_mcp_json(tmp_path, {"trac-http": _helper_entry(command)})
        _, token, _ = cli.resolve_endpoint(
            _ns(), tmp_path, {**os.environ, "HTOK": "from-shell"}
        )
        assert token == "from-shell"

    def test_helper_overrides_a_static_header_of_the_same_name(
        self, tmp_path
    ):
        command = _helper_script(tmp_path, _PRINT_TOKEN)
        _write_mcp_json(
            tmp_path,
            {
                "trac-http": _helper_entry(
                    command,
                    headers={"Authorization": "Bearer ${TOKEN_A}"},
                )
            },
        )
        _, token, _ = cli.resolve_endpoint(
            _ns(), tmp_path, {**os.environ, "TOKEN_A": "static-token"}
        )
        assert token == "fake-helper-token"

    def test_static_header_used_when_helper_has_no_authorization(
        self, tmp_path
    ):
        command = _helper_script(
            tmp_path, 'print(\'{"X-Other": "v"}\')\n'
        )
        _write_mcp_json(
            tmp_path,
            {
                "trac-http": _helper_entry(
                    command,
                    headers={"Authorization": "Bearer ${TOKEN_A}"},
                )
            },
        )
        _, token, _ = cli.resolve_endpoint(
            _ns(), tmp_path, {**os.environ, "TOKEN_A": "static-token"}
        )
        assert token == "static-token"

    def test_helper_without_authorization_and_no_static_is_none(
        self, tmp_path
    ):
        command = _helper_script(tmp_path, "print('{}')\n")
        _write_mcp_json(tmp_path, {"trac-http": _helper_entry(command)})
        _, token, _ = cli.resolve_endpoint(
            _ns(), tmp_path, dict(os.environ)
        )
        assert token is None

    def test_token_env_means_the_helper_is_not_run(self, tmp_path):
        marker = tmp_path / "ran"
        command = _helper_script(
            tmp_path,
            f"open({str(marker)!r}, 'w').write('x')\n" + _PRINT_TOKEN,
        )
        _write_mcp_json(tmp_path, {"trac-http": _helper_entry(command)})
        _, token, _ = cli.resolve_endpoint(
            _ns(token_env="TOKEN_B"),
            tmp_path,
            {**os.environ, "TOKEN_B": "tok-b"},
        )
        assert token == "tok-b"
        assert not marker.exists()

    def test_non_bearer_authorization_from_helper_is_refused(
        self, tmp_path
    ):
        command = _helper_script(
            tmp_path, 'print(\'{"Authorization": "Basic c2VjcmV0"}\')\n'
        )
        _write_mcp_json(tmp_path, {"trac-http": _helper_entry(command)})
        with pytest.raises(cli.CliError, match="not 'Bearer") as exc:
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        assert exc.value.code == cli.EXIT_CONNECT_ERROR
        assert "c2VjcmV0" not in str(exc.value)

    def test_failing_helper_reports_exit_and_stderr_never_stdout(
        self, tmp_path
    ):
        command = _helper_script(
            tmp_path,
            "import sys\n"
            "print('Bearer sk-SECRET-on-stdout')\n"
            "sys.stderr.write('vault locked\\n')\n"
            "sys.stderr.write('pass: entry not found\\n\\n')\n"
            "sys.exit(7)\n",
        )
        _write_mcp_json(tmp_path, {"trac-http": _helper_entry(command)})
        with pytest.raises(cli.CliError) as exc:
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        assert exc.value.code == cli.EXIT_CONNECT_ERROR
        msg = str(exc.value)
        assert "exited 7" in msg
        assert "trac-http" in msg
        assert ".mcp.json" in msg
        assert "pass: entry not found" in msg
        assert "vault locked" not in msg  # only the last line
        assert "SECRET" not in msg

    def test_stderr_line_is_truncated(self, tmp_path):
        command = _helper_script(
            tmp_path,
            "import sys\nsys.stderr.write('e' * 1000 + '\\n')\n"
            "sys.exit(2)\n",
        )
        _write_mcp_json(tmp_path, {"trac-http": _helper_entry(command)})
        with pytest.raises(cli.CliError) as exc:
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        assert "e" * 200 in str(exc.value)
        assert "e" * 201 not in str(exc.value)

    @pytest.mark.parametrize(
        "stdout",
        [
            "Bearer sk-SECRET-not-json",
            '["sk-SECRET-a-list"]',
            '"sk-SECRET-a-string"',
            '{"Authorization": 12345, "X": "sk-SECRET"}',
            '{"Authorization": {"v": "sk-SECRET"}}',
            "",
        ],
        ids=[
            "not json",
            "array",
            "string",
            "non-string value",
            "nested value",
            "empty",
        ],
    )
    def test_unusable_output_is_a_connect_error_without_content(
        self, tmp_path, stdout
    ):
        command = _helper_script(
            tmp_path, f"import sys\nsys.stdout.write({stdout!r})\n"
        )
        _write_mcp_json(tmp_path, {"trac-http": _helper_entry(command)})
        with pytest.raises(cli.CliError) as exc:
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        assert exc.value.code == cli.EXIT_CONNECT_ERROR
        msg = str(exc.value)
        assert "did not print a JSON object of headers" in msg
        assert "SECRET" not in msg
        assert "12345" not in msg

    def test_helper_that_cannot_start_is_a_connect_error(
        self, tmp_path
    ):
        _write_mcp_json(
            tmp_path,
            {"trac-http": _helper_entry("/no/such/helper-254")},
        )
        with pytest.raises(cli.CliError) as exc:
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        # Through a shell a missing command is exit 127, not OSError.
        assert exc.value.code == cli.EXIT_CONNECT_ERROR
        assert "exited 127" in str(exc.value)

    def test_oserror_on_start_is_a_connect_error(
        self, tmp_path, monkeypatch
    ):
        # A shell that cannot be spawned (cwd vanished, EMFILE, ...).
        _write_mcp_json(tmp_path, {"trac-http": _helper_entry("true")})

        def _boom(*a, **k):
            raise OSError("no fork for you")

        monkeypatch.setattr(cli.subprocess, "Popen", _boom)
        with pytest.raises(cli.CliError, match="no fork for you") as e:
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        assert e.value.code == cli.EXIT_CONNECT_ERROR

    def test_slow_helper_times_out(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "HEADERS_HELPER_TIMEOUT_S", 0.5)
        command = _helper_script(
            tmp_path, "import time\ntime.sleep(5)\n" + _PRINT_TOKEN
        )
        _write_mcp_json(tmp_path, {"trac-http": _helper_entry(command)})
        start = time.monotonic()
        with pytest.raises(cli.CliError) as exc:
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        assert time.monotonic() - start < 4
        assert exc.value.code == cli.EXIT_CONNECT_ERROR
        assert "timed out" in str(exc.value)

    def test_helper_escaping_the_process_group_does_not_hang_the_reap(
        self, tmp_path, monkeypatch
    ):
        # A descendant that called setsid escapes the group kill and
        # keeps the pipes open for 8 s; the wait after a timeout must be
        # bounded, not follow it.
        monkeypatch.setattr(cli, "HEADERS_HELPER_TIMEOUT_S", 0.5)
        monkeypatch.setattr(cli, "_HELPER_REAP_TIMEOUT_S", 0.5)
        _write_mcp_json(
            tmp_path,
            {"trac-http": _helper_entry("setsid sleep 8 & sleep 5")},
        )
        start = time.monotonic()
        with pytest.raises(cli.CliError) as exc:
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        assert time.monotonic() - start < 3
        assert exc.value.code == cli.EXIT_CONNECT_ERROR
        assert "timed out" in str(exc.value)

    def test_empty_authorization_from_helper_never_falls_back(
        self, tmp_path
    ):
        command = _helper_script(
            tmp_path, 'print(\'{"Authorization": ""}\')\n'
        )
        _write_mcp_json(
            tmp_path,
            {
                "trac-http": _helper_entry(
                    command,
                    headers={"Authorization": "Bearer static-token"},
                )
            },
        )
        with pytest.raises(cli.CliError) as exc:
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        assert exc.value.code == cli.EXIT_CONNECT_ERROR
        assert "printed an empty Authorization header" in str(exc.value)
        assert "static-token" not in str(exc.value)

    def test_whitespace_authorization_from_helper_is_empty(
        self, tmp_path
    ):
        command = _helper_script(
            tmp_path, 'print(\'{"authorization": "  "}\')\n'
        )
        _write_mcp_json(
            tmp_path,
            {
                "trac-http": _helper_entry(
                    command,
                    headers={"Authorization": "Bearer static-token"},
                )
            },
        )
        with pytest.raises(
            cli.CliError, match="printed an empty Authorization"
        ):
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))

    def test_helper_name_collision_is_case_insensitive(self, tmp_path):
        command = _helper_script(
            tmp_path,
            'print(\'{"authorization": "Bearer fake-helper-token"}\')\n',
        )
        _write_mcp_json(
            tmp_path,
            {
                "trac-http": _helper_entry(
                    command,
                    headers={"Authorization": "Bearer static-token"},
                )
            },
        )
        _, token, _ = cli.resolve_endpoint(
            _ns(), tmp_path, dict(os.environ)
        )
        assert token == "fake-helper-token"

    @pytest.mark.parametrize(
        "bad", [["pass", "show", "trac"], 7, {"cmd": "x"}, None]
    )
    def test_non_string_helper_is_a_connect_error(self, tmp_path, bad):
        _write_mcp_json(
            tmp_path,
            {
                "trac-http": _helper_entry(
                    bad, headers={"Authorization": "Bearer static"}
                )
            },
        )
        with pytest.raises(cli.CliError) as exc:
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        assert exc.value.code == cli.EXIT_CONNECT_ERROR
        assert "headersHelper for server 'trac-http' in " in str(
            exc.value
        )
        assert "must be a command string" in str(exc.value)

    def test_whitespace_helper_string_counts_as_absent(self, tmp_path):
        _write_mcp_json(
            tmp_path,
            {
                "trac-http": _helper_entry(
                    "  \t",
                    headers={"Authorization": "Bearer ${TOKEN_A}"},
                )
            },
        )
        _, token, _ = cli.resolve_endpoint(
            _ns(), tmp_path, {"TOKEN_A": "static-token"}
        )
        assert token == "static-token"

    def test_control_characters_in_stderr_line_are_replaced(
        self, tmp_path
    ):
        command = _helper_script(
            tmp_path,
            "import sys\n"
            "sys.stderr.write('\\x1b[31mred\\x1b[0m\\ttab\\x07bell\\n')\n"
            "sys.exit(3)\n",
        )
        _write_mcp_json(tmp_path, {"trac-http": _helper_entry(command)})
        with pytest.raises(cli.CliError) as exc:
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        msg = str(exc.value)
        assert "?[31mred?[0m?tab?bell" in msg
        assert all(c.isprintable() for c in msg)

    def test_interrupt_kills_the_helper_and_reraises(
        self, tmp_path, monkeypatch
    ):
        """Ctrl-C while the CLI waits must not orphan the helper: the
        wait is interrupted by a real SIGINT sent to the main thread
        from a timer thread, and the helper (which records its pid) must
        be dead afterwards."""
        import signal
        import threading

        main_thread = threading.get_ident()

        pidfile = tmp_path / "pid"
        command = _helper_script(
            tmp_path,
            "import os, time\n"
            f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
            "time.sleep(30)\n",
        )
        _write_mcp_json(tmp_path, {"trac-http": _helper_entry(command)})

        def _interrupt():
            deadline = time.monotonic() + 10
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(0.3)
            signal.pthread_kill(main_thread, signal.SIGINT)

        threading.Thread(target=_interrupt, daemon=True).start()
        with pytest.raises(KeyboardInterrupt):
            cli.resolve_endpoint(_ns(), tmp_path, dict(os.environ))
        pid = int(pidfile.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            pytest.fail("the helper outlived the interrupted CLI")

    def test_empty_helper_string_is_ignored(self, tmp_path):
        _write_mcp_json(
            tmp_path,
            {
                "trac-http": _helper_entry(
                    "", headers={"Authorization": "Bearer ${TOKEN_A}"}
                )
            },
        )
        _, token, _ = cli.resolve_endpoint(
            _ns(), tmp_path, {"TOKEN_A": "static-token"}
        )
        assert token == "static-token"


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
            trac_url="http://fake/trac_test",
            write_gate=False,
            link_rewrites=(),
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

    def test_wiki_pull_prints_each_warning_on_stderr_after_the_write(
        self, tmp_path, capsys
    ):
        """``wiki_file_pull`` returns the translator's (and the pin's)
        warnings in structuredContent; the CLI prints each as a
        ``trac-mcp: warning:`` line on stderr, and the file is written
        and the exit is 0 all the same."""

        class _Html(FakeTrac):
            def get_wiki_page_html(self, name, version=None):
                return (
                    '<h1 id="T">T</h1>\n'
                    '<form action="/search"><input name="q"></form>\n'
                )

        trac = _Html()
        trac.pages["P"] = ["= T =\n"]
        out = tmp_path / "out.md"
        with _served(trac) as url:
            rc = _cli(url, "wiki-pull", "P", str(out))
        assert rc == cli.EXIT_OK
        written = out.read_text()
        assert written.startswith('---\ntitle: "T"\n')
        assert "\n---\n\n# T" in written
        captured = capsys.readouterr()
        lines = [
            ln
            for ln in captured.err.splitlines()
            if ln.startswith("trac-mcp: warning: ")
        ]
        assert len(lines) >= 1
        assert any("form" in ln for ln in lines), captured.err
        # stdout keeps only the status line, with no warning text.
        assert "warning" not in captured.out
        assert "Pulled wiki page 'P'" in captured.out

    def test_wiki_pull_without_warnings_prints_none(
        self, tmp_path, capsys
    ):
        trac = FakeTrac()
        trac.pages["P"] = ["= T =\n"]
        out = tmp_path / "out.wiki"
        with _served(trac) as url:
            rc = _cli(
                url, "wiki-pull", "P", str(out), "--format", "tracwiki"
            )
        assert rc == cli.EXIT_OK
        assert "warning" not in capsys.readouterr().err

    def test_wiki_push_stored_but_readback_failed_exits_4_not_1(
        self, tmp_path, capsys
    ):
        """Trac /trac_mcp_server #140: the page is stored and only the
        read-back of its new version fails. Exit 1 means nothing was
        stored, so this must be 4, and the page written exactly once."""
        from trac_mcp_server.core.client import (
            WikiPageStoredReadbackFailed,
        )

        class _ReadbackFails(FakeTrac):
            def put_wiki_page(self, name, content, comment, version):
                self.pages.setdefault(name, []).append(content)
                raise WikiPageStoredReadbackFailed(
                    f"Page '{name}' was stored, but reading back its new "
                    "version failed: Internal error",
                    name,
                    f"http://fake/trac_test/wiki/{name}",
                    xmlrpc.client.Fault(1, "Internal error"),
                )

        src = tmp_path / "page.wiki"
        src.write_text("= Title =\n")
        trac = _ReadbackFails()
        with _served(trac) as url:
            rc = _cli(url, "wiki-push", "P", str(src))
        assert rc == cli.EXIT_STORED_INCOMPLETE
        assert rc != cli.EXIT_TOOL_ERROR
        assert trac.pages["P"] == ["= Title =\n"]
        captured = capsys.readouterr()
        assert "WAS stored" in captured.err
        assert "do not retry" in captured.err
        assert "Created wiki page 'P' (version unknown)" in captured.out

    def test_wiki_push_possible_overwrite_warns_on_stderr_exits_0(
        self, tmp_path, capsys
    ):
        """Trac /trac_mcp_server #130: the update was based on version 3
        but stored as 5. The write succeeded, so the exit stays 0; the
        warning goes to stderr."""

        class _Raced(FakeTrac):
            def get_wiki_page_info(self, name, version=None):
                return {"name": name, "version": 3}

            def put_wiki_page(self, name, content, comment, version):
                self.pages.setdefault(name, []).append(content)
                return {
                    "version": 5,
                    "possible_overwrite": True,
                    "intervening_versions": [4],
                }

        src = tmp_path / "page.wiki"
        src.write_text("= Title =\n")
        with _served(_Raced()) as url:
            rc = _cli(url, "wiki-push", "P", str(src))
        assert rc == cli.EXIT_OK
        captured = capsys.readouterr()
        assert "Updated wiki page 'P' (version 5)" in captured.out
        assert "possible overwrite on wiki page 'P'" in captured.err
        assert "version 4" in captured.err
        assert "wiki_diff" in captured.err

    def test_wiki_push_uncontended_prints_no_overwrite_warning(
        self, tmp_path, capsys
    ):
        src = tmp_path / "page.wiki"
        src.write_text("= Title =\n")
        trac = FakeTrac()
        trac.pages["P"] = ["old\n"]
        with _served(trac) as url:
            assert _cli(url, "wiki-push", "P", str(src)) == cli.EXIT_OK
        captured = capsys.readouterr()
        assert "overwrite" not in captured.err.lower()
        assert "overwrite" not in captured.out.lower()

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
        err = capsys.readouterr().err
        assert "wrong" not in err
        # The real cause, not "ExceptionGroup: unhandled errors in a
        # TaskGroup" (review finding on #111).
        assert "HTTP 401" in err
        assert "token" in err
        assert "ExceptionGroup" not in err

    def test_headers_helper_token_reaches_the_server(
        self, tmp_path, capsys
    ):
        """Through main(), with no --url: the entry's headersHelper
        supplies the bearer token the real server checks (Trac
        /trac_mcp_server #254)."""
        src = tmp_path / "a.wiki"
        src.write_text("= A =\n")
        trac = FakeTrac()
        with _served(trac) as url:
            command = _helper_script(
                tmp_path,
                "import json\n"
                f'print(json.dumps({{"Authorization": "Bearer {_TOKEN}"}}))\n',
            )
            project = tmp_path / "project"
            project.mkdir()
            _write_mcp_json(
                project,
                {
                    "trac-http": {
                        "type": "http",
                        "url": url,
                        "headersHelper": command,
                    }
                },
            )
            rc = cli.main(
                ["wiki-push", "P", str(src)],
                cwd=project,
                environ=dict(os.environ),
            )
        assert rc == cli.EXIT_OK
        assert trac.pages["P"] == ["= A =\n"]
        assert "no bearer token" not in capsys.readouterr().err

    def test_headers_helper_without_authorization_says_where(
        self, tmp_path, capsys
    ):
        src = tmp_path / "a.wiki"
        src.write_text("= A =\n")
        trac = FakeTrac()
        with _served(trac) as url:
            command = _helper_script(tmp_path, "print('{}')\n")
            _write_mcp_json(
                tmp_path,
                {
                    "trac-http": {
                        "type": "http",
                        "url": url,
                        "headersHelper": command,
                    }
                },
            )
            rc = cli.main(
                ["wiki-push", "P", str(src)],
                cwd=tmp_path,
                environ=dict(os.environ),
            )
        assert rc == cli.EXIT_CONNECT_ERROR
        err = capsys.readouterr().err
        assert "supplies no Authorization header" in err
        assert ".mcp.json (trac-http)" in err
        assert "TRAC_MCP_AUTH_TOKEN is unset" not in err

    def test_no_token_from_env_keeps_the_env_wording(
        self, tmp_path, capsys
    ):
        src = tmp_path / "a.wiki"
        src.write_text("= A =\n")
        trac = FakeTrac()
        with _served(trac) as url:
            rc = cli.main(
                ["--url", url, "wiki-push", "P", str(src)],
                cwd=Path("/"),
                environ={},
            )
        assert rc == cli.EXIT_CONNECT_ERROR
        assert (
            "no bearer token sent (TRAC_MCP_AUTH_TOKEN is unset)"
            in capsys.readouterr().err
        )

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

    from tests.live_helpers import trac_test_registry
    from trac_mcp_server.config import Config

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

    with _served_instances(trac_test_registry(default_config)) as url:
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


@pytest.mark.live
def test_live_ticket_comment_edit_long_body_and_expect_file(
    tmp_path, capsys
):
    """Trac /trac_mcp_server #220, end to end on a /trac_test scratch
    ticket: a comment is added (under the add limit), then edited from a
    ~12000-character file (over it); ``ticket-pull --comment`` returns the
    file's bytes exactly, and ``--expect-file`` with the original -- now
    stale -- body is refused and changes nothing. The write gate is live,
    so the bodies carry no links."""
    import asyncio
    import os
    import re

    from tests.live_helpers import trac_test_registry
    from trac_mcp_server.config import Config

    default_config = Config(
        trac_url=os.environ["TRAC_URL"],
        username=os.environ["TRAC_USERNAME"],
        password=os.environ["TRAC_PASSWORD"],
        insecure=os.environ.get("TRAC_INSECURE", "").lower()
        in ("1", "true", "yes"),
    )
    instance = ["--instance", "/trac_test"]
    original = "Original comment for ticket 220.\nsecond line\n"
    long_body = "".join(
        f"Line {i:04d} of the long edited comment, plain words only.\n"
        for i in range(200)
    )
    assert len(long_body) > 10000
    first = tmp_path / "original.wiki"
    first.write_bytes(original.encode("utf-8"))
    edited = tmp_path / "edited.wiki"
    edited.write_bytes(long_body.encode("utf-8"))
    desc = tmp_path / "desc.wiki"
    desc.write_bytes(
        b"Scratch ticket for the ticket-comment-edit live row.\n"
    )

    with _served_instances(trac_test_registry(default_config)) as url:
        ticket_id = None
        try:
            assert (
                _cli(
                    url,
                    *instance,
                    "ticket-create",
                    "--summary",
                    "Ticket 220 live CLI scratch",
                    "--description-file",
                    str(desc),
                )
                == cli.EXIT_OK
            )
            created = re.search(
                r"Created ticket #(\d+)", capsys.readouterr().out
            )
            assert created
            ticket_id = int(created.group(1))
            tid = str(ticket_id)
            assert (
                _cli(url, *instance, "ticket-comment", tid, str(first))
                == cli.EXIT_OK
            )
            cnum = str(_printed_comment_number(capsys.readouterr().out))

            assert (
                _cli(
                    url,
                    *instance,
                    "ticket-comment-edit",
                    tid,
                    cnum,
                    str(edited),
                    "--expect-file",
                    str(first),
                )
                == cli.EXIT_OK
            )
            assert (
                f"Stored comment {cnum} matches the text sent, byte-for-byte"
                in capsys.readouterr().out
            )
            back = tmp_path / "back.wiki"
            assert (
                _cli(
                    url,
                    *instance,
                    "ticket-pull",
                    tid,
                    str(back),
                    "--comment",
                    cnum,
                )
                == cli.EXIT_OK
            )
            assert back.read_bytes() == edited.read_bytes()

            # The original body is stale now: refused, nothing changed.
            assert (
                _cli(
                    url,
                    *instance,
                    "ticket-comment-edit",
                    tid,
                    cnum,
                    str(first),
                    "--expect-file",
                    str(first),
                )
                == cli.EXIT_TOOL_ERROR
            )
            assert "nothing was edited" in capsys.readouterr().err
            again = tmp_path / "again.wiki"
            assert (
                _cli(
                    url,
                    *instance,
                    "ticket-pull",
                    tid,
                    str(again),
                    "--comment",
                    cnum,
                )
                == cli.EXIT_OK
            )
            assert again.read_bytes() == edited.read_bytes()
        finally:
            if ticket_id is not None:
                asyncio.run(
                    cli.call_tool(
                        url,
                        _TOKEN,
                        "ticket_delete",
                        {
                            "ticket_id": ticket_id,
                            "instance": "/trac_test",
                        },
                    )
                )


# ---------------------------------------------------------------------------
# Failures the CLI must name precisely, before any network call where it can
# ---------------------------------------------------------------------------


def _no_network(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("call_tool must not run")

    monkeypatch.setattr(cli, "call_tool", _boom)


def test_named_token_env_unset_fails_before_connecting(
    tmp_path, monkeypatch, capsys
):
    _no_network(monkeypatch)
    f = tmp_path / "a.wiki"
    f.write_text("x")
    rc = cli.main(
        [
            "--url",
            "http://x/mcp",
            "--token-env",
            "NOPE",
            "wiki-push",
            "P",
            str(f),
        ],
        cwd=tmp_path,
        environ={},
    )
    assert rc == cli.EXIT_CONNECT_ERROR
    assert "NOPE is not set" in capsys.readouterr().err


def test_missing_output_dir_fails_before_downloading(
    tmp_path, monkeypatch, capsys
):
    _no_network(monkeypatch)
    rc = cli.main(
        [
            "--url",
            "http://x/mcp",
            "attach-get",
            "--ticket",
            "1",
            "a.bin",
            str(tmp_path / "missing" / "a.bin"),
        ],
        cwd=tmp_path,
        environ={"TRAC_MCP_AUTH_TOKEN": "t"},
    )
    assert rc == cli.EXIT_USAGE_ERROR
    assert "output directory does not exist" in capsys.readouterr().err


def test_result_without_payload_is_a_tool_error(
    tmp_path, monkeypatch, capsys
):
    async def _old_server(url, token, name, arguments):
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="Pulled ...")],
            structuredContent=None,
        )

    monkeypatch.setattr(cli, "call_tool", _old_server)
    rc = cli.main(
        [
            "--url",
            "http://x/mcp",
            "wiki-pull",
            "P",
            str(tmp_path / "p.md"),
        ],
        cwd=tmp_path,
        environ={"TRAC_MCP_AUTH_TOKEN": "t"},
    )
    assert rc == cli.EXIT_TOOL_ERROR
    assert "carries no content" in capsys.readouterr().err
    assert not (tmp_path / "p.md").exists()


# ---------------------------------------------------------------------------
# Ticket bodies (#114): ticket-comment / -describe / -create / -pull
# ---------------------------------------------------------------------------

# Everything an inline JSON body or a shell heredoc used to mangle: both
# quote kinds, backslashes, a literal two-character \n, a tab, a non-ASCII
# dash, a TracWiki code block, and a trailing newline.
_HARD_BODY = (
    "= Heading =\n"
    "\"double\" 'single' \\backslash\\ and a literal \\n here\n"
    "\tTab — em dash\n"
    "{{{\n  indented code\n}}}\n"
)


class FakeTracTickets(FakeTrac):
    """FakeTrac plus the ticket client methods the ticket tools call.

    ``_ts`` is a numeric string that changes on every write, as in Trac;
    ``update_ticket`` enforces ``base_ts`` the way the real client does.
    Comment rows are ``[time, author, "comment", number, body, permanent]``
    with the number in ``oldvalue``, as the server's own handlers read them.
    """

    def __init__(self):
        super().__init__()
        self.tickets: dict[int, dict] = {}
        self.changelogs: dict[int, list[list]] = {}
        self._clock = 1_700_000_000
        self.get_calls = 0
        self.updates: list[dict] = []
        self.creates: list[dict] = []
        # Called after each get_ticket: a "concurrent" writer.
        self.on_get = None
        self.omit_ts = False
        self.hide_changelog = False
        # The real XML-RPC transport folds CR/CRLF to LF before Trac
        # stores a comment; off by default so byte-exact round trips of
        # the bodies below are unchanged.
        self.fold_cr = False
        self.comment_edits: list[dict] = []

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    def add_ticket(self, ticket_id, description="old", comments=()):
        """Seed a ticket; ``comments`` is a list of (number, body)."""
        self.tickets[ticket_id] = {
            "summary": f"ticket {ticket_id}",
            "description": description,
            "status": "new",
            "type": "defect",
            "_ts": str(self._tick() * 1_000_000),
        }
        self.changelogs[ticket_id] = [
            [self._tick(), "alice", "comment", str(n), body, 1]
            for n, body in comments
        ]

    def touch(self, ticket_id, who="bob"):
        """Someone else changes the ticket."""
        self.tickets[ticket_id]["_ts"] = str(self._tick() * 1_000_000)
        self.changelogs[ticket_id].append(
            [self._clock, who, "keywords", "", "x", 1]
        )

    def get_ticket(self, ticket_id):
        self.get_calls += 1
        attrs = dict(self.tickets[ticket_id])
        if self.omit_ts:
            attrs.pop("_ts")
        result = [ticket_id, self._clock, self._clock, attrs]
        if self.on_get:
            self.on_get(ticket_id)
        return result

    def get_ticket_changelog(self, ticket_id):
        if self.hide_changelog:
            return []
        return list(self.changelogs[ticket_id])

    def update_ticket(
        self,
        ticket_id,
        comment="",
        attributes=None,
        notify=False,
        base_ts=None,
    ):
        from trac_mcp_server.core.client import TicketUpdateConflict

        self.updates.append(
            {
                "ticket_id": ticket_id,
                "comment": comment,
                "attributes": dict(attributes or {}),
                "base_ts": base_ts,
            }
        )
        ticket = self.tickets[ticket_id]
        if base_ts is not None and str(base_ts) != ticket["_ts"]:
            raise TicketUpdateConflict(
                "stale",
                ticket_id,
                base_ts,
                [
                    {
                        "field": "keywords",
                        "author": "bob",
                        "timestamp": "t",
                    }
                ],
            )
        for key, value in (attributes or {}).items():
            ticket[key] = value
        changed = self._tick()
        if comment:
            if self.fold_cr:
                comment = comment.replace("\r\n", "\n").replace(
                    "\r", "\n"
                )
            numbers = [
                int(r[3])
                for r in self.changelogs[ticket_id]
                if r[2] == "comment"
            ]
            self.changelogs[ticket_id].append(
                [
                    # Trac's compact XML-RPC time, as the real changelog
                    # reports it.
                    time.strftime(
                        "%Y%m%dT%H:%M:%S", time.gmtime(changed)
                    ),
                    "agent",
                    "comment",
                    str(max(numbers, default=0) + 1),
                    comment,
                    1,
                ]
            )
        # Trac's _ts counts microseconds; the reply is the real
        # [id, created, changed, attributes] shape.
        ticket["_ts"] = str(changed * 1_000_000)
        stamp = time.strftime("%Y%m%dT%H:%M:%S", time.gmtime(changed))
        return [ticket_id, stamp, stamp, {"_ts": ticket["_ts"]}]

    def edit_ticket_comment(self, ticket_id, cnum, comment):
        """The plugin's editComment: no size limit, folds CR/CRLF like
        the transport does when ``fold_cr`` is on, and moves ``_ts``."""
        self.comment_edits.append(
            {"ticket_id": ticket_id, "cnum": cnum, "comment": comment}
        )
        if self.fold_cr:
            comment = comment.replace("\r\n", "\n").replace("\r", "\n")
        for row in self.changelogs[ticket_id]:
            if row[2] == "comment" and str(row[3]) == str(cnum):
                row[4] = comment
        self.tickets[ticket_id]["_ts"] = str(self._tick() * 1_000_000)
        return None

    def get_ticket_comment_history(self, ticket_id, cnum):
        return [
            [1, "t", "alice", row[4]]
            for row in self.changelogs[ticket_id]
            if row[2] == "comment" and str(row[3]) == str(cnum)
        ]

    def create_ticket(
        self,
        summary,
        description,
        ticket_type=None,
        attributes=None,
        notify=False,
    ):
        new_id = max(self.tickets, default=0) + 1
        self.creates.append(
            {
                "summary": summary,
                "description": description,
                "ticket_type": ticket_type,
                "attributes": dict(attributes or {}),
            }
        )
        self.add_ticket(new_id, description)
        self.tickets[new_id]["type"] = ticket_type
        return new_id


def _body_file(tmp_path, body=_HARD_BODY, name="body.wiki"):
    f = tmp_path / name
    f.write_bytes(body.encode("utf-8"))
    return f


def _printed_comment_number(out: str) -> int:
    import re

    m = re.search(r"Added comment (\d+) to ticket #\d+", out)
    assert m, out
    return int(m.group(1))


def _comments_of(trac, ticket_id):
    return [r for r in trac.changelogs[ticket_id] if r[2] == "comment"]


class TestTicketBodies:
    def test_comment_roundtrip_is_byte_exact(self, tmp_path, capsys):
        src = _body_file(tmp_path)
        trac = FakeTracTickets()
        trac.add_ticket(7)
        with _served(trac) as url:
            assert _cli(url, "ticket-comment", "7", str(src)) == 0
            n = _printed_comment_number(capsys.readouterr().out)
            out = tmp_path / "back.wiki"
            rc = _cli(
                url, "ticket-pull", "7", str(out), "--comment", str(n)
            )
            assert rc == cli.EXIT_OK
        assert out.read_bytes() == src.read_bytes()
        assert trac.updates[0]["comment"] == _HARD_BODY

    def test_describe_roundtrip_is_byte_exact(self, tmp_path):
        src = _body_file(tmp_path)
        trac = FakeTracTickets()
        trac.add_ticket(7)
        with _served(trac) as url:
            assert _cli(url, "ticket-describe", "7", str(src)) == 0
            out = tmp_path / "back.wiki"
            assert _cli(url, "ticket-pull", "7", str(out)) == 0
        assert out.read_bytes() == src.read_bytes()
        assert trac.updates[0]["attributes"] == {
            "description": _HARD_BODY
        }

    def test_create_roundtrip_is_byte_exact(self, tmp_path, capsys):
        src = _body_file(tmp_path)
        trac = FakeTracTickets()
        with _served(trac) as url:
            rc = _cli(
                url,
                "ticket-create",
                "--summary",
                "S",
                "--description-file",
                str(src),
            )
            assert rc == cli.EXIT_OK
            assert "Created ticket #1" in capsys.readouterr().out
            out = tmp_path / "back.wiki"
            assert _cli(url, "ticket-pull", "1", str(out)) == 0
        assert out.read_bytes() == src.read_bytes()

    def test_create_names_the_argument_ticket_type_on_the_wire(
        self, tmp_path, monkeypatch
    ):
        # The server aliases a bare `type` to `ticket_type` today (#97), so
        # the real-substrate test below cannot tell the two apart. The
        # contract is the canonical name; pin what the CLI itself sends.
        sent = []

        async def _record(url, token, name, arguments):
            sent.append((name, arguments))
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(text="Created ticket #9: S")],
                structuredContent=None,
            )

        monkeypatch.setattr(cli, "call_tool", _record)
        src = _body_file(tmp_path)
        rc = cli.main(
            [
                "--url",
                "http://x/mcp",
                "ticket-create",
                "--summary",
                "S",
                "--description-file",
                str(src),
                "--type",
                "task",
            ],
            cwd=tmp_path,
            environ={"TRAC_MCP_AUTH_TOKEN": "t"},
        )
        assert rc == cli.EXIT_OK
        ((name, arguments),) = sent
        assert name == "ticket_create"
        assert arguments["ticket_type"] == "task"
        assert "type" not in arguments

    def test_create_sends_ticket_type_not_type(self, tmp_path):
        # "enhancement", not the default "defect": a dropped argument must
        # not look the same as a delivered one.
        src = _body_file(tmp_path)
        trac = FakeTracTickets()
        with _served(trac) as url:
            rc = _cli(
                url,
                "ticket-create",
                "--summary",
                "S",
                "--description-file",
                str(src),
                "--type",
                "enhancement",
                "--component",
                "cli",
                "--keywords",
                "k",
                "--owner",
                "o",
                "--priority",
                "high",
                "--milestone",
                "m1",
            )
        assert rc == cli.EXIT_OK
        sent = trac.creates[0]
        assert sent["ticket_type"] == "enhancement"
        assert sent["attributes"] == {
            "component": "cli",
            "keywords": "k",
            "owner": "o",
            "priority": "high",
            "milestone": "m1",
        }

    def test_comment_number_is_read_not_counted(self, tmp_path, capsys):
        # Comments 1, 2, 5: a gap, as after a deleted comment. Counting
        # would say 4; the stored number is 6.
        trac = FakeTracTickets()
        trac.add_ticket(7, comments=[(1, "a"), (2, "b"), (5, "c")])
        src = _body_file(tmp_path, "new comment\n")
        with _served(trac) as url:
            assert _cli(url, "ticket-comment", "7", str(src)) == 0
        assert _printed_comment_number(capsys.readouterr().out) == 6

    def test_reply_to_sends_reply_to_and_number_is_right(
        self, tmp_path, capsys
    ):
        trac = FakeTracTickets()
        trac.add_ticket(7, comments=[(1, "first"), (5, "fifth")])
        src = _body_file(tmp_path, "my reply\n")
        with _served(trac) as url:
            rc = _cli(
                url, "ticket-comment", "7", str(src), "--reply-to", "5"
            )
            assert rc == cli.EXIT_OK
            n = _printed_comment_number(capsys.readouterr().out)
            out = tmp_path / "back.wiki"
            rc = _cli(
                url, "ticket-pull", "7", str(out), "--comment", str(n)
            )
            assert rc == cli.EXIT_OK
        assert n == 6
        stored = trac.updates[0]["comment"]
        assert stored.startswith(
            "Replying to [comment:5 alice]:\n> fifth"
        )
        assert out.read_bytes().decode().endswith("my reply\n")

    def test_comment_locks_with_the_ts_it_read(self, tmp_path):
        trac = FakeTracTickets()
        trac.add_ticket(7)
        ts = trac.tickets[7]["_ts"]
        src = _body_file(tmp_path)
        with _served(trac) as url:
            assert _cli(url, "ticket-comment", "7", str(src)) == 0
        assert trac.updates[0]["base_ts"] == ts

    @pytest.mark.parametrize(
        "cmd", ["ticket-comment", "ticket-describe"]
    )
    def test_change_between_read_and_write_is_a_conflict(
        self, tmp_path, capsys, cmd
    ):
        trac = FakeTracTickets()
        trac.add_ticket(7, description="theirs")
        trac.on_get = (
            trac.touch
        )  # a concurrent writer, after our pre-read
        src = _body_file(tmp_path)
        with _served(trac) as url:
            rc = _cli(url, cmd, "7", str(src))
        assert rc == cli.EXIT_TOOL_ERROR
        err = capsys.readouterr().err
        assert "version_conflict" in err
        assert "modified since" in err
        assert trac.tickets[7]["description"] == "theirs"
        assert _comments_of(trac, 7) == []

    def test_stale_base_ts_is_a_conflict(self, tmp_path, capsys):
        trac = FakeTracTickets()
        trac.add_ticket(7)
        src = _body_file(tmp_path)
        with _served(trac) as url:
            rc = _cli(
                url, "ticket-comment", "7", str(src), "--base-ts", "1"
            )
        assert rc == cli.EXIT_TOOL_ERROR
        assert "version_conflict" in capsys.readouterr().err
        assert trac.get_calls == 0  # --base-ts skips the pre-read
        assert _comments_of(trac, 7) == []

    def test_correct_base_ts_succeeds_without_a_preread(self, tmp_path):
        trac = FakeTracTickets()
        trac.add_ticket(7)
        ts = trac.tickets[7]["_ts"]
        src = _body_file(tmp_path)
        with _served(trac) as url:
            rc = _cli(
                url, "ticket-describe", "7", str(src), "--base-ts", ts
            )
        assert rc == cli.EXIT_OK
        assert trac.get_calls == 0
        assert trac.updates[0]["base_ts"] == ts
        assert trac.tickets[7]["description"] == _HARD_BODY

    def test_missing_change_token_refuses_to_write_unlocked(
        self, tmp_path, capsys
    ):
        trac = FakeTracTickets()
        trac.add_ticket(7)
        trac.omit_ts = True
        src = _body_file(tmp_path)
        with _served(trac) as url:
            rc = _cli(url, "ticket-comment", "7", str(src))
        assert rc == cli.EXIT_TOOL_ERROR
        assert "_ts" in capsys.readouterr().err
        assert trac.updates == []

    def test_failed_preread_is_reported(self, tmp_path, capsys):
        trac = FakeTracTickets()  # no ticket 9
        src = _body_file(tmp_path)
        with _served(trac) as url:
            rc = _cli(url, "ticket-comment", "9", str(src))
        assert rc == cli.EXIT_TOOL_ERROR
        assert trac.updates == []

    def test_undeterminable_comment_number_exits_4_not_1(
        self, tmp_path, capsys
    ):
        trac = FakeTracTickets()
        trac.add_ticket(7)
        trac.hide_changelog = True
        src = _body_file(tmp_path)
        with _served(trac) as url:
            rc = _cli(url, "ticket-comment", "7", str(src))
        # 4, not 1: the comment IS stored, and a caller that retries on 1
        # (nothing stored) must not retry this.
        assert rc == cli.EXIT_STORED_INCOMPLETE
        assert rc != cli.EXIT_TOOL_ERROR
        captured = capsys.readouterr()
        assert "WAS stored" in captured.err
        assert "Updated ticket #7" in captured.out
        assert len(trac.updates) == 1

    def test_pull_of_a_missing_comment_exits_1(self, tmp_path, capsys):
        trac = FakeTracTickets()
        trac.add_ticket(7, comments=[(1, "a")])
        out = tmp_path / "out.wiki"
        with _served(trac) as url:
            rc = _cli(
                url, "ticket-pull", "7", str(out), "--comment", "99"
            )
        assert rc == cli.EXIT_TOOL_ERROR
        assert "ticket #7 has no comment 99" in capsys.readouterr().err
        assert not out.exists()

    def test_pull_to_stdout_keeps_stdout_for_the_data(
        self, tmp_path, capfd
    ):
        trac = FakeTracTickets()
        trac.add_ticket(7, description=_HARD_BODY)
        with _served(trac) as url:
            assert _cli(url, "ticket-pull", "7", "-") == 0
        captured = capfd.readouterr()
        assert captured.out == _HARD_BODY
        assert "Pulled" in captured.err

    @pytest.mark.parametrize(
        "argv",
        [
            ["ticket-comment", "7"],
            ["ticket-describe", "7"],
            ["ticket-create", "--summary", "S", "--description-file"],
        ],
    )
    @pytest.mark.parametrize("body", ["", "  \n\t\n"])
    def test_empty_body_is_a_usage_error_with_no_call(
        self, tmp_path, monkeypatch, capsys, argv, body
    ):
        _no_network(monkeypatch)
        src = _body_file(tmp_path, body)
        rc = cli.main(
            ["--url", "http://x/mcp", *argv, str(src)],
            cwd=tmp_path,
            environ={"TRAC_MCP_AUTH_TOKEN": "t"},
        )
        assert rc == cli.EXIT_USAGE_ERROR
        assert "empty" in capsys.readouterr().err

    def test_pull_to_missing_output_dir_fails_before_any_call(
        self, tmp_path, monkeypatch, capsys
    ):
        _no_network(monkeypatch)
        for extra in ([], ["--comment", "1"]):
            rc = cli.main(
                [
                    "--url",
                    "http://x/mcp",
                    "ticket-pull",
                    "7",
                    str(tmp_path / "missing" / "o.wiki"),
                    *extra,
                ],
                cwd=tmp_path,
                environ={"TRAC_MCP_AUTH_TOKEN": "t"},
            )
            assert rc == cli.EXIT_USAGE_ERROR
        assert (
            "output directory does not exist" in capsys.readouterr().err
        )

    def test_non_utf8_body_warns_like_wiki_push(self, tmp_path, capsys):
        trac = FakeTracTickets()
        trac.add_ticket(7)
        src = tmp_path / "latin.wiki"
        src.write_bytes(
            "caf\xe9 cr\xe8me, na\xefve fa\xe7ade\n".encode("latin-1")
        )
        with _served(trac) as url:
            rc = _cli(url, "ticket-comment", "7", str(src))
        assert rc == cli.EXIT_OK
        assert "not UTF-8" in capsys.readouterr().err

    def test_instance_reaches_every_call(self, tmp_path, monkeypatch):
        sent = []

        async def _record(url, token, name, arguments):
            sent.append((name, arguments))
            structured = {
                "ticket_get": {"_ts": "5"},
                "ticket_changelog": {
                    "changelog": [
                        {
                            "field": "comment",
                            "oldvalue": "3",
                            "newvalue": "hi\n",
                        }
                    ]
                },
            }.get(name, {})
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(text="ok")],
                structuredContent=structured,
            )

        monkeypatch.setattr(cli, "call_tool", _record)
        src = _body_file(tmp_path, "hi\n")
        rc = cli.main(
            [
                "--url",
                "http://x/mcp",
                "--instance",
                "/other",
                "ticket-comment",
                "7",
                str(src),
            ],
            cwd=tmp_path,
            environ={"TRAC_MCP_AUTH_TOKEN": "t"},
        )
        assert rc == cli.EXIT_OK
        assert [n for n, _ in sent] == [
            "ticket_get",
            "ticket_update",
            "ticket_changelog",
        ]
        assert all(a["instance"] == "/other" for _, a in sent)
        assert sent[0][1]["include_comments"] is False
        assert sent[1][1]["base_ts"] == "5"


class TestTicketCommentNumberFromServer:
    """The CLI prints the number ticket_update reports (Trac #124) and
    only reads the changelog back when the daemon is older."""

    @staticmethod
    def _recorder(monkeypatch, update_structured):
        sent = []

        async def _record(url, token, name, arguments):
            sent.append(name)
            structured = {
                "ticket_get": {"_ts": "5"},
                "ticket_update": update_structured,
                "ticket_changelog": {
                    "changelog": [
                        {
                            "field": "comment",
                            "oldvalue": "9",
                            "newvalue": "hi\n",
                        }
                    ]
                },
            }[name]
            return SimpleNamespace(
                isError=False,
                content=[SimpleNamespace(text="ok")],
                structuredContent=structured,
            )

        monkeypatch.setattr(cli, "call_tool", _record)
        return sent

    def _run(self, tmp_path):
        src = _body_file(tmp_path, "hi\n")
        return cli.main(
            ["--url", "http://x/mcp", "ticket-comment", "7", str(src)],
            cwd=tmp_path,
            environ={"TRAC_MCP_AUTH_TOKEN": "t"},
        )

    def test_server_number_means_no_changelog_call(
        self, tmp_path, monkeypatch, capsys
    ):
        sent = self._recorder(monkeypatch, {"comment_number": 3})
        assert self._run(tmp_path) == cli.EXIT_OK
        assert sent == ["ticket_get", "ticket_update"]
        assert "Added comment 3 to ticket #7" in capsys.readouterr().out

    def test_older_daemon_without_a_number_falls_back(
        self, tmp_path, monkeypatch, capsys
    ):
        sent = self._recorder(monkeypatch, {})
        assert self._run(tmp_path) == cli.EXIT_OK
        assert sent == [
            "ticket_get",
            "ticket_update",
            "ticket_changelog",
        ]
        assert "Added comment 9 to ticket #7" in capsys.readouterr().out

    def test_null_number_falls_back(self, tmp_path, monkeypatch):
        sent = self._recorder(monkeypatch, {"comment_number": None})
        assert self._run(tmp_path) == cli.EXIT_OK
        assert sent[-1] == "ticket_changelog"

    def test_crlf_body_gets_its_number_from_the_server(
        self, tmp_path, capsys
    ):
        # The changelog text match is an endswith on the sent text; a
        # CRLF body is stored with LF, so only the server's number works.
        trac = FakeTracTickets()
        trac.add_ticket(7, comments=[(1, "a")])
        trac.fold_cr = True
        src = tmp_path / "crlf.wiki"
        src.write_bytes(b"one\r\ntwo\r\n")
        with _served(trac) as url:
            assert _cli(url, "ticket-comment", "7", str(src)) == 0
        assert _printed_comment_number(capsys.readouterr().out) == 2


class TestTicketBodyReview:
    """Findings of the local review of Trac #114."""

    def test_changelog_transport_failure_still_reports_the_write(
        self, tmp_path, monkeypatch, capsys
    ):
        real = cli.call_tool

        async def _flaky(url, token, name, arguments):
            if name == "ticket_changelog":
                raise ConnectionError("changelog read-back dropped")
            return await real(url, token, name, arguments)

        trac = FakeTracTickets()
        trac.add_ticket(7)
        # The server cannot name the comment either, so the CLI falls
        # back to its own read-back -- which is what drops here.
        trac.hide_changelog = True
        src = _body_file(tmp_path)
        with _served(trac) as url:
            monkeypatch.setattr(cli, "call_tool", _flaky)
            rc = _cli(url, "ticket-comment", "7", str(src))
        captured = capsys.readouterr()
        assert rc == cli.EXIT_STORED_INCOMPLETE
        assert "Updated ticket #7" in captured.out
        assert "WAS stored" in captured.err
        assert len(trac.updates) == 1

    def test_stdin_keeps_crlf_like_a_file_does(
        self, tmp_path, monkeypatch, capsys
    ):
        import io

        body = b"line one\r\nline two\r\nlone cr\rend\r\n"
        monkeypatch.setattr(
            sys,
            "stdin",
            io.TextIOWrapper(io.BytesIO(body), encoding="utf-8"),
        )
        trac = FakeTracTickets()
        trac.add_ticket(7)
        with _served(trac) as url:
            assert _cli(url, "ticket-describe", "7", "-") == 0
            out = tmp_path / "back.wiki"
            assert _cli(url, "ticket-pull", "7", str(out)) == 0
        assert out.read_bytes() == body

    def test_oversize_comment_is_refused_before_any_call(
        self, tmp_path, monkeypatch, capsys
    ):
        from trac_mcp_server.core.client import MAX_COMMENT_CHARS

        _no_network(monkeypatch)
        src = _body_file(tmp_path, "x" * (MAX_COMMENT_CHARS + 1))
        rc = cli.main(
            ["--url", "http://x/mcp", "ticket-comment", "7", str(src)],
            cwd=tmp_path,
            environ={"TRAC_MCP_AUTH_TOKEN": "t"},
        )
        assert rc == cli.EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert str(MAX_COMMENT_CHARS) in err
        assert "ticket-describe" in err

    def test_comment_at_the_limit_is_sent(self, tmp_path):
        from trac_mcp_server.core.client import MAX_COMMENT_CHARS

        trac = FakeTracTickets()
        trac.add_ticket(7)
        src = _body_file(tmp_path, "y" * MAX_COMMENT_CHARS)
        with _served(trac) as url:
            assert _cli(url, "ticket-comment", "7", str(src)) == 0
        assert len(trac.updates[0]["comment"]) == MAX_COMMENT_CHARS

    def test_description_has_no_comment_size_limit(self, tmp_path):
        from trac_mcp_server.core.client import MAX_COMMENT_CHARS

        trac = FakeTracTickets()
        trac.add_ticket(7)
        src = _body_file(tmp_path, "z" * (MAX_COMMENT_CHARS * 2))
        with _served(trac) as url:
            assert _cli(url, "ticket-describe", "7", str(src)) == 0

    def test_comment_number_is_the_newest_match(self):
        # The changelog is chronological, so the last match is ours.
        changelog = [
            {"field": "comment", "oldvalue": "1", "newvalue": "ok"},
            {"field": "comment", "oldvalue": "3", "newvalue": "ok"},
            {"field": "status", "oldvalue": "new", "newvalue": "x"},
        ]
        assert cli._comment_number(changelog, "ok") == "3"
        assert cli._comment_number(changelog, "absent") is None

    def test_comment_number_of_a_threaded_row_is_its_own_number(self):
        # Trac /trac_mcp_server #131: "1.10" is comment 10, a reply to
        # 1 -- the last dotted part, as an integer. The shared parser
        # decides, so "1.2" (comment 2) is older than "1.10" here only
        # by position, never by comparing the text.
        changelog = [
            {"field": "comment", "oldvalue": "1.2", "newvalue": "ok"},
            {"field": "comment", "oldvalue": "1.10", "newvalue": "ok"},
        ]
        assert cli._comment_number(changelog, "ok") == "10"
        assert cli._comment_number(changelog[:1], "ok") == "2"

    def test_comment_number_flat_row(self):
        changelog = [
            {"field": "comment", "oldvalue": "5", "newvalue": "hi"}
        ]
        assert cli._comment_number(changelog, "hi") == "5"

    def test_comment_number_skips_rows_without_a_parseable_number(self):
        changelog = [
            {"field": "attachment", "oldvalue": None, "newvalue": "ok"},
            {"field": "comment", "oldvalue": None, "newvalue": "ok"},
            {"field": "comment", "oldvalue": "abc", "newvalue": "ok"},
            {
                "field": "comment",
                "oldvalue": "\u00b2",
                "newvalue": "ok",
            },
        ]
        assert cli._comment_number(changelog, "ok") is None
        # An unparseable newest row does not hide an older good one.
        changelog.insert(
            0, {"field": "comment", "oldvalue": "4", "newvalue": "ok"}
        )
        assert cli._comment_number(changelog, "ok") == "4"

    def test_pull_finds_a_threaded_comment_by_its_own_number(
        self, tmp_path
    ):
        trac = FakeTracTickets()
        trac.add_ticket(
            7,
            comments=[
                (1, "one"),
                ("1.2", "threaded two"),
                (3, "three"),
            ],
        )
        out = tmp_path / "out.wiki"
        with _served(trac) as url:
            rc = _cli(
                url, "ticket-pull", "7", str(out), "--comment", "2"
            )
        assert rc == cli.EXIT_OK
        assert out.read_text() == "threaded two"

    def test_pull_skips_attachment_and_non_numeric_rows(self, tmp_path):
        trac = FakeTracTickets()
        trac.add_ticket(7, comments=[("abc", "junk"), (2, "two")])
        trac.changelogs[7].append(
            [trac._tick(), "alice", "attachment", None, "f.txt", 1]
        )
        out = tmp_path / "out.wiki"
        with _served(trac) as url:
            rc = _cli(
                url, "ticket-pull", "7", str(out), "--comment", "2"
            )
        assert rc == cli.EXIT_OK
        assert out.read_text() == "two"


class TestTicketCommentEdit:
    """``ticket-comment-edit`` (Trac /trac_mcp_server #220): replace comment
    N from a file, locked by base_ts, optional --expect-file, byte-for-byte
    read-back."""

    @staticmethod
    def _row(cnum, body):
        return {
            "field": "comment",
            "oldvalue": str(cnum),
            "newvalue": body,
        }

    def _stub(self, monkeypatch, changelogs=(), edit_error=False):
        """Scripted ``call_tool``. ``changelogs`` answers each
        ``ticket_changelog`` call in turn: a list of rows, the string
        ``"error"`` for an error result, or an exception to raise."""
        sent = []
        queue = list(changelogs)

        async def _record(url, token, name, arguments):
            sent.append((name, arguments))
            structured = {}
            text = "ok"
            is_error = False
            if name == "ticket_get":
                structured = {"_ts": "5"}
            elif name == "ticket_comment_edit":
                text = "Edited comment 3 on ticket #7.\nNOTE: gate line"
                is_error = edit_error
                if edit_error:
                    text = "version_conflict: stale"
            elif name == "ticket_changelog":
                item = queue.pop(0)
                if isinstance(item, BaseException):
                    raise item
                if item == "error":
                    is_error = True
                    text = "changelog failed"
                else:
                    structured = {"changelog": item}
            return SimpleNamespace(
                isError=is_error,
                content=[SimpleNamespace(text=text)],
                structuredContent=structured,
            )

        monkeypatch.setattr(cli, "call_tool", _record)
        return sent

    @staticmethod
    def _run(tmp_path, *extra, body="new text\n", instance=None):
        src = _body_file(tmp_path, body)
        head = ["--url", "http://x/mcp"]
        if instance:
            head += ["--instance", instance]
        return cli.main(
            [*head, "ticket-comment-edit", "7", "3", str(src), *extra],
            cwd=tmp_path,
            environ={"TRAC_MCP_AUTH_TOKEN": "t"},
        )

    @staticmethod
    def _names(sent):
        return [n for n, _ in sent]

    def test_parser_accepts_the_arguments(self):
        args = cli.build_parser().parse_args(
            [
                "ticket-comment-edit",
                "7",
                "3",
                "f.wiki",
                "--base-ts",
                "9",
                "--expect-file",
                "old.wiki",
            ]
        )
        assert (args.ticket, args.cnum, args.file) == (7, 3, "f.wiki")
        assert args.base_ts == "9"
        assert args.expect_file == "old.wiki"
        bare = cli.build_parser().parse_args(
            ["ticket-comment-edit", "7", "3", "-"]
        )
        assert bare.base_ts is None and bare.expect_file is None

    def test_base_ts_comes_from_ticket_get_and_is_sent(
        self, tmp_path, monkeypatch
    ):
        sent = self._stub(monkeypatch, [[self._row(3, "new text\n")]])
        assert self._run(tmp_path) == cli.EXIT_OK
        assert self._names(sent) == [
            "ticket_get",
            "ticket_comment_edit",
            "ticket_changelog",
        ]
        assert sent[0][1]["include_comments"] is False
        assert sent[1][1] == {
            "ticket_id": 7,
            "cnum": 3,
            "comment": "new text\n",
            "base_ts": "5",
        }

    def test_base_ts_flag_skips_the_read(self, tmp_path, monkeypatch):
        sent = self._stub(monkeypatch, [[self._row(3, "new text\n")]])
        assert self._run(tmp_path, "--base-ts", "42") == cli.EXIT_OK
        assert self._names(sent) == [
            "ticket_comment_edit",
            "ticket_changelog",
        ]
        assert sent[0][1]["base_ts"] == "42"

    def test_instance_reaches_every_call(self, tmp_path, monkeypatch):
        sent = self._stub(
            monkeypatch,
            [[self._row(3, "old\n")], [self._row(3, "new text\n")]],
        )
        old = _body_file(tmp_path, "old\n", "old.wiki")
        rc = self._run(
            tmp_path,
            "--expect-file",
            str(old),
            instance="/other",
        )
        assert rc == cli.EXIT_OK
        assert len(sent) == 4
        assert all(a["instance"] == "/other" for _, a in sent)

    def test_body_over_the_comment_limit_is_sent(
        self, tmp_path, monkeypatch
    ):
        from trac_mcp_server.core.client import MAX_COMMENT_CHARS

        big = "x" * (MAX_COMMENT_CHARS * 3 + 1)
        sent = self._stub(monkeypatch, [[self._row(3, big)]])
        assert self._run(tmp_path, body=big) == cli.EXIT_OK
        edit = [a for n, a in sent if n == "ticket_comment_edit"]
        assert edit[0]["comment"] == big

    def test_empty_file_is_a_usage_error_with_no_call(
        self, tmp_path, monkeypatch
    ):
        _no_network(monkeypatch)
        assert self._run(tmp_path, body="  \n") == cli.EXIT_USAGE_ERROR

    def test_expect_file_match_proceeds(self, tmp_path, monkeypatch):
        sent = self._stub(
            monkeypatch,
            [[self._row(3, "old\n")], [self._row(3, "new text\n")]],
        )
        old = _body_file(tmp_path, "old\n", "old.wiki")
        rc = self._run(tmp_path, "--expect-file", str(old))
        assert rc == cli.EXIT_OK
        # Lock read, expect read, write, read-back.
        assert self._names(sent) == [
            "ticket_get",
            "ticket_changelog",
            "ticket_comment_edit",
            "ticket_changelog",
        ]

    def test_expect_file_mismatch_refuses_before_any_write(
        self, tmp_path, monkeypatch, capsys
    ):
        stored = "café SECRETSTORED tail"
        expected = "café SECRETEXPECT tail"
        sent = self._stub(monkeypatch, [[self._row(3, stored)]])
        old = _body_file(tmp_path, expected, "old.wiki")
        rc = self._run(tmp_path, "--expect-file", str(old))
        assert rc == cli.EXIT_TOOL_ERROR
        assert "ticket_comment_edit" not in self._names(sent)
        err = capsys.readouterr().err
        # "café SECRET" is 12 bytes (the e-acute takes two); the
        # texts first differ at the letter after it.
        assert "byte offset 12" in err
        assert str(len(stored.encode())) in err
        assert str(len(expected.encode())) in err
        assert "nothing was edited" in err
        assert "SECRETSTORED" not in err and "SECRETEXPECT" not in err

    def test_expect_file_crlf_matches_a_stored_lf_body(
        self, tmp_path, monkeypatch
    ):
        # A CRLF file this command pushed is stored as LF; the same file
        # must then work as the next --expect-file.
        sent = self._stub(
            monkeypatch,
            [[self._row(3, "a\nb\n")], [self._row(3, "new text\n")]],
        )
        old = _body_file(tmp_path, "a\r\nb\r\n", "old.wiki")
        rc = self._run(tmp_path, "--expect-file", str(old))
        assert rc == cli.EXIT_OK
        assert "ticket_comment_edit" in self._names(sent)

    def test_expect_file_mismatch_offset_is_on_the_folded_texts(
        self, tmp_path, monkeypatch, capsys
    ):
        # Unfolded the texts first differ at byte 2 (LF vs CR); folded,
        # at byte 3 (X vs Y).
        sent = self._stub(monkeypatch, [[self._row(3, "ab\nX")]])
        old = _body_file(tmp_path, "ab\r\nY", "old.wiki")
        rc = self._run(tmp_path, "--expect-file", str(old))
        assert rc == cli.EXIT_TOOL_ERROR
        assert "ticket_comment_edit" not in self._names(sent)
        err = capsys.readouterr().err
        assert "byte offset 3" in err
        assert "stored 4 bytes, expected 4 bytes" in err

    def test_whitespace_expect_file_matches_a_whitespace_stored_comment(
        self, tmp_path, monkeypatch
    ):
        sent = self._stub(
            monkeypatch,
            [[self._row(3, "  \n")], [self._row(3, "new text\n")]],
        )
        old = _body_file(tmp_path, "  \n", "old.wiki")
        rc = self._run(tmp_path, "--expect-file", str(old))
        assert rc == cli.EXIT_OK
        assert "ticket_comment_edit" in self._names(sent)

    def test_empty_expect_file_proceeds_on_an_empty_stored_comment(
        self, tmp_path, monkeypatch
    ):
        sent = self._stub(
            monkeypatch,
            [[self._row(3, "")], [self._row(3, "new text\n")]],
        )
        old = _body_file(tmp_path, "", "old.wiki")
        rc = self._run(tmp_path, "--expect-file", str(old))
        assert rc == cli.EXIT_OK
        assert "ticket_comment_edit" in self._names(sent)

    def test_stdin_cannot_feed_both_file_and_expect_file(
        self, tmp_path, monkeypatch, capsys
    ):
        _no_network(monkeypatch)

        class _Boom:
            @property
            def buffer(self):
                raise AssertionError("stdin must not be read")

            def read(self):
                raise AssertionError("stdin must not be read")

        monkeypatch.setattr(cli.sys, "stdin", _Boom())
        rc = cli.main(
            [
                "--url",
                "http://x/mcp",
                "ticket-comment-edit",
                "7",
                "3",
                "-",
                "--expect-file",
                "-",
            ],
            cwd=tmp_path,
            environ={"TRAC_MCP_AUTH_TOKEN": "t"},
        )
        assert rc == cli.EXIT_USAGE_ERROR
        assert (
            "standard input can feed only one"
            in capsys.readouterr().err
        )

    def test_expect_file_with_a_missing_comment_exits_1(
        self, tmp_path, monkeypatch, capsys
    ):
        sent = self._stub(monkeypatch, [[self._row(1, "other")]])
        old = _body_file(tmp_path, "old\n", "old.wiki")
        rc = self._run(tmp_path, "--expect-file", str(old))
        assert rc == cli.EXIT_TOOL_ERROR
        assert "ticket_comment_edit" not in self._names(sent)
        assert "has no comment 3" in capsys.readouterr().err

    def test_readback_equal_prints_the_match_line(
        self, tmp_path, monkeypatch, capsys
    ):
        self._stub(monkeypatch, [[self._row(3, "new text\n")]])
        assert self._run(tmp_path) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "Edited comment 3 on ticket #7." in out
        assert "NOTE: gate line" in out
        assert (
            "Stored comment 3 matches the text sent, byte-for-byte"
            in out
        )

    def test_readback_equal_after_folding_crlf_exits_0(
        self, tmp_path, monkeypatch, capsys
    ):
        self._stub(monkeypatch, [[self._row(3, "a\nb\nc\nd")]])
        assert self._run(tmp_path, body="a\r\nb\rc\nd") == cli.EXIT_OK
        out = capsys.readouterr().out
        assert (
            "matches the text sent with line endings folded to LF"
            in out
        )
        assert "byte-for-byte" not in out

    def test_readback_different_exits_4_and_names_the_offset(
        self, tmp_path, monkeypatch, capsys
    ):
        self._stub(monkeypatch, [[self._row(3, "new TEXT\n")]])
        rc = self._run(tmp_path)
        assert rc == cli.EXIT_STORED_INCOMPLETE
        captured = capsys.readouterr()
        assert "WAS edited" in captured.err
        assert "byte offset 4" in captured.err
        assert "new text" not in captured.err
        assert "new TEXT" not in captured.err
        assert "Edited comment 3 on ticket #7." in captured.out

    @pytest.mark.parametrize(
        "failure",
        [
            ConnectionError("dropped"),
            "error",
            [],  # the comment is not in the changelog
        ],
    )
    def test_readback_failure_exits_4(
        self, tmp_path, monkeypatch, capsys, failure
    ):
        self._stub(monkeypatch, [failure])
        rc = self._run(tmp_path)
        assert rc == cli.EXIT_STORED_INCOMPLETE
        captured = capsys.readouterr()
        assert "WAS edited" in captured.err
        assert "could not verify" in captured.err
        assert "Edited comment 3 on ticket #7." in captured.out

    def test_tool_error_exits_1_with_no_readback(
        self, tmp_path, monkeypatch, capsys
    ):
        sent = self._stub(monkeypatch, [], edit_error=True)
        rc = self._run(tmp_path)
        assert rc == cli.EXIT_TOOL_ERROR
        assert "version_conflict" in capsys.readouterr().err
        assert "ticket_changelog" not in self._names(sent)

    def test_failed_lock_read_exits_1_before_the_write(
        self, tmp_path, capsys
    ):
        trac = FakeTracTickets()  # no ticket 9
        src = _body_file(tmp_path)
        with _served(trac) as url:
            rc = _cli(url, "ticket-comment-edit", "9", "1", str(src))
        assert rc == cli.EXIT_TOOL_ERROR
        assert trac.comment_edits == []

    def test_real_server_edit_roundtrips_a_long_comment(
        self, tmp_path, capsys
    ):
        from trac_mcp_server.core.client import MAX_COMMENT_CHARS

        long_body = _HARD_BODY * 400
        assert len(long_body) > MAX_COMMENT_CHARS * 3
        trac = FakeTracTickets()
        trac.add_ticket(7, comments=[(1, "first"), (2, "original\n")])
        src = _body_file(tmp_path, long_body)
        with _served(trac) as url:
            rc = _cli(url, "ticket-comment-edit", "7", "2", str(src))
            assert rc == cli.EXIT_OK
            out = tmp_path / "back.wiki"
            assert (
                _cli(
                    url, "ticket-pull", "7", str(out), "--comment", "2"
                )
                == cli.EXIT_OK
            )
        assert out.read_bytes() == src.read_bytes()
        assert trac.comment_edits[0]["comment"] == long_body
        assert (
            "Stored comment 2 matches the text sent, byte-for-byte"
            in capsys.readouterr().out
        )

    def test_real_server_stale_base_ts_is_a_conflict(
        self, tmp_path, capsys
    ):
        trac = FakeTracTickets()
        trac.add_ticket(7, comments=[(1, "orig")])
        src = _body_file(tmp_path)
        with _served(trac) as url:
            rc = _cli(
                url,
                "ticket-comment-edit",
                "7",
                "1",
                str(src),
                "--base-ts",
                "1",
            )
        assert rc == cli.EXIT_TOOL_ERROR
        assert "version_conflict" in capsys.readouterr().err
        assert trac.comment_edits == []

    def test_real_server_crlf_edit_reports_the_fold(
        self, tmp_path, capsys
    ):
        trac = FakeTracTickets()
        trac.add_ticket(7, comments=[(1, "orig")])
        trac.fold_cr = True
        src = tmp_path / "crlf.wiki"
        src.write_bytes(b"one\r\ntwo\r\n")
        with _served(trac) as url:
            rc = _cli(url, "ticket-comment-edit", "7", "1", str(src))
        assert rc == cli.EXIT_OK
        assert "folded to LF" in capsys.readouterr().out

    def test_comment_body_helper_takes_the_newest_parseable_row(self):
        rows = [
            self._row(2, "old"),
            {"field": "status", "oldvalue": "a", "newvalue": "b"},
            self._row("1.2", "threaded"),
            {"field": "comment", "oldvalue": "abc", "newvalue": "junk"},
            self._row(2, "newest"),
            {"field": "comment", "oldvalue": "5", "newvalue": None},
        ]
        assert cli._comment_body(rows, 2) == "newest"
        assert cli._comment_body(rows, 5) == ""
        assert cli._comment_body(rows, 9) is None
