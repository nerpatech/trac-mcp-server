"""Comprehensive tests for unified config schema and adapter functions.

Tests all Pydantic models in config_schema.py (UnifiedConfig, TracConfig,
LoggingConfig), the build_config() factory, and the to_legacy_config()
adapter function for backward compatibility.
"""

import pytest
from pydantic import ValidationError

from trac_mcp_server.config_schema import (
    InstanceConfig,
    LoggingConfig,
    ServerConfig,
    TracConfig,
    UnifiedConfig,
    build_config,
    to_legacy_config,
)

# ---------------------------------------------------------------------------
# UnifiedConfig tests
# ---------------------------------------------------------------------------


class TestUnifiedConfig:
    """Tests for the top-level UnifiedConfig model."""

    def test_empty_dict_produces_valid_defaults(self):
        """Empty dict produces valid UnifiedConfig with all defaults."""
        config = UnifiedConfig()
        assert config.trac is not None
        assert config.trac.url is None
        assert config.trac.insecure is False
        assert config.logging is not None
        assert config.logging.level == "INFO"
        assert config.server is not None
        assert config.server.transport == "stdio"

    def test_full_config_with_all_sections(self):
        """Full config with all sections parses correctly."""
        config = UnifiedConfig(
            trac=TracConfig(
                url="https://trac.example.com",
                username="admin",
                password="secret",
                insecure=True,
                debug=True,
            ),
            logging=LoggingConfig(level="DEBUG", file="/tmp/trac.log"),
        )
        assert config.trac.url == "https://trac.example.com"
        assert config.trac.insecure is True
        assert config.logging.level == "DEBUG"
        assert config.logging.file == "/tmp/trac.log"

    def test_unknown_sections_ignored(self):
        """Unknown sections are ignored (forward compatibility)."""
        # Pydantic by default ignores extra fields (model_config does not set
        # extra='forbid'), so extra keys should be silently ignored.
        raw: dict[str, object] = {
            "trac": {"url": "https://trac.example.com"},
            "future_section": {"key": "value"},
        }
        config = UnifiedConfig(**raw)  # type: ignore[arg-type]
        assert config.trac.url == "https://trac.example.com"
        # No attribute for future_section (ignored)
        assert not hasattr(config, "future_section")

    def test_frozen_model_prevents_mutation(self):
        """Frozen model prevents mutation."""
        config = UnifiedConfig()
        with pytest.raises(ValidationError):
            config.trac = TracConfig(url="https://changed.example.com")


# ---------------------------------------------------------------------------
# TracConfig tests
# ---------------------------------------------------------------------------


class TestTracConfig:
    """Tests for TracConfig section model."""

    def test_all_fields_optional_zero_config(self):
        """All fields optional (zero-config)."""
        config = TracConfig()
        assert config.url is None
        assert config.username is None
        assert config.password is None
        assert config.insecure is False
        assert config.debug is False

    def test_url_accepts_valid_string(self):
        """URL accepts any string value (validation happens at load_config level)."""
        config = TracConfig(url="https://trac.example.com")
        assert config.url == "https://trac.example.com"

    def test_boolean_fields_accept_true_false(self):
        """Boolean fields accept true/false."""
        config = TracConfig(insecure=True, debug=True)
        assert config.insecure is True
        assert config.debug is True

        config2 = TracConfig(insecure=False, debug=False)
        assert config2.insecure is False
        assert config2.debug is False

    def test_frozen_model(self):
        """TracConfig is frozen (immutable)."""
        config = TracConfig(url="https://trac.example.com")
        with pytest.raises(ValidationError):
            config.url = "https://changed.example.com"


# ---------------------------------------------------------------------------
# ServerConfig tests
# ---------------------------------------------------------------------------


