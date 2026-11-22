"""MCP resource handlers for Trac operations.

This package contains MCP resource implementations that expose Trac wiki pages
as read-only resources via URI templates.
"""

from .wiki import (
    WIKI_RESOURCE_TEMPLATES,
    WIKI_RESOURCES,
    handle_list_wiki_resource_templates,
    handle_list_wiki_resources,
    handle_read_wiki_resource,
)

__all__ = [
    "handle_list_wiki_resource_templates",
    "handle_list_wiki_resources",
    "handle_read_wiki_resource",
    "WIKI_RESOURCE_TEMPLATES",
    "WIKI_RESOURCES",
]
