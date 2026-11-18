"""
Tests for version module.

Covers __version__ attribute and check_version_consistency().
"""

import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

import trac_mcp_server
from trac_mcp_server.version import (
    _compare_versions,
    _installed_version,
    check_version_consistency,
    cli_version,
    format_cli_version,
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


def _manifest(path, name="trac-mcp-server", version="3.0.0"):
    """Write a pyproject.toml; ``name=None`` leaves ``[project].name``
    out."""
    lines = ["[project]"]
    if name is not None:
        lines.append(f'name = "{name}"')
    lines.append(f'version = "{version}"')
    path.write_text("\n".join(lines) + "\n")
    return path


def _dist(version):
    """A stand-in for the installed-metadata lookup."""
    return lambda: version


class TestCompareVersions:
    """``_compare_versions`` against real files: a source manifest
    first, then the installed distribution's metadata, then nothing to
    compare (Trac /trac_mcp_server #244)."""

    def test_manifest_match(self, tmp_path):
        manifest = _manifest(tmp_path / "pyproject.toml")
        ok, message = _compare_versions("3.0.0", manifest, _dist(None))
        assert ok is True
        assert message == "Version verified: 3.0.0"

    def test_manifest_mismatch(self, tmp_path):
        manifest = _manifest(
            tmp_path / "pyproject.toml", version="9.9.9"
        )
        ok, message = _compare_versions("3.0.0", manifest, _dist(None))
        assert ok is False
        assert "mismatch" in message.lower()
        assert "Runtime: 3.0.0" in message
        assert "Source: 9.9.9" in message
        assert "pyinstaller" in message

    def test_manifest_wins_over_stale_metadata(self, tmp_path):
        # an editable install's dist metadata keeps the install-time
        # version; the manifest is the live source
        manifest = _manifest(tmp_path / "pyproject.toml")
        ok, message = _compare_versions(
            "3.0.0", manifest, _dist("2.2.0")
        )
        assert ok is True
        assert message == "Version verified: 3.0.0"

    def test_manifest_for_another_project_falls_through(self, tmp_path):
        manifest = _manifest(
            tmp_path / "pyproject.toml", name="other", version="9.9.9"
        )
        ok, message = _compare_versions(
            "3.0.0", manifest, _dist("3.0.0")
        )
        assert ok is True
        assert "installed package metadata" in message

    def test_manifest_with_no_name_falls_through(self, tmp_path):
        manifest = _manifest(
            tmp_path / "pyproject.toml", name=None, version="9.9.9"
        )
        ok, message = _compare_versions(
            "3.0.0", manifest, _dist("3.0.0")
        )
        assert ok is True
        assert "installed package metadata" in message

    def test_unparseable_manifest_is_a_read_failure(self, tmp_path):
        manifest = tmp_path / "pyproject.toml"
        manifest.write_text("[project\nname = \n")
        ok, message = _compare_versions(
            "3.0.0", manifest, _dist("3.0.0")
        )
        assert ok is False
        assert message.startswith(
            "Failed to read version from pyproject.toml: "
        )

    def test_unreadable_manifest_is_a_read_failure(self, tmp_path):
        # a directory where the file should be: open() raises OSError
        manifest = tmp_path / "pyproject.toml"
        manifest.mkdir()
        ok, message = _compare_versions(
            "3.0.0", manifest, _dist("3.0.0")
        )
        assert ok is False
        assert message.startswith(
            "Failed to read version from pyproject.toml: "
        )

    def test_no_manifest_metadata_match(self, tmp_path):
        ok, message = _compare_versions(
            "3.0.0", tmp_path / "pyproject.toml", _dist("3.0.0")
        )
        assert ok is True
        assert (
            message
            == "Version verified: 3.0.0 (installed package metadata)"
        )

    def test_no_manifest_metadata_mismatch(self, tmp_path):
        ok, message = _compare_versions(
            "3.0.0", tmp_path / "pyproject.toml", _dist("2.2.0")
        )
        assert ok is False
        assert "mismatch" in message.lower()
        assert "3.0.0" in message
        assert "2.2.0" in message
        assert "metadata" in message
        assert "reinstall" in message
        assert "pyinstaller" not in message

    def test_neither_source_is_consistent_and_says_so(self, tmp_path):
        ok, message = _compare_versions(
            "3.0.0", tmp_path / "pyproject.toml", _dist(None)
        )
        # True: the server logs it at info, not as a warning
        assert ok is True
        assert "3.0.0" in message
        assert "no source manifest or package metadata" in message


class TestInstalledVersion:
    def test_is_the_distributions_version(self, monkeypatch):
        monkeypatch.setattr(
            importlib.metadata, "version", lambda name: "1.2.3"
        )
        assert _installed_version() == "1.2.3"

    def test_none_when_there_is_no_metadata(self, monkeypatch):
        def missing(name):
            raise importlib.metadata.PackageNotFoundError(name)

        monkeypatch.setattr(importlib.metadata, "version", missing)
        assert _installed_version() is None


class TestCheckVersionConsistency:
    """``check_version_consistency()`` itself, as run from this
    checkout (a manifest that names this project)."""

    def test_the_checkout_is_consistent(self):
        ok, message = check_version_consistency()
        assert ok is True, message
        assert (
            message
            == f"Version verified: {trac_mcp_server.__version__}"
        )


REPO = Path(__file__).resolve().parent.parent

#: What a wheel build prints when the interpreter lacks a build tool,
#: not when the project is broken.
_MISSING_BUILD_TOOL = (
    "BackendUnavailable",
    "No module named pip",
    "No module named 'setuptools'",
    "Cannot import 'setuptools.build_meta'",
    "No matching distribution found",
)


@pytest.fixture
def installed_site(tmp_path):
    """This project built into a wheel and unpacked into a directory:
    the layout of a non-editable install, where ``version.py`` sits
    under ``site-packages`` and the parent-of-parent-of-parent path
    names no manifest. The build runs in a copy, so parallel runs never
    share ``build/`` or ``*.egg-info``."""
    root = tmp_path / "project"
    root.mkdir()
    shutil.copytree(
        REPO / "src",
        root / "src",
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.egg-info", "_release.py"
        ),
    )
    for name in ("pyproject.toml", "setup.py", "README.md", "LICENSE"):
        shutil.copy(REPO / name, root / name)
    wheels = tmp_path / "whl"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "-q",
            "--no-deps",
            "--no-build-isolation",
            "-w",
            str(wheels),
            str(root),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        if any(marker in output for marker in _MISSING_BUILD_TOOL):
            pytest.skip(
                "cannot build a wheel offline, a build dependency is "
                f"missing: {output.strip()[-300:]}"
            )
        pytest.fail(f"wheel build failed:\n{output}")
    (wheel,) = wheels.glob("trac_mcp_server-*.whl")
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(site)
    return site


