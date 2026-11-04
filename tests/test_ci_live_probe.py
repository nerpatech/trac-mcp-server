"""Offline guard for ci-live.sh's credential probe (Trac /trac_mcp_server #128).

The probe is a ``python -`` heredoc. It once called ``load_dotenv(".env")``,
which reads only the cwd, so a git worktree nested in the main checkout
(``.claude/worktrees/*``, no ``.env`` of its own because it is gitignored)
failed the probe before pytest ran, although ``tests/conftest.py``'s bare
``load_dotenv()`` finds the main checkout's ``.env`` by walking up. These
tests run the real script by subprocess, in a throwaway directory tree, with
a stub ``pytest`` first on PATH so the live suite itself never runs.

``PYTHON_DOTENV_DISABLED`` is removed from the child's environment on
purpose: ci.sh sets it for the whole pytest run, and an inherited value would
make the probe's ``load_dotenv`` a silent no-op, so every case below would
look like "no .env anywhere".
"""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "ci-live.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is not on PATH"
)

SCRUBBED = (
    "TRAC_URL",
    "TRAC_USERNAME",
    "TRAC_PASSWORD",
    "TRAC_USERNAME_2",
    "TRAC_PASSWORD_2",
    "TRAC_INSECURE",
    "PYTHON_DOTENV_DISABLED",
)


def _dotenv(url: str) -> str:
    return (
        f"TRAC_URL={url}\n"
        "TRAC_USERNAME=u1\n"
        "TRAC_PASSWORD=p1\n"
        "TRAC_USERNAME_2=u2\n"
        "TRAC_PASSWORD_2=p2\n"
    )


@pytest.fixture
def layout(tmp_path):
    """tmp_path/parent/child/ci-live.sh, with a stub pytest and python on PATH."""
    child = tmp_path / "parent" / "child"
    child.mkdir(parents=True)
    script = child / "ci-live.sh"
    shutil.copy2(SCRIPT, script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "pytest"
    stub.write_text('#!/bin/sh\necho "STUB-PYTEST-RAN $@"\nexit 0\n')
    stub.chmod(0o755)
    # The script calls `python -`; make that name resolve to the interpreter
    # running this test, the one that has python-dotenv.
    (bin_dir / "python").symlink_to(sys.executable)

    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED}
    env["PATH"] = (
        f"{bin_dir}:{Path(sys.executable).parent}:{os.environ['PATH']}"
    )
    return tmp_path, child, script, env


def _run(tmp_path, script, env):
    return subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_probe_finds_dotenv_in_an_enclosing_directory(layout):
    tmp_path, _child, script, env = layout
    (tmp_path / "parent" / ".env").write_text(
        _dotenv("http://fake.invalid")
    )
    result = _run(tmp_path, script, env)
    assert result.returncode == 0, result.stderr
    assert "STUB-PYTEST-RAN" in result.stdout


def test_probe_refuses_when_no_dotenv_anywhere(layout):
    tmp_path, _child, script, env = layout
    for ancestor in tmp_path.parents:
        if (ancestor / ".env").exists():
            pytest.skip(
                f"{ancestor / '.env'} exists above tmp_path, so find_dotenv "
                "would find it and this case is not hermetic"
            )
    result = _run(tmp_path, script, env)
    assert result.returncode == 1
    assert "NOT run" in result.stderr
    assert "STUB-PYTEST-RAN" not in result.stdout


def test_probe_prefers_the_checkouts_own_dotenv(layout):
    tmp_path, child, script, env = layout
    (tmp_path / "parent" / ".env").write_text(
        "TRAC_URL=http://parent.invalid\n"
    )
    (child / ".env").write_text(_dotenv("http://child.invalid"))
    result = _run(tmp_path, script, env)
    assert result.returncode == 0, result.stderr
    assert "http://child.invalid" in result.stdout
