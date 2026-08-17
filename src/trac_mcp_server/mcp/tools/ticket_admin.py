"""Admin tool handlers for Trac component and enum management.

This module implements admin-side write operations for Trac components
(typed attributes: name, description, owner) and enum fields (priority,
resolution, severity, type, version). All operations require
TICKET_ADMIN permission.

Enum tools are intentionally generic: a single enum_type field
whitelists the four supported enum services, avoiding near-identical
per-enum tools.
"""

import json

import mcp.types as types

from ...core.async_utils import run_sync
from ...core.client import TracClient
from .errors import build_error_response
from .registry import ToolSpec

_ENUM_TYPES = ["priority", "resolution", "severity", "type", "version"]


TICKET_ADMIN_TOOLS: list[types.Tool] = [
    types.Tool(
        name="ticket_component_create",
        description=(
            "Create a new ticket component. Requires TICKET_ADMIN "
            "permission. Component must not already exist."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Component name (required, must be unique).",
                },
                "description": {
                    "type": "string",
                    "description": "Optional component description.",
                    "default": "",
                },
                "owner": {
                    "type": "string",
                    "description": "Optional default owner username.",
                    "default": "",
                },
            },
            "required": ["name"],
        },
    ),
    types.Tool(
        name="ticket_component_list",
        description=(
            "List all ticket components with their attributes "
            "(name, description, owner). Requires TICKET_VIEW."
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
        },
    ),
    types.Tool(
        name="ticket_enum_create",
        description=(
            "Create a new value for a Trac enum field (priority, "
            "resolution, severity, type, or version). Requires "
            "TICKET_ADMIN permission."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "enum_type": {
                    "type": "string",
                    "enum": _ENUM_TYPES,
                    "description": (
                        "Enum field to mutate. Must be one of "
                        "priority, resolution, severity, type, version."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "New enum value name.",
                },
            },
            "required": ["enum_type", "name"],
        },
    ),
    types.Tool(
        name="ticket_enum_list",
        description=(
            "List all values for a Trac enum field. Returns values in "
            "Trac's configured order. Requires TICKET_VIEW permission."
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
                "enum_type": {
                    "type": "string",
                    "enum": _ENUM_TYPES,
                    "description": (
                        "Enum field to list. Must be one of "
                        "priority, resolution, severity, type, version."
                    ),
                },
            },
            "required": ["enum_type"],
        },
    ),
    types.Tool(
        name="ticket_component_delete",
        description=(
            "Delete a ticket component. Requires TICKET_ADMIN "
            "permission."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Component name to delete.",
                },
            },
            "required": ["name"],
        },
    ),
    types.Tool(
        name="ticket_enum_delete",
        description=(
            "Delete a value from a ticket enum field (priority, "
            "resolution, severity, type, or version). Requires "
            "TICKET_ADMIN permission."
        ),
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "enum_type": {
                    "type": "string",
                    "enum": _ENUM_TYPES,
                    "description": (
                        "Enum field to mutate. Must be one of "
                        "priority, resolution, severity, type, version."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Enum value name to delete.",
                },
            },
            "required": ["enum_type", "name"],
        },
    ),
]


async def _handle_component_create(
    client: TracClient, args: dict
) -> types.CallToolResult:
    name = args.get("name")
    if not name:
        return build_error_response(
            "validation_error",
            "name is required",
            "Pass a non-empty component name.",
        )
    description = args.get("description", "") or ""
    owner = args.get("owner", "") or ""
    await run_sync(client.create_component, name, description, owner)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=f"Component '{name}' created.",
            )
        ]
    )


async def _handle_component_list(
    client: TracClient, args: dict
) -> types.CallToolResult:
    components = await run_sync(client.list_components)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(components),
            )
        ]
    )


async def _handle_enum_create(
    client: TracClient, args: dict
) -> types.CallToolResult:
    enum_type = args.get("enum_type")
    name = args.get("name")
    if not enum_type or enum_type not in _ENUM_TYPES:
        return build_error_response(
            "validation_error",
            f"enum_type must be one of {_ENUM_TYPES}",
            "Pass a valid enum_type.",
        )
    if not name:
        return build_error_response(
            "validation_error",
            "name is required",
            "Pass a non-empty enum value name.",
        )
    await run_sync(client.create_enum, enum_type, name)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=f"{enum_type} value '{name}' created.",
            )
        ]
    )


async def _handle_enum_list(
    client: TracClient, args: dict
) -> types.CallToolResult:
    enum_type = args.get("enum_type")
    if not enum_type or enum_type not in _ENUM_TYPES:
        return build_error_response(
            "validation_error",
            f"enum_type must be one of {_ENUM_TYPES}",
            "Pass a valid enum_type.",
        )
    values = await run_sync(client.list_enum, enum_type)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(values),
            )
        ]
    )


async def _handle_component_delete(
    client: TracClient, args: dict
) -> types.CallToolResult:
    name = args.get("name")
    if not name:
        return build_error_response(
            "validation_error",
            "name is required",
            "Pass a non-empty component name.",
        )
    await run_sync(client.delete_component, name)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=f"Component '{name}' deleted.",
            )
        ]
    )


async def _handle_enum_delete(
    client: TracClient, args: dict
) -> types.CallToolResult:
    enum_type = args.get("enum_type")
    name = args.get("name")
    if not enum_type or enum_type not in _ENUM_TYPES:
        return build_error_response(
            "validation_error",
            f"enum_type must be one of {_ENUM_TYPES}",
            "Pass a valid enum_type.",
        )
    if not name:
        return build_error_response(
            "validation_error",
            "name is required",
            "Pass a non-empty enum value name.",
        )
    await run_sync(client.delete_enum, enum_type, name)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=f"{enum_type} value '{name}' deleted.",
            )
        ]
    )


TICKET_ADMIN_SPECS: list[ToolSpec] = [
    ToolSpec(
        tool=TICKET_ADMIN_TOOLS[0],
        permissions=frozenset({"TICKET_ADMIN"}),
        handler=_handle_component_create,
    ),
    ToolSpec(
        tool=TICKET_ADMIN_TOOLS[1],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_component_list,
    ),
    ToolSpec(
        tool=TICKET_ADMIN_TOOLS[2],
        permissions=frozenset({"TICKET_ADMIN"}),
        handler=_handle_enum_create,
    ),
    ToolSpec(
        tool=TICKET_ADMIN_TOOLS[3],
        permissions=frozenset({"TICKET_VIEW"}),
        handler=_handle_enum_list,
    ),
    ToolSpec(
        tool=TICKET_ADMIN_TOOLS[4],
        permissions=frozenset({"TICKET_ADMIN"}),
        handler=_handle_component_delete,
    ),
    ToolSpec(
        tool=TICKET_ADMIN_TOOLS[5],
        permissions=frozenset({"TICKET_ADMIN"}),
        handler=_handle_enum_delete,
    ),
]


__all__ = [
    "TICKET_ADMIN_SPECS",
    "TICKET_ADMIN_TOOLS",
]
