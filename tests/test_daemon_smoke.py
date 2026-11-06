"""Offline tests for ``ci-live.sh --daemon`` and scripts/daemon_smoke.py
(Trac /trac_mcp_server #161).

The smoke script exists to run against a deployed daemon, which no offline
test can reach. What these tests pin is everything that does not need one:

* the shell dispatch (no args keeps the old path, ``--daemon`` skips the
  probe and pytest, a bad argument is usage + exit 2), by running the real
  ``ci-live.sh`` by subprocess like ``test_ci_live_probe.py``;
* the three-state exit contract -- not run (2) before any network call,
  failed (1) for an unreachable daemon, the token never printed;
* the pure helpers (URL normalisation, the tool comparison, scrubbing);
* all six checks end to end, over the SDK's real streamable-HTTP client and
  a real uvicorn server (``Rules/testing/RealSubstrateNotMocks``), against
  a stand-in server that advertises this checkout's own tool definitions,
  validates every call against their real input schemas, and answers with
  the shapes the real handlers produce.
"""

import asyncio
import http.server
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import anyio
import pytest
from mcp import types
from mcp.server import Server

from tests.test_mcp.test_http_identity import _free_port, _running_app
from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.mcp.server import PING_SPEC
from trac_mcp_server.mcp.tools import ALL_SPECS, ToolRegistry
from trac_mcp_server.mcp.tools.instance_echo import annotate
from trac_mcp_server.mcp.tools.registry import (
    with_instance_param,
    with_page_alias,
    with_strict_schema,
)

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ci-live.sh"
SMOKE = REPO / "scripts" / "daemon_smoke.py"

TOKEN = "smoke-secret-token-7f3a9c1e"
DEFAULT_ENV = "TRAC_MCP_AUTH_TOKEN_STAGE"


def _load_smoke():
    spec = importlib.util.spec_from_file_location(
        "daemon_smoke_under_test", SMOKE
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    [
        "http://192.168.10.4:8092",
        "http://192.168.10.4:8092/",
        "http://192.168.10.4:8092/mcp",
        "http://192.168.10.4:8092/mcp/",
        "  http://192.168.10.4:8092/mcp  ",
    ],
)
def test_normalise_base_strips_mcp_and_slash(given):
    assert smoke.normalise_base(given) == "http://192.168.10.4:8092"


def test_normalise_base_keeps_scheme_and_a_path_prefix():
    assert (
        smoke.normalise_base("https://h.example/proxy/mcp")
        == "https://h.example/proxy"
    )
    assert (
        smoke.normalise_base("https://h.example") == "https://h.example"
    )


@pytest.mark.parametrize(
    "given",
    [
        "",
        "not a url",
        "192.168.10.4:8092",
        "ftp://h:8092",
        "http://",
        "http://h:notaport",
        "http://u:p@h:8092",
        "http://h:8092/?x=1",
        "http://h:8092/#frag",
    ],
)
def test_normalise_base_rejects_what_is_not_a_daemon_url(given):
    with pytest.raises(ValueError):
        smoke.normalise_base(given)


def test_normalise_base_error_never_echoes_the_url():
    with pytest.raises(ValueError) as caught:
        smoke.normalise_base("http://user:hunter2@h:8092")
    assert "hunter2" not in str(caught.value)


def test_compare_tools_reports_missing_and_extra_sorted():
    missing, extra = smoke.compare_tools(
        ["a", "b", "c", "d"], ["d", "z", "b", "y"]
    )
    assert missing == ["a", "c"]
    assert extra == ["y", "z"]


def test_compare_tools_equal_sets_report_nothing():
    assert smoke.compare_tools(["a", "b"], ["b", "a"]) == ([], [])


def test_compare_tools_is_not_symmetric_about_what_fails():
    # extra tools are reported; only a MISSING one fails check 3
    missing, extra = smoke.compare_tools(["a"], ["a", "b"])
    assert missing == [] and extra == ["b"]


def test_checkout_tool_names_come_from_the_servers_own_registry():
    registry = ToolRegistry(
        with_strict_schema(with_page_alias([PING_SPEC, *ALL_SPECS]))
    )
    registered = {tool.name for tool in registry.list_tools()}
    assert smoke.checkout_tool_names() == registered
    assert {"ping", "wiki_get", "ticket_comment_edit"} <= registered


