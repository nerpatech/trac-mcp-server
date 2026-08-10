"""Tests for trac_mcp_server.instances -- multi-instance resolution.

Covers InstanceRegistry.resolve() (default / named / ad-hoc path / ad-hoc
same-host absolute / cross-host rejection / unknown name), credential
inheritance, client cache identity, describe() never leaking passwords,
load_declared_instances() merge precedence, and mtime-based live reload.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trac_mcp_server.config import Config
from trac_mcp_server.instances import (
    InstanceRegistry,
    InstanceSpec,
    UnknownInstanceError,
    load_declared_instances,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_config(**overrides) -> Config:
    defaults = dict(
        trac_url="http://192.168.10.4:8000/trac_mcp_server",
        username="agent_rpc",
        password="secret",
        insecure=False,
    )
    defaults.update(overrides)
    return Config(**defaults)


@pytest.fixture(autouse=True)
def no_real_config_files():
    """Prevent InstanceRegistry from discovering the real repo config file.

    ``instances.py`` binds ``discover_config_files`` directly (not through
    ``config_bootstrap``), so it isn't covered by the patches other test
    modules use -- patch it here instead.
    """
    with patch(
        "trac_mcp_server.instances.discover_config_files",
        return_value=[],
    ):
        yield


# ---------------------------------------------------------------------------
# resolve() -- default
# ---------------------------------------------------------------------------


class TestResolveDefault:
    def test_none_returns_default(self):
        default = _default_config()
        registry = InstanceRegistry(default, {})
        assert registry.resolve(None) is default

    def test_literal_default_returns_default(self):
        default = _default_config()
        registry = InstanceRegistry(default, {})
        assert registry.resolve("default") is default


# ---------------------------------------------------------------------------
# resolve() -- named (declared) instances
# ---------------------------------------------------------------------------


class TestResolveNamed:
    def test_relative_url_joins_default_host(self):
        default = _default_config()
        declared = {"bcs": InstanceSpec(name="bcs", url="/bcs")}
        registry = InstanceRegistry(default, declared)

        config = registry.resolve("bcs")

        assert config.trac_url == "http://192.168.10.4:8000/bcs"
        assert config.username == "agent_rpc"
        assert config.password == "secret"

    def test_explicit_credentials_override_default(self):
        default = _default_config()
        declared = {
            "other": InstanceSpec(
                name="other",
                url="http://192.168.10.4:8000/other",
                username="otheruser",
                password="otherpass",
            )
        }
        registry = InstanceRegistry(default, declared)

        config = registry.resolve("other")

        assert config.username == "otheruser"
        assert config.password == "otherpass"

    def test_unset_insecure_inherits_default(self):
        default = _default_config(insecure=True)
        declared = {"bcs": InstanceSpec(name="bcs", url="/bcs")}
        registry = InstanceRegistry(default, declared)

        assert registry.resolve("bcs").insecure is True

    def test_explicit_insecure_overrides_default(self):
        default = _default_config(insecure=True)
        declared = {
            "bcs": InstanceSpec(name="bcs", url="/bcs", insecure=False)
        }
        registry = InstanceRegistry(default, declared)

        assert registry.resolve("bcs").insecure is False


# ---------------------------------------------------------------------------
# resolve() -- ad-hoc addressing
# ---------------------------------------------------------------------------


class TestResolveAdHoc:
    def test_path_resolves_against_default_host(self):
        default = _default_config()
        registry = InstanceRegistry(default, {})

        config = registry.resolve("/auto_pm")

        assert config.trac_url == "http://192.168.10.4:8000/auto_pm"
        assert config.username == default.username
        assert config.password == default.password

    def test_absolute_same_host_url_allowed(self):
        default = _default_config()
        registry = InstanceRegistry(default, {})

        config = registry.resolve("http://192.168.10.4:8000/core")

        assert config.trac_url == "http://192.168.10.4:8000/core"
        assert config.username == default.username

    def test_cross_host_url_rejected(self):
        default = _default_config()
        registry = InstanceRegistry(default, {})

        with pytest.raises(
            UnknownInstanceError, match="different host"
        ):
            registry.resolve("https://evil.example.com/trac")

    def test_cross_host_error_lists_configured_names(self):
        default = _default_config()
        declared = {"bcs": InstanceSpec(name="bcs", url="/bcs")}
        registry = InstanceRegistry(default, declared)

        with pytest.raises(UnknownInstanceError, match="bcs"):
            registry.resolve("https://evil.example.com/trac")

    def test_unknown_name_lists_configured_names(self):
        default = _default_config()
        declared = {"bcs": InstanceSpec(name="bcs", url="/bcs")}
        registry = InstanceRegistry(default, declared)

        with pytest.raises(UnknownInstanceError, match="bcs"):
            registry.resolve("nonexistent")


# ---------------------------------------------------------------------------
# get_client() -- caching
# ---------------------------------------------------------------------------


class TestClientCache:
    def test_equivalent_spellings_share_client(self):
        default = _default_config()
        registry = InstanceRegistry(default, {})

        client_a = registry.get_client("/bcs")
        client_b = registry.get_client("http://192.168.10.4:8000/bcs")

        assert client_a is client_b

    def test_seed_default_is_reused(self):
        default = _default_config()
        registry = InstanceRegistry(default, {})
        seeded = MagicMock()

        registry.seed_default(seeded)

        assert registry.get_client(None) is seeded
        assert registry.get_client("default") is seeded


# ---------------------------------------------------------------------------
# describe()
# ---------------------------------------------------------------------------


class TestDescribe:
    def test_default_entry_present(self):
        default = _default_config()
        registry = InstanceRegistry(default, {})

        described = registry.describe()

        assert described[0]["name"] == "default"
        assert described[0]["is_default"] is True
        assert described[0]["url"] == default.trac_url

    def test_declared_entries_present(self):
        default = _default_config()
        declared = {"bcs": InstanceSpec(name="bcs", url="/bcs")}
        registry = InstanceRegistry(default, declared)

        names = [e["name"] for e in registry.describe()]

        assert "bcs" in names

    def test_never_includes_password(self):
        default = _default_config()
        declared = {
            "bcs": InstanceSpec(
                name="bcs", url="/bcs", password="secretpass"
            ),
        }
        registry = InstanceRegistry(default, declared)

        described = registry.describe()

        for entry in described:
            assert "password" not in entry
        assert not any(
            "secretpass" in str(v)
            for entry in described
            for v in entry.values()
        )

    def test_credentials_field_explicit_vs_inherited(self):
        default = _default_config()
        declared = {
            "explicit": InstanceSpec(
                name="explicit", url="/x", username="u"
            ),
            "inherited": InstanceSpec(name="inherited", url="/y"),
        }
        registry = InstanceRegistry(default, declared)

        described = {e["name"]: e for e in registry.describe()}

        assert described["explicit"]["credentials"] == "explicit"
        assert described["inherited"]["credentials"] == "inherited"


# ---------------------------------------------------------------------------
# load_declared_instances()
# ---------------------------------------------------------------------------


class TestLoadDeclaredInstances:
    def test_nothing_declared_returns_empty(self, monkeypatch):
        monkeypatch.delenv("TRAC_INSTANCES", raising=False)
        assert load_declared_instances() == {}

    def test_yaml_instances_section(self, monkeypatch):
        monkeypatch.delenv("TRAC_INSTANCES", raising=False)
        raw = {"instances": {"bcs": {"url": "/bcs"}}}

        with (
            patch(
                "trac_mcp_server.instances.discover_config_files",
                return_value=[Path("/fake/config.yml")],
            ),
            patch(
                "trac_mcp_server.instances.load_hierarchical_config",
                return_value=raw,
            ),
        ):
            declared = load_declared_instances()

        assert declared["bcs"].url == "/bcs"

    def test_trac_instances_env_file(self, tmp_path, monkeypatch):
        instances_file = tmp_path / "instances.yml"
        instances_file.write_text("instances:\n  bcs:\n    url: /bcs\n")
        monkeypatch.setenv("TRAC_INSTANCES", str(instances_file))

        declared = load_declared_instances()

        assert declared["bcs"].url == "/bcs"

    def test_bare_mapping_shape_accepted(self, tmp_path, monkeypatch):
        """TRAC_INSTANCES may be a bare {name: {...}} mapping."""
        instances_file = tmp_path / "instances.yml"
        instances_file.write_text("bcs:\n  url: /bcs\n")
        monkeypatch.setenv("TRAC_INSTANCES", str(instances_file))

        declared = load_declared_instances()

        assert declared["bcs"].url == "/bcs"

    def test_env_file_wins_on_name_collision(
        self, tmp_path, monkeypatch
    ):
        instances_file = tmp_path / "instances.yml"
        instances_file.write_text(
            "instances:\n  bcs:\n    url: /bcs-from-env\n"
        )
        monkeypatch.setenv("TRAC_INSTANCES", str(instances_file))
        raw = {"instances": {"bcs": {"url": "/bcs-from-yaml"}}}

        with (
            patch(
                "trac_mcp_server.instances.discover_config_files",
                return_value=[Path("/fake/config.yml")],
            ),
            patch(
                "trac_mcp_server.instances.load_hierarchical_config",
                return_value=raw,
            ),
        ):
            declared = load_declared_instances()

        assert declared["bcs"].url == "/bcs-from-env"


# ---------------------------------------------------------------------------
# Live reload (mtime-gated)
# ---------------------------------------------------------------------------


class TestMtimeReload:
    def test_reload_picks_up_new_instance(self, tmp_path, monkeypatch):
        instances_file = tmp_path / "instances.yml"
        instances_file.write_text("instances:\n  bcs:\n    url: /bcs\n")
        monkeypatch.setenv("TRAC_INSTANCES", str(instances_file))

        default = _default_config()
        registry = InstanceRegistry(default, load_declared_instances())
        assert registry.declared_names() == ["bcs"]

        instances_file.write_text(
            "instances:\n  bcs:\n    url: /bcs\n  auto_pm:\n    url: /auto_pm\n"
        )
        # Force a distinct mtime regardless of filesystem timestamp granularity.
        bumped = instances_file.stat().st_mtime + 1
        os.utime(instances_file, (bumped, bumped))

        registry.resolve(None)  # any resolve() triggers the mtime check

        assert registry.declared_names() == ["auto_pm", "bcs"]

    def test_no_reload_when_unchanged(self, tmp_path, monkeypatch):
        instances_file = tmp_path / "instances.yml"
        instances_file.write_text("instances:\n  bcs:\n    url: /bcs\n")
        monkeypatch.setenv("TRAC_INSTANCES", str(instances_file))

        default = _default_config()
        registry = InstanceRegistry(default, load_declared_instances())

        with patch(
            "trac_mcp_server.instances.load_declared_instances"
        ) as mock_reload:
            registry.resolve(None)
            registry.resolve("bcs")
            mock_reload.assert_not_called()


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_ad_hoc_same_host_addressing():
    """Live: ad-hoc path addressing reaches the configured host.

    Requires --run-live and TRAC_URL/USERNAME/PASSWORD env vars. Uses the
    configured project's own path as the ad-hoc target -- this proves the
    resolution mechanics (host-join, credential inheritance, connection)
    without depending on a second project being reachable by this test
    user's credentials.
    """
    from urllib.parse import urlparse

    from trac_mcp_server.core.client import TracClient

    config = _default_config(
        trac_url=os.environ["TRAC_URL"],
        username=os.environ["TRAC_USERNAME"],
        password=os.environ["TRAC_PASSWORD"],
        insecure=os.environ.get("TRAC_INSECURE", "").lower()
        in ("1", "true", "yes"),
    )
    registry = InstanceRegistry(config, {})

    own_path = urlparse(config.trac_url).path
    client = registry.get_client(own_path)

    assert isinstance(client, TracClient)
    version = client.validate_connection()
    assert version
