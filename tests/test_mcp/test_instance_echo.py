"""Tests for the resolved-instance echo (ticket #94).

The defect these cover is not a crash: omitting ``instance`` resolved to the
server's default and the result said nothing about it, so a write could land
on the wrong Trac project and report success. The assertions below are
therefore all about what a *successful* result carries.

Two properties are load-bearing beyond "the field is there":

- ``content[0]`` is never touched. ``ticket_component_list`` and
  ``ticket_enum_list`` return ``json.dumps(...)`` as their entire text body,
  and a consumer parsing that must keep working.
- an existing key of the same name wins, so a handler that already means
  something by ``instance`` is not silently rewritten.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import mcp.types as types

from trac_mcp_server.mcp.server import handle_call_tool
from trac_mcp_server.mcp.tools.instance_echo import (
    SOURCE_DEFAULT,
    SOURCE_EXPLICIT,
    annotate,
    instance_label,
)

URL = "http://trac.example:8000/bcs"


def _result(text="ok", structured=None, is_error=False):
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
        isError=is_error,
    )


def _marker(result):
    return result.content[-1].text


class TestInstanceLabel:
    """The label is the path, because the path is what a caller passes back."""

    def test_path_from_url(self):
        assert instance_label(URL) == "/bcs"

    def test_trailing_slash_stripped(self):
        assert instance_label(URL + "/") == "/bcs"

    def test_nested_path_kept_whole(self):
        assert instance_label("http://h/trac/bcs") == "/trac/bcs"

    def test_url_without_path_falls_back_to_url(self):
        # Reporting an empty instance would hide a broken configuration.
        assert instance_label("http://trac.example:8000") == (
            "http://trac.example:8000"
        )


class TestAnnotate:
    def test_structured_content_gets_both_fields(self):
        result = annotate(
            _result(structured={"id": 1}), URL, explicit=True
        )

        assert result.structuredContent["instance"] == "/bcs"
        assert (
            result.structuredContent["instance_source"]
            == SOURCE_EXPLICIT
        )

    def test_omitted_instance_is_named_server_default(self):
        result = annotate(_result(structured={}), URL, explicit=False)

        assert (
            result.structuredContent["instance_source"]
            == SOURCE_DEFAULT
        )
        assert SOURCE_DEFAULT in _marker(result)

    def test_existing_keys_are_not_overwritten(self):
        result = annotate(
            _result(
                structured={"instance": "mine", "instance_source": "x"}
            ),
            URL,
            explicit=True,
        )

        assert result.structuredContent["instance"] == "mine"
        assert result.structuredContent["instance_source"] == "x"

    def test_marker_is_appended_as_its_own_block(self):
        payload = json.dumps([{"name": "server"}])
        result = annotate(_result(text=payload), URL, explicit=True)

        assert len(result.content) == 2
        # The whole point of appending rather than editing: still parseable.
        assert json.loads(result.content[0].text) == [
            {"name": "server"}
        ]
        assert _marker(result) == "[instance: /bcs (explicit)]"

    def test_error_results_are_annotated_too(self):
        # The nine recorded slips were all errors; naming the instance on an
        # error is what turns "ticket does not exist" into "wrong instance".
        result = annotate(
            _result(
                text="Error (not_found): Ticket 36 does not exist.",
                is_error=True,
            ),
            "http://trac.example:8000/auto_pm",
            explicit=False,
        )

        assert result.isError
        assert (
            _marker(result) == "[instance: /auto_pm (server_default)]"
        )

    def test_unresolvable_url_annotates_nothing(self):
        # A MagicMock client config, or a half-built one: say nothing rather
        # than report an instance named "<MagicMock id=...>".
        result = annotate(
            _result(structured={"id": 1}), None, explicit=False
        )

        assert len(result.content) == 1
        assert "instance" not in result.structuredContent


class TestDispatchAnnotates:
    """The annotation happens at the one chokepoint every tool call passes."""

    @staticmethod
    def _patched(mock_get_instances, mock_get_registry, url=URL):
        client = MagicMock()
        client.config.trac_url = url
        mock_get_instances.return_value.get_client.return_value = client
        registry = MagicMock()

        async def fake_call_tool(name, args, client):
            return _result(
                structured={"page": "b-node/bench/Inventory"}
            )

        registry.call_tool = MagicMock(side_effect=fake_call_tool)
        mock_get_registry.return_value = registry
        return client

    @patch("trac_mcp_server.mcp.server.get_registry")
    @patch("trac_mcp_server.mcp.server.get_instances")
    def test_omitted_instance_reports_the_default_it_fell_back_to(
        self, mock_get_instances, mock_get_registry
    ):
        self._patched(
            mock_get_instances,
            mock_get_registry,
            "http://h:8000/auto_pm",
        )

        result = asyncio.run(
            handle_call_tool(
                "wiki_update", {"page_name": "b-node/bench/Inventory"}
            )
        )

        assert result.structuredContent["instance"] == "/auto_pm"
        assert (
            result.structuredContent["instance_source"]
            == SOURCE_DEFAULT
        )

    @patch("trac_mcp_server.mcp.server.get_registry")
    @patch("trac_mcp_server.mcp.server.get_instances")
    def test_explicit_instance_is_reported_as_explicit(
        self, mock_get_instances, mock_get_registry
    ):
        self._patched(mock_get_instances, mock_get_registry)

        result = asyncio.run(
            handle_call_tool(
                "wiki_update",
                {
                    "page_name": "b-node/bench/Inventory",
                    "instance": "/bcs",
                },
            )
        )

        assert result.structuredContent["instance"] == "/bcs"
        assert (
            result.structuredContent["instance_source"]
            == SOURCE_EXPLICIT
        )

    @patch("trac_mcp_server.mcp.server.get_registry")
    @patch("trac_mcp_server.mcp.server.get_instances")
    def test_instance_is_still_not_passed_to_the_handler(
        self, mock_get_instances, mock_get_registry
    ):
        self._patched(mock_get_instances, mock_get_registry)
        registry = mock_get_registry.return_value

        asyncio.run(
            handle_call_tool(
                "wiki_get", {"page_name": "X", "instance": "/bcs"}
            )
        )

        name, args, _client = registry.call_tool.call_args[0]
        assert args == {"page_name": "X"}

    @patch("trac_mcp_server.mcp.server.get_instances")
    def test_unknown_tool_error_still_names_the_instance(
        self, mock_get_instances
    ):
        # It resolved an instance before failing, so it can say which one.
        client = MagicMock()
        client.config.trac_url = URL
        mock_get_instances.return_value.get_client.return_value = client

        with patch(
            "trac_mcp_server.mcp.server.get_registry"
        ) as mock_get_registry:
            registry = MagicMock()
            registry.call_tool = MagicMock(
                side_effect=ValueError("Unknown tool: x")
            )
            mock_get_registry.return_value = registry

            result = asyncio.run(handle_call_tool("x", {}))

        assert result.isError
        assert _marker(result) == "[instance: /bcs (server_default)]"

    @patch("trac_mcp_server.mcp.server.get_instances")
    def test_unresolvable_instance_error_is_not_annotated(
        self, mock_get_instances
    ):
        # Nothing was reached, so there is no instance to name.
        from trac_mcp_server.instances import UnknownInstanceError

        mock_get_instances.return_value.get_client.side_effect = (
            UnknownInstanceError("Unknown instance 'nope'.")
        )

        result = asyncio.run(
            handle_call_tool("wiki_get", {"instance": "nope"})
        )

        assert result.isError
        assert len(result.content) == 1
        assert "[instance:" not in result.content[0].text
