"""The Trac-rendered HTML fixture set stays complete and scrubbed (Trac #208).

``tests/fixtures/trac_html/`` holds real documents with the HTML Trac
rendered for them, the set #209's ``html_to_markdown`` translator is built
against. This repository is public, and Trac's HTML carries absolute URLs
on the internal Trac host, so every fixture was scrubbed before it was
committed. This test keeps it that way for any fixture added later.
"""

import json
from pathlib import Path

import pytest

from tests.internal_names import internal_hits

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "trac_html"


def _manifest() -> dict:
    return json.loads(
        (FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8")
    )


def test_every_fixture_has_source_html_and_manifest_entry():
    manifest = _manifest()
    html = {p.stem for p in FIXTURE_DIR.glob("*.html")}
    src = {p.stem for p in FIXTURE_DIR.glob("*.tracwiki")}
    assert html == src == set(manifest), (html ^ src) | (
        html ^ set(manifest)
    )
    assert len(manifest) >= 10


@pytest.mark.parametrize(
    "path",
    sorted(p for p in FIXTURE_DIR.iterdir() if p.is_file()),
    ids=lambda p: p.name,
)
def test_fixture_carries_no_internal_address(path):
    """Also covers fixtures not yet tracked by git, so a new capture fails
    before it is committed, which the git-based scan cannot do."""
    assert internal_hits(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "sample",
    [
        '<a href="http://192.'
        + '168.0.1:8000/trac_mcp_server/wiki/X">',
        "ssh " + "kp" + "oxa" + " deploy.sh",
        "see " + "/home/" + "someone/work",
        "daemon on :8091",
        "mail ops@corp.example.com",
        "user@example.org.attacker.ru",
        "ops@corp.de",
        "me@site.dev",
        "/home/" + "Alice/x",
        "/Users/" + "bob/x",
        "/ro" + "ot/.ssh",
        "nas.home" + ".arpa",
        "100.101" + ".5.6",
        "fd12" + ":3456:789a::1",
    ],
)
def test_check_catches_unscrubbed_text(sample):
    assert internal_hits(sample)


def test_check_passes_placeholders():
    assert (
        internal_hits(
            '<a href="https://trac.example.org/trac_mcp_server/wiki/X">'
            " user@example.org /home/USER HOSTNAME"
        )
        == []
    )
