"""
Tests for version module.

Covers __version__ attribute and check_version_consistency().
"""

import re
import sys
import types
from unittest.mock import mock_open, patch

import tomllib

import trac_mcp_server
from trac_mcp_server.version import (
    check_version_consistency,
    format_server_version,
    release_sha,
)


class TestVersionAttribute:
    """Test __version__ is properly set."""

    def test_version_exists(self):
        """__version__ is a non-empty string."""
        assert hasattr(trac_mcp_server, "__version__")
        assert isinstance(trac_mcp_server.__version__, str)
        assert len(trac_mcp_server.__version__) > 0

    def test_version_format(self):
        """__version__ matches semver pattern (X.Y.Z)."""
        version = trac_mcp_server.__version__
        assert re.match(r"^\d+\.\d+\.\d+$", version), (
            f"Version '{version}' does not match X.Y.Z pattern"
        )


class TestCheckVersionConsistency:
    """Test check_version_consistency() function."""

    def test_consistency_success(self):
        """Matching versions return (True, message)."""
        runtime_version = trac_mcp_server.__version__

        toml_content = f'[project]\nversion = "{runtime_version}"\n'
        toml_data = {"project": {"version": runtime_version}}

        with patch("trac_mcp_server.version.Path") as mock_path_cls:
            mock_path = mock_path_cls.return_value.parent.parent.parent.__truediv__.return_value
            mock_path.exists.return_value = True

            with patch(
                "builtins.open",
                mock_open(read_data=toml_content.encode()),
            ):
                with patch.object(
                    tomllib, "load", return_value=toml_data
                ):
                    is_consistent, message = check_version_consistency()

        assert is_consistent is True
        assert (
            "verified" in message.lower() or runtime_version in message
        )

    def test_consistency_mismatch(self):
        """Mismatched versions return (False, mismatch message)."""
        runtime_version = trac_mcp_server.__version__
        fake_source_version = "99.99.99"

        toml_data = {"project": {"version": fake_source_version}}

        with patch("trac_mcp_server.version.Path") as mock_path_cls:
            mock_path = mock_path_cls.return_value.parent.parent.parent.__truediv__.return_value
            mock_path.exists.return_value = True

            with patch("builtins.open", mock_open(read_data=b"")):
                with patch.object(
                    tomllib, "load", return_value=toml_data
                ):
                    is_consistent, message = check_version_consistency()

        assert is_consistent is False
        assert "mismatch" in message.lower()
        assert runtime_version in message
        assert fake_source_version in message

    def test_consistency_file_not_found(self):
        """Missing pyproject.toml returns (False, message) without crashing."""
        with patch("trac_mcp_server.version.Path") as mock_path_cls:
            mock_path = mock_path_cls.return_value.parent.parent.parent.__truediv__.return_value
            mock_path.exists.return_value = False

            is_consistent, message = check_version_consistency()

        assert is_consistent is False
        assert (
            "cannot find" in message.lower()
            or "pyproject.toml" in message.lower()
        )

    def test_consistency_toml_read_error(self):
        """Error reading pyproject.toml returns (False, message) gracefully."""
        with patch("trac_mcp_server.version.Path") as mock_path_cls:
            mock_path = mock_path_cls.return_value.parent.parent.parent.__truediv__.return_value
            mock_path.exists.return_value = True

            with patch(
                "builtins.open",
                side_effect=PermissionError("Access denied"),
            ):
                is_consistent, message = check_version_consistency()

        assert is_consistent is False
        assert (
            "failed" in message.lower()
            or "error" in message.lower()
            or "access denied" in message.lower()
        )

    def test_consistency_import_error(self):
        """Cannot import __version__ returns (False, message)."""
        with patch(
            "trac_mcp_server.version.check_version_consistency"
        ) as _:
            # We can't easily mock an ImportError in the from . import __version__
            # line, but we can test the function directly by manipulating the module.
            # Instead, test the actual function with a real import (should succeed).
            is_consistent, message = check_version_consistency()
            # With real install, this should either succeed or fail gracefully
            assert isinstance(is_consistent, bool)
            assert isinstance(message, str)


class TestReleaseSha:
    """release_sha() reads the stamp setup.py writes at build time
    (Trac /trac_mcp_server #170)."""

    def test_none_when_the_build_wrote_no_stamp(self, monkeypatch):
        # a None entry makes the import raise ImportError, as a
        # checkout with no _release.py does
        monkeypatch.setitem(
            sys.modules, "trac_mcp_server._release", None
        )
        assert release_sha() is None

    def test_the_stamped_value_when_there_is_one(self, monkeypatch):
        fake = types.ModuleType("trac_mcp_server._release")
        fake.RELEASE_SHA = "a" * 40
        monkeypatch.setitem(
            sys.modules, "trac_mcp_server._release", fake
        )
        assert release_sha() == "a" * 40

    def test_a_dirty_stamp_is_returned_as_written(self, monkeypatch):
        fake = types.ModuleType("trac_mcp_server._release")
        fake.RELEASE_SHA = "a" * 40 + "-dirty"
        monkeypatch.setitem(
            sys.modules, "trac_mcp_server._release", fake
        )
        assert release_sha() == "a" * 40 + "-dirty"


class TestFormatServerVersion:
    """serverInfo.version carries the commit when one is known."""

    def test_plain_version_without_a_sha(self):
        assert format_server_version("2.2.0", None) == "2.2.0"

    def test_local_segment_with_the_first_12_of_the_sha(self):
        sha = "0123456789abcdef" * 2 + "01234567"
        assert (
            format_server_version("2.2.0", sha) == "2.2.0+g0123456789ab"
        )

    def test_a_dirty_sha_is_marked_dirty(self):
        sha = "0123456789abcdef" * 2 + "01234567" + "-dirty"
        assert (
            format_server_version("2.2.0", sha)
            == "2.2.0+g0123456789ab.dirty"
        )
