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
from .ticket_read import _ticket_url

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
                        "'id' (default): values are ticket numbers. Otherwise the text field to "
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
                    "description": (
                        "Comma-separated ticket fields to return for each match. 'id' and 'url' "
                        "are always returned and cannot be requested."
                    ),
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
    items: list[str] | None = None
    if isinstance(raw, list):
        items = [str(item) for item in raw]
    else:
        text = str(raw or "").strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                items = [str(item) for item in parsed]
        if items is None:
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


_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Field types a value can be searched in as text.
_TEXT_TYPES = frozenset({"text", "textarea"})

# A longer digit string is not a ticket number and never reaches int().
_MAX_DIGITS = 18

# Keys that change how Trac runs or lays out a query rather than what it selects.
_CONTROL_KEYS = frozenset(
    {
        "max",
        "page",
        "order",
        "desc",
        "group",
        "groupdesc",
        "col",
        "format",
        "report",
        "verbose",
        "rows",
        "row",
    }
)


def _check_scope(scope: str | None, own_key: str) -> str | None:
    """Return the offending key of a caller query that could override the
    chunk query, or None.

    Trac keeps the last value of a repeated ``max``, extends the value list of a
    repeated field key (OR-ing into the chunk's alternatives) and starts a new
    constraint group at ``or``, which the chunk constraint does not reach.
    """
    for clause in re.split(r"(?<!\\)&", scope or ""):
        if not clause:
            continue
        if clause.strip().lower() == "or":
            return "or"
        if "=" not in clause:
            return clause.strip()
        key = clause.split("=", 1)[0].rstrip("~^$!").strip().lower()
        if key in _CONTROL_KEYS or key == own_key:
            return key
    return None


def _with_scope(query: str, scope: str | None) -> str:
    return f"{query}&{scope}" if scope else query


def _needle(term: str, list_field: bool) -> str:
    """The part of a term to put in the Trac query, escaped.

    The whole-word post-filter decides the match, so the query only has to return
    a superset of the candidates and may search for any substring of the term.
    Trac has no escape for a backslash, and substitutes the literal ``$USER`` in a
    filter value with the caller's name, so both separate segments. A list field
    (``keywords``, ``cc``) is also split by Trac on whitespace and double quotes
    with a leading ``-`` meaning NOT, so there the needle is the longest segment
    between those characters too. Any other field is one ``LIKE '%value%'``, so
    its needle keeps whitespace, quotes and dashes.
    """
    separator = r'[\s\\"]+|\$USER' if list_field else r"\\|\$USER"
    segments = re.split(separator, term)
    if list_field:
        segments = [segment.lstrip("-") for segment in segments]
    needle = max(segments, key=len, default="")
    # "|" separates alternatives and "&" conditions in a Trac query
    return needle.replace("|", r"\|").replace("&", r"\&")


def _ticket_number(term: str) -> int | None:
    digits = term.removeprefix("#")
    if (
        digits
        and digits.isascii()
        and digits.isdecimal()
        and len(digits) <= _MAX_DIGITS
    ):
        return int(digits)
    return None


async def _load(
    client: TracClient, ids: list[int], cache: dict[int, dict | None]
) -> None:
    """Fetch the tickets not in ``cache`` yet; a ticket that failed to load is
    cached as None and not fetched again."""

    async def one(tid: int) -> tuple[int, dict | None]:
        try:
            ticket = await run_sync_limited(client.get_ticket, tid)
            return tid, ticket[3]
        except Exception:
            return tid, None

    todo = [tid for tid in dict.fromkeys(ids) if tid not in cache]
    for tid, attrs in await gather_limited([one(tid) for tid in todo]):
        cache[tid] = attrs


def _compact(
    client: TracClient, tid: int, attrs: dict, fields: list[str]
) -> dict[str, Any]:
    return {
        "id": tid,
        **{name: attrs.get(name) or "" for name in fields},
        "url": _ticket_url(client, tid),
    }


def _invalid(message: str, action: str) -> types.CallToolResult:
    return build_error_response("validation_error", message, action)


