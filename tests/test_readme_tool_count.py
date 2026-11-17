"""README's "Available Tools" section and the tool reference page must
match the registered tools.

The names come from the built registry (``build_registry`` over
``[PING_SPEC] + ALL_SPECS`` with the page alias and strict schema, the way
the server and ``tests/test_mcp/test_build_registry.py`` build it), as a
list so a duplicate registration shows. The checks:

- the registered names have no duplicates;
- the section's heading count equals the registered count;
- the backticked names in the section's table rows are exactly the
  registered names, with no row listed twice, and the row total equals the
  heading count;
- every ``### `` line inside the section reads ``### Title (k)``, each k
  equals that subsection's row count, and the k values sum to the heading
  count.

Parsing is scoped to that one section (parsed once): other README tables
also start rows with a backticked name (environment variables).

``docs/reference/mcp-tools.md`` is held to the same registry (Trac
/trac_mcp_server #242):

- every registered tool has a heading line exactly ``## <name>``;
- every ``## `` heading is either exactly a registered tool name or one of
  the explicit ``PROSE_HEADINGS`` ("Instance Routing", "Write Gate
  Status"); anything else fails and is listed, so a removed or renamed tool
  leaves no stale section however the old heading is dressed up
  (``## old_tool (removed)``, ``## `old_tool` ``). A heading is not judged
  by its shape;
- lines inside fenced code blocks are not headings: a fenced ``## x`` hides
  no missing section and reports no extra one.
"""

import functools
import re
from collections import Counter
from pathlib import Path

from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.mcp.server import PING_SPEC, build_registry
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import (
    with_page_alias,
    with_strict_schema,
)

README = Path(__file__).resolve().parent.parent / "README.md"
TOOLS_PAGE = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "reference"
    / "mcp-tools.md"
)

HEADING = re.compile(r"^## Available Tools \((\d+)\)\s*$")
SUBSECTION = re.compile(r"^### (.+) \((\d+)\)\s*$")
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
# The tool reference's prose sections: the only `## ` headings that are not
# a registered tool name.
PROSE_HEADINGS = frozenset({"Instance Routing", "Write Gate Status"})


def _registered() -> list[str]:
    registry = build_registry(
        with_strict_schema(with_page_alias([PING_SPEC] + ALL_SPECS)),
        None,
        ServerConfig(),
    )
    return [tool.name for tool in registry.list_tools()]


def _duplicates(names: list[str]) -> list[str]:
    return sorted(n for n, c in Counter(names).items() if c > 1)


@functools.cache
def _section_lines() -> tuple[str, ...]:
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
    return tuple(lines[start:end])


def _heading_count() -> int:
    heading = HEADING.match(_section_lines()[0])
    assert heading is not None
    return int(heading.group(1))


def test_registered_names_are_unique():
    names = _registered()
    assert len(names) == len(set(names)), (
        f"duplicate registered tool names: {_duplicates(names)}"
    )


def test_heading_count_matches_registry():
    assert _heading_count() == len(_registered())


def test_table_names_match_registry():
    rows = [
        m.group(1)
        for line in _section_lines()
        if (m := ROW.match(line))
    ]
    registered = _registered()
    missing = sorted(set(registered) - set(rows))
    extra = sorted(set(rows) - set(registered))
    assert set(rows) == set(registered), (
        f"README missing {missing}; README lists unregistered {extra}"
    )
    assert len(rows) == len(set(rows)), (
        f"README lists rows twice: {_duplicates(rows)}"
    )
    assert len(rows) == _heading_count(), (
        f"heading says {_heading_count()}, tables have {len(rows)} rows"
    )


def test_subsection_counts_match_rows():
    counts: dict[str, list[int]] = {}
    current: str | None = None
    for line in _section_lines()[1:]:
        if line.startswith("### "):
            sub = SUBSECTION.match(line)
            assert sub, (
                f"subsection heading is not '### Title (k)': {line!r}"
            )
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
    total = sum(k for k, _ in counts.values())
    assert total == _heading_count(), (
        f"subsection counts sum to {total}, heading says {_heading_count()}"
    )


def _headings_outside_fences(text: str) -> list[str]:
    """The text of every ``## `` heading in ``text`` that is not inside a
    fenced code block (a fence closes on the same character, at least as
    long as the one that opened it)."""
    headings: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        marker = FENCE.match(line)
        if fence is not None:
            if (
                marker
                and marker.group(1)[0] == fence[0]
                and len(marker.group(1)) >= len(fence)
            ):
                fence = None
        elif marker:
            fence = marker.group(1)
        elif line.startswith("## "):
            headings.append(line[3:].strip())
    return headings


def _page_headings() -> list[str]:
    """The text of every ``## `` heading of the tool reference page."""
    return _headings_outside_fences(
        TOOLS_PAGE.read_text(encoding="utf-8")
    )


def _unknown_headings(
    headings: list[str], registered: set[str]
) -> list[str]:
    """Headings that are neither a registered tool name nor a prose one."""
    return [h for h in headings if h not in registered | PROSE_HEADINGS]


def test_tool_reference_has_a_section_per_registered_tool():
    headings = set(_page_headings())
    missing = sorted(set(_registered()) - headings)
    assert not missing, (
        f"{TOOLS_PAGE.name} has no '## <name>' section for "
        f"{len(missing)} registered tool(s): {missing}"
    )


def test_tool_reference_sections_name_registered_tools():
    registered = set(_registered())
    headings = _page_headings()
    unknown = _unknown_headings(headings, registered)
    assert not unknown, (
        f"{TOOLS_PAGE.name} has '## ' headings that are neither a "
        f"registered tool name nor one of {sorted(PROSE_HEADINGS)}: "
        f"{unknown}"
    )
    named = [h for h in headings if h in registered]
    assert len(named) == len(set(named)), (
        f"{TOOLS_PAGE.name} has a section twice: {_duplicates(named)}"
    )


def test_headings_inside_fences_are_not_headings():
    text = "\n".join(
        [
            "## real",
            "```markdown",
            "## fenced",
            "```",
            "  ~~~",
            "  ## tilde fenced",
            "  ```",
            "  ## still tilde fenced",
            "  ~~~",
            "## after",
        ]
    )
    assert _headings_outside_fences(text) == ["real", "after"]


def test_unknown_headings_are_listed_not_judged_by_shape():
    registered = {"old_tool", "ticket_get"}
    headings = [
        "ticket_get",
        "Instance Routing",
        "old_tool (removed)",
        "`old_tool`",
        "Mystery Section",
    ]
    assert _unknown_headings(headings, registered) == [
        "old_tool (removed)",
        "`old_tool`",
        "Mystery Section",
    ]
