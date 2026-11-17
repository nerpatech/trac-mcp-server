"""No tracked file carries an internal address or name (Trac /trac_mcp_server #217).

This repository is public. #208 scrubbed and guarded the captured Trac HTML
fixtures; this test extends the guard to every tracked file, in two tiers
(see ``tests/internal_names.py``): files under ``tests/fixtures/`` are held
to ``FIXTURE_RE``, every other tracked file to the narrower ``CORE_RE``.

A hit that is legitimate goes in ``ALLOWLIST`` with the reason it is allowed.
Entries are keyed on ``(path, matched_text)`` and the text is compared
exactly, so two spellings of one name are two entries. An entry that matches
nothing fails ``test_allowlist_has_no_stale_entries``, so the list cannot
outlive the thing it excuses.

Strings that would match are built at runtime here, so this file carries no
literal hit of its own and needs no allowlist entries for itself.
"""

import subprocess
from pathlib import Path

import pytest

from tests.internal_names import CORE_RE, FIXTURE_RE, internal_hits

ROOT = Path(__file__).resolve().parent.parent

# Pieces of names, joined where used so no literal here matches CORE_RE.
_HOST = "kp" + "oxa"
_AUTHOR = "Maz" + "urov"
_HOME = "/home" + "/"
_FIXTURE_TEST = "tests/test_trac_html_fixtures.py"

_DEPLOY_REASON = (
    "the retired debian deploy names the host it turns away;"
    " its test asserts that"
)
_DOCKER_REASON = "the container's own user, " + _HOME + "trac-mcp"

#: ``(path, matched_text)`` -> why the hit is legitimate.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("deploy.sh", _HOST): _DEPLOY_REASON,
    ("constraints/deploy.txt", _HOST): _DEPLOY_REASON,
    ("tests/test_deploy_pins.py", _HOST): _DEPLOY_REASON,
    ("CHANGELOG.md", _HOST): "release history of that deploy",
    (
        "plugins/tracrpc_comment/setup.py",
        _AUTHOR,
    ): "public package authorship",
    ("Dockerfile", _HOME + "t"): _DOCKER_REASON,
}
# The fixture check's own planted defects: one input per CORE_RE alternative
# it must catch, so the file naming them cannot also be scrubbed of them.
ALLOWLIST.update(
    {
        (_FIXTURE_TEST, hit): "the fixture check's own planted defects"
        for hit in (
            "192." + "168.",
            _HOST,
            _HOME + "s",
            _HOME + "O",
            "/Users" + "/b",
            "/ro" + "ot/",
            ".home" + ".arpa",
            "100.101" + ".5.6",
            "fd12" + ":3456:",
        )
    }
)


def _tracked_files() -> list[str]:
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if Path(top).resolve() != ROOT:
            pytest.skip("not the root of a git checkout")
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout (or git is unavailable)")
    return [p for p in out.split("\0") if p]


def _scan() -> list[tuple[str, int, str]]:
    """``(path, line number, matched text)`` for every hit, allowlisted or not."""
    hits: list[tuple[str, int, str]] = []
    for rel in _tracked_files():
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        pattern = (
            FIXTURE_RE if rel.startswith("tests/fixtures/") else CORE_RE
        )
        for lineno, line in enumerate(text.splitlines(), start=1):
            hits.extend(
                (rel, lineno, hit)
                for hit in internal_hits(line, pattern)
            )
    return hits


def test_no_tracked_file_carries_an_internal_name():
    offenders = [
        f"{rel}:{lineno}: {hit}"
        for rel, lineno, hit in _scan()
        if (rel, hit) not in ALLOWLIST
    ]
    assert not offenders, (
        "internal address or name in a tracked file (scrub it, or allowlist it"
        " with a reason in tests/test_no_internal_names.py):\n"
        + "\n".join(offenders)
    )


def test_allowlist_has_no_stale_entries():
    seen = {(rel, hit) for rel, _, hit in _scan()}
    stale = sorted(set(ALLOWLIST) - seen)
    assert not stale, f"allowlist entries that match nothing: {stale}"
