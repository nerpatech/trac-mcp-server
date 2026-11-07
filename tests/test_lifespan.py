"""Tests for trac_mcp_server.mcp.lifespan — server startup/shutdown lifecycle.

Tests the server_lifespan() async context manager which:
- Loads .env file, then YAML config files (as fallbacks), then env vars with CLI overrides
- Calls load_config() with unified precedence (CLI > env > YAML > defaults)
- Creates TracClient and validates connection
- Initializes concurrency semaphore
- Fails fast on config errors or connection failures
- Prints status messages to stderr
"""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from trac_mcp_server.config import Config
from trac_mcp_server.mcp.lifespan import server_lifespan

# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

# Shorthand patches used across many tests
# NOTE: After the bootstrap_config() extraction (Phase 20-01), load_dotenv,
# discover_config_files, load_hierarchical_config, and load_config live in
# trac_mcp_server.config_bootstrap (not lifespan). Patches target that module.
_NO_CONFIG_FILES = patch(
    "trac_mcp_server.config_bootstrap.discover_config_files",
    return_value=[],
)
_MOCK_DOTENV = patch("trac_mcp_server.config_bootstrap.load_dotenv")


def _make_config(**overrides):
    """Create a valid Config for testing."""
    defaults = {
        "trac_url": "https://trac.example.com/trac",
        "username": "testuser",
        "password": "testpass",
        "insecure": False,
        "debug": False,
        "max_parallel_requests": 5,
    }
    defaults.update(overrides)
    return Config(**defaults)


# -------------------------------------------------------------------------
# server_lifespan() — successful startup (env var path, no YAML)
# -------------------------------------------------------------------------


class TestServerLifespanSuccess:
    """Tests for the happy path through server_lifespan()."""

    async def test_successful_startup(self):
        mock_client = MagicMock()
        config = _make_config()

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.lifespan.init_semaphore"
            ) as mock_init_sem,
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            async with server_lifespan() as ctx:
                assert ctx["client"] is mock_client
                mock_run_sync.assert_called_once_with(
                    mock_client.validate_connection
                )
                mock_init_sem.assert_called_once_with(5)

    async def test_semaphore_uses_max_parallel_from_config(self):
        mock_client = MagicMock()
        config = _make_config(max_parallel_requests=12)

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.init_semaphore"
            ) as mock_init_sem,
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            async with server_lifespan() as _:
                mock_init_sem.assert_called_once_with(12)

    async def test_startup_with_config_overrides(self):
        mock_client = MagicMock()
        config = _make_config()
        overrides = {
            "url": "http://test",
            "username": "u",
            "password": "p",
            "insecure": True,
        }

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ) as mock_load,
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            async with server_lifespan(config_overrides=overrides) as _:
                mock_load.assert_called_once_with(
                    url="http://test",
                    username="u",
                    password="p",
                    insecure=True,
                    debug=False,
                    yaml_fallbacks=None,
                )

    async def test_startup_without_overrides_calls_load_config(self):
        mock_client = MagicMock()
        config = _make_config()

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ) as mock_load,
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            async with server_lifespan() as _:
                mock_load.assert_called_once_with(
                    url=None,
                    username=None,
                    password=None,
                    insecure=False,
                    debug=False,
                    yaml_fallbacks=None,
                )


# -------------------------------------------------------------------------
# server_lifespan() — YAML config file path (unified flow)
# -------------------------------------------------------------------------