def test_an_installed_copy_compares_against_its_package_metadata(
    installed_site,
):
    """The installed layout, by subprocess against the real wheel
    contents (``Rules/testing/RealSubstrateNotMocks``): before #244 this
    reported ``Cannot find pyproject.toml`` and compared nothing."""
    code = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import trac_mcp_server.version as v\n"
        "from trac_mcp_server import __version__\n"
        "print(json.dumps([v.__file__, __version__,"
        " list(v.check_version_consistency())]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(installed_site)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    module_file, version, (ok, message) = json.loads(
        result.stdout.strip().splitlines()[-1]
    )
    # the installed copy was imported, not the checkout's
    assert (
        Path(module_file)
        .resolve()
        .is_relative_to(installed_site.resolve())
    ), module_file
    assert ok is True, message
    assert "installed package metadata" in message
    assert version in message


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

    @pytest.mark.parametrize(
        "value", ["unknown", "0123456789ab", "", "a" * 40 + "-dirt"]
    )
    def test_a_value_that_is_not_a_sha_is_none(
        self, monkeypatch, value
    ):
        fake = types.ModuleType("trac_mcp_server._release")
        fake.RELEASE_SHA = value
        monkeypatch.setitem(
            sys.modules, "trac_mcp_server._release", fake
        )
        assert release_sha() is None


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


