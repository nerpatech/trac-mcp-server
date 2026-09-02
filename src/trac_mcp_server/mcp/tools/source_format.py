"""Source-format declaration shared by every write tool (ticket #62).

Before this, no write tool could be *told* its input format and every one
of them converted unconditionally. An author writing TracWiki -- which is
what a surgical repair to an existing page requires -- had the content
silently mangled: a ``{{{#!python}}}`` processor block is not recognised
as a code construct by the Markdown converter, so paragraph handling eats
its leading whitespace and stores syntactically invalid code, with no
warning and a plausible-looking render.

Two rules govern the parameter, and both are deliberate:

- **No ``auto``.** ``wiki_file_push`` offers three values because it has a
  filename to go on. Here there is only content, and ticket #47 is this
  project's own evidence that guessing from content is unreliable -- a
  marker-poor Markdown document scored as TracWiki and was stored
  unconverted. Declaring is safe; sniffing is not.
- **The default stays ``markdown``**, so this is purely additive and every
  existing caller keeps working unchanged. Flipping the default to
  verbatim belongs to ticket #63, once the accommodation layer is gone
  and the caller set is known.

Handlers branch on the resolved value and, on the ``tracwiki`` arm, skip
the converter entirely rather than configuring it to pass through. That
branch is written inline at each call site, following ``wiki_file_push``
(``wiki_file.py``), rather than being hoisted in here: the conversion
call has to stay visible in its own module for the existing tests that
patch it by module path to keep biting.
"""

from typing import Any

import mcp.types as types

from .errors import build_error_response

#: Accepted values, in schema order. Deliberately two, not three.
SOURCE_FORMATS = ("markdown", "tracwiki")

DEFAULT_SOURCE_FORMAT = "markdown"

#: JSON-schema fragment spliced into every write tool's inputSchema, so
#: the six declarations cannot drift apart.
FORMAT_PROPERTY: dict[str, Any] = {
    "type": "string",
    "enum": list(SOURCE_FORMATS),
    "default": DEFAULT_SOURCE_FORMAT,
    "description": (
        "Format of the content you are supplying (default: markdown). "
        "'markdown' converts to TracWiki before storing. 'tracwiki' "
        "stores your text byte-for-byte, skipping the converter "
        "entirely -- use it when you are hand-authoring TracWiki, "
        "since running that through the Markdown converter silently "
        "strips indentation inside {{{ }}} blocks. There is no 'auto': "
        "the format is declared, never guessed from content."
    ),
}


def resolve_source_format(
    args: dict,
) -> tuple[str, types.CallToolResult | None]:
    """Read and validate the ``format`` argument.

    Returns ``(format, None)`` when valid, or ``(default, error_result)``
    when not -- callers return the error before writing anything.

    Validated here rather than left to the declared JSON-Schema enum,
    which is advisory on the way in; ``convert_preview`` established the
    same handler-side check.
    """
    fmt = args.get("format", DEFAULT_SOURCE_FORMAT)
    if fmt not in SOURCE_FORMATS:
        return DEFAULT_SOURCE_FORMAT, build_error_response(
            "validation_error",
            f"format must be 'markdown' or 'tracwiki', got '{fmt}'",
            "Provide format='markdown' (the default, converts your "
            "Markdown to TracWiki) or format='tracwiki' (stores your "
            "text verbatim). There is no 'auto' value.",
        )
    return fmt, None
