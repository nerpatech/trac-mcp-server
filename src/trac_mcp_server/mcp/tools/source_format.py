"""The inline tools have no source-format parameter (tickets #62, #69).

Ticket #62 gave the six write tools a ``format`` parameter, defaulting to
``markdown``. That default aimed the *destructive* failure at
hand-authored TracWiki -- a ``{{{#!python}}}`` processor block is not
recognised as a code construct by the Markdown converter, so paragraph
handling eats its leading whitespace and stores syntactically invalid
code -- and the *safe* one at Markdown, which merely renders as literal
``#`` and ``**``. The destructive side is invisible at every checkpoint
an author habitually uses: the call succeeds, ``warnings`` comes back
empty, and the render looks plausible.

Ticket #69 removed the alternative rather than moving the default.
The inline write and read tools store and return TracWiki bytes, so
there is no conversion step left to select and nothing to omit. That is
the same trade #62 made in refusing an ``auto`` value -- declaring beats
guessing -- carried one step further: not having the question at all
beats declaring.

What survives, deliberately:

- ``convert_preview`` and the standalone ``trac-convert`` binary (#16),
  which exist to convert and are where a caller holding Markdown goes.
- ``wiki_file_push`` / ``wiki_file_pull``, which have a *filename* to go
  on. The inline tools have only content, and ticket #47 is this
  project's own evidence that guessing from content is unreliable.

This module is now the rejection gate rather than the parameter, so the
eleven call sites still share one declaration and cannot drift apart.
A removed parameter is a loud, mechanical break at the call site, which
is the whole reason removal was preferred to a re-defaulted one: under a
moved default a stale caller still gets *something*, silently.
"""

import mcp.types as types

from .errors import build_error_response

#: Values that agree with the new behaviour. Passing one is a no-op, so
#: a caller already following the store rule needs no change at all.
_ACCEPTED_FORMAT = "tracwiki"

_HINT = (
    "Drop the argument -- content is stored and returned as TracWiki, "
    "byte-for-byte, and there is no conversion step to select. If you "
    "are holding Markdown, convert it yourself first with the "
    "trac-convert binary and pass the TracWiki it produces."
)


def reject_removed_conversion_args(
    args: dict,
) -> types.CallToolResult | None:
    """Reject a request for the Markdown path that no longer exists.

    Returns ``None`` when the call is fine -- the argument is absent, or
    present and agreeing -- and an error result when a caller is asking
    for conversion. Callers return that error before reading or writing
    anything.

    Checked here rather than left to the declared JSON Schema, which
    says nothing at all about a property it no longer declares: a stale
    client's ``format="markdown"`` would otherwise be dropped on the
    floor and the caller would keep believing it had asked for Markdown.
    """
    fmt = args.get("format")
    if fmt is not None and fmt != _ACCEPTED_FORMAT:
        return build_error_response(
            "validation_error",
            f"The 'format' parameter was removed: got '{fmt}', and the "
            "Markdown write path no longer exists.",
            _HINT,
        )

    raw = args.get("raw")
    if raw is not None and not raw:
        return build_error_response(
            "validation_error",
            "The 'raw' parameter was removed, and raw=false asks for "
            "Markdown, which these tools no longer return.",
            _HINT,
        )

    return None
