"""Name the instance a tool call actually landed on (ticket #94).

Every tool takes an optional ``instance`` argument (#18). Omitting it falls
back to the server's single legacy ``TRAC_URL`` triple, and that fallback is
silent: nothing in the result says which Trac project the call reached. For a
read that is confusing; for a write it is a correctness hazard, because a page
or ticket that exists on both the intended instance and the default one is
written to the wrong project with no error at all.

The nine recorded slips all landed on the default by accident, and every one
of them was caught only by luck -- a link the default instance happens not to
have, a ticket number it happens not to carry. This module removes the luck:
the resolved instance is stated on *every* result, success or failure, so a
wrong-instance call is visible from the response alone.

Two surfaces carry it, because which one a client shows its model is the
client's choice, not ours:

  * ``structuredContent`` gets ``instance`` and ``instance_source`` keys, when
    the handler produced a dict. Claude Code surfaces this in preference to
    the text block, so for the ~34 handlers that set it, this is the field a
    model actually reads.
  * the content list gets one extra ``TextContent`` block. Appended as its own
    block rather than folded into the existing text on purpose: two handlers
    return ``json.dumps(...)`` as their whole text body, and a consumer that
    parses ``content[0].text`` must keep working byte-for-byte.

Neither surface is ever overwritten -- an existing key of the same name wins,
so a handler that means something else by ``instance`` is left alone.
"""

from urllib.parse import urlparse

import mcp.types as types

INSTANCE_KEY = "instance"
SOURCE_KEY = "instance_source"

#: The caller named the instance, by path, URL or configured name.
SOURCE_EXPLICIT = "explicit"
#: No ``instance`` argument was passed and the server's default answered.
#: This is the value worth grepping for: on any session whose project is not
#: the default instance's own, it means the call went somewhere else.
SOURCE_DEFAULT = "server_default"


def instance_label(url: str) -> str:
    """Path form of a resolved instance URL: ``/bcs`` from ``http://h:8000/bcs``.

    The path is what a caller passes back as ``instance``, so echoing the path
    makes the fix for a wrong-instance call a copy-paste. Falls back to the
    whole string when the URL has no usable path, which keeps a malformed
    configuration visible rather than reporting an empty instance.
    """
    try:
        path = urlparse(url).path.rstrip("/")
    except ValueError:
        return url
    return path or url


def annotate(
    result: types.CallToolResult, url: str, explicit: bool
) -> types.CallToolResult:
    """Record the resolved instance on ``result``, in place.

    Args:
        result: The handler's result, error or not.
        url: Resolved instance URL the call was executed against.
        explicit: Whether the caller passed an ``instance`` argument.

    Returns:
        The same result object, annotated.
    """
    if not isinstance(url, str) or not url:
        return result

    label = instance_label(url)
    source = SOURCE_EXPLICIT if explicit else SOURCE_DEFAULT

    if isinstance(result.structuredContent, dict):
        result.structuredContent.setdefault(INSTANCE_KEY, label)
        result.structuredContent.setdefault(SOURCE_KEY, source)

    if result.content is None:
        result.content = []
    result.content.append(
        types.TextContent(
            type="text", text=f"[{INSTANCE_KEY}: {label} ({source})]"
        )
    )
    return result
