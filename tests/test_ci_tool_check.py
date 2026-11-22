"""ci.sh and ci-live.sh refuse to start without the tools they call
(Trac /trac_mcp_server #279, L34).

Both scripts call python, ruff, pyright and pytest by bare name, so a shell
whose PATH lacks the venv fails deep inside, with a bare "command not found"
(exit 127) that reads like a broken change. They now check first, print one
line naming what is missing and the fix, and exit 2: the "cannot run" state
of the three-state contract (0 clean, 1 failed, 2 not run). These tests run
the real scripts by subprocess with a PATH holding only stub tools, so
nothing real is ever started.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None, reason="bash is not on PATH"
)

CI_TOOLS = ("python", "ruff", "pyright", "pytest")


def _path_with(tmp_path, *tools):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in tools:
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    return str(bin_dir)


def _run(script, tmp_path, *tools, args=()):
    assert BASH is not None
    return subprocess.run(
        [BASH, str(REPO / script), *args],
        cwd=tmp_path,
        env={"PATH": _path_with(tmp_path, *tools)},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_ci_sh_with_no_tools_is_exit_2_naming_all_four(tmp_path):
    result = _run("ci.sh", tmp_path)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert _named_missing(result.stderr) == list(CI_TOOLS)
    assert "PATH=" in result.stderr
    assert "./ci.sh" in result.stderr
    assert "=== " not in result.stdout, "must stop before any step"


def _named_missing(stderr):
    """The tools the one-line message lists, from
    "<script>: not on PATH: a, b. Fix: ..."."""
    named = stderr.split("not on PATH: ", 1)[1].split(". Fix", 1)[0]
    return named.split(", ")


@pytest.mark.parametrize("missing", CI_TOOLS)
def test_ci_sh_names_only_the_missing_tool(tmp_path, missing):
    present = [t for t in CI_TOOLS if t != missing]
    result = _run("ci.sh", tmp_path, *present)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert _named_missing(result.stderr) == [missing]


def test_ci_sh_message_is_one_line(tmp_path):
    result = _run("ci.sh", tmp_path)
    assert len(result.stderr.strip().splitlines()) == 1


def test_ci_live_sh_without_python_or_pytest_is_exit_2(tmp_path):
    result = _run("ci-live.sh", tmp_path)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert _named_missing(result.stderr) == ["python", "pytest"]
    assert "./ci-live.sh" in result.stderr
    assert "PATH=" in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1


def test_ci_live_sh_daemon_mode_needs_python_not_pytest(tmp_path):
    # --daemon execs scripts/daemon_smoke.py and never runs pytest, so a
    # missing pytest must not stop it; a missing python must.
    result = _run(
        "ci-live.sh", tmp_path, args=("--daemon", "http://x.invalid")
    )
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert _named_missing(result.stderr) == ["python"]
