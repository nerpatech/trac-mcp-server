#!/usr/bin/env python3
"""Refuse an environment that differs from the declared one (Trac #262).

The declaration is ``constraints/deploy.txt`` (exact pins) and
``.python-version``. GitHub Actions and the local ``.venv`` both install
against them, so a green ``ci.sh`` means a green GitHub run. This script
is the check that the running environment really is that one: before
#262 the ``test`` job installed unconstrained and failed on a mistune and
a CPython release that no local run had ever seen.

It walks the installed requirement closure of ``trac-mcp-server`` with
extra ``dev`` (markers evaluated with the right ``extra``, extras named on
a requirement such as ``pyright[nodejs]`` followed) and fails for:

  - a member installed at a version other than its pin,
  - a member with no pin,
  - a member that is required but not installed,
  - a running Python other than ``.python-version``.

The root package is exempt from pinning, and a package installed outside
the closure is ignored.

Exit codes: 0 clean, 1 drift found, 2 cannot run (a declaration file is
missing or unreadable, or ``trac-mcp-server`` is not installed).

Usage:

    python scripts/check_env.py [--constraints FILE]
        [--python-version-file FILE]
"""

import argparse
import importlib.metadata
import platform
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

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


def _same_version(installed: str, declared: str) -> bool:
    try:
        return Version(installed) == Version(declared)
    except InvalidVersion:
        return installed == declared


def parse_constraints(path: Path) -> dict[str, str]:
    """``{normalised name: version}`` for every ``name==version`` line;
    blank lines and comments are skipped."""
    try:
        text = path.read_text()
    except OSError as e:
        raise CannotRun(f"cannot read {path}: {e}") from e
    pins: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, version = line.partition("==")
        if not sep or not name.strip() or not version.strip():
            raise CannotRun(f"{path}: not an exact pin: {line!r}")
        pins[norm(name.strip())] = version.strip()
    return pins


def read_python_version(path: Path) -> str:
    try:
        text = path.read_text().strip()
    except OSError as e:
        raise CannotRun(f"cannot read {path}: {e}") from e
    if not text:
        raise CannotRun(f"{path} is empty")
    return text


def closure(
    lookup: Lookup,
) -> tuple[dict[str, str], list[str]]:
    """The installed closure of ROOT with ROOT_EXTRAS, as
    ``({name: installed version}, [names required but missing])``,
    the root excluded. Raises CannotRun when ROOT is not installed."""
    try:
        lookup(ROOT)
    except importlib.metadata.PackageNotFoundError:
        raise CannotRun(f"{ROOT} is not installed") from None
    installed: dict[str, str] = {}
    missing: list[str] = []
    # name -> extras already walked for it; a dependency reached again
    # with a new extra is walked again, with the same extras it is not.
    walked: dict[str, set[str]] = {}
    queue: list[tuple[str, frozenset[str]]] = [(ROOT, ROOT_EXTRAS)]
    while queue:
        name, extras = queue.pop()
        key = norm(name)
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
        if key != norm(ROOT):
            installed[key] = dist.version
        for raw in dist.requires or []:
            req = Requirement(raw)
            contexts = walked[key] or {""}
            if req.marker is not None and not any(
                req.marker.evaluate({"extra": e}) for e in contexts
            ):
                continue
            queue.append((req.name, frozenset(req.extras)))
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
    args = parser.parse_args(argv)
    try:
        pins = parse_constraints(args.constraints)
        wanted = read_python_version(args.python_version_file)
        installed, missing = closure(lookup)
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
