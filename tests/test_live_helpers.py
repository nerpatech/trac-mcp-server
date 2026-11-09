"""Offline tests for the live rows' /trac_test URL (Trac #203).

`trac_test_url()` derives the destructive-test instance from TRAC_URL's
host and port, unless TRAC_TEST_URL names it outright (rookery #69 moves
/trac_test to its own origin, and a 308 across ports drops Basic
credentials). `trac_test_registry()` makes a server/handler stack that
resolves ``instance="/trac_test"`` land on the same URL. No network.
"""

import dataclasses

import pytest

from tests import live_helpers
from tests.live_helpers import is_trac_test_url, trac_test_url
from trac_mcp_server.config import Config
from trac_mcp_server.instances import Identity

PROJECT_URL = "http://trac.example.org:8000/some_project"
DERIVED = "http://trac.example.org:8000/trac_test"
OWN_ORIGIN = "http://trac.example.org:8001/trac_test"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TRAC_URL", PROJECT_URL)
    monkeypatch.delenv("TRAC_TEST_URL", raising=False)
    return monkeypatch


def _default_config() -> Config:
    return Config(trac_url=PROJECT_URL, username="u", password="p")


class TestTracTestUrl:
    def test_unset_derives_from_trac_url_host_and_port(self, env):
        assert trac_test_url() == DERIVED

    def test_unset_drops_trac_urls_project_path(self, env):
        env.setenv("TRAC_URL", "https://h.example.org:9443/a/b/c")
        assert trac_test_url() == "https://h.example.org:9443/trac_test"

    def test_set_is_used_verbatim(self, env):
        env.setenv("TRAC_TEST_URL", OWN_ORIGIN)
        assert trac_test_url() == OWN_ORIGIN

    def test_set_has_its_trailing_slash_stripped(self, env):
        env.setenv("TRAC_TEST_URL", OWN_ORIGIN + "/")
        assert trac_test_url() == OWN_ORIGIN

    def test_set_is_not_derived_from_trac_url(self, env):
        env.setenv("TRAC_URL", "http://other.example.com/x")
        env.setenv("TRAC_TEST_URL", OWN_ORIGIN)
        assert trac_test_url() == OWN_ORIGIN

    def test_empty_is_treated_as_unset(self, env):
        env.setenv("TRAC_TEST_URL", "")
        assert trac_test_url() == DERIVED

    def test_a_path_not_ending_in_trac_test_raises(self, env):
        env.setenv(
            "TRAC_TEST_URL", "http://trac.example.org:8001/auto_pm"
        )
        with pytest.raises(ValueError, match="TRAC_TEST_URL"):
            trac_test_url()

    def test_a_bare_origin_raises(self, env):
        env.setenv("TRAC_TEST_URL", "http://trac.example.org:8001")
        with pytest.raises(ValueError, match="TRAC_TEST_URL"):
            trac_test_url()

    @pytest.mark.parametrize(
        "bad",
        [
            "/trac_test",
            "trac.example.org:8001/trac_test",
            "ftp://h/trac_test",
            "http:///trac_test",
        ],
    )
    def test_a_value_without_http_scheme_and_host_raises(
        self, env, bad
    ):
        env.setenv("TRAC_TEST_URL", bad)
        with pytest.raises(ValueError, match="TRAC_TEST_URL"):
            trac_test_url()

    @pytest.mark.parametrize(
        "bad",
        [
            "http://user:s3cret@h:8001/trac_tst",
            "http://user:s3cret@h:8001/trac_test_x",
            "user:s3cret@h:8001/trac_test",
        ],
    )
    def test_the_error_never_echoes_the_value(self, env, bad):
        env.setenv("TRAC_TEST_URL", bad)
        with pytest.raises(ValueError) as info:
            trac_test_url()
        for text in (str(info.value), repr(info.value)):
            assert "s3cret" not in text
            assert "user:" not in text
            assert "TRAC_TEST_URL" in text

    def test_the_path_error_names_the_path(self, env):
        env.setenv("TRAC_TEST_URL", "http://h:8001/trac_tst")
        with pytest.raises(ValueError, match="/trac_tst"):
            trac_test_url()

    def test_the_result_always_passes_the_write_guard(self, env):
        assert is_trac_test_url(trac_test_url())
        env.setenv("TRAC_TEST_URL", OWN_ORIGIN + "/")
        assert is_trac_test_url(trac_test_url())

    def test_config_uses_the_resolved_url(self, env):
        env.setenv("TRAC_TEST_URL", OWN_ORIGIN)
        env.setenv("TRAC_USERNAME", "u")
        env.setenv("TRAC_PASSWORD", "p")
        assert live_helpers.trac_test_config().trac_url == OWN_ORIGIN


