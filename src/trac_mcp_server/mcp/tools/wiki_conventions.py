"""``wiki_conventions`` -- an instance's declared wiki style (ticket #123).

One read-only call that answers "how do I write a page here?": the style
the instance's ``WikiStart`` declares, that style page's body, and every
``PageTemplates/*`` page. Nothing is hardcoded -- neither an instance name
nor a style name. The declaration is a line of ``WikiStart``::

    '''Wiki style:''' [auto_pm:wiki:Rules/documentation/WikiStyleAgent agent]

whose link names the style page (on this instance, or on another one
through its InterTrac prefix) and whose label is the style's name.
"""

import asyncio
import json
import re
import xmlrpc.client
from dataclasses import dataclass
from urllib.parse import urlparse

import mcp.types as types

from ...converters.common import blank_code_fences
from ...core.async_utils import run_sync_limited
from ...core.client import TracClient
from ...instances import (
    InstanceNotAllowedError,
    UnknownInstanceError,
    caller_identity,
)
from .instances import get_instance_registry, own_intertrac_prefix
from .registry import ToolSpec

_MARKER = "'''Wiki style:'''"

#: A valid declaration: the whole line, from column 0.
_DECLARATION_RE = re.compile(
    r"^'''Wiki style:''' \[(?:(?P<prefix>[A-Za-z0-9_-]+):)?wiki:"
    r"(?P<page>[^\s\]]+) (?P<label>[a-z]+)\]\s*$"
)

_TEMPLATE_PREFIX = "PageTemplates/"


@dataclass(frozen=True, slots=True)
class StyleDeclaration:
    """What ``WikiStart`` declares.

    ``style`` is the declared label, or ``"malformed"`` / ``"undeclared"``.
    ``prefix``/``page``/``label`` are set only when a style was parsed
    (``prefix`` is ``None`` for a same-instance link). ``lines`` are the
    marker lines found, in document order.
    """

    style: str
    prefix: str | None
    page: str | None
    label: str | None
    lines: tuple[str, ...]


def parse_style_declaration(text: str) -> StyleDeclaration:
    """Parse ``WikiStart``'s text into a :class:`StyleDeclaration`.

    A ``{{{ }}}`` block is blanked first, so a line quoted in a code block
    (valid or not) never counts. A marker line is any line whose
    ``lstrip()`` starts with ``'''Wiki style:'''``; one that fails the
    declaration regex (leading whitespace, missing label, trailing text)
    makes the whole declaration ``malformed``, as do valid lines that
    disagree. Identical duplicates are accepted.
    """
    # blank_code_fences keeps every character position and every "\n", so
    # the blanked and the original text split into the same lines. Split
    # on "\n" only: str.splitlines also breaks on \x0c,   and the
    # like, which blanking would turn into spaces and shift the pairing.
    scan = blank_code_fences(text).split("\n")
    original = text.split("\n")
    found = [
        original[i].rstrip("\r")
        for i, line in enumerate(scan)
        if line.lstrip().startswith(_MARKER)
    ]
    if not found:
        return StyleDeclaration("undeclared", None, None, None, ())

    matches = [_DECLARATION_RE.match(line) for line in found]
    lines = tuple(found)
    if any(m is None for m in matches):
        return StyleDeclaration("malformed", None, None, None, lines)
    parsed = {
        (m["prefix"], m["page"], m["label"]) for m in matches if m
    }
    if len(parsed) != 1:
        return StyleDeclaration("malformed", None, None, None, lines)
    ((prefix, page, label),) = parsed
    return StyleDeclaration(label, prefix, page, label, lines)


def _fault_reason(fault: xmlrpc.client.Fault) -> str:
    """Classify a fault the way ``translate_xmlrpc_error`` does (its
    ``not found``/``does not exist`` and ``permission``/``denied``
    substring tests on the lower-cased fault string), plus fault code 403:
    Trac's PermissionError text ("WIKI_VIEW privileges are required ...")
    need not contain either word. Anything else is ``error``."""
    text = str(fault.faultString).lower()
    if "not found" in text or "does not exist" in text:
        return "not_found"
    if (
        fault.faultCode == 403
        or "permission" in text
        or "denied" in text
    ):
        return "permission_denied"
    return "error"


