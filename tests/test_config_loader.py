"""Tests for trac_mcp_server.config_loader — hierarchical config loading."""

import textwrap

import pytest
import yaml

from trac_mcp_server.config_loader import (
    _interpolate_recursive,
    _load_yaml_with_includes,
    discover_config_files,
    interpolate_env_vars,
    load_hierarchical_config,
)

# -------------------------------------------------------------------------
# Env var interpolation
# -------------------------------------------------------------------------


class TestInterpolateEnvVars:
    """Tests for ${VAR} and ${VAR:-default} substitution."""

    def test_replaces_set_var(self, monkeypatch):
        monkeypatch.setenv("MY_HOST", "localhost")
        assert interpolate_env_vars("${MY_HOST}") == "localhost"

    def test_unset_var_replaced_with_empty(self, monkeypatch):
        monkeypatch.delenv("UNSET_VAR_XYZ", raising=False)
        assert interpolate_env_vars("${UNSET_VAR_XYZ}") == ""

    def test_default_used_when_unset(self, monkeypatch):
        monkeypatch.delenv("UNSET_VAR_XYZ", raising=False)
        assert (
            interpolate_env_vars("${UNSET_VAR_XYZ:-fallback}")
            == "fallback"
        )

    def test_default_ignored_when_set(self, monkeypatch):
        monkeypatch.setenv("MY_PORT", "8080")
        assert interpolate_env_vars("${MY_PORT:-3000}") == "8080"

    def test_multiple_vars_in_one_string(self, monkeypatch):
        monkeypatch.setenv("HOST_A", "db.local")
        monkeypatch.setenv("PORT_A", "5432")
        assert (
            interpolate_env_vars("${HOST_A}:${PORT_A}")
            == "db.local:5432"
        )

    def test_nested_dict_interpolation(self, monkeypatch):
        monkeypatch.setenv("DB_URL", "postgres://localhost")
        data = {"db": {"url": "${DB_URL}", "pool": 5}}
        result = _interpolate_recursive(data)
        assert result == {
            "db": {"url": "postgres://localhost", "pool": 5}
        }

    def test_non_string_values_untouched(self):
        data = {"count": 42, "enabled": True, "items": [1, 2, 3]}
        assert _interpolate_recursive(data) == data

    def test_literal_dollar_brace_no_closing(self):
        # No closing } — should be left untouched
        assert interpolate_env_vars("${NO_CLOSE") == "${NO_CLOSE"

    def test_empty_env_var_uses_default(self, monkeypatch):
        monkeypatch.setenv("EMPTY_VAR", "")
        assert (
            interpolate_env_vars("${EMPTY_VAR:-fallback}") == "fallback"
        )

    def test_list_values_interpolated(self, monkeypatch):
        monkeypatch.setenv("ITEM_VAL", "resolved")
        data = ["${ITEM_VAL}", "static", 99]
        result = _interpolate_recursive(data)
        assert result == ["resolved", "static", 99]


# -------------------------------------------------------------------------
# YAML !include support
# -------------------------------------------------------------------------


