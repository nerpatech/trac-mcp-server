"""Batch ticket tool handlers for MCP server.

This module implements batch ticket operations: batch create, batch update,
and batch delete. All operations use best-effort processing -- every item
is attempted, and per-item success/failure is reported in the response.
Parallelism is bounded by the existing gather_limited/run_sync_limited
infrastructure.
"""

import logging
from typing import Any

import mcp.types as types

from ...converters import markdown_to_tracwiki
from ...core.async_utils import gather_limited, run_sync_limited
from ...core.client import TracClient
from .constants import DEFAULT_TICKET_TYPE
from .errors import build_error_response
from .registry import ToolSpec

logger = logging.getLogger(__name__)


# Tool definitions for list_tools()
TICKET_BATCH_TOOLS = [
    types.Tool(
        name="ticket_batch_create",
        description="Create multiple tickets in a single batch operation. Best-effort: all items attempted, per-item results reported. Bounded by TRAC_MAX_PARALLEL_REQUESTS semaphore.",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "tickets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "description": {"type": "string"},
                            "ticket_type": {"type": "string"},
                            "priority": {"type": "string"},
                            "component": {"type": "string"},
                            "milestone": {"type": "string"},
                            "owner": {"type": "string"},
                            "keywords": {"type": "string"},
                            "cc": {"type": "string"},
                        },
                        "required": ["summary", "description"],
                    },
                    "description": "List of ticket objects to create",
                }
            },
            "required": ["tickets"],
        },
    ),
    types.Tool(
        name="ticket_batch_delete",
        description="Delete multiple tickets in a single batch operation. Best-effort: all items attempted, per-item results reported. Requires TICKET_ADMIN permission.",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_ids": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "description": "List of ticket IDs to delete",
                }
            },
            "required": ["ticket_ids"],
        },
    ),
    types.Tool(
        name="ticket_batch_update",
        description="Update multiple tickets in a single batch operation. Best-effort: all items attempted, per-item results reported.",
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticket_id": {
                                "type": "integer",
                                "minimum": 1,
                            },
                            "comment": {"type": "string"},
                            "status": {"type": "string"},
                            "resolution": {"type": "string"},
                            "priority": {"type": "string"},
                            "component": {"type": "string"},
                            "milestone": {"type": "string"},
                            "owner": {"type": "string"},
                            "keywords": {"type": "string"},
                            "cc": {"type": "string"},
                        },
                        "required": ["ticket_id"],
                    },
                    "description": "List of update objects with ticket_id and fields to change",
                }
            },
            "required": ["updates"],
        },
    ),
]


async def _handle_batch_create(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_batch_create."""
    tickets = args.get("tickets")
    if not tickets:
        return build_error_response(
            "validation_error",
            "tickets list is required and cannot be empty",
            "Provide a non-empty tickets array.",
        )

    max_size = client.config.max_batch_size
    if len(tickets) > max_size:
        return build_error_response(
            "validation_error",
            f"Batch size {len(tickets)} exceeds maximum {max_size}. Split into smaller batches.",
            "Reduce the number of tickets per request.",
        )

    async def _create_one(
        index: int, ticket_data: dict
    ) -> dict[str, Any]:
        summary = ticket_data.get("summary")
        description = ticket_data.get("description")

        if not summary:
            return {
                "index": index,
                "summary": "",
                "error": "summary is required",
            }
        if not description:
            return {
                "index": index,
                "summary": summary,
                "error": "description is required",
            }

        try:
            description_tracwiki = markdown_to_tracwiki(description)
            ticket_type = ticket_data.get(
                "ticket_type", DEFAULT_TICKET_TYPE
            )
            attributes: dict[str, Any] = {}

            for field in (
                "priority",
                "component",
                "milestone",
                "owner",
                "cc",
                "keywords",
            ):
                if field in ticket_data:
                    attributes[field] = ticket_data[field]

            ticket_id = await run_sync_limited(
                client.create_ticket,
                summary,
                description_tracwiki,
                ticket_type,
                attributes,
            )
            return {"id": ticket_id, "summary": summary}
        except Exception as e:
            return {
                "index": index,
                "summary": ticket_data.get("summary", ""),
                "error": str(e),
            }

    results = await gather_limited(
        [_create_one(i, t) for i, t in enumerate(tickets)]
    )

    created = [r for r in results if "id" in r]
    failed = [r for r in results if "error" in r]
    total = len(tickets)

    # Build text response
    lines = [
        f"Batch create: {len(created)}/{total} succeeded, {len(failed)} failed."
    ]
    if created:
        lines.append("")
        lines.append("Created:")
        for item in created:
            lines.append(f"  - #{item['id']}: {item['summary']}")
    if failed:
        lines.append("")
        lines.append("Failed:")
        for item in failed:
            lines.append(
                f"  - [index {item.get('index', '?')}] {item.get('summary', '')}: {item['error']}"
            )

    return types.CallToolResult(
        content=[types.TextContent(type="text", text="\n".join(lines))],
        structuredContent={
            "created": created,
            "failed": failed,
            "total": total,
            "succeeded": len(created),
            "failed_count": len(failed),
        },
    )


async def _handle_batch_delete(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_batch_delete."""
    ticket_ids = args.get("ticket_ids")
    if not ticket_ids:
        return build_error_response(
            "validation_error",
            "ticket_ids list is required and cannot be empty",
            "Provide a non-empty ticket_ids array.",
        )

    max_size = client.config.max_batch_size
    if len(ticket_ids) > max_size:
        return build_error_response(
            "validation_error",
            f"Batch size {len(ticket_ids)} exceeds maximum {max_size}. Split into smaller batches.",
            "Reduce the number of ticket IDs per request.",
        )

    async def _delete_one(ticket_id: int) -> dict[str, Any]:
        try:
            await run_sync_limited(client.delete_ticket, ticket_id)
            return {"id": ticket_id}
        except Exception as e:
            return {"id": ticket_id, "error": str(e)}

    results = await gather_limited(
        [_delete_one(tid) for tid in ticket_ids]
    )

    deleted = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]
    total = len(ticket_ids)

    # Build text response
    lines = [
        f"Batch delete: {len(deleted)}/{total} succeeded, {len(failed)} failed."
    ]
    if deleted:
        lines.append("")
        lines.append("Deleted:")
        for item in deleted:
            lines.append(f"  - #{item['id']}")
    if failed:
        lines.append("")
        lines.append("Failed:")
        for item in failed:
            lines.append(f"  - #{item['id']}: {item['error']}")

    return types.CallToolResult(
        content=[types.TextContent(type="text", text="\n".join(lines))],
        structuredContent={
            "deleted": [r["id"] for r in deleted],
            "failed": failed,
            "total": total,
            "succeeded": len(deleted),
            "failed_count": len(failed),
        },
    )


