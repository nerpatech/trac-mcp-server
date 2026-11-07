"""Tests for build_registry() in mcp/server.py: the tool filters main()
applies, over the real tool list."""

import pytest

from trac_mcp_server.config_schema import ServerConfig
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