class TestTracTestRegistry:
    @pytest.mark.parametrize("set_var", [False, True])
    def test_trac_test_resolves_to_trac_test_url(self, env, set_var):
        if set_var:
            env.setenv("TRAC_TEST_URL", OWN_ORIGIN)
        registry = live_helpers.trac_test_registry(_default_config())
        resolved = registry.resolve("/trac_test")
        assert resolved.trac_url == trac_test_url()
        assert resolved.trac_url == (OWN_ORIGIN if set_var else DERIVED)

    @pytest.mark.parametrize("set_var", [False, True])
    def test_credentials_are_the_default_configs(self, env, set_var):
        if set_var:
            env.setenv("TRAC_TEST_URL", OWN_ORIGIN)
        registry = live_helpers.trac_test_registry(_default_config())
        resolved = registry.resolve("/trac_test")
        assert (resolved.username, resolved.password) == ("u", "p")

    def test_a_caller_identity_replaces_the_credentials(self, env):
        env.setenv("TRAC_TEST_URL", OWN_ORIGIN)
        registry = live_helpers.trac_test_registry(_default_config())
        identity = Identity(name="i", username="alice", password="pw")
        resolved = registry.resolve("/trac_test", identity)
        assert resolved.trac_url == OWN_ORIGIN
        assert (resolved.username, resolved.password) == ("alice", "pw")

    def test_the_default_instance_is_untouched(self, env):
        env.setenv("TRAC_TEST_URL", OWN_ORIGIN)
        registry = live_helpers.trac_test_registry(_default_config())
        assert registry.resolve(None).trac_url == PROJECT_URL


class TestRegistryKeepsTheRowsConfig:
    def test_a_non_default_field_is_inherited(self, env):
        config = dataclasses.replace(
            _default_config(), write_gate=False
        )
        assert config.write_gate is False
        for set_var in (False, True):
            if set_var:
                env.setenv("TRAC_TEST_URL", OWN_ORIGIN)
            resolved = live_helpers.trac_test_registry(config).resolve(
                live_helpers.TEST_INSTANCE
            )
            assert resolved.write_gate is False
            assert resolved.trac_url == trac_test_url()

    def test_unset_config_matches_the_old_empty_registry(self, env):
        from trac_mcp_server.instances import InstanceRegistry

        config = dataclasses.replace(
            _default_config(), write_gate=False
        )
        old = InstanceRegistry(config, {}).resolve("/trac_test")
        new = live_helpers.trac_test_registry(config).resolve(
            "/trac_test"
        )
        assert new == old


class TestRegistryReload:
    def test_the_trac_test_spec_survives_a_declared_reload(
        self, env, monkeypatch
    ):
        from trac_mcp_server import instances

        env.setenv("TRAC_TEST_URL", OWN_ORIGIN)
        monkeypatch.setattr(
            instances, "load_declared_instances", lambda: {}
        )
        cfg = Config(
            trac_url="http://trac.example.org:8000/proj",
            username="u",
            password="p",
        )
        registry = live_helpers.trac_test_registry(cfg)
        before = registry.resolve("/trac_test")
        assert before.trac_url == OWN_ORIGIN
        # A config / TRAC_INSTANCES file changed mtime: the real
        # _reload_if_changed replaces _declared from the loader.
        monkeypatch.setattr(
            registry,
            "_snapshot_mtimes",
            lambda: {instances.Path("/nonexistent"): 1.0},
        )
        after = registry.resolve("/trac_test")
        assert registry._mtimes != {}  # the reload branch ran
        assert after.trac_url == OWN_ORIGIN
