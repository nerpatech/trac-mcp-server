"""User activity tool: user_activity.

What one account did in Trac in a period: tickets created, ticket changes
and comments, wiki page edits -- in one call, without the timeline plugin.

A ticket query can only filter on a ticket's *last* change (changetime), so
"the tickets X changed on day D" cannot be a query: a ticket changed on D
and again later is not found. This tool asks for every ticket whose last
change is on or after D, reads their change logs in ``system.multicall``
batches and keeps the entries of the period made by the account. Wiki edits
come from ``wiki.getRecentChanges`` since D and each page's versions back to
D.
"""

import base64
import json
import os
import re
import xmlrpc.client
import zoneinfo
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import mcp.types as types

from ...core.async_utils import run_sync
from ...core.client import TracClient
from .errors import build_error_response
from .registry import ToolSpec

# Tickets whose change logs one call reads (the newest changes first); more
# makes the result incomplete rather than the call endless.
MAX_TICKETS = 3000
# Change logs per system.multicall.
CHANGELOG_BATCH = 25
# Wiki pages, and versions per page, one call reads.
MAX_WIKI_PAGES = 300
MAX_WIKI_VERSIONS = 50
# Activities in the answer, and characters of a comment.
MAX_ACTIVITIES = 1000
COMMENT_CHARS = 200

# The JWT claim that names the Trac user of a bearer token: the claim the
# Trac-side proxy maps to REMOTE_USER (mod_auth_openidc
# OIDCOAuthRemoteUserClaim).
USER_CLAIM_ENV = "TRAC_MCP_OIDC_USER_CLAIM"
DEFAULT_USER_CLAIM = "preferred_username"

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


USER_ACTIVITY_TOOLS = [
    types.Tool(
        name="user_activity",
        description=(
            "What one account did in Trac in a period, in one call: tickets it "
            "created, every ticket change and comment it made (also on tickets "
            "changed again later, which a changetime query misses), and wiki "
            "page edits. Use it for 'what did I / X do in Trac on <day>' and "
            "'who changed what'. The signed-in account is used when username is "
            "empty; the result names it. Returns totals, complete, and the "
            "activities oldest first with ticket or page, fields changed, "
            "comment text (shortened) and URL."
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
                "date_from": {
                    "type": "string",
                    "description": "First day as YYYY-MM-DD (or an ISO date-time), inclusive, in time_zone.",
                },
                "date_to": {
                    "type": "string",
                    "description": "Last day, inclusive; empty means the day of date_from.",
                },
                "username": {
                    "type": "string",
                    "description": "Trac user name of the account; empty means the signed-in account.",
                },
                "time_zone": {
                    "type": "string",
                    "description": "IANA time zone of the days and of the times in the answer (default UTC).",
                    "default": "UTC",
                },
                "include_wiki": {
                    "type": "boolean",
                    "description": "Also list wiki page edits (default true).",
                    "default": True,
                },
            },
            "required": ["date_from"],
        },
    ),
]


def _bound(value: str, zone: zoneinfo.ZoneInfo, end: bool) -> datetime:
    """A period bound: a day (its first or last moment) or a date-time."""
    value = value.strip()
    if _DAY.fullmatch(value):
        day = date.fromisoformat(value)
        moment = datetime.combine(
            day, time.max if end else time.min, zone
        )
        return moment.replace(microsecond=999999 if end else 0)
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=zone)


def _to_utc(value: Any) -> datetime | None:
    """A Trac timestamp (xmlrpc DateTime, datetime or unix seconds) in UTC.

    Trac's XML-RPC sends DateTime values in UTC without a zone.
    """
    match value:
        case xmlrpc.client.DateTime():
            try:
                parsed = datetime.strptime(
                    value.value, "%Y%m%dT%H:%M:%S"
                )
            except ValueError:
                return None
            return parsed.replace(tzinfo=timezone.utc)
        case datetime():
            return (
                value
                if value.tzinfo
                else value.replace(tzinfo=timezone.utc)
            )
        case int() | float():
            return datetime.fromtimestamp(value, tz=timezone.utc)
    return None


