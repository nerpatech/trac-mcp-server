"""Tests for the write-path ``format`` declaration (ticket #62).

Every write tool converted its input unconditionally, so an author who
wrote TracWiki had no way to say so and had it silently mangled. These
tests pin the declaration on all seven call sites.

The seeded defect is a TracWiki processor block carrying an indented
body. Run through the Markdown converter -- which is what every write
path did -- the block is not recognised as a code construct, so
paragraph handling eats the leading whitespace and produces
syntactically invalid Python with no warning at all.

Every assertion here is on the *stored bytes*. None is on the render and
none is on the warning list: the loss produced zero warnings and a
plausible-looking render on both occasions it happened, so a test
written against either signal passes on the broken case.
"""

import asyncio
import unittest
import xmlrpc.client
from unittest.mock import MagicMock, patch

from trac_mcp_server.config import Config
from trac_mcp_server.mcp.tools import TICKET_TOOLS, WIKI_TOOLS
from trac_mcp_server.mcp.tools.ticket_batch import (
    _handle_batch_create,
    _handle_batch_update,
)
from trac_mcp_server.mcp.tools.ticket_write import (
    _handle_create as _handle_ticket_create,
)
from trac_mcp_server.mcp.tools.ticket_write import (
    _handle_update as _handle_ticket_update,
)
from trac_mcp_server.mcp.tools.wiki_write import (
    _handle_create as _handle_wiki_create,
)
from trac_mcp_server.mcp.tools.wiki_write import (
    _handle_update as _handle_wiki_update,
)

# The seeded defect. Fed to the Markdown converter this loses the
# four-space indent on the `return` line -- measured, not assumed.
SEEDED = '{{{#!python\ndef f(x):\n    return {"a": 1}\n}}}'

# Tools that gain the parameter. Batch tools carry it at the call level.
FORMAT_TOOLS = (
    "ticket_create",
    "ticket_update",
    "ticket_batch_create",
    "ticket_batch_update",
    "wiki_create",
    "wiki_update",
)


def _tool(name):
    for tool in list(TICKET_TOOLS) + list(WIKI_TOOLS):
        if tool.name == name:
            return tool
    raise AssertionError(f"tool {name} not found")


class TestFormatSchema(unittest.TestCase):
    """The parameter is declared identically on all six tools."""

    def test_all_six_tools_declare_format(self):
        for name in FORMAT_TOOLS:
            with self.subTest(tool=name):
                props = _tool(name).inputSchema["properties"]
                self.assertIn(
                    "format",
                    props,
                    f"{name} cannot be told its source format",
                )
                self.assertEqual(
                    props["format"]["enum"], ["markdown", "tracwiki"]
                )
                self.assertEqual(props["format"]["default"], "markdown")

    def test_format_is_never_required(self):
        """Purely additive -- every existing caller keeps working."""
        for name in FORMAT_TOOLS:
            with self.subTest(tool=name):
                required = _tool(name).inputSchema.get("required", [])
                self.assertNotIn("format", required)

    def test_no_auto_value_offered(self):
        """`auto` is content-sniffing, which is what ticket #47 showed
        to be unreliable and what SurgicalRepairs condition 4 forbids.
        wiki_file_push's three-value enum is deliberately not copied.
        """
        for name in FORMAT_TOOLS:
            with self.subTest(tool=name):
                props = _tool(name).inputSchema["properties"]
                self.assertNotIn("auto", props["format"]["enum"])

    def test_batch_item_schemas_do_not_carry_format(self):
        """Decision 1: the parameter sits at the call level. Pinned so a
        later move to per-item is a deliberate change, not a drift.
        """
        for name, key in (
            ("ticket_batch_create", "tickets"),
            ("ticket_batch_update", "updates"),
        ):
            with self.subTest(tool=name):
                props = _tool(name).inputSchema["properties"]
                item_props = props[key]["items"]["properties"]
                self.assertNotIn("format", item_props)