def test_scrub_removes_the_token_and_any_bearer_value():
    text = f"boom {TOKEN} and Authorization: Bearer other-secret!"
    out = smoke.scrub(text, TOKEN)
    assert TOKEN not in out
    assert "other-secret" not in out


class _Group(Exception):
    """Stands in for an ExceptionGroup (3.10 has none)."""

    def __init__(self, *exceptions):
        super().__init__("group")
        self.exceptions = exceptions


def test_describe_exception_unwraps_groups_and_scrubs():
    group = _Group(
        _Group(ConnectionRefusedError(f"refused for {TOKEN}")),
        TimeoutError(),
    )
    line = smoke.describe_exception(group, TOKEN)
    assert "ConnectionRefusedError" in line
    assert "TimeoutError" in line
    assert TOKEN not in line
    assert "\n" not in line


def test_nonce_is_fresh_and_page_body_carries_it():
    first, second = smoke.make_nonce(), smoke.make_nonce()
    assert first != second
    body = smoke.smoke_page_body(first)
    assert first in body
    assert body == smoke.smoke_page_body(first)
    assert ":" not in first


def test_first_difference_names_the_offset():
    assert "offset 3" in smoke.first_difference("abcdef", "abcXef")
    assert "offset 2" in smoke.first_difference("ab", "abcd")


# ---------------------------------------------------------------------------
# A stand-in for the daemon's Trac, answering with the real handlers' shapes
# ---------------------------------------------------------------------------


def _text(text, structured=None, error=False):
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
        isError=error,
    )


def _err(kind, message):
    return _text(
        f"Error ({kind}): {message}\n\nAction: retry", error=True
    )