def signed_in_user(client: TracClient) -> str | None:
    """The Trac user name of the account the client acts as, if known.

    Under OIDC it is a claim of the caller's own access token, read without
    checking the signature: it only selects which entries to keep, Trac
    decides what the token may see. With basic auth it is the configured
    user name.
    """
    token = getattr(client.config, "bearer_token", None)
    if token:
        parts = token.split(".")
        if len(parts) == 3:
            try:
                padded = parts[1] + "=" * (-len(parts[1]) % 4)
                claims = json.loads(base64.urlsafe_b64decode(padded))
            except (ValueError, RecursionError):
                return None
            claim = os.environ.get(USER_CLAIM_ENV) or DEFAULT_USER_CLAIM
            name = (
                claims.get(claim) if isinstance(claims, dict) else None
            )
            return name if isinstance(name, str) and name else None
        return None
    name = getattr(client.config, "username", None)
    return name or None


def _changelogs(client: TracClient, ids: list[int]) -> dict[int, list]:
    """``{id: changeLog}`` for each ticket whose change log loaded."""
    return client._batched_multicall(
        ids,
        CHANGELOG_BATCH,
        lambda tid: [
            {"methodName": "ticket.changeLog", "params": [tid]}
        ],
        lambda tid, values: values[0] or [],
        lambda items: client._fetch_each(
            items, client.get_ticket_changelog
        ),
    )


def _collect(
    client: TracClient,
    user: str,
    start: datetime,
    end: datetime,
    include_wiki: bool,
) -> dict[str, Any]:
    """Read and filter; runs in a worker thread."""
    base = client.config.trac_url.rstrip("/")
    wanted = user.casefold()
    since = (start.astimezone(timezone.utc) - timedelta(days=1)).date()
    complete = True

    # Every ticket whose last change is on or after the first day, newest
    # first. One day of slack: Trac reads the date in the account's zone.
    ids = [
        int(i)
        for i in client.search_tickets(
            f"changetime={since.isoformat()}..&max=0&order=changetime&desc=1"
        )
        or []
    ]
    if len(ids) > MAX_TICKETS:
        ids = ids[:MAX_TICKETS]
        complete = False
    logs = _changelogs(client, ids)
    if len(logs) < len(ids):
        complete = False

    groups: dict[tuple[int, datetime], dict[str, Any]] = {}
    for tid, log in logs.items():
        for row in log:
            if len(row) < 5 or str(row[1]).casefold() != wanted:
                continue
            when = _to_utc(row[0])
            if when is None or not (start <= when <= end):
                continue
            entry = groups.setdefault(
                (tid, when),
                {
                    "time": when,
                    "kind": "ticket_change",
                    "ticket": tid,
                    "fields": [],
                },
            )
            field, new = row[2], row[4]
            if field == "comment":
                text = str(new or "").strip()
                if text:
                    entry["comment"] = text[:COMMENT_CHARS] + (
                        "..." if len(text) > COMMENT_CHARS else ""
                    )
            elif field not in entry["fields"]:
                entry["fields"].append(field)

    # Tickets the account created in the period.
    created: dict[int, datetime] = {}
    query = (
        f"reporter={user}&time={since.isoformat()}.."
        f"{(end.astimezone(timezone.utc) + timedelta(days=1)).date().isoformat()}&max=0"
    )
    created_ids = [int(i) for i in client.search_tickets(query) or []]
    tickets = client.get_tickets(
        sorted(set(created_ids) | {t for t, _ in groups})
    )
    for tid in created_ids:
        got = tickets.get(tid)
        when = _to_utc(got[1]) if got and len(got) > 1 else None
        if when is not None and start <= when <= end:
            created[tid] = when

    activities: list[dict[str, Any]] = []
    for tid, when in created.items():
        activities.append(
            {"time": when, "kind": "ticket_created", "ticket": tid}
        )
    activities.extend(groups.values())
    for item in activities:
        tid = item["ticket"]
        got = tickets.get(tid)
        attrs = (
            got[3]
            if got and len(got) > 3 and isinstance(got[3], dict)
            else {}
        )
        item["summary"] = attrs.get("summary")
        item["url"] = f"{base}/ticket/{tid}"

    if include_wiki:
        wiki, wiki_complete = _wiki_edits(
            client, wanted, start, end, base
        )
        activities.extend(wiki)
        complete = complete and wiki_complete

    activities.sort(key=lambda a: a["time"])
    return {
        "activities": activities,
        "complete": complete,
        "tickets_scanned": len(ids),
    }


