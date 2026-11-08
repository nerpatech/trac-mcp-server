"""Version checking utilities for detecting stale MCP server binaries."""

from pathlib import Path


def release_sha() -> str | None:
    """The commit this build was made from, or None.

    ``setup.py`` writes ``trac_mcp_server/_release.py`` into the build
    directory when it builds from a source checkout (Trac
    /trac_mcp_server #170); a checkout run in place, an editable install
    and a build from an sdist have no such module. A trailing ``-dirty``
    means a tracked file differed from the commit at build time.
    """
    try:
        from ._release import RELEASE_SHA
    except ImportError:
        return None
    if isinstance(RELEASE_SHA, str) and RELEASE_SHA:
        return RELEASE_SHA
    return None


def format_server_version(version: str, sha: str | None) -> str:
    """``serverInfo.version``: the package version, plus a PEP
    440-style local segment naming the commit when it is known.

    ``2.2.0`` + ``<sha>`` gives ``2.2.0+g<first 12 of sha>``; a dirty
    sha gives ``2.2.0+g<12>.dirty``.
    """
    if not sha:
        return version
    dirty = sha.endswith("-dirty")
    short = sha.removesuffix("-dirty")[:12]
    return f"{version}+g{short}" + (".dirty" if dirty else "")


def check_version_consistency() -> tuple[bool, str]:
    """Check if runtime version matches source version in pyproject.toml.

    Returns:
        Tuple of (is_consistent, message) where:
        - is_consistent: True if versions match, False otherwise
        - message: Descriptive message about version status

    This function compares the runtime __version__ against the version
    specified in pyproject.toml to detect when a PyInstaller binary
    has become stale and needs rebuilding.
    """
    # Import runtime version
    try:
        from . import __version__ as runtime_version
    except ImportError:
        return False, "Cannot import __version__ from trac_mcp_server"

    # Import tomllib with fallback for Python < 3.11
    try:
        import tomllib  # type: ignore[import-not-found]  # Python 3.11+ stdlib, fallback to tomli
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return (
                False,
                "tomllib/tomli not available for version checking",
            )

    # Locate pyproject.toml relative to this module
    pyproject_path = (
        Path(__file__).parent.parent.parent / "pyproject.toml"
    )

    if not pyproject_path.exists():
        return (
            False,
            "Cannot find pyproject.toml for version comparison",
        )

    # Read source version from pyproject.toml
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
            source_version = data.get("project", {}).get(
                "version", "unknown"
            )
    except Exception as e:
        return False, f"Failed to read version from pyproject.toml: {e}"

    # Compare versions
    if runtime_version != source_version:
        return False, (
            f"Version mismatch detected! "
            f"Runtime: {runtime_version}, Source: {source_version}. "
            f"Binary is stale - rebuild with: pyinstaller trac-mcp-server.spec"
        )

    return True, f"Version verified: {runtime_version}"
