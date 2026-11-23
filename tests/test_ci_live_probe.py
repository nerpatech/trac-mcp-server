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
import shlex
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


def _dotenv(url: str, user: str = "u1") -> str:
    return (
        f"TRAC_URL={url}\n"
        f"TRAC_USERNAME={user}\n"
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
    # running this test, the one that has python-dotenv. A wrapper rather than
    # a symlink: the interpreter then runs from its real path, so venv
    # detection (pyvenv.cfg next to the executable) works as usual.
    wrapper = bin_dir / "python"
    wrapper.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n'
    )
    wrapper.chmod(0o755)

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
    # 2, not 1: the suite could not run, which is not a failed run
    # (Trac #279, the exit-code contract in ci-live.sh's header).
    assert result.returncode == 2
    assert "NOT run" in result.stderr
    assert "STUB-PYTEST-RAN" not in result.stdout


def test_probe_prefers_the_checkouts_own_dotenv(layout):
    tmp_path, child, script, env = layout
    (tmp_path / "parent" / ".env").write_text(
        _dotenv("http://parent.invalid", user="parent_user")
    )
    (child / ".env").write_text(
        _dotenv("http://child.invalid", user="child_user")
    )
    result = _run(tmp_path, script, env)
    assert result.returncode == 0, result.stderr
    assert "http://child.invalid" in result.stdout
    assert "child_user" in result.stdout
    assert "parent" not in result.stdout


def test_no_arguments_still_runs_the_probe_then_pytest(layout):
    # Trac #161 added `--daemon`; the argument-less path must be the
    # one it always was: probe banner, probe passes, the suite runs.
    tmp_path, _child, script, env = layout
    (tmp_path / "parent" / ".env").write_text(
        _dotenv("http://fake.invalid")
    )
    result = _run(tmp_path, script, env)
    assert result.returncode == 0, result.stderr
    assert "=== Live credentials ===" in result.stdout
    assert "STUB-PYTEST-RAN tests/ -q --run-live" in result.stdout
    assert "=== Live checks passed ===" in result.stdout
    assert "DAEMON SMOKE" not in result.stdout


def test_a_probe_that_crashes_is_exit_2_not_1(layout):
    # Any non-zero exit of the probe is "could not run", not only its own
    # sys.exit(2) paths: a .env that is not UTF-8 makes load_dotenv raise
    # UnicodeDecodeError, which Python exits 1 on (Trac #279).
    tmp_path, _child, script, env = layout
    (tmp_path / "parent" / ".env").write_bytes(b"TRAC_URL=\xff\xfe\n")
    result = _run(tmp_path, script, env)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "UnicodeDecodeError" in result.stderr
    assert "NOT run" in result.stderr
    assert "STUB-PYTEST-RAN" not in result.stdout


def _stub_pytest_exiting(layout, code, output="STUB-PYTEST-RAN"):
    tmp_path, _child, script, env = layout
    (tmp_path / "parent" / ".env").write_text(
        _dotenv("http://fake.invalid")
    )
    stub = tmp_path / "bin" / "pytest"
    stub.write_text(f'#!/bin/sh\necho "{output}"\nexit {code}\n')
    # The script keeps a copy of pytest's output in a mktemp file to read
    # its verdict; a TMPDIR of our own shows that file is removed after.
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    result = _run(tmp_path, script, {**env, "TMPDIR": str(scratch)})
    assert list(scratch.iterdir()) == [], "temp file left behind"
    return result


def test_a_failing_live_suite_is_exit_1(layout):
    result = _stub_pytest_exiting(layout, 1)
    assert result.returncode == 1
    assert "STUB-PYTEST-RAN" in result.stdout
    assert "=== Live checks passed ===" not in result.stdout


@pytest.mark.parametrize("code", [2, 3, 4, 5])
def test_a_live_suite_that_did_not_complete_is_exit_2(layout, code):
    # pytest's own codes 2-5: interrupted, internal error, usage error, no
    # tests collected. None of them is a verdict on the change.
    result = _stub_pytest_exiting(layout, code)
    assert result.returncode == 2
    assert "STUB-PYTEST-RAN" in result.stdout
    assert "=== Live checks passed ===" not in result.stdout


@pytest.mark.parametrize("count", ["1 error", "3 errors"])
def test_a_collection_error_is_exit_1(layout, count):
    # pytest also exits 2 when a test module fails to import. That is a
    # broken change, not a broken environment, so it is a failure (Trac
    # #279). The line is pytest's own summary, streamed through unchanged.
    line = f"!!!!!!! Interrupted: {count} during collection !!!!!!!"
    result = _stub_pytest_exiting(layout, 2, output=line)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert line in result.stdout
    assert "=== Live checks passed ===" not in result.stdout


def test_an_interruption_without_collection_errors_stays_exit_2(layout):
    line = "!!!!!!! KeyboardInterrupt !!!!!!!"
    result = _stub_pytest_exiting(layout, 2, output=line)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert line in result.stdout
    assert "NOT run" in result.stderr