class TestServerLifespanYamlConfig:
    """Tests for loading config from .trac_mcp/config.yml via unified flow."""

    async def test_yaml_config_passes_fallbacks_to_load_config(
        self, tmp_path
    ):
        """When YAML file exists, load_config receives yaml_fallbacks dict."""
        config_dir = tmp_path / ".trac_mcp"
        config_dir.mkdir()
        config_file = config_dir / "config.yml"
        config_file.write_text(
            "trac:\n"
            "  url: https://yaml-trac.example.com\n"
            "  username: yamluser\n"
            "  password: yamlpass\n"
        )

        mock_client = MagicMock()
        config = _make_config()

        with (
            _MOCK_DOTENV,
            patch(
                "trac_mcp_server.config_bootstrap.discover_config_files",
                return_value=[config_file],
            ),
            patch(
                "trac_mcp_server.config_bootstrap.load_hierarchical_config",
                return_value={
                    "trac": {
                        "url": "https://yaml-trac.example.com",
                        "username": "yamluser",
                        "password": "yamlpass",
                    }
                },
            ),
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ) as mock_load,
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            async with server_lifespan() as ctx:
                assert ctx["client"] is mock_client

            # Verify load_config received yaml_fallbacks with non-None values
            call_kwargs = mock_load.call_args.kwargs
            fb = call_kwargs["yaml_fallbacks"]
            assert fb["url"] == "https://yaml-trac.example.com"
            assert fb["username"] == "yamluser"
            assert fb["password"] == "yamlpass"

    async def test_yaml_config_values_used(self, tmp_path):
        """Verify that values from YAML config end up in the Config object."""
        config_dir = tmp_path / ".trac_mcp"
        config_dir.mkdir()
        config_file = config_dir / "config.yml"
        config_file.write_text(
            "trac:\n"
            "  url: https://yaml-trac.example.com\n"
            "  username: yamluser\n"
            "  password: yamlpass\n"
            "  max_parallel_requests: 3\n"
        )

        mock_client = MagicMock()
        captured_config = {}

        def capture_client(config):
            captured_config["config"] = config
            return mock_client

        config = _make_config(
            trac_url="https://yaml-trac.example.com",
            username="yamluser",
            password="yamlpass",
            max_parallel_requests=3,
        )

        with (
            _MOCK_DOTENV,
            patch(
                "trac_mcp_server.config_bootstrap.discover_config_files",
                return_value=[config_file],
            ),
            patch(
                "trac_mcp_server.config_bootstrap.load_hierarchical_config",
                return_value={
                    "trac": {
                        "url": "https://yaml-trac.example.com",
                        "username": "yamluser",
                        "password": "yamlpass",
                        "max_parallel_requests": 3,
                    }
                },
            ),
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                side_effect=capture_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            async with server_lifespan() as _:
                pass

        config = captured_config["config"]
        assert config.trac_url == "https://yaml-trac.example.com"
        assert config.username == "yamluser"
        assert config.password == "yamlpass"
        assert config.max_parallel_requests == 3

    async def test_yaml_config_with_cli_overrides(self, tmp_path):
        """CLI overrides take precedence over YAML config values."""
        config_dir = tmp_path / ".trac_mcp"
        config_dir.mkdir()
        config_file = config_dir / "config.yml"
        config_file.write_text(
            "trac:\n"
            "  url: https://yaml-trac.example.com\n"
            "  username: yamluser\n"
            "  password: yamlpass\n"
        )

        mock_client = MagicMock()
        config = _make_config(
            trac_url="https://cli-override.example.com"
        )
        overrides = {"url": "https://cli-override.example.com"}

        with (
            _MOCK_DOTENV,
            patch(
                "trac_mcp_server.config_bootstrap.discover_config_files",
                return_value=[config_file],
            ),
            patch(
                "trac_mcp_server.config_bootstrap.load_hierarchical_config",
                return_value={
                    "trac": {
                        "url": "https://yaml-trac.example.com",
                        "username": "yamluser",
                        "password": "yamlpass",
                    }
                },
            ),
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ) as mock_load,
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            async with server_lifespan(config_overrides=overrides) as _:
                pass

        # CLI override passed to load_config
        call_kwargs = mock_load.call_args.kwargs
        assert call_kwargs["url"] == "https://cli-override.example.com"
        # YAML fallbacks also passed
        assert (
            call_kwargs["yaml_fallbacks"]["url"]
            == "https://yaml-trac.example.com"
        )

    async def test_yaml_config_stderr_mentions_config_file(
        self, tmp_path
    ):
        """Stderr output should mention the config file path."""
        config_dir = tmp_path / ".trac_mcp"
        config_dir.mkdir()
        config_file = config_dir / "config.yml"
        config_file.write_text(
            "trac:\n"
            "  url: https://yaml-trac.example.com\n"
            "  username: yamluser\n"
            "  password: yamlpass\n"
        )

        mock_client = MagicMock()
        config = _make_config()
        stderr_messages = []

        with (
            _MOCK_DOTENV,
            patch(
                "trac_mcp_server.config_bootstrap.discover_config_files",
                return_value=[config_file],
            ),
            patch(
                "trac_mcp_server.config_bootstrap.load_hierarchical_config",
                return_value={
                    "trac": {
                        "url": "https://yaml-trac.example.com",
                        "username": "yamluser",
                        "password": "yamlpass",
                    }
                },
            ),
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
            patch(
                "trac_mcp_server.mcp.lifespan._stderr_print",
                side_effect=lambda msg: stderr_messages.append(msg),
            ),
        ):
            async with server_lifespan() as _:
                pass

        full_output = "\n".join(stderr_messages)
        assert "config file" in full_output.lower()
        assert str(config_file) in full_output

    async def test_env_var_path_when_no_config_files(self):
        """When no YAML config files exist, load_config gets yaml_fallbacks=None."""
        mock_client = MagicMock()
        config = _make_config()
        stderr_messages = []

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ) as mock_load,
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
            patch(
                "trac_mcp_server.mcp.lifespan._stderr_print",
                side_effect=lambda msg: stderr_messages.append(msg),
            ),
        ):
            async with server_lifespan() as _:
                call_kwargs = mock_load.call_args.kwargs
                assert call_kwargs["yaml_fallbacks"] is None

        full_output = "\n".join(stderr_messages)
        assert "environment variables" in full_output.lower()

    async def test_yaml_fallbacks_exclude_none_values(self, tmp_path):
        """YAML fallbacks dict only contains non-None values from trac section."""
        config_dir = tmp_path / ".trac_mcp"
        config_dir.mkdir()
        config_file = config_dir / "config.yml"
        config_file.write_text(
            "trac:\n  url: https://yaml.example.com\n"
        )

        mock_client = MagicMock()
        config = _make_config()

        with (
            _MOCK_DOTENV,
            patch(
                "trac_mcp_server.config_bootstrap.discover_config_files",
                return_value=[config_file],
            ),
            patch(
                "trac_mcp_server.config_bootstrap.load_hierarchical_config",
                return_value={
                    "trac": {"url": "https://yaml.example.com"}
                },
            ),
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ) as mock_load,
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            async with server_lifespan() as _:
                pass

        fb = mock_load.call_args.kwargs["yaml_fallbacks"]
        # url should be present
        assert fb["url"] == "https://yaml.example.com"
        # None-valued fields (username, password) should be excluded
        assert "username" not in fb
        assert "password" not in fb
        # Default-valued fields (insecure=False, etc.) ARE included since they're non-None
        assert "insecure" in fb


