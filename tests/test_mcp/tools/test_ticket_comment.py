"""Tests for the ticket comment MCP tools (ticket #96).

Handler-level tests with a mocked TracClient: the server-side behaviour these
wrap is proven against real Trac in ``plugins/tracrpc_comment/tests`` and by
live acceptance on ``/trac_test``. What is worth covering HERE is the wiring
the plugin cannot see -- argument validation, and the two fault translations
that decide whether a failure is actionable or just a bare XML-RPC string.
"""

import asyncio
import unittest
import xmlrpc.client
from unittest.mock import MagicMock

from trac_mcp_server.mcp.tools.ticket_comment import (
    TICKET_COMMENT_SPECS,
    TICKET_COMMENT_TOOLS,
    _handle_delete,
    _handle_edit,
    _handle_history,
    _handle_reply,
)


def run(coro):
    return asyncio.run(coro)


def text_of(result):
    return "\n".join(
        block.text
        for block in result.content
        if isinstance(block, type(result.content[0]))
    )


def _client_at_ts(current_ts="100"):
    """A client whose ``get_ticket`` reports ``current_ts`` -- the
    ticket #104 precondition's read. ``wiki_to_html`` is left as a bare
    MagicMock, so the write gate takes its documented fail-open path
    (`test_write_gate.py`'s module docstring): the checks it runs are
    covered there, not duplicated here.
    """
    client = MagicMock()
    client.get_ticket = MagicMock(
        return_value=[1, "created", "modified", {"_ts": current_ts}]
    )
    return client


class TicketCommentToolDefinitionTestCase(unittest.TestCase):
    def test_four_tools_defined(self):
        self.assertEqual(4, len(TICKET_COMMENT_TOOLS))
        self.assertEqual(4, len(TICKET_COMMENT_SPECS))

    def test_names(self):
        self.assertEqual(
            [
                "ticket_comment_edit",
                "ticket_comment_delete",
                "ticket_comment_reply",
                "ticket_comment_history",
            ],
            [t.name for t in TICKET_COMMENT_TOOLS],
        )

    def test_delete_is_flagged_destructive(self):
        delete = TICKET_COMMENT_TOOLS[1]
        self.assertTrue(delete.annotations.destructiveHint)

    def test_history_is_flagged_read_only(self):
        history = TICKET_COMMENT_TOOLS[3]
        self.assertTrue(history.annotations.readOnlyHint)

    def test_edit_is_not_flagged_destructive(self):
        """An edit keeps the prior text as a revision, so it is not."""
        self.assertFalse(
            TICKET_COMMENT_TOOLS[0].annotations.destructiveHint
        )

    def test_permissions_match_the_server_side_requirements(self):
        self.assertEqual(
            [
                frozenset({"TICKET_EDIT_COMMENT"}),
                frozenset({"TICKET_ADMIN"}),
                frozenset({"TICKET_APPEND"}),
                frozenset({"TICKET_VIEW"}),
            ],
            [spec.permissions for spec in TICKET_COMMENT_SPECS],
        )

    def test_every_required_arg_is_a_declared_property(self):
        """Guards the shape that made trac_mcp_server #97 possible.

        An argument named in `required` but absent from `properties`, or a
        handler reading a key the schema never declares, fails only at call
        time -- jsonschema validates against this schema before dispatch.
        """
        for tool in TICKET_COMMENT_TOOLS:
            schema = tool.inputSchema
            for name in schema.get("required", []):
                self.assertIn(
                    name,
                    schema["properties"],
                    "%s: required arg %r is not a declared property"
                    % (tool.name, name),
                )


class TicketCommentValidationTestCase(unittest.TestCase):
    def test_edit_requires_all_four_args(self):
        result = run(_handle_edit(MagicMock(), {"ticket_id": 1}))
        self.assertTrue(result.isError)
        self.assertIn("cnum", text_of(result))
        self.assertIn("comment", text_of(result))
        self.assertIn("base_ts", text_of(result))

    def test_edit_omitted_base_ts_never_reaches_get_ticket(self):
        """Ticket #104: base_ts is required, not merely recommended --
        unlike ticket_update there is no server-side lock to fall back
        to, so a call missing it must refuse before touching the
        ticket at all."""
        client = MagicMock()
        result = run(
            _handle_edit(
                client, {"ticket_id": 1, "cnum": 1, "comment": "x"}
            )
        )
        self.assertTrue(result.isError)
        self.assertIn("base_ts", text_of(result))
        client.get_ticket.assert_not_called()

    def test_delete_requires_ticket_and_cnum(self):
        result = run(_handle_delete(MagicMock(), {"ticket_id": 1}))
        self.assertTrue(result.isError)
        self.assertIn("cnum", text_of(result))

    def test_cnum_zero_is_not_treated_as_missing(self):
        """`if not cnum` would reject 0 as absent.

        Trac never issues comment 0, but a validator that conflates 0 with
        "not provided" reports the wrong error and hides the real one.
        """
        client = _client_at_ts("100")
        client.edit_ticket_comment = MagicMock(return_value=True)
        result = run(
            _handle_edit(
                client,
                {
                    "ticket_id": 1,
                    "cnum": 0,
                    "comment": "x",
                    "base_ts": "100",
                },
            )
        )
        self.assertFalse(result.isError)
        client.edit_ticket_comment.assert_called_once_with(1, 0, "x")


