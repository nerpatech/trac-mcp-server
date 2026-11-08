"""Error response builders and shared utilities for MCP tool handlers.

This module provides structured error responses with corrective actions
to help AI agents recover from errors without human intervention, plus
shared formatting utilities used across tool modules.
"""

import re
import time
import xmlrpc.client
from datetime import datetime, timezone
from typing import Any

import mcp.types as types

from ...core.client import unwrap_tracrpc_fault


def build_error_response(
    error_type: str, message: str, corrective_action: str
) -> types.CallToolResult:
    """Build a structured error response with corrective action.

    Args:
        error_type: Error category (not_found, permission_denied, version_conflict, validation_error, server_error)
        message: Human-readable error description
        corrective_action: Specific action the agent can take to resolve the error

    Returns:
        CallToolResult with isError=True

    Examples:
        >>> build_error_response("not_found", "Ticket #123 not found", "Use ticket_search to verify ticket exists.")
        CallToolResult(content=[TextContent(...)], isError=True)
    """
    error_text = f"Error ({error_type}): {message}\n\nAction: {corrective_action}"

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=error_text)],
        isError=True,
    )


# ---------------------------------------------------------------------------
# Shared formatting utilities
# ---------------------------------------------------------------------------


def format_timestamp(timestamp: Any) -> str:
    """Format timestamp for display.

    Handles datetime objects, Unix timestamps (int/float), and
    xmlrpc.client.DateTime objects. Uses timezone-aware UTC conversion.

    Args:
        timestamp: datetime, int/float Unix timestamp, or other value

    Returns:
        Formatted date string (YYYY-MM-DD HH:MM)
    """
    match timestamp:
        case datetime() as dt:
            return dt.strftime("%Y-%m-%d %H:%M")
        case xmlrpc.client.DateTime() as dt_val:
            # xmlrpc.client.DateTime wraps an iso-like string with no
            # attached tz; convert via timetuple -> mktime (local) then
            # fall back to string form if conversion fails.
            try:
                dt = datetime.fromtimestamp(
                    time.mktime(dt_val.timetuple())
                )
                return dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, OverflowError):
                return str(timestamp)
        case int() | float() as ts:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M")
        case str() as s if s:
            # Trac's XmlRpcPlugin returns iso8601 strings like
            # "20260406T00:03:15" (compact form, no separators, no tz).
            # Parse what we recognize; fall back to passthrough for
            # anything else so we never raise on display formatting.
            for fmt in (
                "%Y%m%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
            ):
                try:
                    dt = datetime.strptime(s, fmt)
                    return dt.strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    continue
            return s
        case _:
            return str(timestamp)


# ---------------------------------------------------------------------------
# Domain-specific corrective action messages
# ---------------------------------------------------------------------------

_DOMAIN_MESSAGES: dict[str, dict[str, str]] = {
    "ticket": {
        "not_found": "Use ticket_search to verify ticket exists.",
        "permission": "Try adding a comment instead, or contact ticket owner.",
        "version": "Fetch current version with ticket_get(ticket_id=N), then retry update.",
        "server": "Contact Trac administrator or retry later.",
    },
    "wiki": {
        "not_found": "Use wiki_search to find available pages.",
        "not_found_named": "Use wiki_search to find pages similar to '{entity_name}'.",
        "permission": "Contact Trac administrator for write access to wiki pages.",
        "version": "Fetch current version with wiki_get(page_name='{entity_name}'), then retry update.",
        "server": "Contact Trac administrator or retry later.",
    },
    "milestone": {
        "not_found": "Use milestone_list to verify milestone exists.",
        "permission": "Contact Trac administrator for TICKET_ADMIN permission.",
        "already_exists": "Use milestone_update to modify existing milestone, or choose different name.",
        "server": "Contact Trac administrator or retry later.",
    },
}


# A quoted span in a fault's text is a NAME the fault mentions (Trac
# quotes page, milestone and ticket names: 'X' or "X"), not evidence of
# what went wrong. Single quotes count only where the quote is not inside
# a word, so "don't" is left alone. A single-quoted span may itself hold
# apostrophes (``'Bob's/Notes'``): it ends at the first quote not
# followed by a word character. Typographic quote pairs count too.
_DOUBLE_QUOTED = re.compile(r'"[^"]*"')
_SINGLE_QUOTED = re.compile(r"(?<!\w)'.*?'(?!\w)")
_TYPOGRAPHIC_SINGLE = re.compile("\u2018[^\u2019]*\u2019")
_TYPOGRAPHIC_DOUBLE = re.compile("\u201c[^\u201d]*\u201d")