# -------------------------------------------------------------------------
# server_lifespan() — config error path
# -------------------------------------------------------------------------


class TestServerLifespanConfigError:
    """Tests for config validation failures in server_lifespan()."""

    async def test_config_error_raises_runtime_error(self):
        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                side_effect=ValueError("Trac URL not found"),
            ),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            with pytest.raises(
                RuntimeError, match="Configuration error"
            ):
                async with server_lifespan() as _:
                    pass  # pragma: no cover -- should not reach here

    async def test_config_error_includes_original_message(self):
        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                side_effect=ValueError("Trac URL not found"),
            ),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            with pytest.raises(
                RuntimeError, match="Trac URL not found"
            ):
                async with server_lifespan() as _:
                    pass  # pragma: no cover

    async def test_config_error_stderr_messages(self):
        stderr_messages = []

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                side_effect=ValueError("missing URL"),
            ),
            patch(
                "trac_mcp_server.mcp.lifespan._stderr_print",
                side_effect=lambda msg: stderr_messages.append(msg),
            ),
        ):
            with pytest.raises(RuntimeError):
                async with server_lifespan() as _:
                    pass  # pragma: no cover

        # Should print startup message, then error messages
        assert any("starting" in m.lower() for m in stderr_messages)
        assert any("Configuration error" in m for m in stderr_messages)
        assert any("TRAC_URL" in m for m in stderr_messages)


# -------------------------------------------------------------------------
# server_lifespan() — connection error path
# -------------------------------------------------------------------------


