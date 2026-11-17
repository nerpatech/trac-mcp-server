"""What counts as an internal address or name in this public repository.

Two tiers share one definition (Trac /trac_mcp_server #217, building on #208):

- ``CORE_RE`` is internal anywhere: private IPv4 ranges, IPv6 ULA, the
  internal host and person names, home-directory paths.
- ``FIXTURE_RE`` is ``CORE_RE`` plus what is only suspect inside captured
  fixture data: the org's own domain, ``.lan`` hosts, the internal daemon
  ports and any mail address. Docs and tests legitimately mention several
  of those (``:8000`` as the default Trac port, fake ``.lan`` hosts).

The internal names and the root path are written split across adjacent
string literals so this file does not itself match ``CORE_RE``.
"""

import re

#: Private IPv4 (192.168/16, 10/8, 172.16/12, CGNAT 100.64/10), IPv6 ULA
#: (fdXX:), the internal names, and home-directory paths. ``/home/USER`` is
#: the scrub's own placeholder.
CORE_RE = re.compile(
    r"192\.168\.|\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b172\.(?:1[6-9]|2\d|3[01])\."
    r"|\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"
    r"|\bfd[0-9a-f]{2}:[0-9a-f]{0,4}:"
    r"|(?i:kp"
    "oxa|fr-"
    "tun|maz"
    "urov)"
    r"|/home/(?!USER\b)[A-Za-z]|/Users/[A-Za-z]"
    r"|/ro"
    r"ot/|\.home\.arpa\b",
)

#: ``CORE_RE`` plus the alternatives that only fixtures are held to.
FIXTURE_RE = re.compile(
    CORE_RE.pattern + r"|(?i:nerpa\.)|\.lan\b|:809\d\b|:8000\b"
    r"|[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b",
)


def internal_hits(
    text: str, pattern: re.Pattern[str] = FIXTURE_RE
) -> list[str]:
    """The matched text of every internal address or name in ``text``.

    ``user@example.org`` is the scrub's own placeholder and never a hit.
    """
    return [
        m.group(0)
        for m in pattern.finditer(text)
        if m.group(0) != "user@example.org"
    ]
