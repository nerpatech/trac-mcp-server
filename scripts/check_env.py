#!/usr/bin/env python3
"""Refuse an environment that differs from the declared one (Trac #262).

The declaration is ``constraints/deploy.txt`` (exact pins) and
``.python-version``. GitHub Actions and the local ``.venv`` both install
against them, so a green ``ci.sh`` means a green GitHub run. This script
is the check that the running environment really is that one: before
#262 the ``test`` job installed unconstrained and failed on a mistune and
a CPython release that no local run had ever seen.

It walks the requirement closure of ``trac-mcp-server`` with extra
``dev`` (markers evaluated with the right ``extra``, extras named on a
requirement such as ``pyright[nodejs]`` followed). The root package's own
direct requirements are read from the checkout's ``pyproject.toml``
(``[project].dependencies`` plus ``[project.optional-dependencies].dev``),
not from installed metadata, which goes stale after a pyproject edit with
no reinstall and, in a worktree, belongs to another checkout; every
further requirement comes from installed metadata. It fails for:

  - a member installed at a version other than its pin,
  - a member with no pin,
  - a member that is required but not installed,
  - a running Python other than ``.python-version``.

The root package is exempt from pinning, and a package installed outside
the closure is ignored.

Exit codes: 0 clean, 1 drift found, 2 cannot run (a declaration file or
``pyproject.toml`` is missing, unreadable or malformed, a requirement or
marker cannot be parsed or evaluated, ``packaging`` is not installed, or
``trac-mcp-server`` is not installed).

Usage:

    python scripts/check_env.py [--constraints FILE]
        [--python-version-file FILE] [--pyproject FILE]
"""

import argparse
import importlib.metadata
import platform
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import tomllib

if TYPE_CHECKING:
    from packaging.requirements import Requirement

# ``packaging`` is imported inside the functions that use it, so a venv
# without it exits 2 with a message (see ``require_packaging``) instead
# of a traceback exiting 1.

REPO = Path(__file__).resolve().parent.parent
ROOT = "trac-mcp-server"
ROOT_EXTRAS = frozenset({"dev"})


class Dist(Protocol):
    """The part of ``importlib.metadata.Distribution`` this reads."""

    @property
    def version(self) -> str: ...

    @property
    def requires(self) -> list[str] | None: ...


Lookup = Callable[[str], Dist]


class CannotRun(Exception):
    """The check has nothing to compare against (exit 2)."""


def norm(name: str) -> str:
    """PEP 503 name normalisation."""
    return re.sub(r"[-_.]+", "-", name).lower()


def require_packaging() -> None:
    """Raise CannotRun when ``packaging`` is not importable."""
    try:
        import packaging.markers  # noqa: F401
        import packaging.requirements  # noqa: F401
        import packaging.version  # noqa: F401
    except ImportError as e:
        raise CannotRun(
            f"the packaging library is not importable: {e}"
        ) from e


def _same_version(installed: str, declared: str) -> bool:
    from packaging.version import InvalidVersion, Version

    try:
        return Version(installed) == Version(declared)
    except InvalidVersion:
        return installed == declared


_PIN = re.compile(
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"\s*==\s*(?P<version>[A-Za-z0-9][A-Za-z0-9._+!-]*)"
)


def parse_constraints(path: Path) -> dict[str, str]:
    """``{normalised name: version}`` for every ``name==version`` line.

    Blank lines, whole-line comments and inline ``# comments`` are
    skipped. Anything else (an environment marker after ``;``, a ``===``
    pin, a range, an extra, a wildcard) raises CannotRun naming the line
    number: the check compares versions for equality and cannot honour
    what it does not understand."""
    try:
        text = path.read_text()
    except OSError as e:
        raise CannotRun(f"cannot read {path}: {e}") from e
    pins: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        if ";" in line:
            raise CannotRun(
                f"{path}:{number}: an environment marker is not "
                f"supported, pins are unconditional: {line!r}"
            )
        match = _PIN.fullmatch(line)
        if match is None:
            raise CannotRun(
                f"{path}:{number}: not an exact name==version pin: "
                f"{line!r}"
            )
        pins[norm(match["name"])] = match["version"]
    return pins


def read_python_version(path: Path) -> str:
    """The one version in a ``.python-version`` file; blank lines and
    ``#`` lines are ignored."""
    try:
        text = path.read_text()
    except OSError as e:
        raise CannotRun(f"cannot read {path}: {e}") from e
    versions = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not versions:
        raise CannotRun(f"{path} is empty")
    if len(versions) > 1:
        raise CannotRun(
            f"{path} lists {len(versions)} versions "
            f"({', '.join(versions)}); exactly one version is required"
        )
    return versions[0]