class TestServerConfig:
    """Tests for ServerConfig section model."""

    def test_defaults(self):
        """Defaults are stdio transport, loopback host, port 8080, /mcp path."""
        config = ServerConfig()
        assert config.transport == "stdio"
        assert config.host == "127.0.0.1"
        assert config.port == 8080
        assert config.path == "/mcp"
        assert config.auth_token is None
        assert config.allow_unauthenticated is False
        assert config.allowed_hosts == []
        assert config.allowed_origins == []

    def test_transport_rejects_invalid_value(self):
        """transport only accepts 'stdio' or 'http'."""
        with pytest.raises(ValidationError):
            ServerConfig(transport="sse")  # type: ignore[arg-type]

    def test_port_out_of_range_rejected(self):
        """port must be between 1 and 65535."""
        with pytest.raises(ValidationError):
            ServerConfig(port=0)
        with pytest.raises(ValidationError):
            ServerConfig(port=65536)

    def test_custom_values_accepted(self):
        """Custom values round-trip through the model."""
        config = ServerConfig(
            transport="http",
            host="0.0.0.0",
            port=9090,
            path="/api/mcp",
            auth_token="secret",
            allow_unauthenticated=True,
            allowed_hosts=["example.com:*"],
            allowed_origins=["https://example.com"],
        )
        assert config.transport == "http"
        assert config.host == "0.0.0.0"
        assert config.port == 9090
        assert config.path == "/api/mcp"
        assert config.auth_token == "secret"
        assert config.allow_unauthenticated is True
        assert config.allowed_hosts == ["example.com:*"]
        assert config.allowed_origins == ["https://example.com"]

    def test_frozen_model(self):
        """ServerConfig is frozen (immutable)."""
        config = ServerConfig()
        with pytest.raises(ValidationError):
            config.port = 9999


# ---------------------------------------------------------------------------
# LoggingConfig tests
# ---------------------------------------------------------------------------


class TestLoggingConfig:
    """Tests for LoggingConfig section model."""

    def test_defaults(self):
        """Default values are INFO level, no file."""
        config = LoggingConfig()
        assert config.level == "INFO"
        assert config.file is None

    def test_custom_values(self):
        """Custom values are accepted."""
        config = LoggingConfig(level="DEBUG", file="/tmp/trac.log")
        assert config.level == "DEBUG"
        assert config.file == "/tmp/trac.log"

    def test_frozen_model(self):
        """LoggingConfig is frozen (immutable)."""
        config = LoggingConfig()
        with pytest.raises(ValidationError):
            config.level = "DEBUG"


# ---------------------------------------------------------------------------
# Adapter tests: to_legacy_config
# ---------------------------------------------------------------------------


class TestToLegacyConfig:
    """Tests for the to_legacy_config() adapter function."""

    def test_full_unified_config_produces_valid_legacy_config(self):
        """to_legacy_config with full unified config produces valid Config."""
        from trac_mcp_server.config import Config

        unified = UnifiedConfig(
            trac=TracConfig(
                url="https://trac.example.com",
                username="admin",
                password="secret",
                insecure=True,
                debug=True,
            ),
        )
        legacy = to_legacy_config(unified)
        assert isinstance(legacy, Config)
        assert legacy.trac_url == "https://trac.example.com"
        assert legacy.username == "admin"
        assert legacy.password == "secret"
        assert legacy.insecure is True
        assert legacy.debug is True

    def test_cli_overrides_win_over_config(self):
        """CLI overrides win over config values."""
        unified = UnifiedConfig(
            trac=TracConfig(
                url="https://config.example.com",
                username="config-user",
                password="config-pass",
            ),
        )
        legacy = to_legacy_config(
            unified,
            cli_overrides={
                "url": "https://cli.example.com",
                "username": "cli-user",
                "password": "cli-pass",
            },
        )
        assert legacy.trac_url == "https://cli.example.com"
        assert legacy.username == "cli-user"
        assert legacy.password == "cli-pass"

    def test_env_var_overrides_win_over_config(self):
        """Env vars (interpolated into config values) win when present."""
        # This tests that the adapter uses the values already in the unified
        # config. Env var interpolation happens at the loader level, not here.
        # The adapter just passes through whatever is in the unified config.
        unified = UnifiedConfig(
            trac=TracConfig(
                url="https://from-env.example.com",
                username="env-user",
                password="env-pass",
            ),
        )
        legacy = to_legacy_config(unified)
        assert legacy.trac_url == "https://from-env.example.com"
        assert legacy.username == "env-user"
        assert legacy.password == "env-pass"

    def test_zero_config_with_no_cli_overrides(self):
        """Zero-config with no CLI overrides produces Config with empty strings."""
        from trac_mcp_server.config import Config

        unified = UnifiedConfig()
        legacy = to_legacy_config(unified)
        assert isinstance(legacy, Config)
        assert legacy.trac_url == ""
        assert legacy.username == ""
        assert legacy.password == ""
        assert legacy.insecure is False
        assert legacy.debug is False

    def test_partial_cli_overrides(self):
        """Partial CLI overrides: only provided keys override config."""
        unified = UnifiedConfig(
            trac=TracConfig(
                url="https://config.example.com",
                username="config-user",
                password="config-pass",
            ),
        )
        legacy = to_legacy_config(
            unified,
            cli_overrides={"url": "https://cli.example.com"},
        )
        assert legacy.trac_url == "https://cli.example.com"
        assert legacy.username == "config-user"
        assert legacy.password == "config-pass"

    def test_insecure_override_from_cli(self):
        """CLI insecure=True overrides config insecure=False."""
        unified = UnifiedConfig(
            trac=TracConfig(insecure=False),
        )
        legacy = to_legacy_config(
            unified,
            cli_overrides={"insecure": True},
        )
        assert legacy.insecure is True