class TestIncludeDirective:
    """Tests for !include YAML loading via ConfigLoader subclass."""

    def test_include_relative_file(self, tmp_path):
        secrets = tmp_path / "secrets.yml"
        secrets.write_text("api_key: secret123\n")

        main = tmp_path / "config.yml"
        main.write_text("secrets: !include secrets.yml\n")

        result = _load_yaml_with_includes(main)
        assert result == {"secrets": {"api_key": "secret123"}}

    def test_include_absolute_path(self, tmp_path):
        secrets = tmp_path / "abs_secrets.yml"
        secrets.write_text("token: abc\n")

        main = tmp_path / "config.yml"
        main.write_text(f"auth: !include {secrets}\n")

        result = _load_yaml_with_includes(main)
        assert result == {"auth": {"token": "abc"}}

    def test_include_nonexistent_raises(self, tmp_path):
        main = tmp_path / "config.yml"
        main.write_text("data: !include missing.yml\n")

        with pytest.raises(FileNotFoundError, match="missing.yml"):
            _load_yaml_with_includes(main)

    def test_circular_include_raises(self, tmp_path):
        a = tmp_path / "a.yml"
        b = tmp_path / "b.yml"
        a.write_text("x: !include b.yml\n")
        b.write_text("y: !include a.yml\n")

        with pytest.raises(ValueError, match="Circular include"):
            _load_yaml_with_includes(a)

    def test_self_include_raises(self, tmp_path):
        a = tmp_path / "a.yml"
        a.write_text("x: !include a.yml\n")

        with pytest.raises(ValueError, match="Circular include"):
            _load_yaml_with_includes(a)

    def test_nested_includes(self, tmp_path):
        c = tmp_path / "c.yml"
        c.write_text("val: deep\n")

        b = tmp_path / "b.yml"
        b.write_text("inner: !include c.yml\n")

        a = tmp_path / "a.yml"
        a.write_text("outer: !include b.yml\n")

        result = _load_yaml_with_includes(a)
        assert result == {"outer": {"inner": {"val": "deep"}}}

    def test_global_safe_loader_not_polluted(self, tmp_path):
        """Verify that !include is NOT registered on yaml.SafeLoader."""
        cfg = tmp_path / "test.yml"
        cfg.write_text("x: !include other.yml\n")

        # Using yaml.safe_load should fail — the tag is unknown
        with pytest.raises(yaml.constructor.ConstructorError):
            with open(cfg) as fh:
                yaml.safe_load(fh)

    def test_include_returns_scalar(self, tmp_path):
        """!include can inline a scalar value (not just dicts)."""
        val = tmp_path / "version.yml"
        val.write_text('"1.2.3"\n')

        main = tmp_path / "config.yml"
        main.write_text("version: !include version.yml\n")

        result = _load_yaml_with_includes(main)
        assert result == {"version": "1.2.3"}


# -------------------------------------------------------------------------
# Convention-based file discovery
# -------------------------------------------------------------------------


