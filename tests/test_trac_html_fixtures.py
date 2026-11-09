"""The Trac-rendered HTML fixture set stays complete and scrubbed (Trac #208).

``tests/fixtures/trac_html/`` holds real documents with the HTML Trac
rendered for them, the set #209's ``html_to_markdown`` translator is built
against. This repository is public, and Trac's HTML carries absolute URLs
on the internal Trac host, so every fixture was scrubbed before it was
committed. This test keeps it that way for any fixture added later.
"""

import json
import re
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "trac_html"

#: An internal address or name. Private IPv4 ranges, the internal Trac
#: and daemon ports, hostnames, home-directory paths and mail addresses.
#: ``user@example.org`` is the scrub's own placeholder.
INTERNAL_RE = re.compile(
    r"192\.168\.|\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b172\.(?:1[6-9]|2\d|3[01])\."
    r"|\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"
    r"|\bfd[0-9a-f]{2}:[0-9a-f]{0,4}:"
    r"|(?i:kpoxa|fr-tun|mazurov|nerpa\.)|/home/(?!USER\b)[A-Za-z]|/Users/[A-Za-z]"
    r"|/root/|\.lan\b|\.home\.arpa\b|:809\d\b|:8000\b"
    r"|[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b",
)


def internal_hits(text: str) -> list[str]:
    return [
        m.group(0)
        for m in INTERNAL_RE.finditer(text)
        if m.group(0) != "user@example.org"
    ]


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
    assert internal_hits(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "sample",
    [
        '<a href="http://192.168.10.4:8000/trac_mcp_server/wiki/X">',
        "ssh kpoxa deploy.sh",
        "see /home/someone/work",
        "daemon on :8091",
        "mail ops@corp.example.com",
        "user@example.org.attacker.ru",
        "ops@corp.de",
        "me@site.dev",
        "/home/Oleg/x",
        "/Users/bob/x",
        "/root/.ssh",
        "nas.home.arpa",
        "100.101.5.6",
        "fd12:3456:789a::1",
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
