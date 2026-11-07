"""Tests for build_registry() in mcp/server.py: the tool filters main()
applies, over the real tool list."""

import contextlib
import logging
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest

from trac_mcp_server.config import Config
from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.instances import InstanceRegistry
from trac_mcp_server.logger import setup_logging
from trac_mcp_server.mcp import server as server_module
from trac_mcp_server.mcp.server import PING_SPEC, build_registry
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import (
    with_page_alias,
    with_strict_schema,
)

SPECS = with_strict_schema(with_page_alias([PING_SPEC] + ALL_SPECS))


def _names(registry):
    return {tool.name for tool in registry.list_tools()}


def test_no_filter_registers_every_tool(capsys):
    registry = build_registry(SPECS, None, ServerConfig())
    assert registry.tool_count() == len(SPECS)
    assert capsys.readouterr().err == ""


def test_read_only_reaches_the_registry(capsys):
    """read_only from the config is applied: no tool left is a write tool."""
    registry = build_registry(SPECS, None, ServerConfig(read_only=True))
    names = _names(registry)
    assert "ticket_get" in names
    assert "ticket_update" not in names
    assert all(
        spec.tool.annotations.readOnlyHint
        for spec in SPECS
        if spec.tool.name in names
    )
    # Hidden although they read: they can write a local file.
    assert not names & {
        "ticket_attachment_get",
        "wiki_attachment_get",
        "wiki_file_pull",
    }
    err = capsys.readouterr().err
    assert (
        f"Read-only mode: {len(names)} of {len(SPECS)} tools pass"
        in err
    )


def test_disabled_tools_reach_the_registry(capsys):
    """Only matched names are reported as disabled; unknown ones get a
    warning on stderr, where a stdio operator sees it."""
    registry = build_registry(
        SPECS,
        None,
        ServerConfig(
            disabled_tools=["ticket_get", "ticket_comment_histroy"]
        ),
    )
    assert "ticket_get" not in _names(registry)
    assert registry.tool_count() == len(SPECS) - 1
    err = capsys.readouterr().err
    assert "Disabled tools: ticket_get\n" in err
    assert "not found (ignored): ticket_comment_histroy" in err


def test_each_filter_reports_its_own_count(capsys):
    """With two filters, each line credits only its own filter; the last
    line gives the combined count."""
    registry = build_registry(
        SPECS,
        frozenset({"TICKET_VIEW"}),
        ServerConfig(read_only=True),
        "perms.txt",
    )
    err = capsys.readouterr().err
    read_only_count = len(
        [s for s in SPECS if s.tool.annotations.readOnlyHint]
    )
    assert f"Read-only mode: {read_only_count} of {len(SPECS)}" in err
    assert "Permissions file: perms.txt (" in err
    assert (
        f"Tools enabled: {registry.tool_count()} of {len(SPECS)}" in err
    )


@pytest.mark.parametrize("disabled", [["list_instances"], []])
def test_disabled_list_instances_warns(capsys, disabled):
    build_registry(SPECS, None, ServerConfig(disabled_tools=disabled))
    warned = "list_instances is disabled" in capsys.readouterr().err
    assert warned is bool(disabled)


@contextlib.contextmanager
def _own_root_logging():
    """Give the body a root logger of its own, then put back the old one.

    pytest puts capture handlers on the root logger for each phase of a
    test, and basicConfig() does nothing while any handler is there, so
    setup_logging() would configure nothing. It has to be entered inside
    the test body: the call phase's handlers are added after any fixture
    has run.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers = []
    try:
        yield
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)


_ALL_FILTERS = ServerConfig(
    read_only=True,
    disabled_tools=[
        "ticket_get",
        "ticket_comment_histroy",
        "list_instances",
    ],
)

# Each startup line the three filters produce, as it reads on stderr.
_STARTUP_LINES = [
    "Read-only mode: ",
    "Disabled tools: list_instances, ticket_get",
    "Tools enabled: ",
    "not found (ignored): ticket_comment_histroy",
    "list_instances is disabled",
]


def _assert_each_line_once(err):
    for line in _STARTUP_LINES:
        assert err.count(line) == 1, (
            f"{line!r} x{err.count(line)}:\n{err}"
        )


def test_http_logging_shows_each_startup_line_once(capsys, monkeypatch):
    """Under http the logger's own stderr handler already shows the line;
    printing it as well showed every line twice."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_FILE", raising=False)
    with _own_root_logging():
        setup_logging(mode="http")
        build_registry(SPECS, None, _ALL_FILTERS)
    _assert_each_line_once(capsys.readouterr().err)