class FakeTrac:
    """In-memory wiki and tickets behind the daemon's tool names.

    Result shapes follow the real handlers (wiki_read, wiki_write,
    ticket_read, ticket_write, ticket_comment). The switches seed the
    defects the smoke exists to catch.
    """

    def __init__(self):
        self.pages = {
            "WikiStart": {"content": "= Welcome =\n", "version": 3}
        }
        self.tickets = {}
        self.plugin = True
        self.mangle_pages = False
        self.hide_tools: set[str] = set()
        self.wikistart_error = False
        self.drop_comment_number = False
        self.edit_creates_revision = True
        self.calls: list[str] = []
        # (tool, instance argument as received) for every call
        self.instances: list[tuple[str, str | None]] = []
        self.answer_as: str | None = None  # answer from elsewhere
        self.echo_instance = True
        self._ts = 1000

    # -- tools/list -------------------------------------------------------
    def tools(self):
        # The pipeline server.main applies: page alias, the `instance`
        # argument, then strict schemas -- so a call is validated the way
        # the daemon validates it.
        specs = with_instance_param(
            with_page_alias([PING_SPEC, *ALL_SPECS]), []
        )
        registry = ToolRegistry(with_strict_schema(specs))
        return [
            t
            for t in registry.list_tools()
            if t.name not in self.hide_tools
        ]

    # -- dispatch ---------------------------------------------------------
    async def call(self, name, args):
        self.calls.append(name)
        instance = args.get("instance")
        self.instances.append((name, instance))
        result = getattr(self, f"_{name}")(args)
        if self.echo_instance:
            # the real echo (tools/instance_echo.py), as the daemon adds it
            where = self.answer_as or instance or "/auto_pm"
            annotate(
                result,
                f"http://trac.invalid{where}",
                explicit=instance is not None,
            )
        return result

    def _bump(self):
        self._ts += 7
        return str(self._ts)

    def _wiki_get(self, args):
        page = self.pages.get(args["page_name"])
        if page is None or (
            self.wikistart_error and args["page_name"] == "WikiStart"
        ):
            return _err("not_found", "no such page")
        data = {
            "name": args["page_name"],
            "content": page["content"],
            "version": page["version"],
            "author": "agent_rpc",
            "lastModified": "2026-11-06 10:00",
        }
        return _text(page["content"], data)

    def _store(self, content):
        return content[:-1] if self.mangle_pages else content

    def _wiki_create(self, args):
        if args["page_name"] in self.pages:
            return _err("already_exists", "exists")
        self.pages[args["page_name"]] = {
            "content": self._store(args["content"]),
            "version": 1,
        }
        return _text(
            f"Created wiki page '{args['page_name']}' (version 1)"
        )

    def _wiki_update(self, args):
        page = self.pages.get(args["page_name"])
        if page is None or page["version"] != args["version"]:
            return _err("version_conflict", "stale")
        page["content"] = self._store(args["content"])
        page["version"] += 1
        return _text(f"Updated wiki page to version {page['version']}")

    def _ticket_search(self, args):
        query = args.get("query", "status!=closed")
        wanted = None
        for clause in query.split("&"):
            if clause.startswith("summary~="):
                wanted = clause[len("summary~=") :]
        rows = [
            {
                "id": tid,
                "summary": t["summary"],
                "status": t["status"],
                "owner": t["owner"],
                "url": f"http://trac/ticket/{tid}",
            }
            for tid, t in self.tickets.items()
            if t["status"] != "closed"
            and (wanted is None or wanted in t["summary"])
        ]
        return _text(
            f"Found {len(rows)} tickets",
            {
                "tickets": rows,
                "total": len(rows),
                "showing": len(rows),
                "failed_ids": [],
            },
        )

    def _ticket_create(self, args):
        tid = len(self.tickets) + 1
        self.tickets[tid] = {
            "summary": args["summary"],
            "status": "new",
            "owner": args.get("owner", ""),
            "comments": [],
            "ts": self._bump(),
        }
        return _text(f"Created ticket #{tid}: {args['summary']}")

    def _ticket_get(self, args):
        ticket = self.tickets.get(args["ticket_id"])
        if ticket is None:
            return _err("not_found", "no ticket")
        return _text(
            "ticket", {"_ts": ticket["ts"], "id": args["ticket_id"]}
        )

    def _ticket_update(self, args):
        ticket = self.tickets[args["ticket_id"]]
        if args.get("base_ts") != ticket["ts"]:
            return _err("version_conflict", "stale base_ts")
        ticket["comments"].append([args["comment"]])
        ticket["ts"] = self._bump()
        number = (
            None
            if self.drop_comment_number
            else len(ticket["comments"])
        )
        return _text(
            "Updated",
            {
                "ticket_id": args["ticket_id"],
                "_ts": ticket["ts"],
                "comment_number": number,
                "comment_stored_matches": number is not None,
                "message": "Updated",
            },
        )

    def _ticket_comment_edit(self, args):
        if not self.plugin:
            return _err(
                "method_not_available",
                "MethodNotFound: ticket.editComment -- install the "
                "'tracrpc_comment' plugin",
            )
        ticket = self.tickets[args["ticket_id"]]
        if args["base_ts"] != ticket["ts"]:
            return _err("version_conflict", "stale base_ts")
        revisions = ticket["comments"][args["cnum"] - 1]
        if self.edit_creates_revision:
            revisions.append(args["comment"])
        else:
            revisions[-1] = args["comment"]
        ticket["ts"] = self._bump()
        return _text(f"Edited comment {args['cnum']}")

    def _ticket_comment_history(self, args):
        if not self.plugin:
            return _err(
                "method_not_available", "tracrpc_comment missing"
            )
        revisions = self.tickets[args["ticket_id"]]["comments"][
            args["cnum"] - 1
        ]
        lines = [
            f"Comment {args['cnum']} on ticket #{args['ticket_id']} -- "
            f"{len(revisions)} revision(s), oldest first:"
        ]
        for number, body in enumerate(revisions):
            lines.append(
                f"\n--- revision {number} by agent_rpc at t ---"
            )
            lines.append(body)
        return _text("\n".join(lines))


class _RawSession:
    """The one call the checks make on an SDK ClientSession."""

    def __init__(self, trac):
        self.trac = trac

    async def call_tool(self, name, arguments):
        return await self.trac.call(name, arguments)


def FakeSession(trac, instance="/trac_test"):  # noqa: N802
    """What checks 4-6 are handed: the stand-in, scoped to an instance."""
    return smoke.Scoped(_RawSession(trac), instance)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def trac():
    return FakeTrac()


