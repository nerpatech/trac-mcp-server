"""Each tool's ``**Parameters:**`` table in ``docs/reference/mcp-tools.md``
is held to the tool's registered input schema (Trac /trac_mcp_server #252).

The schemas come from the built registry, the way
``tests/test_readme_tool_count.py`` builds it (``build_registry`` over
``[PING_SPEC] + ALL_SPECS`` with the page alias and strict schema), so a
parameter the server adds, drops or makes required shows up here. One test
per registered tool, so a failure names the tool. Per tool:

- the section is the lines under ``## <tool>`` up to the next ``## ``;
  lines inside fenced code blocks (``` or ~~~) are ignored. A ``## <tool>``
  heading that appears more than once fails. The parameters block runs from
  the ``**Parameters:**`` line to the next line that starts with ``**``,
  unless the next non-blank line after that ``**`` line is a table header
  (then it is a caption such as ``**Each ticket object:**`` and its table
  belongs to the block). A section with no ``**Parameters:**`` line passes
  only if the schema has no property besides ``instance``. A block with no
  row (the ``*(none)*`` row of ``ping`` and ``get_server_time``) means no
  parameters;
- every table body row in the block (any ``|`` line after the header and the
  ``|---`` divider) must start with exactly one backticked name, or be the
  ``*(none)*`` placeholder; anything else is reported with its line, so an
  un-backticked or stale row cannot hide;
- the first table of the block holds the top-level parameters. A name
  without a dot is compared with the schema's top-level ``properties`` minus
  ``instance`` (which the docs describe once, under "Instance Routing"). A
  dotted row ``a.b`` is compared with ``properties[a]["properties"]``, and
  fails if ``a`` is not an object with properties. Nested scopes are
  mandatory: every top-level parameter that is an object with ``properties``
  needs a dotted row for each of them;
- any later table of the block (``Each ticket object:`` and the like) holds
  the item fields of the tool's one top-level array parameter whose
  ``items`` is an object with ``properties``; no such parameter, or more
  than one, fails, and so does a tool with exactly one such parameter and no
  item table;
- names are compared as sets per scope, missing and extra reported
  separately;
- the Required cell (third), with ``*`` and whitespace stripped and case
  ignored, is ``Yes`` when the scope's own ``required`` list has the name,
  ``One of`` when some branch of the top-level ``anyOf`` requires it (for
  example ``page_name`` / ``page``), otherwise ``No``. Any other cell fails,
  except the named ``HANDLER_ENFORCED`` entries below.

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
NAME_CELL = re.compile(r"^`([^`]+)`$")
NONE_CELL = "*(none)*"
# Cells split on a pipe that is not escaped as ``\|``.
CELL_SPLIT = re.compile(r"(?<!\\)\|")
DIVIDER_CELL = re.compile(r":?-+:?")
PARAMETERS = "**Parameters:**"

# A line of the page: (1-based line number, text).
Line = tuple[int, str]
# A table row: (line number, cells).
Row = tuple[int, list[str]]


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


def _sections(text: str) -> dict[str, list[list[Line]]]:
    """For each ``## `` heading (outside fences), the lines of each section
    under it, to the next heading, with fenced lines left out. A heading
    that appears twice maps to two sections."""
    sections: dict[str, list[list[Line]]] = {}
    current: list[Line] | None = None
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        marker = FENCE.match(line)
        if fence is not None:
            if (
                marker
                and marker.group(1)[0] == fence[0]
                and len(marker.group(1)) >= len(fence)
            ):
                fence = None
            continue
        if marker:
            fence = marker.group(1)
        elif line.startswith("## "):
            current = []
            sections.setdefault(line[3:].strip(), []).append(current)
        elif current is not None:
            current.append((number, line))
    return sections


def _cells(line: str) -> list[str]:
    cells = [c.strip() for c in CELL_SPLIT.split(line.strip())]
    # drop the empty cells outside the leading and trailing pipes
    return cells[1:-1] if line.rstrip().endswith("|") else cells[1:]


def _is_divider(cells: list[str]) -> bool:
    return bool(cells) and all(DIVIDER_CELL.fullmatch(c) for c in cells)


def _starts_table(lines: list[Line], i: int) -> bool:
    """Whether ``lines[i]`` is a table header: a ``|`` row whose next line
    is the ``|---`` divider."""
    return (
        i + 1 < len(lines)
        and lines[i][1].lstrip().startswith("|")
        and lines[i + 1][1].lstrip().startswith("|")
        and _is_divider(_cells(lines[i + 1][1]))
    )


def _parameters_block(lines: list[Line]) -> list[Line] | None:
    """The lines after ``**Parameters:**`` up to the next ``**`` line that
    is not the caption of a table, or None without a ``**Parameters:**``
    line."""
    start = next(
        (
            i
            for i, (_, text) in enumerate(lines)
            if text.strip() == PARAMETERS
        ),
        None,
    )
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if not lines[i][1].startswith("**"):
            continue
        following = next(
            (
                j
                for j in range(i + 1, len(lines))
                if lines[j][1].strip()
            ),
            None,
        )
        if following is not None and _starts_table(lines, following):
            continue
        end = i
        break
    return lines[start + 1 : end]


def _tables(block: list[Line]) -> list[list[Row]]:
    """Each table of the block, as its rows, a table being a run of
    consecutive ``|`` lines."""
    tables: list[list[Row]] = []
    run: list[Row] | None = None
    for number, line in block:
        if line.lstrip().startswith("|"):
            if run is None:
                run = []
                tables.append(run)
            run.append((number, _cells(line)))
        else:
            run = None
    return tables


def _rows(table: list[Row]) -> tuple[list[tuple[str, str]], list[str]]:
    """``(name, required cell)`` for each body row of a table, and the
    problems of the rows that are not one backticked name. The first row is
    the header and divider rows are skipped; ``*(none)*`` is a placeholder
    with no name."""
    rows: list[tuple[str, str]] = []
    problems: list[str] = []
    for number, cells in table[1:]:
        if _is_divider(cells):
            continue
        first = cells[0] if cells else ""
        match = NAME_CELL.match(first)
        if match:
            required = cells[2] if len(cells) > 2 else ""
            rows.append((match.group(1), required))
        elif first != NONE_CELL:
            problems.append(
                f"line {number}: the row's first cell is not exactly one "
                f"backticked name: {first!r}"
            )
    return rows, problems


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


def _is_object_with_properties(schema: object) -> bool:
    return (
        isinstance(schema, dict)
        and schema.get("type") == "object"
        and bool(schema.get("properties"))
    )


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
    # and so does every object parameter, even with no dotted row
    for name, prop in schema.get("properties", {}).items():
        if name != "instance" and _is_object_with_properties(prop):
            by_parent.setdefault((name,), [])
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
        for part in parent:
            scope = scope.get("properties", {}).get(part, {})
        if not _is_object_with_properties(scope):
            names = [f"`{where}.{leaf}`" for leaf, _ in scoped]
            problems.append(
                f"{', '.join(names)}: `{where}` is not an object with "
                "properties in the schema"
            )
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


def _problems(
    tool: str, text: str | None = None, schema: dict | None = None
) -> list[str]:
    """Everything wrong with ``tool``'s parameters block in ``text`` (the
    tool reference page by default) against ``schema`` (the registered one
    by default)."""
    if schema is None:
        schema = SCHEMAS[tool]
    if text is None:
        text = TOOLS_PAGE.read_text(encoding="utf-8")
    sections = _sections(text).get(tool)
    if not sections:
        return [f"no '## {tool}' section"]
    problems: list[str] = []
    if len(sections) > 1:
        problems.append(
            f"the '## {tool}' heading appears {len(sections)} times"
        )
    block = _parameters_block(sections[0])
    own = set(schema.get("properties", {})) - {"instance"}
    if block is None:
        if own:
            problems.append(
                f"no '{PARAMETERS}' line, but the schema has "
                f"parameters {sorted(own)}"
            )
        return problems
    tables = _tables(block)
    enforced = {
        name: value
        for (t, name), value in HANDLER_ENFORCED.items()
        if t == tool
    }
    top_rows: list[tuple[str, str]] = []
    if tables:
        top_rows, row_problems = _rows(tables[0])
        problems += row_problems
    problems += _top_level_problems(schema, top_rows, enforced)
    scopes = _item_scopes(schema)
    if len(scopes) == 1 and len(tables) < 2:
        problems.append(
            f"no per-item table for the array parameter `{scopes[0][0]}`"
        )
    for number, table in enumerate(tables[1:], start=2):
        if len(scopes) != 1:
            problems.append(
                f"table {number} is a per-item table, but the schema has "
                f"{len(scopes)} array parameters of objects "
                f"({[n for n, _ in scopes]}), not exactly one"
            )
            continue
        name, items = scopes[0]
        item_rows, row_problems = _rows(table)
        problems += row_problems
        problems += _compare(
            f"each `{name}` item (table {number})",
            item_rows,
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


HEADER = [
    "| Name | Type | Required | Default | Description |",
    "|------|------|----------|---------|-------------|",
]


def _doc(tool: str, *body: str) -> str:
    return "\n".join(
        [
            f"## {tool}",
            "",
            PARAMETERS,
            "",
            *body,
            "",
            "**Success Response:**",
        ]
    )


def _item_rows(first: str = "summary") -> list[str]:
    return [
        f"| `{first}` | string | **Yes** | - | x |",
        "| `description` | string | **Yes** | - | x |",
        "| `ticket_type` | string | No | - | x |",
        "| `priority` | string | No | - | x |",
        "| `component` | string | No | - | x |",
        "| `milestone` | string | No | - | x |",
        "| `owner` | string | No | - | x |",
        "| `keywords` | string | No | - | x |",
        "| `cc` | string | No | - | x |",
    ]


BATCH_TOP = [
    *HEADER,
    "| `tickets` | array | **Yes** | - | x |",
    "| `target_cap` | integer | No | `50` | x |",
]


def _batch(*after_top: str) -> str:
    return _doc("ticket_batch_create", *BATCH_TOP, *after_top)


def _has(problems: list[str], expected: str) -> bool:
    return any(expected in p for p in problems)


def test_the_seeded_batch_page_is_clean():
    """The control for the seeds below: they break this page one way each."""
    text = _batch("", "Each ticket object:", "", *HEADER, *_item_rows())
    assert _problems("ticket_batch_create", text) == []


def test_a_renamed_per_item_row_is_reported():
    text = _batch(
        "", "Each ticket object:", "", *HEADER, *_item_rows("summry")
    )
    problems = _problems("ticket_batch_create", text)
    assert _has(
        problems,
        "each `tickets` item (table 2): not documented: ['summary']",
    )
    assert _has(
        problems,
        "each `tickets` item (table 2): documented but not in schema: ['summry']",
    )


def test_a_per_item_table_behind_a_bold_caption_still_counts():
    text = _batch(
        "",
        "**Each ticket object:**",
        "",
        *HEADER,
        *_item_rows("summry"),
    )
    problems = _problems("ticket_batch_create", text)
    assert _has(problems, "documented but not in schema: ['summry']")
    # without the table the caption would end the block: no per-item table
    clean = _batch(
        "", "**Each ticket object:**", "", *HEADER, *_item_rows()
    )
    assert _problems("ticket_batch_create", clean) == []


def test_a_bold_line_before_prose_still_ends_the_block():
    text = _batch(
        "", "**Notes:**", "", "Prose.", "", *HEADER, *_item_rows()
    )
    assert _has(
        _problems("ticket_batch_create", text),
        "no per-item table for the array parameter `tickets`",
    )


def test_a_deleted_per_item_table_is_reported():
    assert _has(
        _problems("ticket_batch_create", _batch()),
        "no per-item table for the array parameter `tickets`",
    )


def test_a_deleted_dotted_row_is_reported():
    text = _doc(
        "milestone_create",
        *HEADER,
        "| `name` | string | **Yes** | - | x |",
        "| `attributes` | object | No | `{}` | x |",
        "| `attributes.completed` | string | No | - | x |",
        "| `attributes.description` | string | No | - | x |",
        "| `target_cap` | integer | No | `50` | x |",
    )
    assert _has(
        _problems("milestone_create", text),
        "attributes.*: not documented: ['due']",
    )


def test_an_object_parameter_with_no_dotted_rows_is_reported():
    text = _doc(
        "milestone_create",
        *HEADER,
        "| `name` | string | **Yes** | - | x |",
        "| `attributes` | object | No | `{}` | x |",
        "| `target_cap` | integer | No | `50` | x |",
    )
    assert _has(
        _problems("milestone_create", text),
        "attributes.*: not documented: ['completed', 'description', 'due']",
    )


def test_an_extra_table_needs_exactly_one_array_of_objects():
    extra = [*HEADER, "| `a` | string | No | - | x |"]
    text = _doc(
        "t",
        *HEADER,
        "| `p` | string | No | - | x |",
        "",
        "More:",
        "",
        *extra,
    )
    one_string = {
        "type": "object",
        "properties": {"p": {"type": "string"}},
    }
    problems = _problems("t", text, one_string)
    assert _has(
        problems, "0 array parameters of objects ([]), not exactly one"
    )
    arr = {
        "type": "array",
        "items": {"type": "object", "properties": {"a": {}}},
    }
    two_arrays = {
        "type": "object",
        "properties": {"p": arr, "q": arr},
    }
    problems = _problems("t", text, two_arrays)
    assert _has(
        problems,
        "2 array parameters of objects (['p', 'q']), not exactly one",
    )


def test_an_unbackticked_stale_row_is_reported():
    text = _doc(
        "wiki_get",
        *HEADER,
        "| `page_name` | string | **One of** | - | x |",
        "| `page` | string | **One of** | - | x |",
        "| `version` | integer | No | - | x |",
        "| old_param | string | No | - | removed |",
    )
    problems = _problems("wiki_get", text)
    assert _has(
        problems,
        "line 10: the row's first cell is not exactly one backticked "
        "name: 'old_param'",
    )


def test_a_duplicated_section_is_reported():
    text = _doc("ping", *HEADER, f"| {NONE_CELL} | - | - | - | x |")
    assert _problems("ping", text) == []
    assert _has(
        _problems("ping", text + "\n" + text),
        "the '## ping' heading appears 2 times",
    )


def test_a_dotted_row_under_a_string_parameter_is_reported():
    text = _doc(
        "wiki_get",
        *HEADER,
        "| `page_name` | string | **One of** | - | x |",
        "| `page_name.x` | string | No | - | x |",
        "| `page` | string | **One of** | - | x |",
        "| `version` | integer | No | - | x |",
    )
    assert _has(
        _problems("wiki_get", text),
        "`page_name.x`: `page_name` is not an object with properties",
    )


def test_a_seeded_drift_is_reported():
    """The comparison fails on a deliberately broken table (a row dropped,
    a flag flipped, an extra row, a fenced table ignored)."""
    text = "\n".join(
        [
            "## wiki_get",
            "**Parameters:**",
            *HEADER,
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
    text = _doc(
        "ticket_attachment_get",
        *HEADER,
        "| `ticket_id` | integer | **Yes** | - | x |",
        "| `filename` | string | **Yes** | - | x |",
        "| `output_path` | string | No | - | x |",
    )
    problems = _problems("ticket_attachment_get", text)
    assert _has(
        problems, "`output_path` Required is 'No', the allowlist"
    )