class TestFormatValidation(unittest.TestCase):
    """A bad value is rejected before anything is written."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.config = Config(
            trac_url="http://test", username="test", password="test"
        )

    def _assert_rejected(self, result, mock_run_sync):
        self.assertTrue(result.isError)
        self.assertIn("validation_error", result.content[0].text)
        self.assertIn("format", result.content[0].text)
        mock_run_sync.assert_not_called()

    def test_ticket_create_rejects_bad_format(self):
        for bad in ("auto", "", "Markdown", "tracwiky"):
            with self.subTest(value=bad):
                with patch(
                    "trac_mcp_server.mcp.tools.ticket_write.run_sync"
                ) as mock_run_sync:
                    result = asyncio.run(
                        _handle_ticket_create(
                            self.mock_client,
                            {
                                "summary": "s",
                                "description": "d",
                                "format": bad,
                            },
                        )
                    )
                    self._assert_rejected(result, mock_run_sync)

    def test_ticket_update_rejects_bad_format(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_ticket_update(
                    self.mock_client,
                    {"ticket_id": 1, "comment": "c", "format": "auto"},
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_wiki_create_rejects_bad_format(self):
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_wiki_create(
                    self.mock_client,
                    {
                        "page_name": "P",
                        "content": "c",
                        "format": "auto",
                    },
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_wiki_update_rejects_bad_format(self):
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_wiki_update(
                    self.mock_client,
                    {
                        "page_name": "P",
                        "content": "c",
                        "version": 2,
                        "format": "auto",
                    },
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_batch_create_rejects_bad_format(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_batch_create(
                    self.mock_client,
                    {
                        "tickets": [
                            {"summary": "s", "description": "d"}
                        ],
                        "format": "auto",
                    },
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_batch_update_rejects_bad_format(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_batch_update(
                    self.mock_client,
                    {
                        "updates": [{"ticket_id": 1, "comment": "c"}],
                        "format": "auto",
                    },
                )
            )
            self._assert_rejected(result, mock_run_sync)


class TestTracwikiStoredByteIdentically(unittest.TestCase):
    """format="tracwiki" stores the author's bytes, unchanged.

    The real converter is left in place throughout -- mocking it away
    would test the mock, not the round trip.
    """

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.config = Config(
            trac_url="http://test", username="test", password="test"
        )

    def test_seeded_defect_is_real(self):
        """The payload must actually be mangled by the Markdown path, or
        the six tests below prove nothing (SeededDefectFirst).
        """
        from trac_mcp_server.converters import markdown_to_tracwiki

        self.assertNotEqual(markdown_to_tracwiki(SEEDED), SEEDED)
        self.assertIn("\nreturn ", markdown_to_tracwiki(SEEDED))

    def test_ticket_create_description(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = 42
            asyncio.run(
                _handle_ticket_create(
                    self.mock_client,
                    {
                        "summary": "s",
                        "description": SEEDED,
                        "format": "tracwiki",
                    },
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)

    def test_ticket_update_description(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            asyncio.run(
                _handle_ticket_update(
                    self.mock_client,
                    {
                        "ticket_id": 7,
                        "description": SEEDED,
                        "format": "tracwiki",
                    },
                )
            )
            attrs = mock_run_sync.call_args[0][3]
            self.assertEqual(attrs["description"], SEEDED)

    def test_ticket_update_comment(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            asyncio.run(
                _handle_ticket_update(
                    self.mock_client,
                    {
                        "ticket_id": 7,
                        "comment": SEEDED,
                        "format": "tracwiki",
                    },
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)

    def test_ticket_update_governs_description_and_comment_together(
        self,
    ):
        """One flag, both fields -- they convert at separate call sites,
        so a fix applied to only one leg would pass a single-field test.
        """
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            asyncio.run(
                _handle_ticket_update(
                    self.mock_client,
                    {
                        "ticket_id": 7,
                        "comment": SEEDED,
                        "description": SEEDED,
                        "format": "tracwiki",
                    },
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)
            self.assertEqual(
                mock_run_sync.call_args[0][3]["description"], SEEDED
            )

    def test_ticket_update_reply_to_keeps_body_verbatim(self):
        """The quote block is assembled from raw changelog TracWiki and
        prepended after conversion, so it is already format-agnostic.
        Pinned rather than left to inspection.
        """
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:

            def side_effect(fn, *a, **kw):
                if fn is self.mock_client.get_ticket_changelog:
                    return [
                        (
                            "ts",
                            "alice",
                            "comment",
                            "3",
                            "quoted body",
                            0,
                        )
                    ]
                return True

            mock_run_sync.side_effect = side_effect
            asyncio.run(
                _handle_ticket_update(
                    self.mock_client,
                    {
                        "ticket_id": 7,
                        "comment": SEEDED,
                        "reply_to": 3,
                        "format": "tracwiki",
                    },
                )
            )
            sent = mock_run_sync.call_args[0][2]
            self.assertTrue(sent.endswith(SEEDED))
            self.assertIn("Replying to [comment:3 alice]", sent)

    def test_batch_create_description(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_run_sync:
            mock_run_sync.return_value = 42
            asyncio.run(
                _handle_batch_create(
                    self.mock_client,
                    {
                        "tickets": [
                            {"summary": "s", "description": SEEDED}
                        ],
                        "format": "tracwiki",
                    },
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)

    def test_batch_update_comment(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            asyncio.run(
                _handle_batch_update(
                    self.mock_client,
                    {
                        "updates": [
                            {"ticket_id": 1, "comment": SEEDED}
                        ],
                        "format": "tracwiki",
                    },
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)

    def test_batch_format_applies_to_every_item(self):
        """Call-level parameter, so it governs the whole batch."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_run_sync:
            mock_run_sync.return_value = 42
            asyncio.run(
                _handle_batch_create(
                    self.mock_client,
                    {
                        "tickets": [
                            {"summary": "a", "description": SEEDED},
                            {"summary": "b", "description": SEEDED},
                        ],
                        "format": "tracwiki",
                    },
                )
            )
            self.assertEqual(mock_run_sync.call_count, 2)
            for call in mock_run_sync.call_args_list:
                self.assertEqual(call[0][2], SEEDED)

    def test_wiki_create_content(self):
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            calls = [0]

            def side_effect(*a, **kw):
                calls[0] += 1
                if calls[0] == 1:
                    raise xmlrpc.client.Fault(404, "Page not found")
                return {"name": "P", "version": 1}

            mock_run_sync.side_effect = side_effect
            asyncio.run(
                _handle_wiki_create(
                    self.mock_client,
                    {
                        "page_name": "P",
                        "content": SEEDED,
                        "format": "tracwiki",
                    },
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)

    def test_wiki_update_content(self):
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = {"name": "P", "version": 3}
            asyncio.run(
                _handle_wiki_update(
                    self.mock_client,
                    {
                        "page_name": "P",
                        "content": SEEDED,
                        "version": 2,
                        "format": "tracwiki",
                    },
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)

    def test_wiki_paths_do_not_call_the_converter_at_all(self):
        """Decision: skip the converter, don't configure it to pass
        through. auto_convert would pass source==target through
        untouched, but routing TracWiki into a Markdown converter is the
        habit this ticket removes.
        """
        with (
            patch(
                "trac_mcp_server.mcp.tools.wiki_write.run_sync"
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.tools.wiki_write.auto_convert"
            ) as mock_convert,
        ):
            mock_run_sync.return_value = {"name": "P", "version": 3}
            asyncio.run(
                _handle_wiki_update(
                    self.mock_client,
                    {
                        "page_name": "P",
                        "content": SEEDED,
                        "version": 2,
                        "format": "tracwiki",
                    },
                )
            )
            mock_convert.assert_not_called()

    def test_ticket_paths_do_not_call_the_converter_at_all(self):
        with (
            patch(
                "trac_mcp_server.mcp.tools.ticket_write.run_sync"
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.tools.ticket_write.markdown_to_tracwiki"
            ) as mock_convert,
        ):
            mock_run_sync.return_value = 42
            asyncio.run(
                _handle_ticket_create(
                    self.mock_client,
                    {
                        "summary": "s",
                        "description": SEEDED,
                        "format": "tracwiki",
                    },
                )
            )
            mock_convert.assert_not_called()


class TestMarkdownDefaultUnchanged(unittest.TestCase):
    """Ticket #47 must not regress.

    Its symptom is a *silent* pass-through of unconverted Markdown, so
    these assert on the declaration reaching auto_convert -- not merely
    on the output looking converted. Introducing a parameter and
    branching on it is exactly where detection gets reintroduced as a
    default.
    """

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.config = Config(
            trac_url="http://test", username="test", password="test"
        )

    # A marker-poor Markdown document: a bare table, no heading, bold,
    # fence or link. This is what the heuristic misclassified in #47.
    MARKER_POOR = "| a | b |\n|---|---|\n| 1 | 2 |"

    def _run_wiki_update(self, args):
        with (
            patch(
                "trac_mcp_server.mcp.tools.wiki_write.run_sync"
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.tools.wiki_write.auto_convert"
            ) as mock_convert,
        ):
            mock_run_sync.return_value = {"name": "P", "version": 3}

            async def fake(*a, **kw):
                from trac_mcp_server.converters.common import (
                    ConversionResult,
                )

                return ConversionResult(
                    text="||a||b||",
                    source_format="markdown",
                    target_format="tracwiki",
                    converted=True,
                )

            mock_convert.side_effect = fake
            asyncio.run(
                _handle_wiki_update(
                    self.mock_client,
                    {
                        "page_name": "P",
                        "content": self.MARKER_POOR,
                        "version": 2,
                        **args,
                    },
                )
            )
            mock_convert.assert_called_once()
            return mock_convert.call_args

    def test_wiki_update_omitted_format_declares_markdown(self):
        _, kwargs = self._run_wiki_update({})
        self.assertEqual(kwargs.get("source_format"), "markdown")

    def test_wiki_update_explicit_markdown_declares_markdown(self):
        _, kwargs = self._run_wiki_update({"format": "markdown"})
        self.assertEqual(kwargs.get("source_format"), "markdown")

    def test_wiki_create_omitted_format_declares_markdown(self):
        with (
            patch(
                "trac_mcp_server.mcp.tools.wiki_write.run_sync"
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.tools.wiki_write.auto_convert"
            ) as mock_convert,
        ):
            calls = [0]

            def side_effect(*a, **kw):
                calls[0] += 1
                if calls[0] == 1:
                    raise xmlrpc.client.Fault(404, "Page not found")
                return {"name": "P", "version": 1}

            mock_run_sync.side_effect = side_effect

            async def fake(*a, **kw):
                from trac_mcp_server.converters.common import (
                    ConversionResult,
                )

                return ConversionResult(
                    text="||a||b||",
                    source_format="markdown",
                    target_format="tracwiki",
                    converted=True,
                )

            mock_convert.side_effect = fake
            asyncio.run(
                _handle_wiki_create(
                    self.mock_client,
                    {"page_name": "P", "content": self.MARKER_POOR},
                )
            )
            _, kwargs = mock_convert.call_args
            self.assertEqual(kwargs.get("source_format"), "markdown")

    def test_ticket_create_omitted_format_still_converts(self):
        """Default behaviour is byte-identical to before the parameter."""
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = 42
            asyncio.run(
                _handle_ticket_create(
                    self.mock_client,
                    {"summary": "s", "description": "**bold**"},
                )
            )
            self.assertEqual(
                mock_run_sync.call_args[0][2], "'''bold'''"
            )

    def test_ticket_create_explicit_markdown_still_converts(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = 42
            asyncio.run(
                _handle_ticket_create(
                    self.mock_client,
                    {
                        "summary": "s",
                        "description": "**bold**",
                        "format": "markdown",
                    },
                )
            )
            self.assertEqual(
                mock_run_sync.call_args[0][2], "'''bold'''"
            )

    def test_batch_create_omitted_format_still_converts(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_run_sync:
            mock_run_sync.return_value = 42
            asyncio.run(
                _handle_batch_create(
                    self.mock_client,
                    {
                        "tickets": [
                            {"summary": "s", "description": "**bold**"}
                        ]
                    },
                )
            )
            self.assertEqual(
                mock_run_sync.call_args[0][2], "'''bold'''"
            )


if __name__ == "__main__":
    unittest.main()
