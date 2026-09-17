"""Timeline tool: timeline_search.

Requires the ``tracrpc_comment`` plugin on the Trac server (see
``plugins/`` in this repo). Stock XmlRpcPlugin exposes no timeline
methods at all, so against a server without it this fails with a
``MethodNotFound`` fault, which is translated here into a message that
says so rather than leaving the caller to guess.

``timeline.getEvents`` renders each event's title/description/url
server-side and returns plain text -- not TracWiki or Markdown -- so this
tool passes them through as-is rather than converting them (ticket #93).
"""

import xmlrpc.client

import mcp.types as types

from ...core.async_utils import run_sync
from ...core.client import TracClient
from .errors import build_error_response
from .registry import ToolSpec

# Raised by Trac's RPC dispatcher when the plugin is not installed/enabled.
_MISSING_METHOD_HINT = (
    "This tool needs the 'tracrpc_comment' plugin on the Trac server. "
    "Install it from the trac-mcp-server repo's plugins/ directory and "
    "enable it with: trac-admin <env> config set components "
    "'tracrpc_comment.*' enabled"
)

DEFAULT_MAX_RESULTS = 100
# Mirrors tracrpc_comment.timeline.MAX_EVENTS_CAP -- the server refuses a
# larger request anyway, so clamp here rather than send it and be capped.
MAX_RESULTS_CAP = 1000


TIMELINE_TOOLS = [
    types.Tool(
        name="timeline_search",
        description=(
            "Search the Trac timeline -- tickets, wiki edits, "
            "changesets, milestones and more, ordered together and "
            "author-attributed. Each event's title/description/url are "
            "rendered server-side as plain text, not TracWiki or "
            "Markdown."
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
                "start": {
                    "type": "integer",
                    "description": "Start of the time range, as a unix timestamp (seconds since epoch), inclusive (required).",
                },
                "stop": {
                    "type": "integer",
                    "description": "End of the time range, as a unix timestamp (seconds since epoch), inclusive (required).",
                },
                "filters": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Event kinds to include (e.g. ticket, wiki, changeset, milestone -- available kinds depend on what's enabled on this instance). Omit for every kind; pass an empty list to select none.",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"Maximum events to return, newest first (default: {DEFAULT_MAX_RESULTS}, max: {MAX_RESULTS_CAP}).",
                    "default": DEFAULT_MAX_RESULTS,
                    "minimum": 1,
                    "maximum": MAX_RESULTS_CAP,
                },
            },
            "required": ["start", "stop"],
        },
    ),
]


def _translate_missing_method(err: xmlrpc.client.Fault):
    """Turn 'no such method' into an actionable message, or re-raise.

    Without this the caller sees a bare MethodNotFound and has no way to
    know the server simply lacks the plugin -- which is the single most
    likely reason this tool fails.
    """
    text = err.faultString.lower()
    if "not found" in text and "method" in text:
        return build_error_response(
            "method_not_available",
            err.faultString,
            _MISSING_METHOD_HINT,
        )
    return None


async def _handle_search(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle timeline_search."""
    start_ts = args.get("start")
    stop_ts = args.get("stop")
    missing = [
        name
        for name, value in (("start", start_ts), ("stop", stop_ts))
        if value is None
    ]
    if missing:
        return build_error_response(
            "validation_error",
            "%s is required" % ", ".join(missing),
            "Provide %s." % ", ".join(missing),
        )

    max_results = args.get("max_results", DEFAULT_MAX_RESULTS)
    max_results = min(max(1, max_results), MAX_RESULTS_CAP)

    try:
        # An explicitly empty filters list means "select nothing"
        # server-side; only a genuinely omitted filters key resolves to
        # "every available kind".
        if "filters" in args and args["filters"] is not None:
            filters = list(args["filters"])
        else:
            available = await run_sync(client.get_timeline_filters)
            filters = [name for name, _label in available]

        events = await run_sync(
            client.get_timeline_events,
            start_ts,
            stop_ts,
            filters,
            max_results,
        )
    except xmlrpc.client.Fault as e:
        translated = _translate_missing_method(e)
        if translated:
            return translated
        raise

    if not events:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text="No timeline events in the given range.",
                )
            ],
            structuredContent={"events": [], "total": 0, "showing": 0},
        )

    total = len(events)
    lines = [f"{total} timeline event(s), newest first:", ""]
    events_json = []
    for event in events:
        kind = event.get("kind", "")
        date = event.get("date")
        author = event.get("author", "")
        title = event.get("title", "")
        url = event.get("url", "")
        lines.append(f"- [{kind}] {date} {author}: {title} ({url})")
        events_json.append(
            {
                "kind": kind,
                "date": str(date),
                "author": author,
                "title": title,
                "description": event.get("description", ""),
                "url": url,
            }
        )

    return types.CallToolResult(
        content=[types.TextContent(type="text", text="\n".join(lines))],
        structuredContent={
            "events": events_json,
            "total": total,
            "showing": total,
        },
    )


TIMELINE_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=TIMELINE_TOOLS[0],
        permissions=frozenset({"TIMELINE_VIEW"}),
        handler=_handle_search,
    ),
]
