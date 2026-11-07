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
import io
import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from trac_mcp_server.logger import (
    JsonFormatter,
    logging_reaches_stderr,
    setup_logging,
    startup_note,
)
from trac_mcp_server.mcp.server import (
    _report_version,
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


# -------------------------------------------------------------------------
# logging_reaches_stderr() / startup_note() (Trac #167)
# -------------------------------------------------------------------------


@pytest.fixture
def note_logger():
    """A dedicated logger that does not propagate, so only the handlers a
    test adds count; all of its state is restored on exit."""
    lg = logging.getLogger("test_startup_note.dedicated")
    saved = (lg.level, lg.propagate, list(lg.handlers))
    lg.setLevel(logging.INFO)
    lg.propagate = False
    lg.handlers = []
    yield lg
    for handler in lg.handlers:
        handler.close()
    lg.level, lg.propagate = saved[0], saved[1]
    lg.handlers = saved[2]


class TestLoggingReachesStderr:
    def test_stderr_stream_handler_reaches(self, note_logger, capsys):
        note_logger.addHandler(logging.StreamHandler(sys.stderr))
        assert logging_reaches_stderr(note_logger, logging.INFO)

    def test_file_handler_does_not_reach(self, note_logger, tmp_path):
        note_logger.addHandler(logging.FileHandler(tmp_path / "x.log"))
        assert not logging_reaches_stderr(note_logger, logging.INFO)
        assert not logging_reaches_stderr(note_logger, logging.ERROR)

    def test_stdout_stream_handler_does_not_reach(
        self, note_logger, capsys
    ):
        note_logger.addHandler(logging.StreamHandler(sys.stdout))
        assert not logging_reaches_stderr(note_logger, logging.INFO)

    def test_level_below_the_handlers_does_not_reach(
        self, note_logger, capsys
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.WARNING)
        note_logger.addHandler(handler)
        assert not logging_reaches_stderr(note_logger, logging.INFO)
        assert logging_reaches_stderr(note_logger, logging.WARNING)

    def test_level_below_the_loggers_does_not_reach(
        self, note_logger, capsys
    ):
        note_logger.addHandler(logging.StreamHandler(sys.stderr))
        note_logger.setLevel(logging.WARNING)
        assert not logging_reaches_stderr(note_logger, logging.INFO)

    def test_handler_on_a_propagated_to_parent_reaches(
        self, note_logger, capsys
    ):
        parent = logging.getLogger("test_startup_note")
        saved = (list(parent.handlers), parent.level)
        try:
            parent.handlers = [logging.StreamHandler(sys.stderr)]
            note_logger.propagate = True
            assert logging_reaches_stderr(note_logger, logging.INFO)
        finally:
            parent.handlers, parent.level = saved

    def test_stream_replaced_after_the_handler_was_built(
        self, note_logger
    ):
        """A handler on a stream that is no longer sys.stderr does not
        count."""
        note_logger.addHandler(logging.StreamHandler(io.StringIO()))
        assert not logging_reaches_stderr(note_logger, logging.INFO)


class TestStartupNote:
    def test_stderr_handler_means_no_print(self, note_logger, capsys):
        note_logger.addHandler(logging.StreamHandler(sys.stderr))
        printed = []
        startup_note(
            note_logger,
            "hello %s",
            "x",
            printed="fallback",
            print_fn=printed.append,
        )
        assert printed == []
        # the logger itself wrote the line
        assert "hello x" in capsys.readouterr().err

    def test_file_handler_only_prints(self, note_logger, tmp_path):
        log = tmp_path / "x.log"
        note_logger.addHandler(logging.FileHandler(log))
        printed = []
        startup_note(
            note_logger, "hello %s", "x", print_fn=printed.append
        )
        assert printed == ["hello x"]
        assert "hello x" in log.read_text()

    def test_level_below_the_handler_prints(self, note_logger, capsys):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.WARNING)
        note_logger.addHandler(handler)
        printed = []
        startup_note(note_logger, "quiet", print_fn=printed.append)
        assert printed == ["quiet"]

    def test_printed_str_is_printed_as_given(
        self, note_logger, tmp_path
    ):
        note_logger.addHandler(logging.FileHandler(tmp_path / "x.log"))
        printed = []
        startup_note(
            note_logger,
            "Trac URL: %s",
            "http://t",
            printed="  Trac URL: http://t",
            print_fn=printed.append,
        )
        assert printed == ["  Trac URL: http://t"]

    def test_printed_list_is_one_line_each(self, note_logger, tmp_path):
        note_logger.addHandler(logging.FileHandler(tmp_path / "x.log"))
        printed = []
        startup_note(
            note_logger,
            "Failed: %s",
            "boom",
            level=logging.ERROR,
            printed=["ERROR: Failed.", "  boom", "  Check it."],
            print_fn=printed.append,
        )
        assert printed == ["ERROR: Failed.", "  boom", "  Check it."]

    def test_default_fallback_has_the_warning_prefix(
        self, note_logger, tmp_path
    ):
        note_logger.addHandler(logging.FileHandler(tmp_path / "x.log"))
        printed = []
        startup_note(
            note_logger,
            "odd %s",
            "thing",
            level=logging.WARNING,
            print_fn=printed.append,
        )
        startup_note(note_logger, "plain", print_fn=printed.append)
        assert printed == ["Warning: odd thing", "plain"]

    def test_default_print_goes_to_stderr(
        self, note_logger, tmp_path, capsys
    ):
        note_logger.addHandler(logging.FileHandler(tmp_path / "x.log"))
        startup_note(note_logger, "to stderr", printed=["a", "b"])
        captured = capsys.readouterr()
        assert captured.err == "a\nb\n"
        assert captured.out == ""

    def test_logs_even_when_it_prints(self, note_logger, tmp_path):
        log = tmp_path / "x.log"
        note_logger.addHandler(logging.FileHandler(log))
        startup_note(
            note_logger,
            "logged",
            printed="shown",
            print_fn=lambda t: None,
        )
        assert "logged" in log.read_text()


class TestReportVersion:
    """The version-mismatch warning in main() goes through one helper."""

    _MSG = "Version mismatch: runtime 1 vs source 2"
    _WARNING = f"⚠️  Warning: {_MSG}"

    @pytest.fixture
    def server_logger(self):
        lg = logging.getLogger("trac_mcp_server.mcp.server")
        saved = (lg.level, lg.propagate, list(lg.handlers))
        yield lg
        for handler in lg.handlers:
            if handler not in saved[2]:
                handler.close()
        lg.level, lg.propagate = saved[0], saved[1]
        lg.handlers = saved[2]

    def test_http_shape_warns_once(self, server_logger, capsys):
        server_logger.setLevel(logging.INFO)
        server_logger.addHandler(logging.StreamHandler(sys.stderr))
        _report_version(False, self._MSG)
        err = capsys.readouterr().err
        assert err.count(self._MSG) == 1
        assert "⚠️" not in err

    def test_stdio_file_shape_warns_once_on_stderr(
        self, server_logger, tmp_path, capsys
    ):
        server_logger.setLevel(logging.WARNING)
        log = tmp_path / "x.log"
        server_logger.addHandler(logging.FileHandler(log))
        _report_version(False, self._MSG)
        assert capsys.readouterr().err == self._WARNING + "\n"
        assert self._MSG in log.read_text()

    def test_consistent_version_prints_nothing(
        self, server_logger, tmp_path, capsys
    ):
        server_logger.setLevel(logging.INFO)
        server_logger.addHandler(
            logging.FileHandler(tmp_path / "x.log")
        )
        _report_version(True, "all good")
        assert capsys.readouterr().err == ""
