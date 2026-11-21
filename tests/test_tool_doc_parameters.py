"""Each tool's ``**Parameters:**`` table in ``docs/reference/mcp-tools.md``
is held to the tool's registered input schema (Trac /trac_mcp_server #252).

The schemas come from the built registry, the way
``tests/test_readme_tool_count.py`` builds it (``build_registry`` over
``[PING_SPEC] + ALL_SPECS`` with the page alias and strict schema), so a
parameter the server adds, drops or makes required shows up here. One test
per registered tool, so a failure names the tool. Per tool:

- the section is the lines under ``## <tool>`` up to the next ``## ``;
  lines inside fenced code blocks (``` or ~~~) are ignored. The parameters
  block runs from the ``**Parameters:**`` line to the next line that starts
  with ``**``. A section with no ``**Parameters:**`` line passes only if the
  schema has no property besides ``instance``. A block with no row (the
  ``*(none)*`` row of ``ping`` and ``get_server_time`` has no backticked
  name) means no parameters;
- the first table of the block holds the top-level parameters. A row's name
  is the first backticked cell. A name without a dot is compared with the
  schema's top-level ``properties`` minus ``instance`` (which the docs
  describe once, under "Instance Routing"). A dotted row ``a.b`` is compared
  with ``properties[a]["properties"]``, so ``a`` must be an object with
  properties;
- any later table of the block (``Each ticket object:`` and the like) holds
  the item fields of the tool's one top-level array parameter whose
  ``items`` is an object with ``properties``; no such parameter, or more
  than one, fails;
- names are compared as sets per scope, missing and extra reported
  separately;
- the Required cell (third), with ``*`` and whitespace stripped and case
  ignored, is ``Yes`` when the scope's own ``required`` list has the name,
  ``One of`` when some branch of the top-level ``anyOf`` requires it (for
  example ``page_name`` / ``page``), otherwise ``No``. Any other cell fails.

Types, defaults and descriptions are not compared: that would mostly freeze
wording, while a missing or extra parameter, or a wrong required flag, is
what sends a caller to the wrong call.
"""

import re
from pathlib import Path

import pytest

from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.mcp.server import PING_SPEC, build_registry
from trac_mcp_server.mcp.tools import ALL_SPECS
from trac_mcp_server.mcp.tools.registry import (
    with_page_alias,
    with_strict_schema,
)

TOOLS_PAGE = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "reference"
    / "mcp-tools.md"
)

FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
NAME_CELL = re.compile(r"^\s*`([^`]+)`\s*$")
# Cells split on a pipe that is not escaped as ``\|``.
CELL_SPLIT = re.compile(r"(?<!\\)\|")
PARAMETERS = "**Parameters:**"


def _schemas() -> dict[str, dict]:
    registry = build_registry(
        with_strict_schema(with_page_alias([PING_SPEC] + ALL_SPECS)),
        None,
        ServerConfig(),
    )
    return {
        tool.name: tool.inputSchema for tool in registry.list_tools()
    }


SCHEMAS = _schemas()


def _sections(text: str) -> dict[str, list[str]]:
    """Lines under each ``## `` heading (outside fences), to the next one."""
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
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
            if current is not None:
                current.append(line)
            continue
        if marker:
            fence = marker.group(1)
        elif line.startswith("## "):
            current = sections.setdefault(line[3:].strip(), [])
            continue
        if current is not None:
            current.append(line)
    return sections


def _fenced_free(lines: list[str]) -> list[str]:
    """``lines`` without fenced code blocks and their fence lines."""
    out: list[str] = []
    fence: str | None = None
    for line in lines:
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
        else:
            out.append(line)
    return out


def _parameters_block(lines: list[str]) -> list[str] | None:
    """The lines from ``**Parameters:**`` to the next ``**`` line, or None."""
    lines = _fenced_free(lines)
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if line.strip() == PARAMETERS
        ),
        None,
    )
    if start is None:
        return None
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].startswith("**")
        ),
        len(lines),
    )
    return lines[start + 1 : end]


def _tables(block: list[str]) -> list[list[list[str]]]:
    """Each table of the block, as its rows' cells, a table being a run of
    consecutive ``|`` lines."""
    tables: list[list[list[str]]] = []
    run: list[list[str]] | None = None
    for line in block:
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in CELL_SPLIT.split(line.strip())]
            # drop the empty cells outside the leading and trailing pipes
            cells = (
                cells[1:-1]
                if line.rstrip().endswith("|")
                else cells[1:]
            )
            if run is None:
                run = []
                tables.append(run)
            run.append(cells)
        else:
            run = None
    return tables


def _rows(table: list[list[str]]) -> list[tuple[str, str]]:
    """``(name, required cell)`` for each row whose first cell is a
    backticked name (the header, the divider and ``*(none)*`` have none)."""
    rows = []
    for cells in table:
        match = NAME_CELL.match(cells[0]) if cells else None
        if match:
            required = cells[2] if len(cells) > 2 else ""
            rows.append((match.group(1), required))
    return rows


