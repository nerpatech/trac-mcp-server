"""List tool for comparisons: check many ticket ids or texts in one call.

The model passes a whole list; the server runs the Trac queries and the matching, so a
comparison needs one tool call instead of one search per item.
"""

import json
import re
from typing import Any

import mcp.types as types

from ...core.async_utils import (
    gather_limited,
    run_sync,
    run_sync_limited,
)
from ...core.client import TracClient
from .errors import build_error_response
from .registry import ToolSpec

MAX_TERMS = 1000
ID_CHUNK = 100
TEXT_CHUNK = 15

TICKET_LIST_TOOLS = [
    types.Tool(
        name="ticket_check_list",
        description=(
            "Check a whole list of ticket numbers or search texts (for example keys from another "
            "system) against Trac in one call. Returns which values exist, with the requested "
            "fields, and which are missing. Use this instead of one search per value."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "values": {
                    "type": "string",
                    "description": (
                        "Ticket numbers or search texts, separated by commas or new lines; a JSON "
                        "array of strings is accepted too. At most 1000."
                    ),
                },
                "match_field": {
                    "type": "string",
                    "default": "id",
                    "description": (
                        "'id' (default): values are ticket numbers. Otherwise the ticket field to "
                        "find the values in as whole words, e.g. 'summary', 'keywords', 'description'."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "(Optional) Trac query that restricts where to look, e.g. 'status!=closed&component=Core'.",
                },
                "fields": {
                    "type": "string",
                    "default": "summary,status",
                    "description": "Comma-separated ticket fields to return for each match.",
                },
                "only_missing": {
                    "type": "boolean",
                    "default": False,
                    "description": "Return only the values without a match.",
                },
            },
            "required": ["values"],
        },
    ),
]


def parse_terms(raw: Any) -> list[str]:
    """Accept a JSON array or a list separated by commas, semicolons or new lines."""
    if isinstance(raw, list):
        items = [str(item) for item in raw]
    else:
        text = str(raw or "").strip()
        items = []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    items = [str(item) for item in parsed]
            except ValueError:
                items = []
        if not items:
            items = re.split(r"[,;\n]+", text)
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        item = item.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def word_match(term: str, text: str) -> bool:
    """Whole-word match: "ABC-12" does not match inside "ABC-123"."""
    return (
        re.search(
            r"(?i)(^|[^\w])" + re.escape(term) + r"($|[^\w])",
            text or "",
        )
        is not None
    )


def _with_scope(query: str, scope: str | None) -> str:
    return f"{query}&{scope}" if scope else query


def _query_value(term: str) -> str:
    # "|" separates alternatives and "&" conditions in a Trac query
    return re.sub(r"[|&]", " ", term)


async def _fetch(
    client: TracClient, ids: list[int], fields: list[str]
) -> dict[int, dict]:
    async def one(tid: int) -> tuple[int, dict]:
        attrs = (await run_sync_limited(client.get_ticket, tid))[3]
        return tid, attrs

    return dict(await gather_limited([one(tid) for tid in ids]))


def _compact(
    tid: int, attrs: dict, fields: list[str]
) -> dict[str, Any]:
    return {"id": tid, **{name: attrs.get(name, "") for name in fields}}


async def _handle_check_list(
    client: TracClient, args: dict
) -> types.CallToolResult:
    terms = parse_terms(args.get("values"))
    if not terms:
        return build_error_response(
            "validation_error",
            "values is empty",
            "Pass ticket numbers or texts.",
        )
    if len(terms) > MAX_TERMS:
        return build_error_response(
            "validation_error",
            f"at most {MAX_TERMS} values per call, got {len(terms)}",
            "Split the list.",
        )
    match_field = args.get("match_field") or "id"
    scope = args.get("query") or None
    fields = [
        name.strip()
        for name in (args.get("fields") or "summary,status").split(",")
        if name.strip()
    ]
    only_missing = bool(args.get("only_missing"))
    result: dict[str, Any] = {"values_checked": len(terms)}

    if match_field == "id":
        numbers: dict[str, int] = {}
        invalid = []
        for term in terms:
            digits = term.lstrip("#")
            if digits.isdigit():
                numbers[term] = int(digits)
            else:
                invalid.append(term)
        existing: set[int] = set()
        ids = sorted(set(numbers.values()))
        for offset in range(0, len(ids), ID_CHUNK):
            chunk = ids[offset : offset + ID_CHUNK]
            query = _with_scope(
                "id=" + "|".join(map(str, chunk)) + "&max=0", scope
            )
            existing.update(
                await run_sync(client.search_tickets, query)
            )
        found_terms = [
            term
            for term, number in numbers.items()
            if number in existing
        ]
        missing = [term for term in numbers if term not in found_terms]
        found_count = len(found_terms)
        found: dict[str, Any] = {}
        if not only_missing and found_terms:
            details = await _fetch(
                client,
                sorted({numbers[t] for t in found_terms}),
                fields,
            )
            found = {
                term: _compact(
                    numbers[term], details[numbers[term]], fields
                )
                for term in found_terms
            }
        if invalid:
            result["not_a_ticket_number"] = invalid
    else:
        hits: dict[str, list[dict]] = {}
        for offset in range(0, len(terms), TEXT_CHUNK):
            chunk = terms[offset : offset + TEXT_CHUNK]
            query = _with_scope(
                f"{match_field}~="
                + "|".join(_query_value(term) for term in chunk)
                + "&max=0",
                scope,
            )
            candidates = await run_sync(client.search_tickets, query)
            details = await _fetch(
                client,
                list(candidates),
                list(dict.fromkeys(fields + [match_field])),
            )
            for tid, attrs in details.items():
                for term in chunk:
                    if word_match(
                        term, str(attrs.get(match_field, ""))
                    ):
                        hits.setdefault(term, []).append(
                            _compact(tid, attrs, fields)
                        )
        missing = [term for term in terms if term not in hits]
        found_count = len(hits)
        found = {} if only_missing else hits

    result.update(
        {
            "found_count": found_count,
            "missing_count": len(missing),
            "missing": missing,
        }
    )
    if not only_missing:
        result["found"] = found
    text = json.dumps(result, ensure_ascii=False)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=result,
    )


TICKET_LIST_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=TICKET_LIST_TOOLS[0],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_check_list,
    ),
]