class TestServerLifespanConnectionError:
    """Tests for Trac connection failures in server_lifespan()."""

    async def test_connection_error_raises_runtime_error(self):
        config = _make_config()
        mock_client = MagicMock()

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                side_effect=ConnectionError("Connection refused"),
            ),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            with pytest.raises(RuntimeError, match="connection failed"):
                async with server_lifespan() as _:
                    pass  # pragma: no cover

    async def test_connection_error_includes_original_message(self):
        config = _make_config()
        mock_client = MagicMock()

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                side_effect=ConnectionError("Connection refused"),
            ),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            with pytest.raises(
                RuntimeError, match="Connection refused"
            ):
                async with server_lifespan() as _:
                    pass  # pragma: no cover

    async def test_connection_error_stderr_messages(self):
        config = _make_config()
        mock_client = MagicMock()
        stderr_messages = []

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                side_effect=ConnectionError("Connection refused"),
            ),
            patch(
                "trac_mcp_server.mcp.lifespan._stderr_print",
                side_effect=lambda msg: stderr_messages.append(msg),
            ),
        ):
            with pytest.raises(RuntimeError):
                async with server_lifespan() as _:
                    pass  # pragma: no cover

        assert any(
            "connection failed" in m.lower() for m in stderr_messages
        )
        assert any("Connection refused" in m for m in stderr_messages)
        assert any("TRAC_URL" in m for m in stderr_messages)

    async def test_generic_exception_also_caught(self):
        """Non-ConnectionError exceptions from validate_connection are also caught."""
        config = _make_config()
        mock_client = MagicMock()

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                side_effect=Exception("Unexpected XML-RPC fault"),
            ),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            with pytest.raises(RuntimeError, match="connection failed"):
                async with server_lifespan() as _:
                    pass  # pragma: no cover


# -------------------------------------------------------------------------
# server_lifespan() — shutdown path
# -------------------------------------------------------------------------


class TestServerLifespanShutdown:
    """Tests for the shutdown (exit) path of server_lifespan()."""

    async def test_shutdown_completes_without_error(self):
        config = _make_config()
        mock_client = MagicMock()

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
            patch("trac_mcp_server.mcp.lifespan._stderr_print"),
        ):
            async with server_lifespan() as _:
                pass  # enter and exit cleanly

    async def test_shutdown_prints_message(self):
        config = _make_config()
        mock_client = MagicMock()
        stderr_messages = []

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
            patch(
                "trac_mcp_server.mcp.lifespan._stderr_print",
                side_effect=lambda msg: stderr_messages.append(msg),
            ),
        ):
            async with server_lifespan() as _:
                pass  # enter and exit

        assert any(
            "shutting down" in m.lower() for m in stderr_messages
        )

    async def test_success_stderr_messages_full_sequence(self):
        """Verify the full sequence of stderr messages on successful startup."""
        config = _make_config()
        mock_client = MagicMock()
        stderr_messages = []

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                return_value=config,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.TracClient",
                return_value=mock_client,
            ),
            patch(
                "trac_mcp_server.mcp.lifespan.run_sync",
                return_value="1.3.5",
            ),
            patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
            patch(
                "trac_mcp_server.mcp.lifespan._stderr_print",
                side_effect=lambda msg: stderr_messages.append(msg),
            ),
        ):
            async with server_lifespan() as _:
                pass

        # Verify key startup messages in order
        full_output = "\n".join(stderr_messages)
        assert "starting" in full_output.lower()
        assert "Configuration loaded" in full_output
        assert "Connected to Trac API version" in full_output
        assert "1.3.5" in full_output
        assert "Server ready" in full_output
        assert "shutting down" in full_output.lower()


# -------------------------------------------------------------------------
# Startup lines print once per transport shape (Trac #167)
# -------------------------------------------------------------------------

_PKG_LOGGER = "trac_mcp_server"


@pytest.fixture
def pkg_logger():
    """The ``trac_mcp_server`` logger with its state saved and restored."""
    lg = logging.getLogger(_PKG_LOGGER)
    saved = (lg.level, lg.propagate, list(lg.handlers))
    yield lg
    for handler in lg.handlers:
        if handler not in saved[2]:
            handler.close()
    lg.level, lg.propagate = saved[0], saved[1]
    lg.handlers = saved[2]


async def _run_lifespan(config=None, run_sync_effect=None):
    """Run server_lifespan() with the Trac side mocked and
    ``_stderr_print`` left real (it writes to the current sys.stderr)."""
    config = config or _make_config()
    with (
        _MOCK_DOTENV,
        _NO_CONFIG_FILES,
        patch(
            "trac_mcp_server.config_bootstrap.load_config",
            return_value=config,
        ),
        patch(
            "trac_mcp_server.mcp.lifespan.TracClient",
            return_value=MagicMock(),
        ),
        patch(
            "trac_mcp_server.mcp.lifespan.run_sync",
            return_value="1.3.5",
            side_effect=run_sync_effect,
        ),
        patch("trac_mcp_server.mcp.lifespan.init_semaphore"),
    ):
        async with server_lifespan() as _:
            pass


_STARTUP_LINES = [
    "starting",
    "Configuration loaded from",
    "Trac URL",
    "Validating",
    "Connected to Trac API version",
    "Parallel requests",
    "Configured instances",
    "Server ready",
    "shutting down",
]