class TestDiscoverConfigFiles:
    """Tests for discover_config_files() precedence and filtering."""

    def test_env_var_takes_highest_precedence(
        self, tmp_path, monkeypatch
    ):
        custom = tmp_path / "custom.yml"
        custom.write_text("custom: true\n")
        monkeypatch.setenv("TRAC_ASSIST_CONFIG", str(custom))
        monkeypatch.chdir(tmp_path)

        result = discover_config_files()
        assert result[0] == custom.resolve()

    def test_project_yml_before_global(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)

        proj = tmp_path / ".trac_mcp" / "config.yml"
        proj.parent.mkdir(parents=True)
        proj.write_text("project: true\n")

        # Fake home for global
        fake_home = tmp_path / "fakehome"
        monkeypatch.setenv("HOME", str(fake_home))
        global_cfg = fake_home / ".config" / "trac_mcp" / "config.yml"
        global_cfg.parent.mkdir(parents=True)
        global_cfg.write_text("global: true\n")

        result = discover_config_files()
        names = [p.name for p in result]
        assert "config.yml" in names
        # Project should appear before global
        assert result.index(proj.resolve()) < result.index(
            global_cfg.resolve()
        )

    def test_legacy_yaml_extension_compat(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)

        proj_yaml = tmp_path / ".trac_mcp" / "config.yaml"
        proj_yaml.parent.mkdir(parents=True)
        proj_yaml.write_text("legacy: true\n")

        fake_home = tmp_path / "fakehome"
        monkeypatch.setenv("HOME", str(fake_home))

        result = discover_config_files()
        assert proj_yaml.resolve() in [p for p in result]

    def test_xdg_path_discovered(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))

        xdg = tmp_path / ".config" / "trac_mcp" / "config.yml"
        xdg.parent.mkdir(parents=True)
        xdg.write_text("xdg: true\n")

        result = discover_config_files()
        assert xdg.resolve() in result

    def test_legacy_global_discovered(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))

        legacy = tmp_path / ".trac_mcp" / "config.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("legacy_global: true\n")

        result = discover_config_files()
        # CWD is tmp_path, so .trac_mcp/config.yaml is both project and legacy
        assert any(p == legacy.resolve() for p in result)

    def test_missing_files_excluded(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        fake_home = tmp_path / "fakehome"
        monkeypatch.setenv("HOME", str(fake_home))

        # No config files exist
        result = discover_config_files()
        assert result == []

    def test_empty_filesystem_returns_empty(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        fake_home = tmp_path / "nohome"
        monkeypatch.setenv("HOME", str(fake_home))

        assert discover_config_files() == []


# -------------------------------------------------------------------------
# Hierarchical merge
# -------------------------------------------------------------------------


class TestLoadHierarchicalConfig:
    """Tests for load_hierarchical_config() merge and interpolation."""

    def test_global_only_loaded(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))

        xdg = tmp_path / ".config" / "trac_mcp" / "config.yml"
        xdg.parent.mkdir(parents=True)
        xdg.write_text(
            textwrap.dedent("""\
            trac:
              url: https://global.example.com
            """)
        )

        result = load_hierarchical_config()
        assert result["trac"]["url"] == "https://global.example.com"

    def test_project_overrides_global_at_section_level(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))

        # Global config
        xdg = tmp_path / ".config" / "trac_mcp" / "config.yml"
        xdg.parent.mkdir(parents=True)
        xdg.write_text(
            textwrap.dedent("""\
            trac:
              url: https://global.example.com
              username: globaluser
            providers:
              anthropic:
                model: haiku
            """)
        )

        # Project config — trac section replaces global entirely
        proj = tmp_path / ".trac_mcp" / "config.yml"
        proj.parent.mkdir(parents=True)
        proj.write_text(
            textwrap.dedent("""\
            trac:
              url: https://project.example.com
            """)
        )

        result = load_hierarchical_config()
        # Project trac replaces global trac entirely (shallow merge)
        assert result["trac"] == {"url": "https://project.example.com"}
        # providers key from global is retained (not overridden by project)
        assert result["providers"]["anthropic"]["model"] == "haiku"

    def test_env_var_interpolation_after_merge(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("MY_SECRET", "s3cret")

        proj = tmp_path / ".trac_mcp" / "config.yml"
        proj.parent.mkdir(parents=True)
        proj.write_text(
            textwrap.dedent("""\
            trac:
              password: "${MY_SECRET}"
            """)
        )

        result = load_hierarchical_config()
        assert result["trac"]["password"] == "s3cret"

    def test_zero_config_returns_empty_dict(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        fake_home = tmp_path / "nohome"
        monkeypatch.setenv("HOME", str(fake_home))

        result = load_hierarchical_config()
        assert result == {}

    def test_include_within_merged_config(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        fake_home = tmp_path / "fakehome"
        monkeypatch.setenv("HOME", str(fake_home))

        proj_dir = tmp_path / ".trac_mcp"
        proj_dir.mkdir()

        secrets = proj_dir / "secrets.yml"
        secrets.write_text("api_key: key123\n")

        main = proj_dir / "config.yml"
        main.write_text(
            textwrap.dedent("""\
            trac:
              url: https://example.com
            secrets: !include secrets.yml
            """)
        )

        result = load_hierarchical_config()
        assert result["secrets"]["api_key"] == "key123"
        assert result["trac"]["url"] == "https://example.com"

    def test_non_dict_root_skipped(self, tmp_path, monkeypatch):
        """Config files with non-dict root (e.g. a bare list) are skipped."""
        monkeypatch.delenv("TRAC_ASSIST_CONFIG", raising=False)
        custom = tmp_path / "bad.yml"
        custom.write_text("- item1\n- item2\n")
        monkeypatch.setenv("TRAC_ASSIST_CONFIG", str(custom))
        monkeypatch.chdir(tmp_path)
        fake_home = tmp_path / "fakehome"
        monkeypatch.setenv("HOME", str(fake_home))

        result = load_hierarchical_config()
        assert result == {}


# -------------------------------------------------------------------------
# Config bootstrapping
# -------------------------------------------------------------------------


class TestResolveConfigPath:
    """Tests for resolve_config_path() — determining single config path."""

    def test_returns_existing_file(self, tmp_path, monkeypatch):
        """When config files exist, return the first one."""
        from pathlib import Path
        from unittest.mock import patch

        fake_path = Path("/fake/project/.trac_mcp/config.yml")

        with patch(
            "trac_mcp_server.config_loader.discover_config_files",
            return_value=[fake_path],
        ):
            from trac_mcp_server.config_loader import (
                resolve_config_path,
            )

            result = resolve_config_path()
            assert result == fake_path

    def test_returns_default_when_no_files(self, tmp_path, monkeypatch):
        """When no config files exist, return default project-level path."""
        from unittest.mock import patch

        monkeypatch.chdir(tmp_path)

        with patch(
            "trac_mcp_server.config_loader.discover_config_files",
            return_value=[],
        ):
            from trac_mcp_server.config_loader import (
                resolve_config_path,
            )

            result = resolve_config_path()
            assert result.name == "config.yml"
            assert ".trac_mcp" in str(result)
            assert result == tmp_path / ".trac_mcp" / "config.yml"

    def test_returns_highest_precedence(self, tmp_path, monkeypatch):
        """When multiple files exist, return the first (highest precedence)."""
        from pathlib import Path
        from unittest.mock import patch

        project_path = Path("/project/.trac_mcp/config.yml")
        global_path = Path("/home/USER/.config/trac_mcp/config.yml")

        with patch(
            "trac_mcp_server.config_loader.discover_config_files",
            return_value=[project_path, global_path],
        ):
            from trac_mcp_server.config_loader import (
                resolve_config_path,
            )

            result = resolve_config_path()
            assert result == project_path


class TestEnsureConfig:
    """Tests for ensure_config() — bootstrapping config files."""

    def test_noop_when_exists(self, tmp_path, monkeypatch):
        """When config exists, return it without creating anything."""
        from pathlib import Path
        from unittest.mock import patch

        existing_path = Path("/fake/existing/config.yml")

        with patch(
            "trac_mcp_server.config_loader.discover_config_files",
            return_value=[existing_path],
        ):
            from trac_mcp_server.config_loader import ensure_config

            result = ensure_config()
            assert result == existing_path
            # Verify nothing was created in tmp_path
            assert not (tmp_path / ".trac_mcp").exists()

    def test_creates_directory_and_file(self, tmp_path, monkeypatch):
        """When no config exists, create directory and starter file."""
        from unittest.mock import patch

        monkeypatch.chdir(tmp_path)
        target = tmp_path / ".trac_mcp" / "config.yml"

        with patch(
            "trac_mcp_server.config_loader.discover_config_files",
            return_value=[],
        ):
            from trac_mcp_server.config_loader import ensure_config

            result = ensure_config(target=target)

            assert result == target
            assert target.exists()
            assert target.parent.is_dir()

            content = target.read_text()
            assert "# trac-mcp-server configuration" in content
            assert "# trac:" in content
            assert "# logging:" in content

    def test_uses_explicit_target(self, tmp_path, monkeypatch):
        """When explicit target is provided, create file at that path."""
        from unittest.mock import patch

        custom_target = (
            tmp_path / "custom" / "location" / "my-config.yml"
        )

        with patch(
            "trac_mcp_server.config_loader.discover_config_files",
            return_value=[],
        ):
            from trac_mcp_server.config_loader import ensure_config

            result = ensure_config(target=custom_target)

            assert result == custom_target
            assert custom_target.exists()
            assert custom_target.is_file()

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        """Verify parent directories are created with parents=True."""
        from unittest.mock import patch

        deep_target = tmp_path / "a" / "b" / "c" / "config.yml"

        with patch(
            "trac_mcp_server.config_loader.discover_config_files",
            return_value=[],
        ):
            from trac_mcp_server.config_loader import ensure_config

            result = ensure_config(target=deep_target)

            assert result == deep_target
            assert deep_target.exists()
            assert (tmp_path / "a" / "b" / "c").is_dir()

    def test_default_uses_resolve_config_path(
        self, tmp_path, monkeypatch
    ):
        """When no target provided, uses resolve_config_path() for default."""
        from unittest.mock import patch

        monkeypatch.chdir(tmp_path)

        with patch(
            "trac_mcp_server.config_loader.discover_config_files",
            return_value=[],
        ):
            from trac_mcp_server.config_loader import ensure_config

            result = ensure_config()

            # Should use default: CWD / .trac_mcp / config.yml
            expected = tmp_path / ".trac_mcp" / "config.yml"
            assert result == expected
            assert result.exists()
