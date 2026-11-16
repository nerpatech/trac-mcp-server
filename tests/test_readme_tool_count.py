"""README's "Available Tools" section must match the registered tools.

The section's heading count, its table rows, and each subsection's count
are checked against ``[PING_SPEC] + ALL_SPECS``, the registry the server
serves. Parsing is scoped to that one section: other README tables also
start rows with a backticked name (environment variables).
"""

import re
from pathlib import Path

from trac_mcp_server.mcp.server import PING_SPEC
from trac_mcp_server.mcp.tools import ALL_SPECS

README = Path(__file__).resolve().parent.parent / "README.md"

HEADING = re.compile(r"^## Available Tools \((\d+)\)\s*$")
SUBSECTION = re.compile(r"^### (.+) \((\d+)\)\s*$")
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|")


def _registered() -> set[str]:
    return {spec.tool.name for spec in [PING_SPEC, *ALL_SPECS]}


def _section_lines() -> list[str]:
    """Lines from the `## Available Tools (N)` heading to the next `## `."""
    lines = README.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, line in enumerate(lines) if HEADING.match(line)), None
    )
    assert start is not None, (
        "README has no '## Available Tools (N)' heading"
    )
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].startswith("## ")
        ),
        len(lines),
    )
    return lines[start:end]


def test_heading_count_matches_registry():
    heading = HEADING.match(_section_lines()[0])
    assert heading is not None
    assert int(heading.group(1)) == len(_registered())


def test_table_names_match_registry():
    listed = {
        m.group(1)
        for line in _section_lines()
        if (m := ROW.match(line))
    }
    registered = _registered()
    missing = sorted(registered - listed)
    extra = sorted(listed - registered)
    assert listed == registered, (
        f"README missing {missing}; README lists unregistered {extra}"
    )


def test_subsection_counts_match_rows():
    counts: dict[str, list[int]] = {}
    current: str | None = None
    for line in _section_lines()[1:]:
        sub = SUBSECTION.match(line)
        if sub:
            current = sub.group(1)
            counts[current] = [int(sub.group(2)), 0]
        elif current and ROW.match(line):
            counts[current][1] += 1
    assert counts, "no '### Title (k)' subsections found"
    wrong = {
        title: f"heading says {k}, table has {rows}"
        for title, (k, rows) in counts.items()
        if k != rows
    }
    assert not wrong, (
        f"subsection counts differ from their rows: {wrong}"
    )