async def _handle_batch_update(
    client: TracClient, args: dict
) -> types.CallToolResult:
    """Handle ticket_batch_update."""
    updates = args.get("updates")
    if not updates:
        return build_error_response(
            "validation_error",
            "updates list is required and cannot be empty",
            "Provide a non-empty updates array.",
        )

    max_size = client.config.max_batch_size
    if len(updates) > max_size:
        return build_error_response(
            "validation_error",
            f"Batch size {len(updates)} exceeds maximum {max_size}. Split into smaller batches.",
            "Reduce the number of updates per request.",
        )

    async def _update_one(update_data: dict) -> dict[str, Any]:
        ticket_id = update_data.get("ticket_id")
        if not ticket_id:
            return {
                "id": update_data.get("ticket_id", 0),
                "error": "ticket_id is required",
            }

        try:
            comment = update_data.get("comment", "")
            if comment:
                comment = markdown_to_tracwiki(comment)

            attributes: dict[str, Any] = {}
            for field in (
                "status",
                "resolution",
                "priority",
                "component",
                "milestone",
                "owner",
                "cc",
                "keywords",
            ):
                if field in update_data:
                    attributes[field] = update_data[field]

            await run_sync_limited(
                client.update_ticket, ticket_id, comment, attributes
            )
            return {"id": ticket_id}
        except Exception as e:
            return {
                "id": update_data.get("ticket_id", 0),
                "error": str(e),
            }

    results = await gather_limited([_update_one(u) for u in updates])

    updated = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]
    total = len(updates)

    # Build text response
    lines = [
        f"Batch update: {len(updated)}/{total} succeeded, {len(failed)} failed."
    ]
    if updated:
        lines.append("")
        lines.append("Updated:")
        for item in updated:
            lines.append(f"  - #{item['id']}")
    if failed:
        lines.append("")
        lines.append("Failed:")
        for item in failed:
            lines.append(f"  - #{item['id']}: {item['error']}")

    return types.CallToolResult(
        content=[types.TextContent(type="text", text="\n".join(lines))],
        structuredContent={
            "updated": [r["id"] for r in updated],
            "failed": failed,
            "total": total,
            "succeeded": len(updated),
            "failed_count": len(failed),
        },
    )


# ToolSpec list for registry-based dispatch
TICKET_BATCH_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=TICKET_BATCH_TOOLS[0],
        permissions=frozenset({"TICKET_CREATE", "TICKET_BATCH_MODIFY"}),
        handler=_handle_batch_create,
    ),
    ToolSpec(
        tool=TICKET_BATCH_TOOLS[1],
        permissions=frozenset({"TICKET_ADMIN", "TICKET_BATCH_MODIFY"}),
        handler=_handle_batch_delete,
    ),
    ToolSpec(
        tool=TICKET_BATCH_TOOLS[2],
        permissions=frozenset({"TICKET_MODIFY", "TICKET_BATCH_MODIFY"}),
        handler=_handle_batch_update,
    ),
]
