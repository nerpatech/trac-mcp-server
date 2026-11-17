"""No tracked file carries an internal address or name (Trac /trac_mcp_server #217).

This repository is public. #208 scrubbed and guarded the captured Trac HTML
fixtures; this test extends the guard to every tracked file, in two tiers
(see ``tests/internal_names.py``): files under ``tests/fixtures/`` are held
to ``FIXTURE_RE``, every other tracked file to the narrower ``CORE_RE``
(``pattern_for``). Every tracked file is scanned: one that is not UTF-8 is
decoded with ``surrogateescape`` rather than skipped, and a symlink is
scanned as its target string, not followed.

A hit that is legitimate goes in ``ALLOWLIST`` with the reason it is allowed.
Entries are keyed on ``(path, token)``, where the token is the regex match
extended forward to the next whitespace or one of ``"'<>()[]{},;`` and the
backtick, so an entry excuses that one spelling and not whatever else
begins the same way. The token is compared exactly, so two spellings of one
name are two entries. An entry that matches nothing fails
``test_allowlist_has_no_stale_entries``, so the list cannot outlive the
thing it excuses.

Strings that would match are built at runtime here, so this file carries no
literal hit of its own and needs no allowlist entries for itself.
"""

import functools
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.internal_names import (
    CORE_RE,
    FIXTURE_RE,
    internal_hits,
    internal_matches,
)

ROOT = Path(__file__).resolve().parent.parent

# Pieces of names, joined where used so no literal here matches CORE_RE.
_HOST = "kp" + "oxa"
_AUTHOR = "Maz" + "urov"
_HOME = "/home" + "/"

_DEPLOY_REASON = (
    "the retired debian deploy names the host it turns away;"
    " its test asserts that"
)
_HISTORY_REASON = "release history of that deploy"
_DOCKER_REASON = "the container's own user, " + _HOME + "trac-mcp"

#: ``(path, token)`` -> why the hit is legitimate.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("deploy.sh", _HOST): _DEPLOY_REASON,
    ("constraints/deploy.txt", _HOST): _DEPLOY_REASON,
    ("tests/test_deploy_pins.py", _HOST): _DEPLOY_REASON,
    ("tests/test_deploy_pins.py", _HOST + "."): _DEPLOY_REASON,
    ("CHANGELOG.md", _HOST): _HISTORY_REASON,
    ("CHANGELOG.md", _HOST + "-shaped"): _HISTORY_REASON,
    (
        "plugins/tracrpc_comment/setup.py",
        _AUTHOR,
    ): "public package authorship",
    ("Dockerfile", _HOME + "trac-mcp"): _DOCKER_REASON,
}

#: Where a hit's token ends: whitespace or one of these characters.
_TOKEN_END = re.compile(r"""[\s"'<>()\[\]{},;`]""")


def pattern_for(path: str) -> re.Pattern[str]:
    """The pattern a tracked file is held to: fixtures are held to more."""
    return FIXTURE_RE if path.startswith("tests/fixtures/") else CORE_RE


def _tokens(line: str, pattern: re.Pattern[str]) -> list[str]:
    """Each hit in ``line``, extended forward to the end of its token."""
    tokens = []
    for m in internal_matches(line, pattern):
        end = _TOKEN_END.search(line, m.end())
        tokens.append(
            line[m.start() : end.start() if end else len(line)]
        )
    return tokens


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


def _text_of(path: Path) -> str | None:
    if os.path.islink(path):
        return os.readlink(path)
    if not path.is_file():
        return None
    return path.read_bytes().decode("utf-8", errors="surrogateescape")


@functools.cache
def _scan() -> tuple[tuple[str, int, str], ...]:
    """``(path, line number, token)`` for every hit, allowlisted or not."""
    hits = []
    for rel in _tracked_files():
        text = _text_of(ROOT / rel)
        if text is None:
            continue
        pattern = pattern_for(rel)
        for lineno, line in enumerate(text.splitlines(), start=1):
            hits.extend(
                (rel, lineno, token) for token in _tokens(line, pattern)
            )
    return tuple(hits)


def test_no_tracked_file_carries_an_internal_name():
    offenders = [
        f"{rel}:{lineno}: {token}"
        for rel, lineno, token in _scan()
        if (rel, token) not in ALLOWLIST
    ]
    assert not offenders, (
        "internal address or name in a tracked file (scrub it, or allowlist it"
        " with a reason in tests/test_no_internal_names.py):\n"
        + "\n".join(offenders)
    )


def test_allowlist_has_no_stale_entries():
    seen = {(rel, token) for rel, _, token in _scan()}
    stale = sorted(set(ALLOWLIST) - seen)
    assert not stale, f"allowlist entries that match nothing: {stale}"


#: Internal-looking samples, built at runtime. CORE_RE must catch each.
_CORE_SAMPLES = [
    "192." + "168.1.1",
    "10." + "1.2.3",
    "172." + "16.0.1",
    "172." + "31.255.1",
    "100." + "64.0.1",
    "100." + "127.1.1",
    "fd12" + ":3456:789a::1",
    _HOST,
    _HOST.upper(),
    "fr-" + "tun",
    _AUTHOR,
    _AUTHOR.lower(),
    _HOME + "alice/x",
    "/Users" + "/bob/x",
    "/ro" + "ot/.ssh",
    "nas.home" + ".arpa",
]

#: Only suspect inside fixtures: CORE_RE must let each through, FIXTURE_RE
#: must catch each.
_FIXTURE_ONLY_SAMPLES = [
    "http://h:8000/x",
    "http://h:8092/x",
    "trac.lan",
    "a@example.com",
    "nerpa.tech",
]


@pytest.mark.parametrize("sample", _CORE_SAMPLES)
def test_core_pattern_catches_core_samples(sample):
    assert internal_hits(sample, CORE_RE)
    assert internal_hits(sample, FIXTURE_RE)


@pytest.mark.parametrize("sample", _FIXTURE_ONLY_SAMPLES)
def test_core_pattern_lets_fixture_only_samples_through(sample):
    assert internal_hits(sample, CORE_RE) == []


@pytest.mark.parametrize("sample", _FIXTURE_ONLY_SAMPLES)
def test_fixture_pattern_catches_fixture_only_samples(sample):
    assert internal_hits(sample, FIXTURE_RE)


def test_placeholders_are_not_core_hits():
    assert (
        internal_hits("user@example.org " + _HOME + "USER/x", CORE_RE)
        == []
    )


def test_pattern_for_picks_the_tier_by_path():
    assert pattern_for("tests/fixtures/x.html") is FIXTURE_RE
    assert pattern_for("tests/fixtures/sub/x.txt") is FIXTURE_RE
    assert pattern_for("src/x.py") is CORE_RE
    assert pattern_for("tests/test_x.py") is CORE_RE


def test_token_runs_to_whitespace_or_a_delimiter():
    line = (
        'a "'
        + _HOME
        + 'trac-mcp" b ('
        + _HOST
        + ") c "
        + _HOST
        + "-stage d"
    )
    assert _tokens(line, CORE_RE) == [
        _HOME + "trac-mcp",
        _HOST,
        _HOST + "-stage",
    ]