def root_requirements(path: Path) -> list[tuple[str, str]]:
    """``[(requirement, extra)]`` for the root package, from pyproject.

    ``[project].dependencies`` carry extra ``""``; each entry of
    ``[project.optional-dependencies]`` for a name in ROOT_EXTRAS carries
    that extra, which is what its markers are evaluated with."""
    try:
        with path.open("rb") as f:
            project = tomllib.load(f).get("project", {})
    except OSError as e:
        raise CannotRun(f"cannot read {path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise CannotRun(f"cannot parse {path}: {e}") from e
    if not isinstance(project, dict):
        raise CannotRun(f"{path}: [project] is not a table")
    found: list[tuple[str, str]] = [
        (r, "") for r in project.get("dependencies", [])
    ]
    optional = project.get("optional-dependencies", {})
    for extra in sorted(ROOT_EXTRAS):
        found.extend((r, extra) for r in optional.get(extra, []))
    return found


def _parse(raw: str) -> "Requirement":
    from packaging.requirements import InvalidRequirement, Requirement

    try:
        return Requirement(raw)
    except InvalidRequirement as e:
        raise CannotRun(f"cannot parse requirement {raw!r}: {e}") from e


def _applies(req: "Requirement", extras: Sequence[str]) -> bool:
    """Whether ``req``'s marker holds with any of ``extras`` (``""`` when
    none)."""
    if req.marker is None:
        return True
    from packaging.markers import (
        InvalidMarker,
        UndefinedEnvironmentName,
    )

    try:
        return any(
            req.marker.evaluate({"extra": e}) for e in extras or [""]
        )
    except (InvalidMarker, UndefinedEnvironmentName) as e:
        raise CannotRun(
            f"cannot evaluate the marker of {str(req)!r}: {e}"
        ) from e


def closure(
    lookup: Lookup,
    root_reqs: Sequence[tuple[str, str]],
) -> tuple[dict[str, str], list[str]]:
    """The closure of ROOT, as ``({name: installed version}, [names
    required but missing])``, the root excluded.

    The root's direct requirements are ``root_reqs`` (from pyproject, see
    ``root_requirements``); every other package's come from its installed
    metadata. Raises CannotRun when ROOT is not installed or a
    requirement cannot be parsed or evaluated."""
    try:
        lookup(ROOT)
    except importlib.metadata.PackageNotFoundError:
        raise CannotRun(f"{ROOT} is not installed") from None
    installed: dict[str, str] = {}
    missing: list[str] = []
    # name -> extras already walked for it; a dependency reached again
    # with a new extra is walked again, with the same extras it is not.
    walked: dict[str, set[str]] = {}
    queue: list[tuple[str, frozenset[str]]] = []

    def enqueue(raw: str, extras: Sequence[str]) -> None:
        req = _parse(raw)
        if _applies(req, extras):
            queue.append((req.name, frozenset(req.extras)))

    for raw, extra in root_reqs:
        enqueue(raw, [extra])
    while queue:
        name, extras = queue.pop()
        key = norm(name)
        if key == norm(ROOT):
            continue
        todo = extras - walked.get(key, set())
        if key in walked and not todo:
            continue
        walked.setdefault(key, set()).update(extras)
        try:
            dist = lookup(name)
        except importlib.metadata.PackageNotFoundError:
            if key not in missing:
                missing.append(key)
            continue
        installed[key] = dist.version
        for raw in dist.requires or []:
            enqueue(raw, sorted(walked[key]))
    return installed, missing


def problems(
    pins: dict[str, str],
    installed: dict[str, str],
    missing: list[str],
    wanted_python: str,
    running_python: str,
) -> list[str]:
    found: list[str] = []
    for name in sorted(installed):
        version = installed[name]
        if name not in pins:
            found.append(
                f"{name}: installed {version}, "
                "no pin in constraints/deploy.txt"
            )
        elif not _same_version(version, pins[name]):
            found.append(
                f"{name}: installed {version}, declared {pins[name]}"
            )
    for name in sorted(missing):
        found.append(f"{name}: required but not installed")
    if running_python != wanted_python:
        found.append(
            f"Python: running {running_python}, "
            f".python-version declares {wanted_python}"
        )
    return found


def main(
    argv: Sequence[str] | None = None,
    *,
    lookup: Lookup = importlib.metadata.distribution,
    python_version: Callable[[], str] = platform.python_version,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--constraints",
        type=Path,
        default=REPO / "constraints/deploy.txt",
    )
    parser.add_argument(
        "--python-version-file",
        type=Path,
        default=REPO / ".python-version",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=REPO / "pyproject.toml",
    )
    args = parser.parse_args(argv)
    try:
        require_packaging()
        pins = parse_constraints(args.constraints)
        wanted = read_python_version(args.python_version_file)
        installed, missing = closure(
            lookup, root_requirements(args.pyproject)
        )
    except CannotRun as e:
        print(f"env: cannot run: {e}", file=sys.stderr)
        return 2
    running = python_version()
    found = problems(pins, installed, missing, wanted, running)
    if found:
        print(
            "env: the environment differs from the declared one "
            "(constraints/deploy.txt, .python-version):",
            file=sys.stderr,
        )
        for line in found:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(
        f"env: OK, {len(installed)} packages match "
        f"constraints/deploy.txt, Python {running}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