class TicketCommentBaseTsTestCase(unittest.TestCase):
    """Ticket #104's precondition on ticket_comment_edit."""

    def test_matching_base_ts_proceeds(self):
        client = _client_at_ts("100")
        client.edit_ticket_comment = MagicMock(return_value=True)
        result = run(
            _handle_edit(
                client,
                {
                    "ticket_id": 7,
                    "cnum": 3,
                    "comment": "fixed",
                    "base_ts": "100",
                },
            )
        )
        self.assertFalse(result.isError, text_of(result))
        client.get_ticket.assert_called_once_with(7)
        client.edit_ticket_comment.assert_called_once_with(
            7, 3, "fixed"
        )

    def test_mismatched_base_ts_refuses_without_editing(self):
        """The exact shape of #104's incident: the ticket this
        ticket_id/instance pair actually resolves to is not the one the
        caller read, so its _ts differs and the edit must not happen."""
        client = _client_at_ts("999")
        client.edit_ticket_comment = MagicMock(return_value=True)
        result = run(
            _handle_edit(
                client,
                {
                    "ticket_id": 7,
                    "cnum": 3,
                    "comment": "fixed",
                    "base_ts": "100",
                },
            )
        )
        self.assertTrue(result.isError)
        self.assertIn("version_conflict", text_of(result))
        self.assertIn("999", text_of(result))
        client.edit_ticket_comment.assert_not_called()

    def test_previous_text_snippet_is_included(self):
        """Ticket #104 item 3: visible even when the call succeeds."""
        client = _client_at_ts("100")
        client.edit_ticket_comment = MagicMock(return_value=True)
        client.get_ticket_comment_history = MagicMock(
            return_value=[[0, "2026-09-17", "alice", "the old text"]]
        )
        result = run(
            _handle_edit(
                client,
                {
                    "ticket_id": 7,
                    "cnum": 3,
                    "comment": "new text",
                    "base_ts": "100",
                },
            )
        )
        self.assertFalse(result.isError, text_of(result))
        self.assertIn("the old text", text_of(result))

    def test_a_history_read_failure_does_not_block_the_edit(self):
        """Best-effort: fetching the snippet is a bonus, not a gate."""
        client = _client_at_ts("100")
        client.edit_ticket_comment = MagicMock(return_value=True)
        client.get_ticket_comment_history = MagicMock(
            side_effect=xmlrpc.client.Fault(1, "boom")
        )
        result = run(
            _handle_edit(
                client,
                {
                    "ticket_id": 7,
                    "cnum": 3,
                    "comment": "new text",
                    "base_ts": "100",
                },
            )
        )
        self.assertFalse(result.isError, text_of(result))
        client.edit_ticket_comment.assert_called_once_with(
            7, 3, "new text"
        )