def _normalise(cell: str) -> str:
    return re.sub(r"[\s*]", "", cell).lower()


# Requirements the handler enforces and the schema cannot express, so the
# schema says optional (not in ``required``, in no ``anyOf`` branch) while
# the docs say more. Each entry is (tool, top-level parameter) -> the
# normalised Required cell the docs must carry, exactly:
# - the `with local` entries (ticket_attachment_get.output_path,
#   wiki_attachment_get.output_path, wiki_file_pull.file_path): the handler
#   requires the parameter where the server's file_access is `local` and
#   refuses it elsewhere; the schema cannot depend on server configuration;
# - the `one of` entries (ticket_attachment_put, wiki_attachment_put:
#   content_base64 / file_path; wiki_file_detect_format, wiki_file_push:
#   content / file_path): the handler wants one of the two, but the schema
#   has no ``anyOf`` for it.
# Adding an entry needs a reason like these; an entry the schema comes to
# state itself (``required`` / ``anyOf``) is removed, so the strict rule
# owns it (``test_handler_enforced_entries_are_not_stale``).
HANDLER_ENFORCED: dict[tuple[str, str], str] = {
    ("ticket_attachment_get", "output_path"): "with`local`",
    ("ticket_attachment_put", "content_base64"): "oneof",
    ("ticket_attachment_put", "file_path"): "oneof",
    ("wiki_attachment_get", "output_path"): "with`local`",
    ("wiki_attachment_put", "content_base64"): "oneof",
    ("wiki_attachment_put", "file_path"): "oneof",
    ("wiki_file_pull", "file_path"): "with`local`",
    ("wiki_file_push", "content"): "oneof",
    ("wiki_file_push", "file_path"): "oneof",
    ("wiki_file_detect_format", "content"): "oneof",
    ("wiki_file_detect_format", "file_path"): "oneof",
}


def _expected_required(
    name: str, scope: dict, any_of: list[dict] | None
) -> str:
    if name in scope.get("required", []):
        return "yes"
    if any_of and any(
        name in branch.get("required", []) for branch in any_of
    ):
        return "oneof"
    return "no"


def _properties(schema: dict, where: str) -> dict:
    props = schema.get("properties")
    assert isinstance(props, dict) and props, (
        f"{where} is not an object with properties in the schema"
    )
    return props


def _compare(
    where: str,
    rows: list[tuple[str, str]],
    scope: dict,
    any_of: list[dict] | None,
    skip: frozenset[str] = frozenset(),
    handler_enforced: dict[str, str] | None = None,
) -> list[str]:
    """Problems of one table scope: ``rows`` (bare names) against the
    properties and required lists of ``scope``."""
    problems: list[str] = []
    props = scope.get("properties", {})
    documented = [name for name, _ in rows]
    dupes = sorted({n for n in documented if documented.count(n) > 1})
    if dupes:
        problems.append(f"{where}: rows listed twice: {dupes}")
    expected = set(props) - skip
    missing = sorted(expected - set(documented))
    extra = sorted(set(documented) - expected)
    if missing:
        problems.append(f"{where}: not documented: {missing}")
    if extra:
        problems.append(
            f"{where}: documented but not in schema: {extra}"
        )
    word = {"yes": "Yes", "oneof": "One of", "no": "No"}
    for name, cell in rows:
        if name not in expected:
            continue
        want = _expected_required(name, scope, any_of)
        if handler_enforced and name in handler_enforced:
            if want != "no":
                problems.append(
                    f"{where}: `{name}` is allowlisted as handler-enforced "
                    f"but the schema now says {word[want]!r}"
                )
            elif _normalise(cell) != handler_enforced[name]:
                problems.append(
                    f"{where}: `{name}` Required is {cell!r}, the "
                    f"allowlist expects {handler_enforced[name]!r}"
                )
            continue
        if _normalise(cell) != want:
            problems.append(
                f"{where}: `{name}` Required is {cell!r}, "
                f"schema says {word[want]!r}"
            )
    return problems


def _top_level_problems(
    schema: dict,
    rows: list[tuple[str, str]],
    handler_enforced: dict[str, str] | None = None,
) -> list[str]:
    any_of = schema.get("anyOf")
    problems: list[str] = []
    by_parent: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    for name, cell in rows:
        *parent, leaf = name.split(".")
        by_parent.setdefault(tuple(parent), []).append((leaf, cell))
    # the top level always gets compared, even with no row at all
    by_parent.setdefault((), [])
    for parent, scoped in by_parent.items():
        if not parent:
            problems += _compare(
                "top level",
                scoped,
                schema,
                any_of,
                skip=frozenset({"instance"}),
                handler_enforced=handler_enforced,
            )
            continue
        where = ".".join(parent)
        scope = schema
        try:
            for part in parent:
                scope = _properties(scope, where)[part]
            _properties(scope, where)
        except (AssertionError, KeyError) as err:
            problems.append(f"{where}.*: {err}")
            continue
        problems += _compare(f"{where}.*", scoped, scope, None)
    return problems


