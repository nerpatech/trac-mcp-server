"""Build hook: stamp the wheel with the git commit it was built from.

pyproject.toml stays the metadata source; this file only adds a
``build_py`` subclass (Trac /trac_mcp_server #170). After the package is
built it writes ``trac_mcp_server/_release.py`` into the BUILD directory
(never ``src/``), containing exactly::

    RELEASE_SHA = "<40 hex>"

with ``-dirty`` appended when a tracked file differs from HEAD (untracked
files do not count: a build leaves ``build/`` and ``*.egg-info`` behind).
``trac_mcp_server.version.release_sha()`` reads it back, and ``/healthz``
reports it, so a deployed daemon can say which commit it runs.

When the commit cannot be determined -- git is missing or fails, the
directory is not itself a git checkout (an sdist), or the checkout has no
commit -- no stamp is written and any stamp already in the build
directory is deleted: a stale copy left by an earlier build in the same
``build/`` would otherwise ship a wrong sha. An editable install is never
stamped.

``setup()`` runs only under ``__name__ == "__main__"``, which is how
``setuptools.build_meta`` executes this file, so the functions can be
imported and tested.
"""

import re
import subprocess
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _git(root: Path, *args: str) -> str | None:
    """Stdout of ``git <args>`` run in ``root``; None if it cannot run
    or exits non-zero."""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def release_sha(root: Path) -> str | None:
    """HEAD of the checkout at ``root`` (``-dirty`` if a tracked file
    differs), or None when ``root`` is not itself a git checkout with a
    commit."""
    root = Path(root).resolve()
    top = _git(root, "rev-parse", "--show-toplevel")
    # A directory inside some OTHER repository (an sdist unpacked under
    # one) answers rev-parse with that repository's HEAD; it is not ours.
    if top is None or Path(top.strip()).resolve() != root:
        return None
    head = _git(root, "rev-parse", "HEAD")
    if head is None or not _SHA.match(head.strip()):
        return None
    sha = head.strip()
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    if status is None:
        return None
    return sha + "-dirty" if status.strip() else sha


def write_release(build_lib: str | Path, root: Path) -> None:
    """Write (or, if the sha is unknown, remove) the stamp in
    ``build_lib``."""
    target = Path(build_lib) / "trac_mcp_server" / "_release.py"
    sha = release_sha(root)
    if sha is None:
        target.unlink(missing_ok=True)
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
