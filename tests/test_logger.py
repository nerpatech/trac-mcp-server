"""Tests for logger.py — setup_logging() and JsonFormatter.

Covers:
- CLI mode logging (stderr handler)
- MCP mode logging (file handler)
- Debug level override
- Environment variable LOG_LEVEL handling
- JSON formatter output
- Third-party logger silencing

Strategy: Mock logging.basicConfig to verify setup_logging passes correct args,
since pytest's log capture plugin interferes with actual basicConfig calls.
"""

import asyncio
import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from trac_mcp_server.logger import JsonFormatter, setup_logging
from trac_mcp_server.mcp.server import (
    build_parser,
    config_overrides_from_args,
    main,
)

DEFAULT_LOG = "/tmp/trac-mcp-server.log"


@pytest.fixture(autouse=True)
def _clean_log_file_env(monkeypatch):
    """cli/http/stdio all honour LOG_FILE; keep the caller's out of the tests."""
    monkeypatch.delenv("LOG_FILE", raising=False)


# ---------------------------------------------------------------------------
# setup_logging tests
# ---------------------------------------------------------------------------


class TestSetupLogging:
    """Tests for setup_logging()."""

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_cli_mode_logs_to_stderr(self, mock_basic):
        """CLI mode passes StreamHandler(stderr) to basicConfig."""
        setup_logging(mode="cli")

        mock_basic.assert_called_once()
        kwargs = mock_basic.call_args[1]
        handlers = kwargs["handlers"]
        assert len(handlers) >= 1
        assert isinstance(handlers[0], logging.StreamHandler)
        assert handlers[0].stream is sys.stderr

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_mcp_mode_logs_to_file(self, mock_basic, tmp_path):
        """MCP mode passes filename to basicConfig."""
        log_file = str(tmp_path / "test-mcp.log")
        setup_logging(mode="mcp", log_file=log_file)

        mock_basic.assert_called_once()
        kwargs = mock_basic.call_args[1]
        assert kwargs["filename"] == log_file

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_debug_overrides_level(self, mock_basic):
        """debug=True passes DEBUG level to basicConfig."""
        setup_logging(mode="cli", debug=True)

        mock_basic.assert_called_once()
        kwargs = mock_basic.call_args[1]
        assert kwargs["level"] == logging.DEBUG

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_env_log_level_honored(self, mock_basic, monkeypatch):
        """LOG_LEVEL env var is reflected in basicConfig level."""
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        setup_logging(mode="cli")

        kwargs = mock_basic.call_args[1]
        assert kwargs["level"] == logging.ERROR

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_debug_beats_env(self, mock_basic, monkeypatch):
        """debug=True overrides LOG_LEVEL env var."""
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        setup_logging(mode="cli", debug=True)

        kwargs = mock_basic.call_args[1]
        assert kwargs["level"] == logging.DEBUG

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_cli_mode_with_log_file(self, mock_basic, tmp_path):
        """CLI mode with log_file creates both stderr and file handlers."""
        log_file = str(tmp_path / "test-cli.log")
        setup_logging(mode="cli", log_file=log_file)

        kwargs = mock_basic.call_args[1]
        handlers = kwargs["handlers"]
        stream_handlers = [
            h
            for h in handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        file_handlers = [
            h for h in handlers if isinstance(h, logging.FileHandler)
        ]
        assert len(stream_handlers) >= 1
        assert len(file_handlers) >= 1
        # Clean up file handler
        for h in file_handlers:
            h.close()

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_json_format_uses_json_formatter(self, mock_basic):
        """debug_format='json' sets JsonFormatter on handlers."""
        setup_logging(mode="cli", debug_format="json")

        kwargs = mock_basic.call_args[1]
        handlers = kwargs["handlers"]
        json_formatters = [
            h
            for h in handlers
            if isinstance(h.formatter, JsonFormatter)
        ]
        assert len(json_formatters) >= 1

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_third_party_silenced(self, _mock_basic):
        """Non-DEBUG mode silences urllib3/httpx/httpcore loggers."""
        setup_logging(mode="cli")

        assert logging.getLogger("urllib3").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_mcp_mode_default_log_file(self, mock_basic, monkeypatch):
        """MCP mode defaults to /tmp/trac-mcp-server.log when no log_file given."""
        monkeypatch.delenv("LOG_FILE", raising=False)
        setup_logging(mode="mcp")

        kwargs = mock_basic.call_args[1]
        assert kwargs["filename"] == "/tmp/trac-mcp-server.log"

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_mcp_default_level_is_warning(
        self, mock_basic, monkeypatch
    ):
        """MCP mode defaults to WARNING level."""
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        setup_logging(mode="mcp")

        kwargs = mock_basic.call_args[1]
        assert kwargs["level"] == logging.WARNING

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_cli_default_level_is_info(self, mock_basic, monkeypatch):
        """CLI mode defaults to INFO level."""
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        setup_logging(mode="cli")

        kwargs = mock_basic.call_args[1]
        assert kwargs["level"] == logging.INFO


# ---------------------------------------------------------------------------
# Log file resolution (Trac /trac_mcp_server #157)
# ---------------------------------------------------------------------------


def _file_handlers(handlers):
    return [h for h in handlers if isinstance(h, logging.FileHandler)]


class TestLogFileResolution:
    """--log-file, then LOG_FILE (even empty), then the per-mode default."""

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_stdio_default(self, mock_basic):
        setup_logging(mode="mcp")

        kwargs = mock_basic.call_args[1]
        assert kwargs["filename"] == DEFAULT_LOG

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_stdio_env_var_is_read(self, mock_basic, monkeypatch):
        monkeypatch.setenv("LOG_FILE", "/x")
        setup_logging(mode="mcp")

        kwargs = mock_basic.call_args[1]
        assert kwargs["filename"] == "/x"

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_stdio_arg_beats_env(self, mock_basic, monkeypatch):
        monkeypatch.setenv("LOG_FILE", "/x")
        setup_logging(mode="mcp", log_file="/arg")

        kwargs = mock_basic.call_args[1]
        assert kwargs["filename"] == "/arg"

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_stdio_empty_arg_logs_to_stderr(self, mock_basic, capsys):
        setup_logging(mode="mcp", log_file="")

        kwargs = mock_basic.call_args[1]
        assert "filename" not in kwargs
        handlers = kwargs["handlers"]
        assert len(handlers) == 1
        assert type(handlers[0]) is logging.StreamHandler
        assert handlers[0].stream is sys.stderr
        assert capsys.readouterr().out == ""

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_stdio_empty_env_logs_to_stderr(
        self, mock_basic, monkeypatch
    ):
        monkeypatch.setenv("LOG_FILE", "")
        setup_logging(mode="mcp")

        kwargs = mock_basic.call_args[1]
        assert "filename" not in kwargs
        handlers = kwargs["handlers"]
        assert len(handlers) == 1
        assert type(handlers[0]) is logging.StreamHandler
        assert handlers[0].stream is sys.stderr

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_stdio_empty_arg_keeps_the_stdio_format(self, mock_basic):
        setup_logging(mode="mcp", log_file="")

        handler = mock_basic.call_args[1]["handlers"][0]
        assert (
            handler.formatter._fmt
            == "[%(asctime)s] [%(levelname)s] %(message)s"
        )

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_http_default_has_no_file(self, mock_basic):
        setup_logging(mode="http")

        handlers = mock_basic.call_args[1]["handlers"]
        assert len(handlers) == 1
        assert handlers[0].stream is sys.stderr
        assert _file_handlers(handlers) == []

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_http_env_var_adds_file(
        self, mock_basic, monkeypatch, tmp_path
    ):
        path = str(tmp_path / "a.log")
        monkeypatch.setenv("LOG_FILE", path)
        setup_logging(mode="http")

        handlers = mock_basic.call_args[1]["handlers"]
        files = _file_handlers(handlers)
        try:
            assert any(h.stream is sys.stderr for h in handlers)
            assert [h.baseFilename for h in files] == [path]
        finally:
            for h in files:
                h.close()

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_http_empty_arg_beats_env(
        self, mock_basic, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("LOG_FILE", str(tmp_path / "a.log"))
        setup_logging(mode="http", log_file="")

        handlers = mock_basic.call_args[1]["handlers"]
        assert _file_handlers(handlers) == []
        assert handlers[0].stream is sys.stderr

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_http_whitespace_env_is_no_file(
        self, mock_basic, monkeypatch
    ):
        monkeypatch.setenv("LOG_FILE", " ")
        setup_logging(mode="http")

        handlers = mock_basic.call_args[1]["handlers"]
        assert _file_handlers(handlers) == []

    @patch("trac_mcp_server.logger.logging.basicConfig")
    def test_stdio_whitespace_arg_logs_to_stderr(self, mock_basic):
        setup_logging(mode="mcp", log_file="  \t")

        kwargs = mock_basic.call_args[1]
        assert "filename" not in kwargs
        assert kwargs["handlers"][0].stream is sys.stderr

    def test_http_real_file_receives_records(
        self, monkeypatch, tmp_path
    ):
        """No basicConfig mock: the record lands in the LOG_FILE path."""
        path = tmp_path / "real.log"
        monkeypatch.setenv("LOG_FILE", str(path))
        root = logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        root.handlers.clear()
        try:
            setup_logging(mode="http")
            logging.getLogger("trac_mcp_server.test157").warning(
                "hello-157"
            )
            for h in root.handlers:
                h.flush()
            assert path.exists()
            assert "hello-157" in path.read_text()
        finally:
            for h in root.handlers[:]:
                h.close()
                root.removeHandler(h)
            for h in saved_handlers:
                root.addHandler(h)
            root.setLevel(saved_level)


class TestUnopenableLogFile:
    """main() turns an unopenable log file into a clean RuntimeError."""

    def test_names_the_path_and_raises_runtime_error(self, capsys):
        err = FileNotFoundError(
            2, "No such file or directory", "/nope/x.log"
        )
        with (
            patch(
                "trac_mcp_server.mcp.server.bootstrap_server_config",
                return_value=SimpleNamespace(transport="http"),
            ),
            patch(
                "trac_mcp_server.mcp.server.setup_logging",
                side_effect=err,
            ),
            pytest.raises(RuntimeError, match="/nope/x.log") as exc,
        ):
            asyncio.run(
                main(config_overrides={"log_file": "/nope/x.log"})
            )

        assert exc.value.__cause__ is err
        stderr = capsys.readouterr().err
        assert "ERROR: Cannot open log file /nope/x.log" in stderr

    def test_real_unopenable_path(self, capsys, tmp_path):
        path = str(tmp_path / "missing-dir" / "x.log")
        with (
            patch(
                "trac_mcp_server.mcp.server.bootstrap_server_config",
                return_value=SimpleNamespace(transport="http"),
            ),
            pytest.raises(RuntimeError, match="Cannot open log file"),
        ):
            asyncio.run(main(config_overrides={"log_file": path}))

        assert path in capsys.readouterr().err


class TestLogFileArgument:
    """server.py's --log-file reaches setup_logging only when given."""

    def _overrides(self, argv):
        return config_overrides_from_args(
            build_parser().parse_args(argv)
        )

    def test_not_given_is_not_forwarded(self):
        assert "log_file" not in self._overrides([])

    def test_empty_string_is_forwarded(self):
        assert self._overrides(["--log-file", ""])["log_file"] == ""

    def test_path_is_forwarded(self):
        assert self._overrides(["--log-file", "/p"])["log_file"] == "/p"


# ---------------------------------------------------------------------------
# JsonFormatter tests
# ---------------------------------------------------------------------------


class TestJsonFormatter:
    """Tests for JsonFormatter."""

    def test_basic_output(self):
        """Formatted output is valid JSON with required keys."""
        formatter = JsonFormatter(datefmt="%Y-%m-%d %H:%M:%S")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert "ts" in data
        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert data["msg"] == "Hello world"

    def test_includes_exception(self):
        """Exception info is included in 'exc' key."""
        formatter = JsonFormatter(datefmt="%Y-%m-%d %H:%M:%S")

        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="An error occurred",
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert "exc" in data
        assert "ValueError" in data["exc"]
        assert "test error" in data["exc"]

    def test_single_line_output(self):
        """Output is a single line (no embedded newlines in JSON)."""
        formatter = JsonFormatter(datefmt="%Y-%m-%d %H:%M:%S")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Single line test",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)

        assert "\n" not in output
        # Verify it's still valid JSON
        json.loads(output)
