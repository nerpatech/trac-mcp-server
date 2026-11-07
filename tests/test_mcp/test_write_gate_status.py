"""Ticket #153: the write gate's state is visible outside the daemon.

`list_instances` and the per-write OFF line are tested beside their own
handlers (`tools/test_instances.py`, `tools/test_write_gate.py`). This
file holds the two that live in ``server.py``: ``ping`` and the startup
log line, plus the check that ``list_instances`` and ``ping`` print the
same shared write-gate line.
"""

import asyncio
import dataclasses
import logging
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest

from trac_mcp_server.config import Config
from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.instances import InstanceRegistry
from trac_mcp_server.mcp import server as server_module
from trac_mcp_server.mcp.server import (
    _handle_ping,
    log_startup_settings,
)
from trac_mcp_server.mcp.tools.instances import (
    INSTANCE_SPECS,
    set_instance_registry,
)
from trac_mcp_server.mcp.tools.registry import ToolRegistry
from trac_mcp_server.mcp.tools.write_gate import (
    gate_enabled,
    write_gate_line,
)

LOGGER = "trac_mcp_server.mcp.server"


def _config(**kwargs) -> Config:
    return Config(
        trac_url="http://192.168.10.4:8000/trac_test",
        username="agent_rpc",
        password="secret",
        **kwargs,
    )


def _ping(write_gate: bool):
    client = MagicMock()
    client.config = _config(write_gate=write_gate)
    client.validate_connection.return_value = "1.2.0"
    return asyncio.run(_handle_ping(client, {}))


def _list_instances(write_gate: bool):
    config = _config(write_gate=write_gate)
    client = MagicMock()
    client.config = config
    # No host config files: the registry would otherwise read the machine's.
    with patch(
        "trac_mcp_server.instances.discover_config_files",
        return_value=[],
    ):
        set_instance_registry(InstanceRegistry(config, {}))
        try:
            return asyncio.run(
                ToolRegistry(INSTANCE_SPECS).call_tool(
                    "list_instances", {"discover": False}, client
                )
            )
        finally:
            set_instance_registry(None)


@pytest.mark.parametrize("state", [True, False])
def test_ping_reports_the_write_gate(state):
    result = _ping(state)
    assert not result.isError
    structured = result.structuredContent
    # Claude Code shows structuredContent in preference to the text
    # block, so the dict carries what the text does.
    assert structured["write_gate"] is state
    assert structured["api_version"] == "1.2.0"
    assert "connected successfully" in structured["message"]
    assert structured["message"] == result.content[0].text
    text = result.content[0].text
    assert "API version: 1.2.0" in text
    assert f"write_gate: {'true' if state else 'false'}" in text


# ---------------------------------------------------------------------
# The startup log line.
# ---------------------------------------------------------------------


def _server_config():
    return ServerConfig(
        transport="http", file_access="off", max_inline_bytes=77
    )


def _records(caplog, needle="write_gate="):
    return [
        r
        for r in caplog.records
        if r.name == LOGGER and needle in r.getMessage()
    ]


def test_startup_log_names_the_gate_on_at_info(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    log_startup_settings(_server_config(), True)
    (record,) = _records(caplog)
    assert record.levelno == logging.INFO
    assert record.getMessage() == "write_gate=on"


def test_startup_log_names_the_gate_off_at_warning(caplog):
    """WARNING, so a switch left set after a test stands out in the
    journal rather than reading like every other startup."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    log_startup_settings(_server_config(), False)
    (record,) = _records(caplog)
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "write_gate=off"


@pytest.mark.parametrize(
    "write_gate,expected",
    [(True, "write_gate=on"), (False, "write_gate=off")],
)
async def test_main_logs_the_registrys_write_gate(
    caplog, write_gate, expected
):
    """The wiring: ``main()`` logs the value the lifespan's registry
    holds -- the one ``list_instances`` reports -- once it has loaded
    the Trac config that carries it."""
    registry = InstanceRegistry(_config(write_gate=write_gate), {})

    @asynccontextmanager
    async def _fake_lifespan(config_overrides=None):
        yield {"instances": registry, "client": MagicMock()}

    async def _fake_run_http(_server, _config):
        return None

    caplog.set_level(logging.INFO, logger=LOGGER)
    with (
        patch.object(
            server_module,
            "bootstrap_server_config",
            return_value=_server_config(),
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

    (record,) = _records(caplog)
    assert record.getMessage() == expected
    assert record.levelno == (
        logging.INFO if write_gate else logging.WARNING
    )
    # file_access is logged straight after set_file_access, before the
    # lifespan, so it survives a lifespan validation failure.
    (fa,) = _records(caplog, "file_access=")
    assert fa.levelno == logging.INFO
    assert fa.getMessage() == "file_access=off, max_inline_bytes=77"
    assert caplog.records.index(fa) < caplog.records.index(record)


async def test_file_access_is_logged_when_the_lifespan_fails(caplog):
    @asynccontextmanager
    async def _failing_lifespan(config_overrides=None):
        raise RuntimeError("validation failed")
        yield  # pragma: no cover

    caplog.set_level(logging.INFO, logger=LOGGER)
    with (
        patch.object(
            server_module,
            "bootstrap_server_config",
            return_value=_server_config(),
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
        patch.object(
            server_module, "server_lifespan", _failing_lifespan
        ),
        pytest.raises(RuntimeError),
    ):
        await server_module.main()

    (fa,) = _records(caplog, "file_access=")
    assert fa.levelno == logging.INFO
    assert not _records(caplog)


def test_registry_write_gate_follows_the_default_config():
    config = _config()
    assert InstanceRegistry(config, {}).write_gate is True
    off = dataclasses.replace(config, write_gate=False)
    assert InstanceRegistry(off, {}).write_gate is False


async def test_oidc_clients_carry_the_logged_write_gate(caplog):
    """Startup builds OidcClientCache from the config the registry
    reports: with TRAC_WRITE_GATE=0 the startup log says off, and so does
    the per-user client that OIDC list_instances and every write use."""
    config = Config(
        trac_url="https://trac.example.com/trac",
        username="",
        password="",
        oidc_only=True,
        write_gate=False,
    )
    seen = {}

    async def _fake_run_http(_server, _config):
        client = server_module._oidc_cache.get_client("user-token")
        seen["client_gate"] = gate_enabled(client)

    caplog.set_level(logging.INFO, logger=LOGGER)
    with (
        patch.object(
            server_module,
            "bootstrap_server_config",
            return_value=ServerConfig(
                transport="http",
                oidc_rpc_url="https://trac.example.com/trac-api/login/xmlrpc",
                file_access="off",
            ),
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
        patch(
            "trac_mcp_server.mcp.lifespan.bootstrap_config",
            return_value=(config, []),
        ),
        patch(
            "trac_mcp_server.mcp.lifespan.load_declared_instances",
            return_value={},
        ),
        patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        patch.object(server_module, "run_http", _fake_run_http),
    ):
        await server_module.main()

    (record,) = _records(caplog)
    assert record.getMessage() == "write_gate=off"
    assert seen["client_gate"] is False


@pytest.mark.parametrize("state", [True, False])
def test_ping_and_list_instances_share_the_line(state):
    """One wording for one status: ping and list_instances both print
    write_gate_line()."""
    line = write_gate_line(state)
    assert line in _ping(state).content[0].text
    assert line in _list_instances(state).content[0].text
