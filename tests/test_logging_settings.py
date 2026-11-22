"""resolve_logging_settings(): the logging settings that used to reach
nothing (Trac /trac_mcp_server #277, M15).

TRAC_DEBUG / YAML ``trac.debug`` and YAML ``logging.level`` /
``logging.file`` were parsed and never read. The resolver reads the RAW
``logging`` mapping, so a key counts only when the file sets it:
``LoggingConfig``'s ``level="INFO"`` default must not override stdio's
WARNING default.
"""

import pytest

from trac_mcp_server.config_bootstrap import (
    LoggingSettings,
    resolve_logging_settings,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch, isolated_config):
    for var in ("TRAC_DEBUG", "LOG_LEVEL", "LOG_FILE"):
        monkeypatch.delenv(var, raising=False)


def _yaml(isolated_config, text):
    d = isolated_config / ".trac_mcp"
    d.mkdir(exist_ok=True)
    (d / "config.yml").write_text(text)


def test_defaults_are_the_mode_defaults():
    assert resolve_logging_settings(None) == LoggingSettings(
        debug=False, level=None, log_file=None
    )


class TestDebug:
    def test_env_true(self, monkeypatch):
        monkeypatch.setenv("TRAC_DEBUG", "true")
        assert resolve_logging_settings(None).debug is True

    @pytest.mark.parametrize("raw", ["1", "yes", "on", "TRUE"])
    def test_env_truthy_spellings_match_load_config(
        self, monkeypatch, raw
    ):
        monkeypatch.setenv("TRAC_DEBUG", raw)
        assert resolve_logging_settings(None).debug is True

    def test_yaml_true(self, isolated_config):
        _yaml(isolated_config, "trac:\n  debug: true\n")
        assert resolve_logging_settings(None).debug is True

    def test_env_false_beats_yaml_true(
        self, isolated_config, monkeypatch
    ):
        _yaml(isolated_config, "trac:\n  debug: true\n")
        monkeypatch.setenv("TRAC_DEBUG", "false")
        assert resolve_logging_settings(None).debug is False


class TestLevel:
    def test_yaml_level(self, isolated_config):
        _yaml(isolated_config, "logging:\n  level: error\n")
        assert resolve_logging_settings(None).level == "ERROR"

    def test_yaml_without_a_level_key_leaves_the_mode_default(
        self, isolated_config
    ):
        _yaml(isolated_config, "logging:\n  file: /x.log\n")
        assert resolve_logging_settings(None).level is None

    def test_yaml_without_a_logging_section(self, isolated_config):
        _yaml(isolated_config, "trac:\n  debug: false\n")
        assert resolve_logging_settings(None).level is None

    def test_env_beats_yaml(self, isolated_config, monkeypatch):
        _yaml(isolated_config, "logging:\n  level: ERROR\n")
        monkeypatch.setenv("LOG_LEVEL", "debug")
        assert resolve_logging_settings(None).level == "DEBUG"

    def test_blank_env_falls_through_to_yaml(
        self, isolated_config, monkeypatch
    ):
        _yaml(isolated_config, "logging:\n  level: ERROR\n")
        monkeypatch.setenv("LOG_LEVEL", "  ")
        assert resolve_logging_settings(None).level == "ERROR"

    def test_blank_env_without_yaml_is_none(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "")
        assert resolve_logging_settings(None).level is None

    def test_unknown_env_level_names_the_source_and_valid_names(
        self, monkeypatch
    ):
        monkeypatch.setenv("LOG_LEVEL", "LOUD")
        with pytest.raises(ValueError) as exc:
            resolve_logging_settings(None)
        text = str(exc.value)
        assert "LOG_LEVEL" in text and "LOUD" in text
        for name in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            assert name in text

    def test_unknown_yaml_level_names_the_source(self, isolated_config):
        _yaml(isolated_config, "logging:\n  level: loud\n")
        with pytest.raises(ValueError) as exc:
            resolve_logging_settings(None)
        text = str(exc.value)
        assert "logging.level" in text and "loud" in text
        assert "CRITICAL" in text


class TestLogFile:
    def test_yaml_file(self, isolated_config):
        _yaml(isolated_config, "logging:\n  file: /y.log\n")
        assert resolve_logging_settings(None).log_file == "/y.log"

    def test_yaml_null_file_is_the_mode_default(self, isolated_config):
        _yaml(isolated_config, "logging:\n  file: null\n")
        assert resolve_logging_settings(None).log_file is None

    def test_env_beats_yaml(self, isolated_config, monkeypatch):
        _yaml(isolated_config, "logging:\n  file: /y.log\n")
        monkeypatch.setenv("LOG_FILE", "/e.log")
        assert resolve_logging_settings(None).log_file == "/e.log"

    def test_empty_env_beats_yaml_and_means_no_file(
        self, isolated_config, monkeypatch
    ):
        _yaml(isolated_config, "logging:\n  file: /y.log\n")
        monkeypatch.setenv("LOG_FILE", "")
        assert resolve_logging_settings(None).log_file == ""

    def test_override_beats_env_and_yaml(
        self, isolated_config, monkeypatch
    ):
        _yaml(isolated_config, "logging:\n  file: /y.log\n")
        monkeypatch.setenv("LOG_FILE", "/e.log")
        assert resolve_logging_settings("/o.log").log_file == "/o.log"

    def test_empty_override_beats_env(self, monkeypatch):
        monkeypatch.setenv("LOG_FILE", "/e.log")
        assert resolve_logging_settings("").log_file == ""