def _fault_text(fault: xmlrpc.client.Fault) -> str:
    """A fault's text for keyword tests: TracXMLRPC's "while executing"
    wrapper removed, one outer pair of quotes around the whole message
    removed, every quoted name replaced by a placeholder, then
    lower-cased."""
    text = unwrap_tracrpc_fault(fault).faultString
    stripped = text.strip()
    if (
        len(stripped) >= 2
        and stripped[0] in "'\""
        and stripped[-1] == stripped[0]
    ):
        text = stripped[1:-1]
    text = _DOUBLE_QUOTED.sub("<name>", text)
    text = _SINGLE_QUOTED.sub("<name>", text)
    text = _TYPOGRAPHIC_SINGLE.sub("<name>", text)
    text = _TYPOGRAPHIC_DOUBLE.sub("<name>", text)
    return text.lower()


def classify_fault(
    fault: xmlrpc.client.Fault, domain: str | None = None
) -> str | None:
    """Classify an XML-RPC fault: ``"version_conflict"``, ``"not_found"``,
    ``"permission_denied"``, ``"already_exists"`` or ``None``.

    Fault codes classify first, in this order: 409 (the conflict code of
    the ``wiki.putPageIfVersion`` plugin), 404 (TracXMLRPC's
    ``ResourceNotFound``) and 403 (its ``PermissionError``). Any other
    code is classified by its text, with TracXMLRPC's wrapper removed and
    every quoted name replaced first, because a quoted name is not
    evidence (Trac #188): ``'Invalid Wiki page name 'PermissionNotes'``
    is no permission fault. Then ``not found``/``does not exist`` is
    ``not_found``, ``permission``/``denied`` is ``permission_denied``,
    and, in the ``milestone`` domain only, ``exists``/``already`` is
    ``already_exists``. The text tests are still needed for the code-1
    faults TracXMLRPC sends for the plugin's ``TracError`` and Trac's
    ``ResourceExistsError``.

    An UNQUOTED name in a fault with none of the codes above can still
    match a token.
    """
    match fault.faultCode:
        case 409:
            return "version_conflict"
        case 404:
            return "not_found"
        case 403:
            return "permission_denied"

    text = _fault_text(fault)
    if "not found" in text or "does not exist" in text:
        return "not_found"
    if "permission" in text or "denied" in text:
        return "permission_denied"
    if domain == "milestone" and (
        "exists" in text or "already" in text
    ):
        return "already_exists"
    return None


def translate_xmlrpc_error(
    error: xmlrpc.client.Fault,
    domain: str,
    entity_name: str | None = None,
) -> types.CallToolResult:
    """Translate XML-RPC fault to structured error response.

    Provides domain-specific corrective action messages for ticket,
    wiki, and milestone operations. The fault is classified by
    :func:`classify_fault`: codes first (409 version conflict from
    wiki.putPageIfVersion, 404 not found, 403 permission), so a fault's
    text, which may carry a page or milestone name, never overrides its
    code; then its text with quoted names removed, because a quoted name
    is not evidence (Trac #188). Text is matched for not found,
    permission and milestone already-exists, never for version
    conflicts.

    Args:
        error: XML-RPC fault exception
        domain: Operation domain ("ticket", "wiki", "milestone")
        entity_name: Optional entity name for contextual suggestions
            (e.g., page name for wiki, milestone name)

    Returns:
        CallToolResult with isError=True and corrective action
    """
    msgs = _DOMAIN_MESSAGES.get(domain, _DOMAIN_MESSAGES["ticket"])

    def version_conflict() -> types.CallToolResult:
        action = msgs.get("version", msgs["server"])
        if entity_name:
            action = action.format(entity_name=entity_name)
        return build_error_response(
            "version_conflict", error.faultString, action
        )

    def not_found() -> types.CallToolResult:
        if entity_name and "not_found_named" in msgs:
            action = msgs["not_found_named"].format(
                entity_name=entity_name
            )
        elif entity_name and domain == "wiki":
            action = msgs.get(
                "not_found_named", msgs["not_found"]
            ).format(entity_name=entity_name)
        else:
            action = msgs["not_found"]
        return build_error_response(
            "not_found", error.faultString, action
        )

    def permission_denied() -> types.CallToolResult:
        perm_msg = error.faultString
        if domain == "milestone":
            perm_msg = f"{error.faultString} (requires TICKET_ADMIN for create/update/delete)"
        return build_error_response(
            "permission_denied", perm_msg, msgs["permission"]
        )

    match classify_fault(error, domain):
        case "version_conflict":
            return version_conflict()
        case "not_found":
            return not_found()
        case "permission_denied":
            return permission_denied()
        case "already_exists":
            return build_error_response(
                "already_exists",
                error.faultString,
                msgs["already_exists"],
            )
        case _:
            return build_error_response(
                "server_error", error.faultString, msgs["server"]
            )