def _failure(coro):
    with pytest.raises(smoke.CheckFailed) as caught:
        _run(coro)
    return str(caught.value)


def test_a_check_that_times_out_is_a_failure_with_a_plain_reason():
    out = io.StringIO()
    reporter = smoke.Reporter(TOKEN, out)

    async def never_answers():
        raise TimeoutError

    ok = _run(smoke._step(reporter, TOKEN, 3, "tools", never_answers))
    assert ok is False
    assert out.getvalue() == (
        "FAIL 3 tools: the daemon did not answer in time\n"
    )


# -- check 4 --------------------------------------------------------------


def test_read_passes_and_names_the_instance(trac):
    detail = _run(smoke.check_read(FakeSession(trac)))
    assert "WikiStart" in detail and "/trac_test" in detail


def test_read_fails_on_an_error_result(trac):
    trac.wikistart_error = True
    assert "WikiStart" in _failure(smoke.check_read(FakeSession(trac)))


def test_read_fails_on_empty_content(trac):
    trac.pages["WikiStart"]["content"] = ""
    assert "empty" in _failure(smoke.check_read(FakeSession(trac)))


# -- check 5 --------------------------------------------------------------


def test_write_read_creates_then_updates_the_page(trac):
    session = FakeSession(trac)
    _run(smoke.check_write_read(session))
    assert trac.pages[smoke.WIKI_PAGE]["version"] == 1
    first = trac.pages[smoke.WIKI_PAGE]["content"]
    _run(smoke.check_write_read(session))
    assert trac.pages[smoke.WIKI_PAGE]["version"] == 2
    assert (
        trac.pages[smoke.WIKI_PAGE]["content"] != first
    )  # fresh nonce
    assert "wiki_create" in trac.calls and "wiki_update" in trac.calls


def test_write_read_fails_when_the_read_back_differs(trac):
    trac.mangle_pages = True
    message = _failure(smoke.check_write_read(FakeSession(trac)))
    assert "reads back differently" in message
    assert "offset" in message


def test_write_read_retries_once_on_a_version_race(trac):
    trac.pages[smoke.WIKI_PAGE] = {"content": "old", "version": 5}
    real = trac._wiki_update
    state = {"raced": False}

    def racing(args):
        if not state["raced"]:
            state["raced"] = True
            trac.pages[smoke.WIKI_PAGE]["version"] += 1  # someone wrote
        return real(args)

    trac._wiki_update = racing
    _run(smoke.check_write_read(FakeSession(trac)))
    assert state["raced"]
    assert trac.calls.count("wiki_update") == 2


# -- check 6 --------------------------------------------------------------


def test_comment_plugin_creates_the_ticket_once_and_reuses_it(trac):
    session = FakeSession(trac)
    first = _run(smoke.check_comment_plugin(session))
    second = _run(smoke.check_comment_plugin(session))
    assert "created" in first and "found" in second
    assert len(trac.tickets) == 1
    ticket = trac.tickets[1]
    assert ticket["summary"].startswith(smoke.TICKET_SUMMARY)
    assert ticket["owner"] == "agent_rpc"
    assert [len(revs) for revs in ticket["comments"]] == [2, 2]


def test_comment_plugin_ignores_a_closed_smoke_ticket(trac):
    trac.tickets[9] = {
        "summary": smoke.TICKET_SUMMARY + " (do not close)",
        "status": "closed",
        "owner": "x",
        "comments": [],
        "ts": "1",
    }
    _run(smoke.check_comment_plugin(FakeSession(trac)))
    assert len(trac.tickets) == 2


def test_comment_plugin_missing_names_the_plugin(trac):
    trac.plugin = False
    message = _failure(smoke.check_comment_plugin(FakeSession(trac)))
    assert message == (
        "tracrpc_comment plugin not loaded on the daemon's Trac"
    )


def test_comment_plugin_fails_when_the_edit_left_no_revision(trac):
    trac.edit_creates_revision = False
    message = _failure(smoke.check_comment_plugin(FakeSession(trac)))
    assert "1 revision(s)" in message