class TestFormatCliVersion:
    """``--version`` names the build commit (Trac /trac_mcp_server
    #230); without one it says so instead of printing a bare version
    that reads as a complete answer."""

    SHA = "0123456789abcdef" * 2 + "01234567"

    def test_a_sha_gives_the_server_form(self):
        assert (
            format_cli_version("3.0.0", self.SHA)
            == "3.0.0+g0123456789ab"
        )

    def test_a_dirty_sha_is_marked_dirty(self):
        assert (
            format_cli_version("3.0.0", self.SHA + "-dirty")
            == "3.0.0+g0123456789ab.dirty"
        )

    def test_no_sha_says_the_commit_is_unknown(self):
        assert (
            format_cli_version("3.0.0", None)
            == "3.0.0 (commit unknown)"
        )

    def test_an_empty_sha_says_the_commit_is_unknown(self):
        assert (
            format_cli_version("3.0.0", "") == "3.0.0 (commit unknown)"
        )


class TestReleaseShaFrozen:
    """A PyInstaller binary has no ``_release`` module (setup.py's
    build_py never runs for it); build.sh bundles the sha as a data
    file under ``sys._MEIPASS`` instead (Trac /trac_mcp_server #230)."""

    SHA = "0123456789abcdef" * 2 + "01234567"
    FILE = "trac_mcp_server_RELEASE_SHA"

    @pytest.fixture(autouse=True)
    def no_release_module(self, monkeypatch):
        # an editable checkout has no _release.py; guard it anyway
        monkeypatch.setitem(
            sys.modules, "trac_mcp_server._release", None
        )

    @pytest.fixture
    def meipass(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(
            sys, "_MEIPASS", str(tmp_path), raising=False
        )
        return tmp_path

    def test_a_valid_file_is_the_sha(self, meipass):
        (meipass / self.FILE).write_text(self.SHA + "\n")
        assert release_sha() == self.SHA

    def test_a_dirty_file_is_returned_as_written(self, meipass):
        (meipass / self.FILE).write_text(self.SHA + "-dirty")
        assert release_sha() == self.SHA + "-dirty"

    @pytest.mark.parametrize(
        "text",
        [
            "main",
            "0123456789abcdef" * 2 + "0123456",
            "",
            "0123456789ABCDEF" * 2 + "01234567",
            "0123456789abcdef" * 2 + "01234567-dirt",
        ],
    )
    def test_a_malformed_file_is_none(self, meipass, text):
        (meipass / self.FILE).write_text(text)
        assert release_sha() is None

    def test_a_missing_file_is_none(self, meipass):
        assert release_sha() is None

    def test_an_unreadable_file_is_none(self, meipass):
        # a directory where the file should be: read_text raises OSError
        (meipass / self.FILE).mkdir()
        assert release_sha() is None

    def test_an_undecodable_file_is_none(self, meipass):
        (meipass / self.FILE).write_bytes(
            b"\xff\xfe" + self.SHA.encode()
        )
        assert release_sha() is None

    def test_not_frozen_ignores_the_file(self, tmp_path, monkeypatch):
        (tmp_path / self.FILE).write_text(self.SHA)
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(
            sys, "_MEIPASS", str(tmp_path), raising=False
        )
        assert release_sha() is None

    def test_the_release_module_wins_over_the_file(
        self, meipass, monkeypatch
    ):
        (meipass / self.FILE).write_text(self.SHA)
        fake = types.ModuleType("trac_mcp_server._release")
        fake.RELEASE_SHA = "b" * 40
        monkeypatch.setitem(
            sys.modules, "trac_mcp_server._release", fake
        )
        assert release_sha() == "b" * 40


class TestCliVersion:
    def test_is_the_package_version_and_the_release_sha(
        self, monkeypatch
    ):
        from trac_mcp_server import version as version_module

        monkeypatch.setattr(
            version_module, "release_sha", lambda: "c" * 40
        )
        assert (
            cli_version()
            == f"{trac_mcp_server.__version__}+g{'c' * 12}"
        )

    def test_says_unknown_without_a_sha(self, monkeypatch):
        from trac_mcp_server import version as version_module

        monkeypatch.setattr(version_module, "release_sha", lambda: None)
        assert (
            cli_version()
            == f"{trac_mcp_server.__version__} (commit unknown)"
        )