class TicketCommentHappyPathTestCase(unittest.TestCase):
    def test_edit_passes_args_through(self):
        client = _client_at_ts("100")
        client.edit_ticket_comment = MagicMock(return_value=True)
        result = run(
            _handle_edit(
                client,
                {
                    "ticket_id": 7,
                    "cnum": 3,
                    "comment": "fixed",
                    "base_ts": "100",
                },
            )
        )
        client.edit_ticket_comment.assert_called_once_with(
            7, 3, "fixed"
        )
        self.assertIn("ticket_comment_history", text_of(result))

    def test_delete_defaults_force_to_false(self):
        client = MagicMock()
        client.delete_ticket_comment = MagicMock(return_value=True)
        run(_handle_delete(client, {"ticket_id": 7, "cnum": 3}))
        client.delete_ticket_comment.assert_called_once_with(
            7, 3, False
        )

    def test_delete_reports_the_revert_when_forced(self):
        client = MagicMock()
        client.delete_ticket_comment = MagicMock(return_value=True)
        result = run(
            _handle_delete(
                client, {"ticket_id": 7, "cnum": 3, "force": True}
            )
        )
        client.delete_ticket_comment.assert_called_once_with(7, 3, True)
        self.assertIn("reverted", text_of(result))

    def test_reply_defaults_quote_to_false_and_reports_new_number(self):
        client = MagicMock()
        client.reply_to_ticket_comment = MagicMock(return_value=12)
        result = run(
            _handle_reply(
                client, {"ticket_id": 7, "cnum": 3, "comment": "answer"}
            )
        )
        client.reply_to_ticket_comment.assert_called_once_with(
            7, 3, "answer", False
        )
        self.assertIn("comment 12", text_of(result))
        self.assertIn("threaded reply", text_of(result))

    def test_history_renders_every_revision(self):
        client = MagicMock()
        client.get_ticket_comment_history = MagicMock(
            return_value=[
                [0, "2026-09-17 10:00", "alice", "first"],
                [1, "2026-09-17 11:00", "bob", "second"],
            ]
        )
        result = run(
            _handle_history(client, {"ticket_id": 7, "cnum": 3})
        )
        body = text_of(result)
        self.assertIn("2 revision(s)", body)
        self.assertIn("first", body)
        self.assertIn("second", body)
        self.assertIn("alice", body)
        self.assertIn("bob", body)

    def test_empty_history_says_the_comment_is_missing(self):
        client = MagicMock()
        client.get_ticket_comment_history = MagicMock(return_value=[])
        result = run(
            _handle_history(client, {"ticket_id": 7, "cnum": 99})
        )
        self.assertIn("gaps", text_of(result))


class TicketCommentFaultTranslationTestCase(unittest.TestCase):
    """The two faults that must not reach the caller raw."""

    def _client_raising(self, attr, fault_string):
        # get_ticket defaults to a matching base_ts of "100" so the edit
        # cases below reach the RPC under test rather than being turned
        # away by ticket #104's precondition first.
        client = _client_at_ts("100")
        setattr(
            client,
            attr,
            MagicMock(side_effect=xmlrpc.client.Fault(1, fault_string)),
        )
        return client

    def test_missing_plugin_is_explained(self):
        client = self._client_raising(
            "edit_ticket_comment",
            'RPC method "ticket.editComment" not found',
        )
        result = run(
            _handle_edit(
                client,
                {
                    "ticket_id": 1,
                    "cnum": 1,
                    "comment": "x",
                    "base_ts": "100",
                },
            )
        )
        self.assertTrue(result.isError)
        self.assertIn("tracrpc_comment", text_of(result))

    def test_missing_plugin_explained_for_every_tool(self):
        fault = 'RPC method "ticket.whatever" not found'
        cases = [
            (
                "edit_ticket_comment",
                _handle_edit,
                {
                    "ticket_id": 1,
                    "cnum": 1,
                    "comment": "x",
                    "base_ts": "100",
                },
            ),
            (
                "delete_ticket_comment",
                _handle_delete,
                {"ticket_id": 1, "cnum": 1},
            ),
            (
                "reply_to_ticket_comment",
                _handle_reply,
                {"ticket_id": 1, "cnum": 1, "comment": "x"},
            ),
            (
                "get_ticket_comment_history",
                _handle_history,
                {"ticket_id": 1, "cnum": 1},
            ),
        ]
        for attr, handler, args in cases:
            with self.subTest(handler=attr):
                result = run(
                    handler(self._client_raising(attr, fault), args)
                )
                self.assertIn("tracrpc_comment", text_of(result))

    def test_bundled_change_refusal_keeps_the_servers_wording(self):
        """The refusal names the fields; dropping it would lose the reason."""
        fault = (
            "Refusing to delete comment 1 on ticket #5: it was posted "
            "with changes to status, resolution, and deleting it would "
            "revert them."
        )
        client = self._client_raising("delete_ticket_comment", fault)
        result = run(
            _handle_delete(client, {"ticket_id": 5, "cnum": 1})
        )
        self.assertTrue(result.isError)
        body = text_of(result)
        self.assertIn("status", body)
        self.assertIn("resolution", body)
        self.assertIn("force=true", body)

    def test_an_unrelated_fault_is_not_swallowed(self):
        """Only the two known faults are translated; the rest must propagate.

        A blanket except here would turn a permission error into a
        'install the plugin' message and send the caller after the wrong fix.
        """
        client = self._client_raising(
            "edit_ticket_comment",
            "TICKET_EDIT_COMMENT privileges are required",
        )
        with self.assertRaises(xmlrpc.client.Fault):
            run(
                _handle_edit(
                    client,
                    {
                        "ticket_id": 1,
                        "cnum": 1,
                        "comment": "x",
                        "base_ts": "100",
                    },
                )
            )


if __name__ == "__main__":
    unittest.main()