class TestStartupLinesPrintOnce:
    async def test_http_shape_shows_each_line_once(
        self, pkg_logger, capsys
    ):
        """A stderr handler at INFO (what http mode installs) shows every
        startup line; the lifespan must not print each a second time."""
        pkg_logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(message)s"
            )
        )
        pkg_logger.addHandler(handler)

        await _run_lifespan()

        err = capsys.readouterr().err.lower()
        for fragment in _STARTUP_LINES:
            assert err.count(fragment.lower()) == 1, (
                f"{fragment!r} appears {err.count(fragment.lower())} "
                f"times in:\n{err}"
            )
        # every line is the timestamped log line, none is the raw print
        assert all(line.startswith("[") for line in err.splitlines())

    async def test_stdio_file_shape_prints_todays_sequence(
        self, pkg_logger, tmp_path, capsys
    ):
        """A file handler at WARNING (stdio) leaves stderr to the prints,
        which keep their wording and order byte for byte."""
        pkg_logger.setLevel(logging.WARNING)
        log = tmp_path / "server.log"
        pkg_logger.addHandler(logging.FileHandler(log))

        await _run_lifespan()

        assert capsys.readouterr().err == (
            "Trac MCP Server starting...\n"
            "  Configuration loaded from: environment variables\n"
            "  Trac URL: https://trac.example.com/trac\n"
            "  Validating Trac connection...\n"
            "  Connected to Trac API version 1.3.5\n"
            "  Parallel requests: 5\n"
            "  Configured instances: default only\n"
            "Server ready. Waiting for MCP client connection...\n"
            "Trac MCP Server shutting down.\n"
        )

    async def test_http_shape_connection_failure_shows_once(
        self, pkg_logger, capsys
    ):
        pkg_logger.setLevel(logging.INFO)
        pkg_logger.addHandler(logging.StreamHandler(sys.stderr))

        with pytest.raises(RuntimeError):
            await _run_lifespan(
                run_sync_effect=ConnectionError("Connection refused")
            )

        err = capsys.readouterr().err
        assert err.count("Connection refused") == 1
        assert err.count("Failed to connect to Trac") == 1
        assert "ERROR: Trac connection failed." not in err
        assert err.count("Check TRAC_URL") == 1

    async def test_stdio_file_shape_connection_failure_lines(
        self, pkg_logger, tmp_path, capsys
    ):
        pkg_logger.setLevel(logging.WARNING)
        pkg_logger.addHandler(logging.FileHandler(tmp_path / "s.log"))

        with pytest.raises(RuntimeError):
            await _run_lifespan(
                run_sync_effect=ConnectionError("Connection refused")
            )

        assert capsys.readouterr().err == (
            "Trac MCP Server starting...\n"
            "  Configuration loaded from: environment variables\n"
            "  Trac URL: https://trac.example.com/trac\n"
            "  Validating Trac connection...\n"
            "ERROR: Trac connection failed.\n"
            "  Connection refused\n"
            "  Check TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD.\n"
        )

    async def test_http_shape_config_error_shows_once(
        self, pkg_logger, capsys
    ):
        pkg_logger.setLevel(logging.INFO)
        pkg_logger.addHandler(logging.StreamHandler(sys.stderr))

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                side_effect=ValueError("missing URL"),
            ),
        ):
            with pytest.raises(RuntimeError):
                async with server_lifespan() as _:
                    pass  # pragma: no cover

        err = capsys.readouterr().err
        assert err.count("Configuration error: missing URL") == 1
        assert err.count("Ensure TRAC_URL") == 1

    async def test_stdio_file_shape_config_error_lines(
        self, pkg_logger, tmp_path, capsys
    ):
        pkg_logger.setLevel(logging.WARNING)
        pkg_logger.addHandler(logging.FileHandler(tmp_path / "s.log"))

        with (
            _MOCK_DOTENV,
            _NO_CONFIG_FILES,
            patch(
                "trac_mcp_server.config_bootstrap.load_config",
                side_effect=ValueError("missing URL"),
            ),
        ):
            with pytest.raises(RuntimeError):
                async with server_lifespan() as _:
                    pass  # pragma: no cover

        assert capsys.readouterr().err == (
            "Trac MCP Server starting...\n"
            "ERROR: Configuration error: missing URL\n"
            "  Ensure TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD are set.\n"
        )