async def _handle_check_list(
    client: TracClient, args: dict
) -> types.CallToolResult:
    terms = parse_terms(args.get("values"))
    if not terms:
        return _invalid(
            "values is empty", "Pass ticket numbers or texts."
        )
    if len(terms) > MAX_TERMS:
        return _invalid(
            f"at most {MAX_TERMS} values per call, got {len(terms)}",
            "Split the list.",
        )
    match_field = args.get("match_field") or "id"
    if not _FIELD_NAME.fullmatch(match_field):
        return _invalid(
            f"match_field {match_field!r} is not a ticket field name",
            "Pass 'id' or a field name such as 'summary' or 'keywords'.",
        )
    scope = args.get("query") or None
    own_key = "id" if match_field == "id" else match_field.lower()
    offending = _check_scope(scope, own_key)
    if offending is not None:
        return _invalid(
            f"query may not contain {offending!r}",
            "The query may only narrow by other fields, e.g. "
            "'status!=closed&component=Core'; leave out paging, ordering, "
            f"layout, 'or' and the {own_key!r} field.",
        )
    list_field = False
    if match_field != "id":
        definitions = await run_sync_limited(client.get_ticket_fields)
        text_fields = [
            field
            for field in definitions
            if field.get("type") in _TEXT_TYPES
        ]
        chosen = next(
            (f for f in text_fields if f.get("name") == match_field),
            None,
        )
        if chosen is None:
            names = ", ".join(str(f.get("name")) for f in text_fields)
            return _invalid(
                f"match_field {match_field!r} is not a text field of this Trac",
                f"Pass 'id' or one of the text fields: {names}.",
            )
        list_field = (
            chosen.get("type") == "text"
            and chosen.get("format") == "list"
        )
    fields = [
        name
        for name in (
            part.strip()
            for part in (args.get("fields") or "summary,status").split(
                ","
            )
        )
        if name and name not in ("id", "url")
    ]
    only_missing = bool(args.get("only_missing"))
    result: dict[str, Any] = {"values_checked": len(terms)}
    failed_ids: list[int] = []
    cache: dict[int, dict | None] = {}

    if match_field == "id":
        numbers: dict[str, int] = {}
        invalid = []
        for term in terms:
            number = _ticket_number(term)
            if number is None:
                invalid.append(term)
            else:
                numbers[term] = number
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
        missing = [
            term
            for term, number in numbers.items()
            if number not in existing
        ]
        found_terms = [
            term
            for term, number in numbers.items()
            if number in existing
        ]
        found: dict[str, Any] = {}
        if not only_missing and found_terms:
            wanted = sorted({numbers[t] for t in found_terms})
            await _load(client, wanted, cache)
            failed_ids = [tid for tid in wanted if cache[tid] is None]
            for term in found_terms:
                tid, attrs = numbers[term], cache[numbers[term]]
                found[term] = (
                    {"id": tid, "url": _ticket_url(client, tid)}
                    if attrs is None
                    else _compact(client, tid, attrs, fields)
                )
        found_count = len(found_terms)
        if invalid:
            result["not_a_ticket_number"] = invalid
    else:
        hits: dict[str, list[dict]] = {}
        needles = {term: _needle(term, list_field) for term in terms}
        searchable = [term for term in terms if needles[term]]
        unsearchable = [term for term in terms if not needles[term]]
        missing = []
        unresolved: list[str] = []
        failed: set[int] = set()
        for offset in range(0, len(searchable), TEXT_CHUNK):
            chunk = searchable[offset : offset + TEXT_CHUNK]
            query = _with_scope(
                f"{match_field}~="
                + "|".join(needles[term] for term in chunk)
                + "&max=0",
                scope,
            )
            candidates = list(
                await run_sync(client.search_tickets, query)
            )
            await _load(client, candidates, cache)
            chunk_failed = {t for t in candidates if cache[t] is None}
            failed.update(chunk_failed)
            for tid in candidates:
                attrs = cache[tid]
                if attrs is None:
                    continue
                for term in chunk:
                    if word_match(
                        term, str(attrs.get(match_field) or "")
                    ):
                        hits.setdefault(term, []).append(
                            _compact(client, tid, attrs, fields)
                        )
            for term in chunk:
                if term not in hits:
                    (unresolved if chunk_failed else missing).append(
                        term
                    )
        failed_ids = sorted(failed)
        found_count = len(hits)
        found = {} if only_missing else hits
        if unsearchable:
            result["unsearchable"] = unsearchable
        if unresolved:
            result["unresolved"] = unresolved

    result.update(
        {
            "found_count": found_count,
            "missing_count": len(missing),
            "missing": missing,
            "failed_ids": failed_ids,
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
