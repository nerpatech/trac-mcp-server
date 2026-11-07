"""Build hook: stamp the wheel with the git commit it was built from.

pyproject.toml stays the metadata source; this file only adds a
``build_py`` subclass (Trac /trac_mcp_server #170). After the package is
built it writes ``trac_mcp_server/_release.py`` into the BUILD directory
(never ``src/``), containing exactly::

    RELEASE_SHA = "<40 hex>"

with ``-dirty`` appended when a tracked file differs from HEAD or an
untracked file exists (it may ship in the wheel, e.g. a new module under
``src/``). Gitignored files do not count: a build leaves ``build/`` and
``*.egg-info`` behind, both ignored.
``trac_mcp_server.version.release_sha()`` reads it back, and ``/healthz``
reports it, so a deployed daemon can say which commit it runs.

When the commit cannot be determined -- git is missing or fails, the
directory is not itself a git checkout (an sdist), or the checkout has no
commit -- no stamp is written and any stamp already in the build
directory is deleted: a stale copy left by an earlier build in the same
``build/`` would otherwise ship a wrong sha, and one line on stderr says why. An editable
install is never stamped. Git runs with every ``GIT_*`` variable
dropped from its environment.

``setup()`` runs only under ``__name__ == "__main__"``, which is how
``setuptools.build_meta`` executes this file, so the functions can be
imported and tested.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

_SHA = re.compile(r"[0-9a-f]{40}\Z")


NOT_A_CHECKOUT = "not a git checkout or not this project's toplevel"


def _git_env() -> dict[str, str]:
    """The environment git runs in: every ``GIT_*`` variable dropped, so
    a hook or wrapper exporting ``GIT_DIR`` / ``GIT_WORK_TREE`` /
    ``GIT_INDEX_FILE`` cannot make git answer for some other repository.
    ``LC_ALL=C`` keeps the messages matched below in English."""
    env = {
        k: v for k, v in os.environ.items() if not k.startswith("GIT_")
    }
    env["LC_ALL"] = "C"
    return env


def _first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0] if lines else ""


def _git(root: Path, *args: str) -> tuple[str | None, str]:
    """``(stdout, "")`` of ``git <args>`` run in ``root``; ``(None,
    why)`` if it cannot run or exits non-zero, ``why`` being the reason
    in a few words (git's own first stderr line when it printed one)."""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            env=_git_env(),
        )
    except FileNotFoundError:
        return None, "git not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git failed ({exc})"
    if done.returncode != 0:
        detail = _first_line(done.stderr)
        if "not a git repository" in detail:
            return None, NOT_A_CHECKOUT
        return None, f"git failed ({detail or done.returncode})"
    return done.stdout, ""


def release_sha_or_reason(root: Path) -> tuple[str | None, str]:
    """``(sha, "")``, or ``(None, reason)`` when ``root`` is not itself
    a git checkout with a commit. ``sha`` is HEAD, with ``-dirty``
    appended if a tracked file differs or an untracked, not gitignored
    file exists (it may ship in the wheel)."""
    root = Path(root).resolve()
    top, why = _git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return None, why
    # A directory inside some OTHER repository (an sdist unpacked under
    # one) answers rev-parse with that repository's HEAD; it is not ours.
    if Path(top.strip()).resolve() != root:
        return None, NOT_A_CHECKOUT
    # -q: a repository with no commit exits 1 and prints nothing
    head, why = _git(root, "rev-parse", "-q", "--verify", "HEAD")
    if head is None:
        if why == "git failed (1)":
            return None, "no commit"
        return None, why
    sha = head.strip()
    if not _SHA.match(sha):
        return None, "git failed (HEAD is not a 40-hex sha)"
    status, why = _git(root, "status", "--porcelain")
    if status is None:
        return None, why
    return (sha + "-dirty" if status.strip() else sha), ""


def release_sha(root: Path) -> str | None:
    """HEAD of the checkout at ``root`` (``-dirty`` if it has changes),
    or None when ``root`` is not itself a git checkout with a commit."""
    return release_sha_or_reason(root)[0]


def write_release(build_lib: str | Path, root: Path) -> None:
    """Write (or, if the sha is unknown, remove) the stamp in
    ``build_lib``. An unknown sha is said once on stderr."""
    target = Path(build_lib) / "trac_mcp_server" / "_release.py"
    sha, reason = release_sha_or_reason(root)
    if sha is None:
        target.unlink(missing_ok=True)
        print(
            f"setup.py: no release stamp ({reason}); "
            'the build will report release "unknown"',
            file=sys.stderr,
        )
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f'RELEASE_SHA = "{sha}"\n')


class build_py_with_release(build_py):
    def run(self):
        super().run()
        if getattr(self, "editable_mode", False):
            return
        write_release(self.build_lib, Path(__file__).resolve().parent)


if __name__ == "__main__":
    setup(cmdclass={"build_py": build_py_with_release})