def test_comment_plugin_fails_without_a_comment_number(trac):
    trac.drop_comment_number = True
    message = _failure(smoke.check_comment_plugin(FakeSession(trac)))
    assert "comment_number" in message


# ---------------------------------------------------------------------------
# All six checks, end to end, over real HTTP
# ---------------------------------------------------------------------------


def _fake_daemon_server(trac, version="9.9.9"):
    server = Server("trac-mcp-server", version=version)

    @server.list_tools()
    async def _list_tools():
        return trac.tools()

    @server.call_tool()
    async def _call(name, arguments):
        return await trac.call(name, arguments or {})

    return server


def _config(port):
    return ServerConfig(
        transport="http",
        host="127.0.0.1",
        port=port,
        path="/mcp",
        auth_token=TOKEN,
    )


def _smoke_against(trac, path_suffix="", instance="/trac_test"):
    """Run the whole smoke against a real server; (exit, output)."""
    out = io.StringIO()

    async def body():
        port = _free_port()
        with _running_app(
            _fake_daemon_server(trac), _config(port)
        ) as base:
            return await smoke.run_smoke(
                smoke.normalise_base(base + path_suffix),
                TOKEN,
                out,
                instance,
            )

    code = anyio.run(body)
    return code, out.getvalue()


def test_all_six_checks_pass_against_a_real_server(trac):
    code, output = _smoke_against(trac)
    assert code == 0, output
    for n, name in enumerate(
        ["healthz", "auth_enforced", "tools", "read", "write_read"], 1
    ):
        assert f"PASS {n} {name}" in output
    assert "PASS 6 comment_plugin" in output
    assert "server trac-mcp-server 9.9.9" in output
    assert (
        output.strip()
        .splitlines()[-1]
        .startswith("DAEMON SMOKE PASSED http://127.0.0.1:")
    )
    assert output.strip().splitlines()[-1].endswith("(server 9.9.9)")
    assert TOKEN not in output


def test_the_url_may_end_in_mcp(trac):
    code, output = _smoke_against(trac, "/mcp/")
    assert code == 0, output


def test_a_missing_tool_fails_check_3_and_names_it(trac):
    trac.hide_tools = {"wiki_diff", "ticket_comment_history"}
    code, output = _smoke_against(trac)
    assert code == 1
    assert "FAIL 3 tools" in output
    assert "ticket_comment_history, wiki_diff" in output
    assert "PASS 4" not in output and "FAIL 4" not in output
    assert output.strip().splitlines()[-1] == (
        "DAEMON SMOKE FAILED at check 3"
    )


def test_an_extra_tool_is_noted_not_failed(trac):
    extra = types.Tool(
        name="only_on_the_daemon",
        description="x",
        inputSchema={"type": "object", "properties": {}},
    )
    original = trac.tools
    trac.tools = lambda: [*original(), extra]
    # the stand-in cannot answer it, but check 3 never calls it
    code, output = _smoke_against(trac)
    assert code == 0, output
    assert "NOTE 1 tool(s) on the daemon" in output
    assert "only_on_the_daemon" in output


def test_a_missing_plugin_fails_check_6_with_the_plugin_message(trac):
    trac.plugin = False
    code, output = _smoke_against(trac)
    assert code == 1
    assert (
        "FAIL 6 comment_plugin: tracrpc_comment plugin not loaded on "
        "the daemon's Trac"
    ) in output
    assert output.strip().splitlines()[-1] == (
        "DAEMON SMOKE FAILED at check 6"
    )


def test_a_page_stored_differently_fails_check_5(trac):
    trac.mangle_pages = True
    code, output = _smoke_against(trac)
    assert code == 1
    assert "PASS 4 read" in output
    assert "FAIL 5 write_read" in output
    assert "PASS 6" not in output


def test_the_empty_start_page_fails_check_4(trac):
    trac.pages["WikiStart"]["content"] = ""
    code, output = _smoke_against(trac)
    assert code == 1
    assert "FAIL 4 read" in output


# ---------------------------------------------------------------------------
# ci-live.sh dispatch and the exit contract, by subprocess
# ---------------------------------------------------------------------------

pytestmark_shell = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is not on PATH"
)


