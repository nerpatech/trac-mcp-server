"""Tests for the timeline_search MCP tool (ticket #93).

Handler-level tests with a mocked TracClient: the server-side behaviour
this wraps is proven against real Trac in
``plugins/tracrpc_comment/tests/test_timeline.py``. What is worth
covering HERE is the wiring the plugin cannot see -- argument
validation, filter resolution, and the fault translation that decides
whether a missing-plugin failure is actionable or just a bare XML-RPC
string.
"""

import asyncio
import unittest
import xmlrpc.client
from unittest.mock import MagicMock

from trac_mcp_server.mcp.tools.timeline_read import (
    TIMELINE_SPECS,
    TIMELINE_TOOLS,
    _handle_search,
)


def run(coro):
    return asyncio.run(coro)


def text_of(result):
    return "\n".join(
        block.text
        for block in result.content
        if isinstance(block, type(result.content[0]))
    )


class TimelineToolDefinitionTestCase(unittest.TestCase):
    def test_one_tool_defined(self):
        self.assertEqual(1, len(TIMELINE_TOOLS))
        self.assertEqual(1, len(TIMELINE_SPECS))

    def test_name(self):
        self.assertEqual("timeline_search", TIMELINE_TOOLS[0].name)

    def test_flagged_read_only(self):
        self.assertTrue(TIMELINE_TOOLS[0].annotations.readOnlyHint)

    def test_permissions_match_the_server_side_requirement(self):
        self.assertEqual(
            [frozenset({"TIMELINE_VIEW"})],
            [spec.permissions for spec in TIMELINE_SPECS],
        )

    def test_every_required_arg_is_a_declared_property(self):
        """Guards the shape that made trac_mcp_server #97 possible.

        An argument named in `required` but absent from `properties`, or
        a handler reading a key the schema never declares, fails only at
        call time -- jsonschema validates against this schema before
        dispatch.
        """
        for tool in TIMELINE_TOOLS:
            schema = tool.inputSchema
            for name in schema.get("required", []):
                self.assertIn(
                    name,
                    schema["properties"],
                    "%s: required arg %r is not a declared property"
                    % (tool.name, name),
                )


class TimelineValidationTestCase(unittest.TestCase):
    def test_requires_start_and_stop(self):
        result = run(_handle_search(MagicMock(), {}))
        self.assertTrue(result.isError)
        self.assertIn("start", text_of(result))
        self.assertIn("stop", text_of(result))

    def test_requires_stop_when_only_start_given(self):
        result = run(_handle_search(MagicMock(), {"start": 100}))
        self.assertTrue(result.isError)
        self.assertIn("stop", text_of(result))
        self.assertNotIn("start", text_of(result))


class TimelineHappyPathTestCase(unittest.TestCase):
    def test_omitted_filters_resolve_to_every_available_kind(self):
        """Omitting `filters` must mean "every kind", not "none" --
        `timeline.getEvents` treats an explicitly empty list as "select
        nothing", so the handler has to resolve "all" itself before
        calling it.
        """
        client = MagicMock()
        client.get_timeline_filters = MagicMock(
            return_value=[["ticket", "Tickets"], ["wiki", "Wiki"]]
        )
        client.get_timeline_events = MagicMock(return_value=[])
        run(_handle_search(client, {"start": 100, "stop": 200}))
        client.get_timeline_filters.assert_called_once()
        client.get_timeline_events.assert_called_once_with(
            100, 200, ["ticket", "wiki"], 100
        )

    def test_explicit_filters_are_passed_through_without_resolving(
        self,
    ):
        client = MagicMock()
        client.get_timeline_filters = MagicMock()
        client.get_timeline_events = MagicMock(return_value=[])
        run(
            _handle_search(
                client,
                {"start": 100, "stop": 200, "filters": ["ticket"]},
            )
        )
        client.get_timeline_filters.assert_not_called()
        client.get_timeline_events.assert_called_once_with(
            100, 200, ["ticket"], 100
        )

    def test_explicitly_empty_filters_select_nothing(self):
        """An empty list is a real, distinct choice from omitting the
        key -- it must reach the client as-is, not get widened to "all".
        """
        client = MagicMock()
        client.get_timeline_filters = MagicMock()
        client.get_timeline_events = MagicMock(return_value=[])
        run(
            _handle_search(
                client, {"start": 100, "stop": 200, "filters": []}
            )
        )
        client.get_timeline_filters.assert_not_called()
        client.get_timeline_events.assert_called_once_with(
            100, 200, [], 100
        )

    def test_max_results_is_clamped_to_the_cap(self):
        client = MagicMock()
        client.get_timeline_filters = MagicMock(return_value=[])
        client.get_timeline_events = MagicMock(return_value=[])
        run(
            _handle_search(
                client,
                {"start": 100, "stop": 200, "max_results": 5000},
            )
        )
        client.get_timeline_events.assert_called_once_with(
            100, 200, [], 1000
        )

    def test_events_are_passed_through_as_rendered_by_the_server(self):
        client = MagicMock()
        client.get_timeline_filters = MagicMock(
            return_value=[["ticket", "Tickets"]]
        )
        client.get_timeline_events = MagicMock(
            return_value=[
                {
                    "kind": "ticket",
                    "date": "2026-09-17T12:00:00",
                    "author": "alice",
                    "title": "Ticket #7 created",
                    "description": "plain text body",
                    "url": "http://example/ticket/7",
                }
            ]
        )
        result = run(
            _handle_search(client, {"start": 100, "stop": 200})
        )
        body = text_of(result)
        self.assertIn("Ticket #7 created", body)
        self.assertIn("alice", body)
        self.assertEqual(
            result.structuredContent["events"],
            [
                {
                    "kind": "ticket",
                    "date": "2026-09-17T12:00:00",
                    "author": "alice",
                    "title": "Ticket #7 created",
                    "description": "plain text body",
                    "url": "http://example/ticket/7",
                }
            ],
        )
        self.assertEqual(result.structuredContent["total"], 1)

    def test_no_events_says_so(self):
        client = MagicMock()
        client.get_timeline_filters = MagicMock(return_value=[])
        client.get_timeline_events = MagicMock(return_value=[])
        result = run(
            _handle_search(client, {"start": 100, "stop": 200})
        )
        self.assertIn("No timeline events", text_of(result))
        self.assertEqual(result.structuredContent["total"], 0)


class TimelineFaultTranslationTestCase(unittest.TestCase):
    def test_missing_plugin_is_explained(self):
        client = MagicMock()
        client.get_timeline_filters = MagicMock(
            side_effect=xmlrpc.client.Fault(
                1, 'RPC method "timeline.getFilters" not found'
            )
        )
        result = run(
            _handle_search(client, {"start": 100, "stop": 200})
        )
        self.assertTrue(result.isError)
        self.assertIn("tracrpc_comment", text_of(result))

    def test_an_unrelated_fault_is_not_swallowed(self):
        """Only the missing-method fault is translated; the rest must
        propagate. A blanket except here would turn a permission error
        into a 'install the plugin' message and send the caller after
        the wrong fix.
        """
        client = MagicMock()
        client.get_timeline_filters = MagicMock(
            side_effect=xmlrpc.client.Fault(
                1, "TIMELINE_VIEW privileges are required"
            )
        )
        with self.assertRaises(xmlrpc.client.Fault):
            run(_handle_search(client, {"start": 100, "stop": 200}))


if __name__ == "__main__":
    unittest.main()
