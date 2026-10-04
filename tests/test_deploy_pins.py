"""The deploy pins are tracked, and tracking them does not arm deploy.sh (Trac #109).

Until #109 the pin set lived only as an untracked file in debian's deploy
clone, because its presence was also deploy.sh's "am I the deploy clone"
guard. These tests hold the two jobs apart: the pins must cover every runtime
dependency, and a checkout carrying them must still be refused by deploy.sh.
"""

import importlib.metadata
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import tomllib
from packaging.requirements import Requirement
from packaging.version import Version

REPO = Path(__file__).resolve().parent.parent
PINS = REPO / "constraints" / "deploy.txt"


def _norm(name: str) -> str:
    """PEP 503 name normalisation, so `PyYAML` matches `pyyaml`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins() -> dict[str, Version]:
    pins = {}
    for line in PINS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, version = line.partition("==")
        assert sep, (
            f"constraints/deploy.txt: not an exact pin: {line!r}"
        )
        pins[_norm(name)] = Version(version)
    return pins


def _runtime_deps() -> list[Requirement]:
    with open(REPO / "pyproject.toml", "rb") as f:
        deps = tomllib.load(f)["project"]["dependencies"]
    return [Requirement(d) for d in deps]


def test_every_runtime_dependency_is_pinned():
    pins = _pins()
    missing = [
        r.name for r in _runtime_deps() if _norm(r.name) not in pins
    ]
    assert not missing, f"runtime dependencies with no pin: {missing}"


def test_every_pin_satisfies_its_specifier():
    # A pin pyproject.toml no longer accepts passes CI -- nothing there
    # installs with the constraints -- and fails only on the deploy host,
    # after deploy.sh has already pulled.
    pins = _pins()
    rejected = [
        f"{r.name}: pinned {pins[_norm(r.name)]}, pyproject wants {r.specifier}"
        for r in _runtime_deps()
        if _norm(r.name) in pins
        and not r.specifier.contains(
            pins[_norm(r.name)], prereleases=True
        )
    ]
    assert not rejected, rejected


def test_every_transitive_dependency_is_pinned():
    # Walks the installed metadata from trac-mcp-server down, following the
    # extras each requirement asks for (mcp[cli] pulls in typer, rich, ...)
    # and skipping requirements whose marker is false here.
    pins = _pins()
    seen: set[tuple[str, frozenset[str]]] = set()
    todo = [("trac-mcp-server", frozenset[str]())]
    while todo:
        name, extras = todo.pop()
        if (_norm(name), extras) in seen:
            continue
        seen.add((_norm(name), extras))
        for spec in (
            importlib.metadata.distribution(name).requires or []
        ):
            req = Requirement(spec)
            envs = [{"extra": e} for e in extras] or [{"extra": ""}]
            if req.marker and not any(
                req.marker.evaluate(e) for e in envs
            ):
                continue
            todo.append((req.name, frozenset(req.extras)))
    names = {name for name, _ in seen} - {"trac-mcp-server"}
    assert len(names) > 20, f"walk found too little to be real: {names}"
    missing = sorted(n for n in names if n not in pins)
    assert not missing, (
        f"transitive dependencies with no pin: {missing}"
    )


def test_package_itself_is_not_pinned():
    # `pip install -c constraints/deploy.txt .` installs the checkout; a pin on
    # the package itself would fight it.
    assert "trac-mcp-server" not in _pins()


def _run_deploy(
    tmp_path: Path, home: Path, *, has_unit: bool
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run a copy of deploy.sh from a checkout-shaped dir that HAS the pins.

    `git` and `systemctl` are stubs that log their calls. `git` always fails,
    so nothing real can be pulled or installed whatever the guards decide;
    `systemctl` succeeds when `has_unit` (debian) and fails otherwise (kpoxa).
    Returns the result and the stubs' call log.
    """
    checkout = tmp_path / "checkout"
    (checkout / "constraints").mkdir(parents=True)
    shutil.copy(REPO / "deploy.sh", checkout / "deploy.sh")
    shutil.copy(PINS, checkout / "constraints" / "deploy.txt")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    for tool, status in (
        ("git", 1),
        ("systemctl", 0 if has_unit else 1),
    ):
        stub = stubs / tool
        stub.write_text(
            f'#!/bin/sh\necho "{tool} $*" >> "{log}"\nexit {status}\n'
        )
        stub.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{stubs}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["bash", str(checkout / "deploy.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result, (log.read_text() if log.exists() else "")


def _ran_git(calls: str) -> bool:
    return re.search(r"^git ", calls, re.MULTILINE) is not None


def test_deploy_refuses_a_checkout_that_carries_the_pins(tmp_path):
    # debian: the unit exists, but this is a working checkout.
    result, calls = _run_deploy(
        tmp_path, tmp_path / "home", has_unit=True
    )
    assert result.returncode == 1
    assert "runs only from the deploy clone" in result.stderr
    assert "1/5" not in result.stdout
    assert not _ran_git(calls)


@pytest.mark.parametrize("clone_at_deploy_path", [True, False])
def test_deploy_refuses_a_host_without_the_systemd_unit(
    tmp_path, clone_at_deploy_path
):
    # kpoxa. At the deploy path, only the missing unit can refuse it. Anywhere
    # else, the unit guard must still be the one that answers, so kpoxa is
    # pointed at its own procedure rather than told to move its clone.
    home = tmp_path / "home"
    if clone_at_deploy_path:
        (home / "srv").mkdir(parents=True)
        (home / "srv" / "trac-mcp-server-live").symlink_to(
            tmp_path / "checkout"
        )
    result, calls = _run_deploy(tmp_path, home, has_unit=False)
    assert result.returncode == 1
    assert "cannot read systemd user unit" in result.stderr
    assert "1/5" not in result.stdout
    assert not _ran_git(calls)
