"""Error response builders and shared utilities for MCP tool handlers.

This module provides structured error responses with corrective actions
to help AI agents recover from errors without human intervention, plus
shared formatting utilities used across tool modules.
"""

import time
import xmlrpc.client
from datetime import datetime, timezone
from typing import Any

import mcp.types as types


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


def translate_xmlrpc_error(
    error: xmlrpc.client.Fault,
    domain: str,
    entity_name: str | None = None,
) -> types.CallToolResult:
    """Translate XML-RPC fault to structured error response.

    Provides domain-specific corrective action messages for ticket,
    wiki, and milestone operations. Fault codes classify first, before
    any text is read, where TracXMLRPC and the wiki plugin set them (409
    version conflict from wiki.putPageIfVersion, 404 not found, 403
    permission), so a fault's text, which may carry a page or milestone
    name, never overrides its code. A fault with none of those codes is
    matched on its text for not found, permission and milestone
    already-exists, never for version conflicts.

    Args:
        error: XML-RPC fault exception
        domain: Operation domain ("ticket", "wiki", "milestone")
        entity_name: Optional entity name for contextual suggestions
            (e.g., page name for wiki, milestone name)

    Returns:
        CallToolResult with isError=True and corrective action
    """
    msgs = _DOMAIN_MESSAGES.get(domain, _DOMAIN_MESSAGES["ticket"])
    fault_str = error.faultString.lower()

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

    # Codes first (Trac #186): 409 is wiki.putPageIfVersion's conflict
    # code, matched by code and never by text, since a name containing
    # "version" is no conflict.
    match error.faultCode:
        case 409:
            return version_conflict()
        case 404:
            return not_found()
        case 403:
            return permission_denied()

    match fault_str:
        case s if "not found" in s or "does not exist" in s:
            return not_found()

        case s if "permission" in s or "denied" in s:
            return permission_denied()

        case s if domain == "milestone" and (
            "exists" in s or "already" in s
        ):
            return build_error_response(
                "already_exists",
                error.faultString,
                msgs["already_exists"],
            )

        case _:
            return build_error_response(
                "server_error", error.faultString, msgs["server"]
            )