def _own_instance_path(client: TracClient) -> str:
    return urlparse(client.config.trac_url).path.rstrip("/")


async def _read_page(client: TracClient, name: str) -> tuple[str, int]:
    """A page's body as stored, and its version."""
    body, info = await asyncio.gather(
        run_sync_limited(client.get_wiki_page, name),
        run_sync_limited(client.get_wiki_page_info, name),
    )
    return body, int(info["version"])


async def _read_style_page(
    client: TracClient, prefix: str | None, page: str
) -> dict:
    """The style page's record; a failure is reported, never raised."""
    if prefix is None or prefix == own_intertrac_prefix(client):
        target = client
        path = _own_instance_path(client)
    else:
        path = "/" + prefix
        registry = get_instance_registry()

        def unavailable(reason: str, message: str) -> dict:
            return {
                "instance": path,
                "name": page,
                "available": False,
                "reason": reason,
                "message": message,
            }

        if registry is None:
            return unavailable(
                "unknown_instance", "Instance registry not initialized."
            )
        try:
            target = registry.get_client(
                path, identity=caller_identity()
            )
        except InstanceNotAllowedError as exc:
            return unavailable("instance_not_allowed", str(exc))
        except UnknownInstanceError as exc:
            return unavailable("unknown_instance", str(exc))
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return unavailable("error", str(exc))

    try:
        body, version = await _read_page(target, page)
    except xmlrpc.client.Fault as exc:
        return {
            "instance": path,
            "name": page,
            "available": False,
            "reason": _fault_reason(exc),
            "message": str(exc.faultString),
        }
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return {
            "instance": path,
            "name": page,
            "available": False,
            "reason": "error",
            "message": str(exc),
        }
    return {
        "instance": path,
        "name": page,
        "version": version,
        "body": body,
    }


async def _read_wikistart(client: TracClient) -> str:
    """WikiStart's text; ``""`` when the page does not exist."""
    try:
        return await run_sync_limited(client.get_wiki_page, "WikiStart")
    except xmlrpc.client.Fault as exc:
        if _fault_reason(exc) == "not_found":
            return ""
        raise


async def _read_templates(client: TracClient) -> list[dict]:
    names = sorted(
        n
        for n in await run_sync_limited(client.list_wiki_pages)
        if n.startswith(_TEMPLATE_PREFIX)
    )
    pages = await asyncio.gather(
        *(_read_page(client, n) for n in names)
    )
    return [
        {"name": name, "version": version, "body": body}
        for name, (body, version) in zip(names, pages, strict=True)
    ]


async def _handle_conventions(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle wiki_conventions."""
    declaration = parse_style_declaration(await _read_wikistart(client))

    style_page = None
    if declaration.page is not None:
        style_page = await _read_style_page(
            client, declaration.prefix, declaration.page
        )
    templates = await _read_templates(client)

    result = {
        "style": declaration.style,
        "declaration_lines": list(declaration.lines),
        "style_page": style_page,
        "templates": templates,
    }
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, indent=2),
            )
        ],
        structuredContent=result,
    )


WIKI_CONVENTIONS_TOOLS = [
    types.Tool(
        name="wiki_conventions",
        description=(
            "Get an instance's wiki conventions in one read-only call: the "
            "style its WikiStart declares (a line "
            "`'''Wiki style:''' [<prefix>:wiki:<StylePage> <name>]`, or "
            "`undeclared`/`malformed`), that style page's body as stored "
            "(read from the instance the link names, within the caller's "
            "allowed instances; reported unavailable with a reason rather "
            "than failing), and every PageTemplates/* page with its body. "
            "Bodies are TracWiki, byte-for-byte."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
]

WIKI_CONVENTIONS_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=WIKI_CONVENTIONS_TOOLS[0],
        permissions=frozenset({"WIKI_VIEW"}),
        handler=_handle_conventions,
    ),
]
