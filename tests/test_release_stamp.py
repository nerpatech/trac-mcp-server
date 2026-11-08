"""setup.py stamps the build with the git commit it was built from
(Trac /trac_mcp_server #170).

The stamp functions run against REAL temporary git repositories
(``Rules/testing/RealSubstrateNotMocks``): what they read is git's own
answer, not a mock of it.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / "setup.py"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not on PATH"
)


def _load_setup():
    spec = importlib.util.spec_from_file_location(
        "setup_under_test", SETUP
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup_py = _load_setup()


def _clean_env():
    """The environment without GIT_* variables: a hook or wrapper
    exporting GIT_DIR must not send the fixtures' git into the real
    repository."""
    return {
        k: v for k, v in os.environ.items() if not k.startswith("GIT_")
    }


def _git(root, *args):
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env=_clean_env(),
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "tracked.txt").write_text("one\n")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-q", "-m", "first")
    return root


def test_a_clean_checkout_gives_head_as_40_lowercase_hex(repo):
    sha = setup_py.release_sha(repo)
    assert re.fullmatch(r"[0-9a-f]{40}", sha)
    assert sha == _git(repo, "rev-parse", "HEAD")


def test_a_modified_tracked_file_makes_it_dirty(repo):
    (repo / "tracked.txt").write_text("two\n")
    sha = setup_py.release_sha(repo)
    assert sha == _git(repo, "rev-parse", "HEAD") + "-dirty"


def test_a_staged_change_makes_it_dirty(repo):
    (repo / "tracked.txt").write_text("two\n")
    _git(repo, "add", "tracked.txt")
    assert setup_py.release_sha(repo).endswith("-dirty")


def test_an_untracked_file_makes_it_dirty(repo):
    # an untracked file (a new module under src/) may ship in the wheel
    (repo / "scratch.txt").write_text("x\n")
    sha = setup_py.release_sha(repo)
    assert sha == _git(repo, "rev-parse", "HEAD") + "-dirty"


def test_a_gitignored_file_is_not_dirty(repo):
    # a pip build leaves build/ and *.egg-info behind; both are
    # gitignored and must not make every wheel "dirty"
    (repo / ".gitignore").write_text("build/\n*.egg-info/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")
    (repo / "build").mkdir()
    (repo / "build" / "out.txt").write_text("x\n")
    (repo / "pkg.egg-info").mkdir()
    (repo / "pkg.egg-info" / "PKG-INFO").write_text("x\n")
    assert setup_py.release_sha(repo) == _git(repo, "rev-parse", "HEAD")


def test_a_directory_that_is_not_a_checkout_gives_none(tmp_path):
    plain = tmp_path / "sdist"
    plain.mkdir()
    assert setup_py.release_sha(plain) is None


def test_a_subdirectory_of_another_checkout_gives_none(repo):
    # an sdist unpacked inside some other repository must not take that
    # repository's HEAD for its own
    inner = repo / "unpacked-sdist"
    inner.mkdir()
    assert setup_py.release_sha(inner) is None


def test_a_missing_git_gives_none(repo, monkeypatch):
    monkeypatch.setenv("PATH", str(repo))  # no git there
    assert setup_py.release_sha(repo) is None


def test_a_repository_with_no_commit_gives_none(tmp_path):
    root = tmp_path / "fresh"
    root.mkdir()
    _git(root, "init", "-q")
    assert setup_py.release_sha(root) is None


def test_write_release_writes_exactly_the_constant(repo, tmp_path):
    build_lib = tmp_path / "build" / "lib"
    (build_lib / "trac_mcp_server").mkdir(parents=True)
    setup_py.write_release(build_lib, repo)
    stamp = build_lib / "trac_mcp_server" / "_release.py"
    head = _git(repo, "rev-parse", "HEAD")
    assert stamp.read_text() == f'RELEASE_SHA = "{head}"\n'


def test_write_release_removes_a_stale_stamp_when_the_sha_is_unknown(
    tmp_path,
):
    plain = tmp_path / "sdist"
    plain.mkdir()
    build_lib = tmp_path / "build" / "lib"
    (build_lib / "trac_mcp_server").mkdir(parents=True)
    stale = build_lib / "trac_mcp_server" / "_release.py"
    stale.write_text('RELEASE_SHA = "' + "0" * 40 + '"\n')
    setup_py.write_release(build_lib, plain)
    assert not stale.exists()


def test_write_release_says_why_on_stderr_when_there_is_no_sha(
    tmp_path, capsys
):
    plain = tmp_path / "sdist"
    plain.mkdir()
    build_lib = tmp_path / "build" / "lib"
    (build_lib / "trac_mcp_server").mkdir(parents=True)
    setup_py.write_release(build_lib, plain)
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert err.startswith("setup.py: no release stamp (")
    assert "not a git checkout" in err
    assert err.endswith('); the build will report release "unknown"\n')


def test_write_release_is_silent_on_stderr_when_it_stamps(
    repo, tmp_path, capsys
):
    build_lib = tmp_path / "build" / "lib"
    (build_lib / "trac_mcp_server").mkdir(parents=True)
    setup_py.write_release(build_lib, repo)
    assert capsys.readouterr().err == ""


def test_the_reason_names_git_missing(repo, monkeypatch):
    monkeypatch.setenv("PATH", str(repo))
    sha, reason = setup_py.release_sha_or_reason(repo)
    assert sha is None
    assert "git not found" in reason


def test_the_reason_names_a_repository_with_no_commit(tmp_path):
    root = tmp_path / "fresh"
    root.mkdir()
    _git(root, "init", "-q")
    sha, reason = setup_py.release_sha_or_reason(root)
    assert sha is None
    assert "no commit" in reason


def test_the_reason_names_a_subdirectory_of_another_checkout(repo):
    inner = repo / "unpacked-sdist"
    inner.mkdir()
    sha, reason = setup_py.release_sha_or_reason(inner)
    assert sha is None
    assert "not a git checkout" in reason


def test_the_reason_carries_gits_first_stderr_line(repo, monkeypatch):
    # git failing for its own reason (e.g. "dubious ownership") is
    # reported, not folded into "not a checkout"
    real = subprocess.run

    def failing(cmd, **kwargs):
        if cmd[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(
                cmd, 128, "", "fatal: dubious ownership\nhint: x\n"
            )
        return real(cmd, **kwargs)

    monkeypatch.setattr(setup_py.subprocess, "run", failing)
    sha, reason = setup_py.release_sha_or_reason(repo)
    assert sha is None
    assert "git failed" in reason
    assert "fatal: dubious ownership" in reason
    assert "hint" not in reason


def test_inherited_git_variables_do_not_redirect_the_stamp(
    repo, tmp_path, monkeypatch
):
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    (other / "f").write_text("x\n")
    _git(other, "add", "f")
    _git(other, "commit", "-q", "-m", "other")
    expected = _git(repo, "rev-parse", "HEAD")
    assert _git(other, "rev-parse", "HEAD") != expected
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    assert setup_py.release_sha(repo) == expected


def test_write_release_with_no_sha_and_no_stale_file_is_quiet(tmp_path):
    plain = tmp_path / "sdist"
    plain.mkdir()
    build_lib = tmp_path / "build" / "lib"
    (build_lib / "trac_mcp_server").mkdir(parents=True)
    setup_py.write_release(build_lib, plain)
    assert not (build_lib / "trac_mcp_server" / "_release.py").exists()


# ---------------------------------------------------------------------------
# The real build_py command, by subprocess: the wiring and the
# ``__name__ == "__main__"`` guard
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    """A one-package git project that uses this repo's setup.py."""
    root = tmp_path / "project"
    pkg = root / "src" / "trac_mcp_server"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=77.0"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[project]\nname = "stamp-probe"\nversion = "0.0.1"\n'
    )
    shutil.copy(SETUP, root / "setup.py")
    # the build leaves these behind; this repo's .gitignore has both
    (root / ".gitignore").write_text("build/\n*.egg-info/\n")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "first")
    return root


def _build_py(root, out):
    return subprocess.run(
        [sys.executable, "setup.py", "-q", "build_py", "-d", str(out)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_build_py_stamps_the_build_dir_and_never_src(project, tmp_path):
    out = tmp_path / "out"
    result = _build_py(project, out)
    assert result.returncode == 0, result.stderr
    head = _git(project, "rev-parse", "HEAD")
    stamp = out / "trac_mcp_server" / "_release.py"
    assert stamp.read_text() == f'RELEASE_SHA = "{head}"\n'
    assert not (
        project / "src" / "trac_mcp_server" / "_release.py"
    ).exists()


def test_build_py_in_a_non_checkout_ships_no_stamp(project, tmp_path):
    shutil.rmtree(project / ".git")
    out = tmp_path / "out"
    stale = out / "trac_mcp_server"
    stale.mkdir(parents=True)
    (stale / "_release.py").write_text('RELEASE_SHA = "stale"\n')
    result = _build_py(project, out)
    assert result.returncode == 0, result.stderr
    assert not (out / "trac_mcp_server" / "_release.py").exists()
