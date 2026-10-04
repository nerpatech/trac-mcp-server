"""The deploy pins are tracked, and tracking them does not arm deploy.sh (Trac #109).

Until #109 the pin set lived only as an untracked file in debian's deploy
clone, because its presence was also deploy.sh's "am I the deploy clone"
guard. These tests hold the two jobs apart: the pins must cover every runtime
dependency, and a checkout carrying them must still be refused by deploy.sh.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import tomllib

REPO = Path(__file__).resolve().parent.parent
PINS = REPO / "constraints" / "deploy.txt"


def _norm(name: str) -> str:
    """PEP 503 name normalisation, so `PyYAML` matches `pyyaml`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins() -> dict[str, str]:
    pins = {}
    for line in PINS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, version = line.partition("==")
        assert sep, (
            f"constraints/deploy.txt: not an exact pin: {line!r}"
        )
        pins[_norm(name)] = version
    return pins


def _runtime_deps() -> list[str]:
    with open(REPO / "pyproject.toml", "rb") as f:
        deps = tomllib.load(f)["project"]["dependencies"]
    return [
        _norm(re.split(r"[\s\[<>=!~;]", d, maxsplit=1)[0]) for d in deps
    ]


def test_every_runtime_dependency_is_pinned():
    pins = _pins()
    missing = [d for d in _runtime_deps() if d not in pins]
    assert not missing, f"runtime dependencies with no pin: {missing}"


def test_package_itself_is_not_pinned():
    # `pip install -c constraints/deploy.txt .` installs the checkout; a pin on
    # the package itself would fight it.
    assert "trac-mcp-server" not in _pins()


def _run_deploy(
    tmp_path: Path, home: Path
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run a copy of deploy.sh from a checkout-shaped dir that HAS the pins.

    `git` and `systemctl` are stubs that log and fail, so nothing real can be
    pulled, installed or restarted whatever the guard decides. Returns the
    result and the stubs' call log.
    """
    checkout = tmp_path / "checkout"
    (checkout / "constraints").mkdir(parents=True)
    shutil.copy(REPO / "deploy.sh", checkout / "deploy.sh")
    shutil.copy(PINS, checkout / "constraints" / "deploy.txt")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("git", "systemctl"):
        stub = stubs / tool
        stub.write_text(
            f'#!/bin/sh\necho "{tool} $*" >> "{log}"\nexit 1\n'
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


def test_deploy_refuses_a_checkout_that_carries_the_pins(tmp_path):
    result, calls = _run_deploy(tmp_path, home=tmp_path / "home")
    assert result.returncode == 1
    assert "runs only from the deploy clone" in result.stderr
    assert "1/5" not in result.stdout
    assert calls == ""


def test_deploy_refuses_a_host_without_the_systemd_unit(tmp_path):
    # Make the checkout BE the deploy clone, so the path guard passes and the
    # unit guard (the stub systemctl fails) is what refuses -- kpoxa's case.
    home = tmp_path / "home"
    (home / "srv").mkdir(parents=True)
    (home / "srv" / "trac-mcp-server-live").symlink_to(
        tmp_path / "checkout"
    )
    result, calls = _run_deploy(tmp_path, home=home)
    assert result.returncode == 1
    assert "no systemd user unit" in result.stderr
    assert "1/5" not in result.stdout
    assert "git" not in calls