def _wiki_edits(
    client: TracClient,
    wanted: str,
    start: datetime,
    end: datetime,
    base: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Wiki versions the account saved in the period."""
    complete = True
    changes = (
        client.get_recent_wiki_changes(int(start.timestamp())) or []
    )
    if len(changes) > MAX_WIKI_PAGES:
        changes = changes[:MAX_WIKI_PAGES]
        complete = False
    out: list[dict[str, Any]] = []
    for change in changes:
        name = str(change.get("name") or "")
        version = int(change.get("version") or 0) if name else 0
        info: dict[str, Any] | None = change
        for _ in range(MAX_WIKI_VERSIONS):
            if not info or version < 1:
                break
            when = _to_utc(info.get("lastModified"))
            if when is None or when < start:
                break
            if (
                when <= end
                and str(info.get("author", "")).casefold() == wanted
            ):
                out.append(
                    {
                        "time": when,
                        "kind": "wiki_edit",
                        "page": name,
                        "version": version,
                        "comment": info.get("comment") or None,
                        "url": f"{base}/wiki/{name}?version={version}",
                    }
                )
            version -= 1
            if version < 1:
                break
            try:
                info = client.get_wiki_page_info(name, version)
            except xmlrpc.client.Fault:
                break
        else:
            complete = False
    return out, complete


async def _handle_user_activity(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle user_activity."""
    try:
        zone = zoneinfo.ZoneInfo(args.get("time_zone") or "UTC")
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return build_error_response(
            "validation_error",
            f"unknown time_zone {args.get('time_zone')!r}",
            "Pass an IANA time zone such as Europe/Berlin.",
        )
    date_from = str(args.get("date_from") or "")
    try:
        start = _bound(date_from, zone, end=False)
        end = _bound(
            str(args.get("date_to") or date_from), zone, end=True
        )
    except ValueError:
        return build_error_response(
            "validation_error",
            "date_from and date_to must be YYYY-MM-DD or ISO date-times",
            "Pass e.g. date_from=2026-11-05.",
        )
    if end < start:
        return build_error_response(
            "validation_error",
            "date_to lies before date_from",
            "Swap the two dates.",
        )
    user = str(args.get("username") or "").strip() or signed_in_user(
        client
    )
    if not user:
        return build_error_response(
            "validation_error",
            "the signed-in account is not known",
            "Pass username.",
        )

    found = await run_sync(
        _collect,
        client,
        user,
        start,
        end,
        args.get("include_wiki", True) is not False,
    )
    activities = found["activities"]
    totals: dict[str, int] = {}
    for item in activities:
        totals[item["kind"]] = totals.get(item["kind"], 0) + 1
    comments = sum(
        1
        for a in activities
        if a["kind"] == "ticket_change" and a.get("comment")
    )
    complete = found["complete"]
    if len(activities) > MAX_ACTIVITIES:
        activities = activities[:MAX_ACTIVITIES]
        complete = False
    for item in activities:
        item["time"] = (
            item["time"].astimezone(zone).isoformat(timespec="seconds")
        )
    result = {
        "username": user,
        "period": {
            "from": start.isoformat(timespec="seconds"),
            "to": end.isoformat(timespec="seconds"),
        },
        "tickets_scanned": found["tickets_scanned"],
        "total": sum(totals.values()),
        "by_kind": totals,
        "comments": comments,
        "tickets": len(
            {a["ticket"] for a in activities if "ticket" in a}
        ),
        "complete": complete,
        "activities": activities,
    }
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text=json.dumps(result, ensure_ascii=False)
            )
        ],
        structuredContent=result,
    )


USER_ACTIVITY_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=USER_ACTIVITY_TOOLS[0],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_user_activity,
    ),
]