def test_stdio_with_log_file_still_prints_each_line_once(
    capsys, monkeypatch, tmp_path
):
    """Under stdio the logger writes only the log file, so stderr is
    where the operator sees the line: it must not be dropped."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    with _own_root_logging():
        setup_logging(mode="mcp", log_file=str(tmp_path / "server.log"))
        build_registry(SPECS, None, _ALL_FILTERS)
    _assert_each_line_once(capsys.readouterr().err)


def test_stdio_without_log_file_shows_each_line_once(
    capsys, monkeypatch
):
    """stdio with the log file disabled logs to stderr, like http."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    with _own_root_logging():
        setup_logging(mode="mcp", log_file="")
        build_registry(SPECS, None, _ALL_FILTERS)
    _assert_each_line_once(capsys.readouterr().err)


def test_no_handlers_leaves_the_warning_to_last_resort(capsys):
    """With no handler anywhere, logging's lastResort writes a WARNING to
    stderr itself, so the print must not repeat it; it ignores INFO, which
    only the fallback print shows."""
    with _own_root_logging():
        logging.getLogger().setLevel(logging.WARNING)
        build_registry(
            SPECS,
            None,
            ServerConfig(
                read_only=True,
                disabled_tools=["ticket_comment_histroy"],
            ),
        )
    err = capsys.readouterr().err
    assert (
        err.count("not found (ignored): ticket_comment_histroy") == 1
    ), err
    assert err.count("Read-only mode: ") == 1, err


# ---------------------------------------------------------------------
# The wiring: main() hands the resolved ServerConfig to build_registry().
# ---------------------------------------------------------------------

_SERVED = {spec.tool.name for spec in ALL_SPECS}
_READ_ONLY = {
    spec.tool.name
    for spec in ALL_SPECS
    if spec.tool.annotations.readOnlyHint
}


@pytest.mark.parametrize(
    "server_config,expected",
    [
        pytest.param(
            ServerConfig(transport="http"),
            {"ping"} | _SERVED,
            id="defaults-serve-every-tool",
        ),
        pytest.param(
            ServerConfig(transport="http", read_only=True),
            {"ping"} | _READ_ONLY,
            id="read-only",
        ),
        pytest.param(
            ServerConfig(
                transport="http", disabled_tools=["ticket_get", "nope"]
            ),
            {"ping"} | (_SERVED - {"ticket_get"}),
            id="disabled-tools",
        ),
    ],
)
async def test_main_serves_the_registry_its_config_builds(
    server_config, expected
):
    """``main()`` passes the ServerConfig it resolved into
    ``build_registry`` (``test_build_registry`` above covers the filters
    themselves): the registry passed to ``run_http`` lists exactly the
    tools that config leaves in. A ``main()`` that built the registry from
    a default ``ServerConfig()`` would still serve every tool."""
    served = {}

    @asynccontextmanager
    async def _fake_lifespan(config_overrides=None):
        yield {
            "instances": InstanceRegistry(
                Config(
                    trac_url="http://192.168.10.4:8000/trac_test",
                    username="agent_rpc",
                    password="secret",
                ),
                {},
            ),
            "client": MagicMock(),
        }

    async def _fake_run_stdio():
        raise AssertionError("main() took the stdio path")

    async def _fake_run_http(_server, _config):
        # main() clears the registry in its finally, so read it here.
        served["names"] = {
            tool.name
            for tool in server_module.get_registry().list_tools()
        }

    with (
        patch.object(
            server_module,
            "bootstrap_server_config",
            return_value=server_config,
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
        patch.object(server_module, "_run_stdio", _fake_run_stdio),
    ):
        await server_module.main()

    assert "names" in served, "main() never reached run_http"
    assert served["names"] == expected
    # main()'s finally cleared the globals it set.
    with pytest.raises(RuntimeError):
        server_module.get_registry()