# ---------------------------------------------------------------------------
# build_config factory tests
# ---------------------------------------------------------------------------


class TestBuildConfig:
    """Tests for the build_config() factory function."""

    def test_empty_dict_returns_defaults(self):
        """build_config from empty dict returns UnifiedConfig with defaults."""
        config = build_config({})
        assert isinstance(config, UnifiedConfig)
        assert config.trac.url is None

    def test_partial_sections_fill_defaults(self):
        """build_config with partial sections fills in defaults for missing."""
        config = build_config(
            {
                "trac": {"url": "https://trac.example.com"},
            }
        )
        assert config.trac.url == "https://trac.example.com"
        # Other trac fields get defaults
        assert config.trac.username is None
        assert config.trac.insecure is False
        # Other sections get defaults
        assert config.logging.level == "INFO"

    def test_full_raw_dict(self):
        """build_config from full raw dict produces fully populated config."""
        raw = {
            "trac": {
                "url": "https://trac.example.com",
                "username": "admin",
                "password": "secret",
            },
            "logging": {"level": "DEBUG"},
        }
        config = build_config(raw)
        assert config.trac.url == "https://trac.example.com"
        assert config.logging.level == "DEBUG"

    def test_none_like_empty(self):
        """build_config from falsy dict returns defaults (covers the `if not raw_data` branch)."""
        # An empty dict is falsy, triggering the early return
        config = build_config({})
        assert isinstance(config, UnifiedConfig)


class TestInstanceConfig:
    """Tests for the InstanceConfig model and UnifiedConfig.instances."""

    def test_absent_section_defaults_to_empty_dict(self):
        """No instances: key in raw config -> instances is {}."""
        config = build_config(
            {"trac": {"url": "https://trac.example.com"}}
        )
        assert config.instances == {}

    def test_empty_unified_config_has_empty_instances(self):
        config = UnifiedConfig()
        assert config.instances == {}

    def test_url_only_instance(self):
        config = build_config({"instances": {"bcs": {"url": "/bcs"}}})
        assert config.instances["bcs"].url == "/bcs"
        assert config.instances["bcs"].username is None
        assert config.instances["bcs"].password is None
        assert config.instances["bcs"].insecure is None

    def test_full_instance_fields(self):
        config = build_config(
            {
                "instances": {
                    "bcs": {
                        "url": "https://trac.example.org/bcs",
                        "username": "bcsuser",
                        "password": "bcspass",
                        "insecure": True,
                    }
                }
            }
        )
        inst = config.instances["bcs"]
        assert inst.url == "https://trac.example.org/bcs"
        assert inst.username == "bcsuser"
        assert inst.password == "bcspass"
        assert inst.insecure is True

    def test_multiple_instances(self):
        config = build_config(
            {
                "instances": {
                    "bcs": {"url": "/bcs"},
                    "auto_pm": {"url": "/auto_pm"},
                }
            }
        )
        assert set(config.instances) == {"bcs", "auto_pm"}

    def test_instance_config_is_frozen(self):
        inst = InstanceConfig(url="/bcs")
        with pytest.raises(ValidationError):
            inst.url = "/other"  # type: ignore[misc]

    def test_missing_url_raises(self):
        with pytest.raises(ValidationError):
            InstanceConfig()  # type: ignore[call-arg]
