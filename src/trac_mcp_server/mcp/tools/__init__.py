"""MCP tool handlers for Trac operations.

This package contains MCP tool implementations that wrap the core TracClient
with async handlers, Markdown conversion, and structured error responses.
"""

from .convert_preview import (
    CONVERT_PREVIEW_SPECS,
    CONVERT_PREVIEW_TOOLS,
)
from .errors import build_error_response
from .instances import INSTANCE_SPECS, INSTANCE_TOOLS
from .milestone import MILESTONE_SPECS, MILESTONE_TOOLS
from .registry import ToolRegistry, ToolSpec, load_permissions_file
from .render_check import RENDER_CHECK_SPECS, RENDER_CHECK_TOOLS
from .system import SYSTEM_SPECS, SYSTEM_TOOLS
from .ticket_admin import TICKET_ADMIN_SPECS, TICKET_ADMIN_TOOLS
from .ticket_attachment import (
    TICKET_ATTACHMENT_SPECS,
    TICKET_ATTACHMENT_TOOLS,
)
from .ticket_batch import TICKET_BATCH_SPECS, TICKET_BATCH_TOOLS
from .ticket_read import TICKET_READ_SPECS, TICKET_READ_TOOLS
from .ticket_write import TICKET_WRITE_SPECS, TICKET_WRITE_TOOLS
from .wiki_attachment import (
    WIKI_ATTACHMENT_SPECS,
    WIKI_ATTACHMENT_TOOLS,
)
from .wiki_file import WIKI_FILE_SPECS, WIKI_FILE_TOOLS
from .wiki_read import WIKI_READ_SPECS, WIKI_READ_TOOLS
from .wiki_write import WIKI_WRITE_SPECS, WIKI_WRITE_TOOLS

# Combine ticket tools for backward compatibility
TICKET_TOOLS = (
    TICKET_READ_TOOLS
    + TICKET_WRITE_TOOLS
    + TICKET_BATCH_TOOLS
    + TICKET_ATTACHMENT_TOOLS
)
WIKI_TOOLS = WIKI_READ_TOOLS + WIKI_WRITE_TOOLS + WIKI_ATTACHMENT_TOOLS

# Combined spec lists (parallel to existing TICKET_TOOLS, WIKI_TOOLS)
TICKET_SPECS = (
    TICKET_READ_SPECS
    + TICKET_WRITE_SPECS
    + TICKET_BATCH_SPECS
    + TICKET_ATTACHMENT_SPECS
)
WIKI_SPECS = (
    WIKI_READ_SPECS
    + WIKI_WRITE_SPECS
    + WIKI_FILE_SPECS
    + WIKI_ATTACHMENT_SPECS
)

ALL_SPECS: list[ToolSpec] = (
    SYSTEM_SPECS
    + TICKET_SPECS
    + WIKI_SPECS
    + MILESTONE_SPECS
    + TICKET_ADMIN_SPECS
    + INSTANCE_SPECS
    + CONVERT_PREVIEW_SPECS
    + RENDER_CHECK_SPECS
)

__all__ = [
    "build_error_response",
    # Registry
    "ToolSpec",
    "ToolRegistry",
    "load_permissions_file",
    # Spec lists
    "ALL_SPECS",
    "TICKET_SPECS",
    "WIKI_SPECS",
    "SYSTEM_SPECS",
    "TICKET_READ_SPECS",
    "TICKET_WRITE_SPECS",
    "TICKET_BATCH_SPECS",
    "TICKET_ATTACHMENT_SPECS",
    "WIKI_READ_SPECS",
    "WIKI_WRITE_SPECS",
    "WIKI_FILE_SPECS",
    "WIKI_ATTACHMENT_SPECS",
    "MILESTONE_SPECS",
    "TICKET_ADMIN_SPECS",
    "INSTANCE_SPECS",
    "CONVERT_PREVIEW_SPECS",
    "RENDER_CHECK_SPECS",
    # Tool lists (backward compat)
    "TICKET_TOOLS",
    "TICKET_READ_TOOLS",
    "TICKET_WRITE_TOOLS",
    "TICKET_BATCH_TOOLS",
    "TICKET_ATTACHMENT_TOOLS",
    "TICKET_ADMIN_TOOLS",
    "WIKI_TOOLS",
    "WIKI_READ_TOOLS",
    "WIKI_WRITE_TOOLS",
    "WIKI_ATTACHMENT_TOOLS",
    "WIKI_FILE_TOOLS",
    "MILESTONE_TOOLS",
    "SYSTEM_TOOLS",
    "INSTANCE_TOOLS",
    "CONVERT_PREVIEW_TOOLS",
    "RENDER_CHECK_TOOLS",
]
