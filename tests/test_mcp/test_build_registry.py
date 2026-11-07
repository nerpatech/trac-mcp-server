"""Tests for build_registry() in mcp/server.py: the tool filters main()
applies, over the real tool list."""

import contextlib
import logging

import pytest

from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.logger import setup_logging
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
