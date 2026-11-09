"""Subprocess-level end-to-end tests for the trac-convert CLI (Phase 18-03).

These tests spawn the CLI as a real subprocess to exercise:
- The run() wrapper in cli/convert.py (sys.exit propagation)
- The [project.scripts] entry-point binding in pyproject.toml
- Real OS pipe behavior and fd separation of stdout/stderr

In-process main(argv) tests in test_cli_convert.py cover behavior; these
tests cover WIRING. Do not duplicate behavioral coverage here.
"""

import os
import subprocess
import sys

from trac_mcp_server import __version__

# Invoke via the cli package __main__.py, which calls run() -> sys.exit(main()).
# Using python -m trac_mcp_server.cli (not trac_mcp_server.cli.convert) because
# the __main__.py lives at the cli package level, not inside convert.py.
# This exercises the full run() -> sys.exit() wiring and real OS pipe behaviour.
CLI_MODULE = "trac_mcp_server.cli"


def _run_cli(
    *args: str, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke `python -m trac_mcp_server.cli ...` with optional stdin."""
    return subprocess.run(
        [sys.executable, "-m", CLI_MODULE, *args],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Task 1: Smoke tests (version, help, exit codes, real pipe)
# ---------------------------------------------------------------------------


def test_subprocess_version_flag_exits_0_and_prints_version() -> None:
    """--version exits 0, prints version and program name, no stderr."""
    result = _run_cli("--version")
    assert result.returncode == 0
    assert __version__ in result.stdout
    assert "trac-convert" in result.stdout
    assert result.stderr == ""


def test_subprocess_help_flag_exits_0_and_prints_usage() -> None:
    """--help exits 0 and includes usage text and program name."""
    result = _run_cli("--help")
    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert "trac-convert" in result.stdout


def test_subprocess_missing_required_to_exits_2() -> None:
    """Omitting --to exits 2 (argparse error) with diagnostic on stderr."""
    result = _run_cli()
    assert result.returncode == 2
    assert "--to" in result.stderr
    assert "required" in result.stderr.lower()
    assert result.stdout == ""


def test_subprocess_invalid_to_choice_exits_2() -> None:
    """Passing an invalid --to value exits 2 with argparse error on stderr."""
    result = _run_cli("--to", "wat")
    assert result.returncode == 2
    assert result.stdout == ""
    # argparse emits "invalid choice: 'wat'" or similar
    assert "wat" in result.stderr or "invalid choice" in result.stderr


def test_subprocess_stdin_to_stdout_pipe_roundtrips_content() -> None:
    """Stdin→stdout pipe: real OS pipe, run() wrapper, and convert wiring."""
    result = _run_cli(
        "--from", "md", "--to", "tracwiki", input_text="# Hello\n"
    )
    assert result.returncode == 0
    assert result.stdout.startswith("= Hello =")
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# Task 2: fd separation, file I/O, missing file, pipe roundtrip, UTF-8
# ---------------------------------------------------------------------------


def test_subprocess_stdout_and_stderr_are_separate_fds() -> None:
    """Verbose info: lines go only to stderr; stdout carries converted text."""
    result = _run_cli(
        "--from", "md", "--to", "tracwiki", "-v", input_text="# H\n"
    )
    assert result.returncode == 0
    assert result.stdout.startswith("= H =")
    assert "info:" in result.stderr
    assert "info:" not in result.stdout


def test_subprocess_file_input_and_output_flags(tmp_path) -> None:
    """Positional FILE + -o FILE flags work end-to-end via real subprocess."""
    in_file = tmp_path / "in.md"
    out_file = tmp_path / "out.tw"
    in_file.write_text("# Title\n", encoding="utf-8")

    result = _run_cli(
        str(in_file),
        "--from",
        "md",
        "--to",
        "tracwiki",
        "-o",
        str(out_file),
    )
    assert result.returncode == 0
    assert out_file.read_text(encoding="utf-8").startswith("= Title =")
    assert result.stdout == ""
    assert result.stderr == ""


def test_subprocess_missing_input_file_exits_1(tmp_path) -> None:
    """Referencing a non-existent input file exits 1 with diagnostic on stderr."""
    missing = tmp_path / "does_not_exist.md"
    result = _run_cli(str(missing), "--from", "md", "--to", "md")
    assert result.returncode == 1
    assert "cannot read input file" in result.stderr
    assert result.stdout == ""


def test_subprocess_real_pipe_md_tw_then_tw_md_needs_trac(
    tmp_path,
) -> None:
    """Two-stage pipe through two real subprocesses.

    md -> tracwiki runs offline. tracwiki -> md needs Trac to render the
    text (Trac /trac_mcp_server #210): with none configured the second
    stage exits 4 through the real sys.exit wiring, says what to set on
    stderr and writes nothing to stdout.
    """
    p1 = _run_cli(
        "--from",
        "md",
        "--to",
        "tracwiki",
        input_text="# Hello\n\nBody.\n",
    )
    assert p1.returncode == 0

    p2 = _run_cli_no_trac_env(
        "--from",
        "tracwiki",
        "--to",
        "md",
        input_text=p1.stdout,
        cwd=str(tmp_path),
    )
    assert p2.returncode == 4
    assert (
        "trac-convert: TracWiki -> Markdown needs a Trac connection"
        in p2.stderr
    )
    assert "TRAC_URL" in p2.stderr
    assert "Traceback" not in p2.stderr
    assert p2.stdout == ""


def test_subprocess_non_ascii_stdin_stdout_pipe() -> None:
    """Non-ASCII (UTF-8) input flows cleanly through the subprocess boundary."""
    result = _run_cli(
        "--from", "md", "--to", "md", input_text="# Café\n"
    )
    assert result.returncode == 0
    assert "Café" in result.stdout


# ---------------------------------------------------------------------------
# Phase 24: Trac-flag wiring smoke tests
#
# These tests exercise the argparse dispatch and exit-code propagation
# for the three Trac-facing flags added in Phases 20-22
# (--check-trac, --from-wiki, --to-wiki). Because subprocess tests
# cannot monkeypatch TracClient, they must ONLY use code paths that
# fail BEFORE any Trac wire call is attempted — i.e., missing
# credentials (bootstrap_config raises ValueError) and mutex
# violations. Behavioral coverage of the wire-error paths lives in
# tests/test_cli_convert_trac.py.
# ---------------------------------------------------------------------------


def _run_cli_no_trac_env(
    *args: str,
    input_text: str | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the CLI subprocess with all TRAC_* env vars scrubbed.

    Prevents a real .env in the repo root from injecting live Trac
    credentials that would attempt real network calls during
    subprocess CI runs. Required for the wiring-only tests that must
    exit before any wire call.
    """
    env = os.environ.copy()
    for var in (
        "TRAC_URL",
        "TRAC_USERNAME",
        "TRAC_PASSWORD",
        "TRAC_INSECURE",
        "TRAC_MCP_CONFIG",
    ):
        env.pop(var, None)
    # Disable dotenv's .env file loading so that the project-root .env
    # cannot inject real credentials even when load_dotenv() searches upward
    # from the installed module's __file__ location (which is in src/
    # and finds the project's .env regardless of cwd).
    env["PYTHON_DOTENV_DISABLED"] = "1"
    return subprocess.run(
        [sys.executable, "-m", CLI_MODULE, *args],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=cwd,
    )


def test_subprocess_check_trac_help_shows_all_five_trac_flags() -> None:
    result = _run_cli("--help")
    assert result.returncode == 0
    assert "--check-trac" in result.stdout
    assert "--trac-url" in result.stdout
    assert "--trac-username" in result.stdout
    assert "--trac-password" in result.stdout
    assert "--trac-password-file" in result.stdout
    assert "--from-wiki" in result.stdout
    assert "--to-wiki" in result.stdout


def test_subprocess_check_trac_no_env_exits_4_friendly_error(
    tmp_path,
) -> None:
    result = _run_cli_no_trac_env("--check-trac", cwd=str(tmp_path))
    assert result.returncode == 4
    assert "no Trac credentials found" in result.stderr
    assert "TRAC_URL" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_subprocess_from_wiki_no_env_exits_4(tmp_path) -> None:
    result = _run_cli_no_trac_env(
        "--from-wiki", "AnyPage", "--to", "md", cwd=str(tmp_path)
    )
    assert result.returncode == 4
    assert "no Trac credentials found" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_subprocess_to_wiki_and_output_mutex_exits_1(tmp_path) -> None:
    result = _run_cli_no_trac_env(
        "--to-wiki",
        "AnyPage",
        "-o",
        str(tmp_path / "out.md"),
        cwd=str(tmp_path),
    )
    assert result.returncode == 1
    assert (
        "--to-wiki and --output are mutually exclusive" in result.stderr
    )
    assert result.stdout == ""