def _item_scopes(schema: dict) -> list[tuple[str, dict]]:
    found = []
    for name, prop in schema.get("properties", {}).items():
        items = prop.get("items")
        if (
            prop.get("type") == "array"
            and isinstance(items, dict)
            and items.get("type", "object") == "object"
            and items.get("properties")
        ):
            found.append((name, items))
    return found


def _problems(tool: str, text: str | None = None) -> list[str]:
    schema = SCHEMAS[tool]
    if text is None:
        text = TOOLS_PAGE.read_text(encoding="utf-8")
    lines = _sections(text).get(tool)
    if lines is None:
        return [f"no '## {tool}' section"]
    block = _parameters_block(lines)
    own = set(schema.get("properties", {})) - {"instance"}
    if block is None:
        if own:
            return [
                f"no '{PARAMETERS}' line, but the schema has "
                f"parameters {sorted(own)}"
            ]
        return []
    tables = _tables(block)
    enforced = {
        name: value
        for (t, name), value in HANDLER_ENFORCED.items()
        if t == tool
    }
    problems = _top_level_problems(
        schema, _rows(tables[0]) if tables else [], enforced
    )
    for number, table in enumerate(tables[1:], start=2):
        scopes = _item_scopes(schema)
        if len(scopes) != 1:
            problems.append(
                f"table {number} is a per-item table, but the schema has "
                f"{len(scopes)} array parameters of objects "
                f"({[n for n, _ in scopes]}), not exactly one"
            )
            continue
        name, items = scopes[0]
        problems += _compare(
            f"each `{name}` item (table {number})",
            _rows(table),
            items,
            None,
        )
    return problems


@pytest.mark.parametrize("tool", sorted(SCHEMAS))
def test_parameters_table_matches_schema(tool):
    problems = _problems(tool)
    assert not problems, (
        f"{TOOLS_PAGE.name} '## {tool}' parameters differ from the "
        "registered schema:\n  " + "\n  ".join(problems)
    )


def test_a_seeded_drift_is_reported():
    """The comparison fails on a deliberately broken table (a row dropped,
    a flag flipped, an extra row, a fenced table ignored)."""
    text = "\n".join(
        [
            "## wiki_get",
            "**Parameters:**",
            "| Name | Type | Required | Default | Description |",
            "|------|------|----------|---------|-------------|",
            "| `page_name` | string | **Yes** | - | x |",
            "| `bogus` | string | No | - | x |",
            "```",
            "| `version` | integer | No | - | x |",
            "```",
            "**Success Response:**",
        ]
    )
    problems = _problems("wiki_get", text)
    joined = "\n".join(problems)
    assert "not documented: ['page', 'version']" in joined
    assert "documented but not in schema: ['bogus']" in joined
    assert "`page_name` Required is '**Yes**'" in joined
    assert "schema says 'One of'" in joined


def test_a_table_less_block_means_no_parameters():
    text = "\n".join(
        [
            "## ping",
            "**Parameters:**",
            "",
            "None.",
            "**Success Response:**",
        ]
    )
    assert _problems("ping", text) == []
    assert _problems("wiki_get", text.replace("ping", "wiki_get"))


def test_handler_enforced_entries_are_not_stale():
    stale = []
    for tool, name in HANDLER_ENFORCED:
        schema = SCHEMAS.get(tool)
        if schema is None or name not in schema.get("properties", {}):
            stale.append(f"{tool}.{name}: no longer in the schema")
        elif (
            _expected_required(name, schema, schema.get("anyOf"))
            != "no"
        ):
            stale.append(
                f"{tool}.{name}: the schema now requires it (required / "
                "anyOf), so the strict rule owns it"
            )
    assert not stale, "stale HANDLER_ENFORCED entries: " + "; ".join(
        stale
    )


def test_an_allowlisted_cell_must_match_exactly():
    text = "\n".join(
        [
            "## ticket_attachment_get",
            "**Parameters:**",
            "| Name | Type | Required | Default | Description |",
            "|------|------|----------|---------|-------------|",
            "| `ticket_id` | integer | **Yes** | - | x |",
            "| `filename` | string | **Yes** | - | x |",
            "| `output_path` | string | No | - | x |",
            "**Success Response:**",
        ]
    )
    problems = _problems("ticket_attachment_get", text)
    assert len(problems) == 1
    assert "`output_path`" in problems[0] and "allowlist" in problems[0]
