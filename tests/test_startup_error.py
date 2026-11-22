"""StartupError: the one exception that means "already printed, exit 1"
(Trac /trac_mcp_server #277, L18).

``run()`` used to catch every RuntimeError and exit 1 silently on the
assumption that the message had been printed. Any RuntimeError from
anywhere in the server then lost its traceback.
"""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from trac_mcp_server.mcp import server as server_module
from trac_mcp_server.mcp.errors import StartupError
from trac_mcp_server.mcp.server import main, run


@pytest.fixture(autouse=True)
def _quiet_env(monkeypatch, isolated_config):
    for var in ("LOG_FILE", "LOG_LEVEL", "TRAC_DEBUG"):
        monkeypatch.delenv(var, raising=False)


def test_startup_error_is_a_runtime_error_with_a_docstring():
    assert issubclass(StartupError, RuntimeError)
    assert "stderr" in (StartupError.__doc__ or "")


def _run_with_main(monkeypatch, exc):
    async def fake_main(config_overrides=None):
        raise exc

    monkeypatch.setattr(server_module, "main", fake_main)
    monkeypatch.setattr(sys, "argv", ["trac-mcp-server"])
    run()


def test_run_exits_one_on_startup_error_without_a_traceback(
    monkeypatch, capsys
):
    with pytest.raises(SystemExit) as excinfo:
        _run_with_main(monkeypatch, StartupError("already printed"))

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err


def test_run_does_not_swallow_a_plain_runtime_error(monkeypatch):
    with pytest.raises(RuntimeError, match="boom") as excinfo:
        _run_with_main(monkeypatch, RuntimeError("boom"))

    assert not isinstance(excinfo.value, StartupError)


class TestMainRaisesStartupError:
    def test_server_config_error(self, capsys):
        with (
            patch(
                "trac_mcp_server.mcp.server.bootstrap_server_config",
                side_effect=ValueError("bad transport"),
            ),
            pytest.raises(StartupError, match="bad transport"),
        ):
            asyncio.run(main())

        assert "ERROR: Server configuration error" in (
            capsys.readouterr().err
        )

    def test_unopenable_log_file(self, capsys, tmp_path):
        path = str(tmp_path / "missing-dir" / "x.log")
        with (
            patch(
                "trac_mcp_server.mcp.server.bootstrap_server_config",
                return_value=SimpleNamespace(transport="http"),
            ),
            pytest.raises(StartupError, match="Cannot open log file"),
        ):
            asyncio.run(main(config_overrides={"log_file": path}))

        assert "ERROR: Cannot open log file" in capsys.readouterr().err

    def test_instances_config_error(self, capsys):
        with (
            patch(
                "trac_mcp_server.mcp.server.bootstrap_server_config",
                return_value=SimpleNamespace(transport="http"),
            ),
            patch("trac_mcp_server.mcp.server.setup_logging"),
            patch(
                "trac_mcp_server.mcp.server.load_declared_instances",
                side_effect=ValueError("bad instances"),
            ),
            pytest.raises(StartupError, match="bad instances"),
        ):
            asyncio.run(main())

        assert "ERROR: Instances configuration error" in (
            capsys.readouterr().err
        )

    def test_logging_settings_error(self, capsys, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "LOUD")
        with (
            patch(
                "trac_mcp_server.mcp.server.bootstrap_server_config",
                return_value=SimpleNamespace(transport="http"),
            ),
            patch("trac_mcp_server.mcp.server.setup_logging") as setup,
            pytest.raises(StartupError, match="LOG_LEVEL"),
        ):
            asyncio.run(main())

        setup.assert_not_called()
        err = capsys.readouterr().err
        assert "ERROR: Logging configuration error" in err
        assert "LOG_LEVEL" in err


class TestPermissionsFileError:
    """--permissions-file was the one startup failure with no handler: an
    unreadable or invalid file gave a traceback (Trac /trac_mcp_server
    #277 review)."""

    def _main(self, path):
        with (
            patch(
                "trac_mcp_server.mcp.server.bootstrap_server_config",
                return_value=SimpleNamespace(transport="http"),
            ),
            patch("trac_mcp_server.mcp.server.setup_logging"),
            pytest.raises(
                StartupError, match="Permissions file"
            ) as exc,
        ):
            asyncio.run(
                main(config_overrides={"permissions_file": path})
            )
        return exc.value

    def _error_lines(self, capsys):
        return [
            line
            for line in capsys.readouterr().err.splitlines()
            if line.startswith("ERROR")
        ]

    def test_missing_file(self, capsys, tmp_path):
        path = str(tmp_path / "nope.txt")
        err = self._main(path)

        assert isinstance(err.__cause__, OSError)
        lines = self._error_lines(capsys)
        assert len(lines) == 1
        assert lines[0].startswith("ERROR: Permissions file error: ")
        assert path in lines[0]

    def test_a_directory(self, capsys, tmp_path):
        self._main(str(tmp_path))
        lines = self._error_lines(capsys)
        assert len(lines) == 1
        assert lines[0].startswith("ERROR: Permissions file error: ")

    def test_invalid_permission(self, capsys, tmp_path):
        path = tmp_path / "perm.txt"
        path.write_text("ticket_view\n")
        self._main(str(path))
        lines = self._error_lines(capsys)
        assert len(lines) == 1
        assert "Invalid permission 'ticket_view'" in lines[0]

    def test_empty_file(self, capsys, tmp_path):
        path = tmp_path / "perm.txt"
        path.write_text("# nothing\n")
        self._main(str(path))
        assert "No permissions found" in self._error_lines(capsys)[0]

    def test_not_utf8(self, capsys, tmp_path):
        path = tmp_path / "perm.txt"
        path.write_bytes(b"\xff\xfe\x00bad\n")
        self._main(str(path))
        lines = self._error_lines(capsys)
        assert len(lines) == 1
        assert lines[0].startswith("ERROR: Permissions file error: ")
