"""ci.sh's exit code is the three-state contract, whatever the step's own
code was (Trac /trac_mcp_server #279).

ci.sh runs under ``set -e`` and stops at the first failing step, which used
to pass that step's own code straight through: pytest 2 to 5, pyright 3,
ruff 2, check_env.py's 1, build.sh's 127. Its header promises 0 clean, 1 a
check failed (fix the change), 2 could not run (fix the environment), so
each step's code is now mapped. These tests run the real ci.sh by
subprocess with every tool it calls replaced by a stub on PATH, one stub
exiting the code under test, and a stub build.sh in the working directory
(ci.sh runs ``bash build.sh`` from there). Nothing real is started.
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

STUBS = ("python", "ruff", "pyright", "pytest", "pip", "pyinstaller")
COLLECTION_ERROR = (
    "!!!!!!! Interrupted: 2 errors during collection !!!!!!!"
)


def _gate(tmp_path, stub=None, code=0, output="", build_code=0):
    """Run ci.sh with every tool stubbed to pass, except ``stub``, which
    prints ``output`` and exits ``code``; build.sh exits ``build_code``."""
    assert BASH is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in STUBS:
        rc, said = (code, output) if name == stub else (0, "")
        path = bin_dir / name
        path.write_text(
            f'#!/bin/sh\necho "STUB-{name} $*"\necho "{said}"\nexit {rc}\n'
        )
        path.chmod(0o755)
    (tmp_path / "build.sh").write_text(
        f'echo "STUB-build"\nexit {build_code}\n'
    )
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    result = subprocess.run(
        [BASH, str(REPO / "ci.sh")],
        cwd=tmp_path,
        # The stubs shadow the real tools; /usr/bin:/bin give the script
        # its coreutils (mktemp, tee, grep, rm) and bash for build.sh.
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "TMPDIR": str(scratch),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert list(scratch.iterdir()) == [], "temp file left behind"
    return result


def test_every_step_passing_is_exit_0(tmp_path):
    result = _gate(tmp_path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "STUB-build" in result.stdout
    assert "=== All checks passed ===" in result.stdout


@pytest.mark.parametrize(
    "stub, code, output, step, expected",
    [
        # check_env.py runs as `python scripts/check_env.py`: any refusal
        # means the environment does not match, never the change.
        ("python", 1, "", "env", 2),
        ("python", 2, "", "env", 2),
        ("ruff", 1, "", "ruff", 1),
        ("ruff", 2, "", "ruff", 2),
        ("pyright", 1, "", "pyright", 1),
        ("pyright", 3, "", "pyright", 2),
        ("pytest", 1, "", "pytest", 1),
        ("pytest", 2, COLLECTION_ERROR, "pytest", 1),
        ("pytest", 2, "!!!!!!! KeyboardInterrupt !!!!!!!", "pytest", 2),
        ("pytest", 3, "", "pytest", 2),
        ("pytest", 4, "", "pytest", 2),
        ("pytest", 5, "", "pytest", 2),
    ],
)
def test_a_failing_step_exits_with_the_mapped_code(
    tmp_path, stub, code, output, step, expected
):
    result = _gate(tmp_path, stub=stub, code=code, output=output)
    assert result.returncode == expected, (result.stdout, result.stderr)
    assert f"ci.sh: step {step} " in result.stderr
    assert f" exited {code}; ci.sh exits {expected} " in result.stderr
    assert "=== All checks passed ===" not in result.stdout
    assert "STUB-build" not in result.stdout, (
        "must stop at the first failure"
    )
    if output:
        assert output in result.stdout, (
            "the step's output is still shown"
        )


@pytest.mark.parametrize("code, expected", [(127, 2), (1, 1), (3, 1)])
def test_a_failing_build_exits_with_the_mapped_code(
    tmp_path, code, expected
):
    result = _gate(tmp_path, build_code=code)
    assert result.returncode == expected, (result.stdout, result.stderr)
    assert (
        f"ci.sh: step build (bash build.sh) exited {code}"
        in result.stderr
    )
    assert "=== All checks passed ===" not in result.stdout


def test_the_first_failing_step_is_the_one_reported(tmp_path):
    # ruff fails before pytest would run; pytest's stub is never reached.
    result = _gate(tmp_path, stub="ruff", code=1)
    assert result.returncode == 1
    assert "STUB-pytest" not in result.stdout
    assert "STUB-pyright" not in result.stdout
