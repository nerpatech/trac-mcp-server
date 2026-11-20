"""Tests for ticket_admin MCP tool handlers.

These tests verify the handler functions and spec definitions for the
six admin tools: ticket_component_create, ticket_component_list,
ticket_enum_create, ticket_enum_list, ticket_component_delete,
ticket_enum_delete.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.types import CallToolResult

from trac_mcp_server.mcp.tools.ticket_admin import (
    TICKET_ADMIN_SPECS,
    TICKET_ADMIN_TOOLS,
    _handle_component_create,
    _handle_component_delete,
    _handle_component_list,
    _handle_enum_create,
    _handle_enum_delete,
    _handle_enum_list,
)


@pytest.fixture
def mock_client():
    """Create a MagicMock TracClient for handler tests."""
    return MagicMock()


# -- ticket_component_create ------------------------------------------------


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_component_create_calls_client(
    mock_run_sync, mock_client
):
    """Test _handle_component_create forwards args to client.create_component."""
    mock_run_sync.return_value = None

    result = await _handle_component_create(
        mock_client,
        {"name": "foo", "description": "bar", "owner": "alice"},
    )

    mock_run_sync.assert_called_once_with(
        mock_client.create_component, "foo", "bar", "alice"
    )
    assert isinstance(result, CallToolResult)
    assert not result.isError
    assert "foo" in result.content[0].text
    assert "created" in result.content[0].text.lower()


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_component_create_rejects_empty_name(
    mock_run_sync, mock_client
):
    """Test _handle_component_create returns validation error for empty name."""
    result = await _handle_component_create(mock_client, {"name": ""})

    mock_run_sync.assert_not_called()
    assert isinstance(result, CallToolResult)
    assert result.isError
    assert "validation_error" in result.content[0].text.lower()


# -- ticket_component_list --------------------------------------------------


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_component_list_returns_json_text(
    mock_run_sync, mock_client
):
    """Test _handle_component_list returns JSON-serialized component data."""
    mock_run_sync.return_value = [
        {"name": "a", "owner": "alice", "description": "one"}
    ]

    result = await _handle_component_list(mock_client, {})

    mock_run_sync.assert_called_once_with(mock_client.list_components)
    assert isinstance(result, CallToolResult)
    assert not result.isError
    parsed = json.loads(result.content[0].text)
    assert parsed == [
        {"name": "a", "owner": "alice", "description": "one"}
    ]


# -- ticket_enum_create -----------------------------------------------------


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_enum_create_priority(mock_run_sync, mock_client):
    """Test _handle_enum_create forwards priority/name to client.create_enum."""
    mock_run_sync.return_value = None

    result = await _handle_enum_create(
        mock_client, {"enum_type": "priority", "name": "urgent"}
    )

    mock_run_sync.assert_called_once_with(
        mock_client.create_enum, "priority", "urgent"
    )
    assert isinstance(result, CallToolResult)
    assert not result.isError
    assert "priority" in result.content[0].text
    assert "urgent" in result.content[0].text
    assert "created" in result.content[0].text.lower()


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_enum_create_rejects_invalid_type(
    mock_run_sync, mock_client
):
    """Test _handle_enum_create returns validation error for bad enum_type."""
    result = await _handle_enum_create(
        mock_client, {"enum_type": "bogus", "name": "foo"}
    )

    mock_run_sync.assert_not_called()
    assert isinstance(result, CallToolResult)
    assert result.isError
    assert "validation_error" in result.content[0].text.lower()


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_enum_create_rejects_empty_name(
    mock_run_sync, mock_client
):
    """Test _handle_enum_create returns validation error for empty name."""
    result = await _handle_enum_create(
        mock_client, {"enum_type": "priority", "name": ""}
    )

    mock_run_sync.assert_not_called()
    assert isinstance(result, CallToolResult)
    assert result.isError
    assert "validation_error" in result.content[0].text.lower()


# -- ticket_enum_list -------------------------------------------------------


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_enum_list_returns_json_text(mock_run_sync, mock_client):
    """Test _handle_enum_list returns JSON-serialized enum values."""
    mock_run_sync.return_value = ["blocker", "critical"]

    result = await _handle_enum_list(
        mock_client, {"enum_type": "priority"}
    )

    mock_run_sync.assert_called_once_with(
        mock_client.list_enum, "priority"
    )
    assert isinstance(result, CallToolResult)
    assert not result.isError
    parsed = json.loads(result.content[0].text)
    assert parsed == ["blocker", "critical"]


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_enum_list_rejects_invalid_type(
    mock_run_sync, mock_client
):
    """Test _handle_enum_list returns validation error for bad enum_type."""
    result = await _handle_enum_list(
        mock_client, {"enum_type": "bogus"}
    )

    mock_run_sync.assert_not_called()
    assert isinstance(result, CallToolResult)
    assert result.isError
    assert "validation_error" in result.content[0].text.lower()


# -- Spec verification ------------------------------------------------------


def test_specs_require_ticket_admin_for_writes():
    """Test write specs require TICKET_ADMIN, read specs require TICKET_VIEW."""
    specs_by_name = {s.tool.name: s for s in TICKET_ADMIN_SPECS}

    # Write tools require TICKET_ADMIN
    assert specs_by_name[
        "ticket_component_create"
    ].permissions == frozenset({"TICKET_ADMIN"})
    assert specs_by_name["ticket_enum_create"].permissions == frozenset(
        {"TICKET_ADMIN"}
    )

    # Read tools require only TICKET_VIEW
    assert specs_by_name[
        "ticket_component_list"
    ].permissions == frozenset({"TICKET_VIEW"})
    assert specs_by_name["ticket_enum_list"].permissions == frozenset(
        {"TICKET_VIEW"}
    )


def test_tool_count():
    """Belt-and-suspenders pin: exactly 6 tools and 6 specs."""
    assert len(TICKET_ADMIN_TOOLS) == 6
    assert len(TICKET_ADMIN_SPECS) == 6


# -- ticket_component_delete ------------------------------------------------


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_component_delete_calls_client(
    mock_run_sync, mock_client
):
    """Test _handle_component_delete forwards name to client.delete_component."""
    mock_run_sync.return_value = None

    result = await _handle_component_delete(
        mock_client, {"name": "old-comp"}
    )

    mock_run_sync.assert_called_once_with(
        mock_client.delete_component, "old-comp"
    )
    assert isinstance(result, CallToolResult)
    assert not result.isError
    assert "old-comp" in result.content[0].text
    assert "deleted" in result.content[0].text.lower()


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_component_delete_rejects_empty_name(
    mock_run_sync, mock_client
):
    """Test _handle_component_delete returns validation error for empty name."""
    result = await _handle_component_delete(mock_client, {"name": ""})

    mock_run_sync.assert_not_called()
    assert isinstance(result, CallToolResult)
    assert result.isError
    assert "validation_error" in result.content[0].text.lower()


# -- ticket_enum_delete -----------------------------------------------------


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_enum_delete_calls_client(mock_run_sync, mock_client):
    """Test _handle_enum_delete forwards enum_type and name to client.delete_enum."""
    mock_run_sync.return_value = None

    result = await _handle_enum_delete(
        mock_client, {"enum_type": "priority", "name": "trivial"}
    )

    mock_run_sync.assert_called_once_with(
        mock_client.delete_enum, "priority", "trivial"
    )
    assert isinstance(result, CallToolResult)
    assert not result.isError
    assert "priority" in result.content[0].text
    assert "trivial" in result.content[0].text
    assert "deleted" in result.content[0].text.lower()


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_enum_delete_rejects_invalid_type(
    mock_run_sync, mock_client
):
    """Test _handle_enum_delete returns validation error for bad enum_type."""
    result = await _handle_enum_delete(
        mock_client, {"enum_type": "bogus", "name": "foo"}
    )

    mock_run_sync.assert_not_called()
    assert isinstance(result, CallToolResult)
    assert result.isError
    assert "validation_error" in result.content[0].text.lower()


@patch("trac_mcp_server.mcp.tools.ticket_admin.run_sync")
@pytest.mark.asyncio
async def test_enum_delete_rejects_empty_name(
    mock_run_sync, mock_client
):
    """Test _handle_enum_delete returns validation error for empty name."""
    result = await _handle_enum_delete(
        mock_client, {"enum_type": "priority", "name": ""}
    )

    mock_run_sync.assert_not_called()
    assert isinstance(result, CallToolResult)
    assert result.isError
    assert "validation_error" in result.content[0].text.lower()


# -- Spec verification for deletes ------------------------------------------


def test_specs_require_ticket_admin_for_deletes():
    """Test delete specs require TICKET_ADMIN permission."""
    specs_by_name = {s.tool.name: s for s in TICKET_ADMIN_SPECS}

    assert specs_by_name[
        "ticket_component_delete"
    ].permissions == frozenset({"TICKET_ADMIN"})
    assert specs_by_name["ticket_enum_delete"].permissions == frozenset(
        {"TICKET_ADMIN"}
    )


# -- Live round-trip (create -> list -> delete a version) -------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_version_create_list_delete():
    """End-to-end on `/trac_test`: a version is created, listed, deleted.

    Trac /trac_mcp_server #251. `create_enum` sent a sort-order integer to
    TracXMLRPC's Version model, which wants an attributes dict, so creating
    a version failed live with ``'int' object has no attribute 'items'``;
    no mocked row could see that. The version is deleted in `finally`, so
    a failed assertion does not leave it on the instance.
    """
    import uuid

    from tests.live_helpers import trac_test_client

    client = trac_test_client()
    name = f"t251-{uuid.uuid4().hex[:8]}"

    try:
        created = await _handle_enum_create(
            client, {"enum_type": "version", "name": name}
        )
        assert not created.isError, created.content[0].text

        listed = await _handle_enum_list(
            client, {"enum_type": "version"}
        )
        assert not listed.isError, listed.content[0].text
        assert name in json.loads(listed.content[0].text)
    finally:
        deleted = await _handle_enum_delete(
            client, {"enum_type": "version", "name": name}
        )

    assert not deleted.isError, deleted.content[0].text
    after = await _handle_enum_list(client, {"enum_type": "version"})
    assert name not in json.loads(after.content[0].text)