@pytest.fixture
def shell_env(tmp_path):
    """A child environment with no token, and a pytest that records being
    run: ci-live.sh --daemon must never reach it."""
    marker = tmp_path / "pytest-ran"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "pytest"
    stub.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
    stub.chmod(0o755)
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            DEFAULT_ENV,
            "CUSTOM_SMOKE_TOKEN",
            "TRAC_URL",
            "TRAC_USERNAME",
            "TRAC_PASSWORD",
        )
    }
    env["PATH"] = (
        f"{Path(sys.executable).parent}:{bin_dir}:{os.environ['PATH']}"
    )
    return tmp_path, env, marker


def _ci_live(tmp_path, env, *args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def listener():
    """A listening socket that records whether anything connected."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    sock.setblocking(False)
    yield sock
    sock.close()


def _was_connected_to(sock):
    try:
        conn, _addr = sock.accept()
    except BlockingIOError:
        return False
    conn.close()
    return True


def _assert_not_run(result):
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "NOT RUN" in result.stderr
    assert "this is not a passing result" in result.stderr
    assert "PASS" not in result.stdout
    assert "DAEMON SMOKE" not in result.stdout


@pytestmark_shell
def test_no_token_is_not_run_and_makes_no_network_call(
    shell_env, listener
):
    tmp_path, env, marker = shell_env
    port = listener.getsockname()[1]
    result = _ci_live(
        tmp_path, env, "--daemon", f"http://127.0.0.1:{port}"
    )
    _assert_not_run(result)
    assert DEFAULT_ENV in result.stderr
    assert not _was_connected_to(listener)
    assert not marker.exists()


@pytestmark_shell
def test_a_blank_token_is_not_run(shell_env, listener):
    tmp_path, env, _marker = shell_env
    env[DEFAULT_ENV] = "   "
    port = listener.getsockname()[1]
    result = _ci_live(
        tmp_path, env, "--daemon", f"http://127.0.0.1:{port}"
    )
    _assert_not_run(result)
    assert not _was_connected_to(listener)


@pytest.mark.parametrize(
    "url", ["not-a-url", "ftp://127.0.0.1:1", "http://u:hunter2@h:1"]
)
@pytestmark_shell
def test_a_malformed_url_is_not_run(shell_env, url):
    tmp_path, env, _marker = shell_env
    env[DEFAULT_ENV] = TOKEN
    result = _ci_live(tmp_path, env, "--daemon", url)
    _assert_not_run(result)
    assert "hunter2" not in result.stderr + result.stdout
    assert TOKEN not in result.stderr + result.stdout


@pytestmark_shell
def test_token_env_names_the_variable_read(shell_env, listener):
    tmp_path, env, _marker = shell_env
    env[DEFAULT_ENV] = TOKEN  # set, but --token-env points elsewhere
    port = listener.getsockname()[1]
    result = _ci_live(
        tmp_path,
        env,
        "--daemon",
        f"http://127.0.0.1:{port}",
        "--token-env",
        "CUSTOM_SMOKE_TOKEN",
    )
    _assert_not_run(result)
    assert "CUSTOM_SMOKE_TOKEN" in result.stderr
    assert not _was_connected_to(listener)


@pytestmark_shell
def test_token_env_value_is_used_when_set(shell_env):
    tmp_path, env, _marker = shell_env
    env["CUSTOM_SMOKE_TOKEN"] = TOKEN  # default stays unset
    result = _ci_live(
        tmp_path,
        env,
        "--daemon",
        f"http://127.0.0.1:{_closed_port()}",
        "--token-env",
        "CUSTOM_SMOKE_TOKEN",
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "FAIL 1 healthz" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["--bogus"],
        ["http://127.0.0.1:1"],
        ["--daemon"],
        ["--token-env", "X"],
        ["--daemon", "http://127.0.0.1:1", "--extra"],
        ["--daemon", "http://127.0.0.1:1", "--token-env"],
    ],
)
@pytestmark_shell
def test_a_bad_argument_is_usage_and_exit_2(shell_env, args):
    tmp_path, env, marker = shell_env
    env[DEFAULT_ENV] = TOKEN
    result = _ci_live(tmp_path, env, *args)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "usage: ci-live.sh" in result.stderr
    assert result.stdout == ""
    assert not marker.exists()


@pytestmark_shell
def test_daemon_mode_skips_the_probe_and_pytest(shell_env):
    tmp_path, env, marker = shell_env
    env[DEFAULT_ENV] = TOKEN
    result = _ci_live(
        tmp_path, env, "--daemon", f"http://127.0.0.1:{_closed_port()}"
    )
    assert "Live credentials" not in result.stdout
    assert "Full Suite" not in result.stdout
    assert "Live checks passed" not in result.stdout
    assert not marker.exists()


@pytestmark_shell
def test_an_unreachable_daemon_fails_at_check_1_and_hides_the_token(
    shell_env,
):
    tmp_path, env, _marker = shell_env
    env[DEFAULT_ENV] = TOKEN
    result = _ci_live(
        tmp_path, env, "--daemon", f"http://127.0.0.1:{_closed_port()}"
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "FAIL 1 healthz" in result.stdout
    assert "DAEMON SMOKE FAILED at check 1" in result.stdout
    assert "PASS" not in result.stdout
    assert TOKEN not in result.stdout + result.stderr


class _Handler(http.server.BaseHTTPRequestHandler):
    """healthz is fine; what POST /mcp answers is the test's choice."""

    seen: list = []
    post_status = 200
    echo_authorization = False

    def log_message(self, *_args):
        pass

    def _send(self, status, body, ctype="application/json"):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.seen.append(("GET", self.path))
        if self.path == "/healthz":
            self._send(200, json.dumps({"status": "ok"}))
        else:
            self._send(404, "{}")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.seen.append(("POST", self.path))
        authorized = self.headers.get("Authorization")
        if authorized and self.echo_authorization:
            # a hostile/sloppy server that repeats the request headers
            # back in its error body
            self._send(500, f"internal error; request had {authorized}")
            return
        self._send(self.post_status, "{}")


@pytest.fixture
def fake_http():
    handler = type("H", (_Handler,), {"seen": [], "post_status": 200})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield handler, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytestmark_shell
def test_an_open_daemon_fails_check_2(shell_env, fake_http):
    handler, base = fake_http
    tmp_path, env, _marker = shell_env
    env[DEFAULT_ENV] = TOKEN
    handler.post_status = 200  # answers the unauthenticated POST
    result = _ci_live(tmp_path, env, "--daemon", base)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "PASS 1 healthz" in result.stdout
    assert "FAIL 2 auth_enforced" in result.stdout
    assert "answered 200" in result.stdout
    assert "DAEMON SMOKE FAILED at check 2" in result.stdout
    assert "PASS 3" not in result.stdout


@pytestmark_shell
def test_a_trailing_mcp_in_the_url_is_normalised(shell_env, fake_http):
    handler, base = fake_http
    tmp_path, env, _marker = shell_env
    env[DEFAULT_ENV] = TOKEN
    handler.post_status = 200
    _ci_live(tmp_path, env, "--daemon", base + "/mcp/")
    assert ("GET", "/healthz") in handler.seen
    assert ("POST", "/mcp") in handler.seen


@pytestmark_shell
def test_an_error_body_that_echoes_the_token_is_not_printed(
    shell_env, fake_http
):
    handler, base = fake_http
    tmp_path, env, _marker = shell_env
    env[DEFAULT_ENV] = TOKEN
    handler.post_status = 401  # check 2 passes ...
    handler.echo_authorization = True  # ... the authed POST echoes
    try:
        result = _ci_live(tmp_path, env, "--daemon", base)
    finally:
        handler.echo_authorization = False
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "PASS 2 auth_enforced" in result.stdout
    assert "FAIL 3 tools" in result.stdout
    assert TOKEN not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# The write target: checks 4-6 name the instance on every call
# ---------------------------------------------------------------------------

_WRITE_TOOLS = {
    "wiki_get",
    "wiki_create",
    "wiki_update",
    "ticket_search",
    "ticket_create",
    "ticket_get",
    "ticket_update",
    "ticket_comment_edit",
    "ticket_comment_history",
}


def test_no_call_in_checks_4_to_6_omits_the_instance(trac):
    # twice: the first run creates the page, the second updates it
    for _ in range(2):
        code, output = _smoke_against(trac)
        assert code == 0, output
    assert {name for name, _ in trac.instances} == _WRITE_TOOLS
    omitted = [n for n, inst in trac.instances if inst is None]
    assert omitted == []
    assert {inst for _, inst in trac.instances} == {"/trac_test"}


def test_the_requested_instance_is_what_every_call_names(trac):
    code, output = _smoke_against(trac, instance="/elsewhere")
    assert code == 0, output
    assert {inst for _, inst in trac.instances} == {"/elsewhere"}


def test_an_answer_from_another_instance_fails_the_check(trac):
    trac.answer_as = "/auto_pm"
    code, output = _smoke_against(trac)
    assert code == 1
    assert "PASS 3 tools" in output
    assert "FAIL 4 read" in output
    assert "came from /auto_pm" in output
    assert "PASS 5" not in output
    assert trac.calls == ["wiki_get"]  # nothing was written


def test_a_result_that_does_not_say_which_instance_fails(trac):
    trac.echo_instance = False
    code, output = _smoke_against(trac)
    assert code == 1
    assert "FAIL 4 read" in output
    assert "did not report which instance answered" in output


def test_a_write_answered_from_elsewhere_fails_check_5(trac):
    real = trac._wiki_update

    def strays(args):
        trac.answer_as = "/auto_pm"  # from the write on
        return real(args)

    trac._wiki_update = strays
    trac.pages[smoke.WIKI_PAGE] = {"content": "old", "version": 2}
    code, output = _smoke_against(trac)
    assert code == 1
    assert "FAIL 5 write_read" in output and "/auto_pm" in output


@pytest.mark.parametrize(
    "instance, allow, refused",
    [
        ("/trac_test", False, False),
        ("/trac_test", True, False),
        ("/auto_pm", False, True),
        ("/auto_pm", True, False),
        ("auto_pm", True, True),
        ("/a/b", True, True),
        ("", True, True),
        ("/", True, True),
    ],
)
def test_instance_refusal(instance, allow, refused):
    assert bool(smoke.instance_refusal(instance, allow)) is refused


@pytestmark_shell
def test_an_instance_other_than_trac_test_is_refused_without_the_flag(
    shell_env, listener
):
    tmp_path, env, marker = shell_env
    env[DEFAULT_ENV] = TOKEN
    port = listener.getsockname()[1]
    result = _ci_live(
        tmp_path,
        env,
        "--daemon",
        f"http://127.0.0.1:{port}",
        "--instance",
        "/auto_pm",
    )
    _assert_not_run(result)
    assert "--allow-instance-writes" in result.stderr
    assert TOKEN not in result.stderr + result.stdout
    assert not _was_connected_to(listener)
    assert not marker.exists()


@pytestmark_shell
def test_the_flag_lets_another_instance_through_to_the_network(
    shell_env,
):
    tmp_path, env, _marker = shell_env
    env[DEFAULT_ENV] = TOKEN
    result = _ci_live(
        tmp_path,
        env,
        "--daemon",
        f"http://127.0.0.1:{_closed_port()}",
        "--instance",
        "/auto_pm",
        "--allow-instance-writes",
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "FAIL 1 healthz" in result.stdout


@pytestmark_shell
def test_a_malformed_instance_is_not_run(shell_env, listener):
    tmp_path, env, _marker = shell_env
    env[DEFAULT_ENV] = TOKEN
    port = listener.getsockname()[1]
    result = _ci_live(
        tmp_path,
        env,
        "--daemon",
        f"http://127.0.0.1:{port}",
        "--instance",
        "auto_pm/x",
        "--allow-instance-writes",
    )
    _assert_not_run(result)
    assert not _was_connected_to(listener)


@pytestmark_shell
def test_instance_without_a_value_is_usage(shell_env):
    tmp_path, env, _marker = shell_env
    env[DEFAULT_ENV] = TOKEN
    result = _ci_live(
        tmp_path, env, "--daemon", "http://127.0.0.1:1", "--instance"
    )
    assert result.returncode == 2
    assert "usage: ci-live.sh" in result.stderr
