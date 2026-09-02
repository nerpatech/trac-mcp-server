"""Tests for the TracWiki-only inline surface (tickets #62, #69).

Ticket #62 added a ``format`` parameter to the six write tools, defaulting
to ``markdown``. That default aimed the *destructive* failure at
hand-authored TracWiki -- indentation inside a ``{{{ }}}`` processor block
is silently stripped, with an empty warning list and a plausible render --
and the *safe* one at Markdown, which merely renders as literal ``#`` and
``**``. Ticket #69 removed the alternative rather than moving the default:
the inline write and read tools speak TracWiki only, so there is nothing
left to omit.

Three properties are pinned here:

- **Undeclared is verbatim.** A write with no ``format`` argument, and a
  read with no ``raw`` argument, move stored bytes unchanged. This is the
  seeded row: it fails on the pre-#69 code, which is what makes it worth
  having (SeededDefectFirst).
- **A removed parameter breaks loudly.** ``format="markdown"`` and
  ``raw=false`` are a ``validation_error`` naming ``trac-convert``, not a
  silently ignored argument. ``format="tracwiki"`` and ``raw=true`` are
  accepted as no-ops, since they agree with the new behaviour.
- **The converter is not merely bypassed, it is unreachable** from these
  paths. Patched-out converter symbols must never be called.

Every assertion is on the *stored bytes* or the *returned body*. None is
on the render and none is on the warning list: the loss produced zero
warnings and a plausible-looking render on every occasion it happened, so
a test written against either signal passes on the broken case.

The converters themselves are untouched by #69 and keep their own suites
-- ``test_converter.py``, ``test_cli_convert*.py`` -- because
``convert_preview``, the ``wiki_file_*`` tools and the standalone
``trac-convert`` binary still use them.
"""

import asyncio
import unittest
import xmlrpc.client
from unittest.mock import MagicMock, patch

from trac_mcp_server.config import Config
from trac_mcp_server.mcp.tools import (
    MILESTONE_TOOLS,
    TICKET_TOOLS,
    WIKI_FILE_TOOLS,
    WIKI_TOOLS,
)
from trac_mcp_server.mcp.tools.milestone import (
    _handle_get as _handle_milestone_get,
)
from trac_mcp_server.mcp.tools.ticket_batch import (
    _handle_batch_create,
    _handle_batch_update,
)
from trac_mcp_server.mcp.tools.ticket_read import (
    _handle_changelog,
)
from trac_mcp_server.mcp.tools.ticket_read import (
    _handle_get as _handle_ticket_get,
)
from trac_mcp_server.mcp.tools.ticket_write import (
    _handle_create as _handle_ticket_create,
)
from trac_mcp_server.mcp.tools.ticket_write import (
    _handle_update as _handle_ticket_update,
)
from trac_mcp_server.mcp.tools.wiki_read import (
    _handle_get as _handle_wiki_get,
)
from trac_mcp_server.mcp.tools.wiki_read import (
    _handle_search as _handle_wiki_search,
)
from trac_mcp_server.mcp.tools.wiki_write import (
    _handle_create as _handle_wiki_create,
)
from trac_mcp_server.mcp.tools.wiki_write import (
    _handle_update as _handle_wiki_update,
)

# The seeded defect. Fed to the Markdown converter this loses the
# four-space indent on the `return` line -- measured, not assumed.
# Deliberately the processor-block form and not a Markdown fence: #62
# comment 6 recorded that the fence survives the Markdown path, which
# would leave the seed unable to fail.
SEEDED = '{{{#!python\ndef f(x):\n    return {"a": 1}\n}}}'

# Round-trip bait for the read leg. The Markdown converter escapes the
# CamelCase word to `!WiFi` and rewrites the table, so a no-op
# read-edit-write silently rewrote the stored page (ticket #67).
READ_BAIT = "WiFi and LoRa\n\n||=a=||=b=||\n||1||2||\n"

# The same bait on one line, for the two paths that reformat a body
# before returning it -- changelog indenting and search snippets -- and
# would otherwise be compared against text the formatter has already
# reflowed. Measured: the converter turns this into "**bold** and !WiFi",
# so a body that comes back unchanged really did skip the converter.
READ_BAIT_LINE = "'''bold''' and !WiFi"

# Tools that used to carry `format`.
WRITE_TOOLS = (
    "ticket_create",
    "ticket_update",
    "ticket_batch_create",
    "ticket_batch_update",
    "wiki_create",
    "wiki_update",
)

# Tools that used to carry `raw`.
READ_TOOLS = (
    "ticket_get",
    "ticket_changelog",
    "wiki_get",
    "wiki_search",
    "milestone_get",
)


def _tool(name):
    for tool in (
        list(TICKET_TOOLS)
        + list(WIKI_TOOLS)
        + list(WIKI_FILE_TOOLS)
        + list(MILESTONE_TOOLS)
    ):
        if tool.name == name:
            return tool
    raise AssertionError(f"tool {name} not found")


def _client():
    client = MagicMock()
    client.config = Config(
        trac_url="http://test", username="test", password="test"
    )
    return client


def _ticket_row(description):
    """A get_ticket() response carrying `description`."""
    return [
        7,
        "2026-01-01",
        "2026-01-02",
        {"summary": "s", "description": description, "_ts": "1"},
    ]


class TestSeedIsReal(unittest.TestCase):
    """The payload must actually be destroyed by the Markdown path.

    Without this the verbatim tests below prove nothing: a seed that
    survives conversion cannot distinguish a fixed server from a broken
    one (SeededDefectFirst).
    """

    def test_write_seed_loses_its_indentation(self):
        from trac_mcp_server.converters import markdown_to_tracwiki

        mangled = markdown_to_tracwiki(SEEDED)
        self.assertNotEqual(mangled, SEEDED)
        self.assertIn("\nreturn ", mangled)

    def test_read_seed_is_rewritten(self):
        from trac_mcp_server.converters import tracwiki_to_markdown

        self.assertNotEqual(
            tracwiki_to_markdown(READ_BAIT).text, READ_BAIT
        )
        self.assertNotEqual(
            tracwiki_to_markdown(READ_BAIT_LINE).text, READ_BAIT_LINE
        )


class TestSchemasCarryNoFormatOrRaw(unittest.TestCase):
    """The parameters are gone from the declared surface."""

    def test_write_tools_do_not_declare_format(self):
        for name in WRITE_TOOLS:
            with self.subTest(tool=name):
                props = _tool(name).inputSchema["properties"]
                self.assertNotIn("format", props)

    def test_read_tools_do_not_declare_raw(self):
        for name in READ_TOOLS:
            with self.subTest(tool=name):
                props = _tool(name).inputSchema["properties"]
                self.assertNotIn("raw", props)

    def test_batch_item_schemas_do_not_declare_format(self):
        for name, key in (
            ("ticket_batch_create", "tickets"),
            ("ticket_batch_update", "updates"),
        ):
            with self.subTest(tool=name):
                props = _tool(name).inputSchema["properties"]
                item_props = props[key]["items"]["properties"]
                self.assertNotIn("format", item_props)

    def test_wiki_file_tools_keep_their_format_parameter(self):
        """Deliberately out of scope for #69: these have a filename to
        go on, which the inline tools do not. Pinned so the exclusion is
        a decision rather than an oversight.
        """
        props = _tool("wiki_file_push").inputSchema["properties"]
        self.assertIn("format", props)
        self.assertIn("auto", props["format"]["enum"])


class TestUndeclaredWriteIsVerbatim(unittest.TestCase):
    """The seeded row. No `format` argument, bytes stored unchanged.

    The real converter is left importable throughout -- mocking it away
    would test the mock, not the write path.
    """

    def setUp(self):
        self.mock_client = _client()

    def test_ticket_create_description(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = 42
            asyncio.run(
                _handle_ticket_create(
                    self.mock_client,
                    {"summary": "s", "description": SEEDED},
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
                    {"ticket_id": 7, "description": SEEDED},
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
                    {"ticket_id": 7, "comment": SEEDED},
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)

    def test_ticket_update_covers_description_and_comment_together(
        self,
    ):
        """They convert at separate call sites, so a fix applied to only
        one leg would still pass a single-field test.
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
                    },
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)
            self.assertEqual(
                mock_run_sync.call_args[0][3]["description"], SEEDED
            )

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
                            {"summary": "a", "description": SEEDED},
                            {"summary": "b", "description": SEEDED},
                        ]
                    },
                )
            )
            self.assertEqual(mock_run_sync.call_count, 2)
            for call in mock_run_sync.call_args_list:
                self.assertEqual(call[0][2], SEEDED)

    def test_batch_update_comment(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            asyncio.run(
                _handle_batch_update(
                    self.mock_client,
                    {"updates": [{"ticket_id": 1, "comment": SEEDED}]},
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)

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
                    {"page_name": "P", "content": SEEDED},
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
                    {"page_name": "P", "content": SEEDED, "version": 2},
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)

    def test_marker_poor_markdown_is_stored_as_written(self):
        """Ticket #47's row, inverted by #69 and recorded rather than
        dropped. Its symptom -- marker-poor Markdown stored unconverted
        -- is now the *defined* behaviour of an undeclared write, for
        every input rather than just the ones a heuristic misjudged.
        The heuristic itself still matters and is still pinned, in
        test_converter.py, where it still runs.
        """
        marker_poor = "| a | b |\n|---|---|\n| 1 | 2 |"
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = {"name": "P", "version": 3}
            asyncio.run(
                _handle_wiki_update(
                    self.mock_client,
                    {
                        "page_name": "P",
                        "content": marker_poor,
                        "version": 2,
                    },
                )
            )
            self.assertEqual(mock_run_sync.call_args[0][2], marker_poor)


class TestCarriageReturnsReachTheClientUnchanged(unittest.TestCase):
    """The CRLF row inherited from auto_pm:#90, via #69 comment 6.

    ``wiki_file_push`` normalises CRLF to LF because it reads a file.
    The inline tools take a string and have no such step, and their
    behaviour was unmeasured. The worry was that #69, by storing bytes
    verbatim, would let a pasted CRLF body persist CRLF into an all-LF
    store -- invisibly, since Trac renders the two identically and
    auto_pm:#90 measured that byte size, not the diff, was the only
    signal that surfaced it.

    **Measured end to end on /trac_test after deploying #69: it does
    not.** A CR cannot reach the store through any of these tools,
    because ``xmlrpc.client.dumps`` emits a raw CR rather than escaping
    it as ``&#13;``, and XML 1.0 line-end normalisation collapses it to
    LF on the parser side -- before Trac ever sees the value. Pushing
    ``"line one\r\nline two\r\n"`` through ``wiki_update`` stored it
    with **zero** CRs.

    So these tests pin the handler boundary only: the inline tools add
    no normalisation of their own, which is what makes them verbatim.
    The store stays LF-only for a reason one layer down, and that reason
    is recorded here so a future reader does not re-derive it -- or,
    worse, "fix" a normalisation step into these handlers to solve a
    problem that the transport already rules out.
    """

    def setUp(self):
        self.mock_client = _client()

    CRLF = "line one\r\nline two\r\n"

    def test_wiki_update_preserves_cr(self):
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = {"name": "P", "version": 3}
            asyncio.run(
                _handle_wiki_update(
                    self.mock_client,
                    {
                        "page_name": "P",
                        "content": self.CRLF,
                        "version": 2,
                    },
                )
            )
            sent = mock_run_sync.call_args[0][2]
            self.assertEqual(sent, self.CRLF)
            self.assertEqual(sent.count("\r"), 2)

    def test_ticket_update_comment_preserves_cr(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            asyncio.run(
                _handle_ticket_update(
                    self.mock_client,
                    {"ticket_id": 7, "comment": self.CRLF},
                )
            )
            self.assertEqual(
                mock_run_sync.call_args[0][2].count("\r"), 2
            )

    def test_batch_update_comment_preserves_cr(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_run_sync:
            mock_run_sync.return_value = True
            asyncio.run(
                _handle_batch_update(
                    self.mock_client,
                    {
                        "updates": [
                            {"ticket_id": 1, "comment": self.CRLF}
                        ]
                    },
                )
            )
            self.assertEqual(
                mock_run_sync.call_args[0][2].count("\r"), 2
            )


class TestUndeclaredReadIsVerbatim(unittest.TestCase):
    """No `raw` argument, stored bytes returned unchanged.

    The read half of the seeded row. Before #69 a no-op read-edit-write
    rewrote the stored page while the visible text stayed identical and
    both calls reported no warnings (ticket #67).
    """

    def setUp(self):
        self.mock_client = _client()

    def test_ticket_get_description(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = [_ticket_row(READ_BAIT), []]
            result = asyncio.run(
                _handle_ticket_get(self.mock_client, {"ticket_id": 7})
            )
            self.assertIn(READ_BAIT, result.content[0].text)

    def test_ticket_get_comment_bodies(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = [
                _ticket_row("d"),
                [("ts", "alice", "comment", "1", READ_BAIT, 0)],
            ]
            result = asyncio.run(
                _handle_ticket_get(self.mock_client, {"ticket_id": 7})
            )
            self.assertIn(READ_BAIT.strip(), result.content[0].text)

    def test_ticket_changelog_comment_bodies(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                ("ts", "alice", "comment", "1", READ_BAIT_LINE, 0)
            ]
            result = asyncio.run(
                _handle_changelog(self.mock_client, {"ticket_id": 7})
            )
            self.assertIn(READ_BAIT_LINE, result.content[0].text)

    def test_wiki_get_content(self):
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync_limited"
        ) as mock_run_sync:

            async def side_effect(fn, *a, **kw):
                if fn is self.mock_client.get_wiki_page:
                    return READ_BAIT
                return {"version": 3, "author": "a", "lastModified": ""}

            mock_run_sync.side_effect = side_effect
            result = asyncio.run(
                _handle_wiki_get(self.mock_client, {"page_name": "P"})
            )
            self.assertIn(READ_BAIT, result.content[0].text)

    def test_wiki_search_snippets(self):
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = [
                {"name": "P", "snippet": READ_BAIT_LINE}
            ]
            result = asyncio.run(
                _handle_wiki_search(self.mock_client, {"query": "q"})
            )
            self.assertIn(READ_BAIT_LINE, result.content[0].text)

    def test_milestone_get_description(self):
        with patch(
            "trac_mcp_server.mcp.tools.milestone.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = {
                "name": "m1",
                "due": 0,
                "completed": 0,
                "description": READ_BAIT,
            }
            result = asyncio.run(
                _handle_milestone_get(self.mock_client, {"name": "m1"})
            )
            self.assertIn(READ_BAIT, result.content[0].text)


class TestRemovedParametersBreakLoudly(unittest.TestCase):
    """A stale caller is told, at the call site, exactly once.

    This is the verifiability argument that chose removal over a
    re-defaulted parameter: under a moved default an undeclared caller
    still gets *something*, and what that something silently does is the
    whole defect family. A removed parameter has no silent arm.

    Values that agree with the new behaviour -- `format="tracwiki"`,
    `raw=true` -- are accepted as no-ops, so a caller already following
    the store rule needs no change.
    """

    def setUp(self):
        self.mock_client = _client()

    def _assert_rejected(self, result, mock_run_sync):
        self.assertTrue(result.isError)
        text = result.content[0].text
        self.assertIn("validation_error", text)
        self.assertIn("trac-convert", text)
        mock_run_sync.assert_not_called()

    def test_ticket_create_rejects_markdown(self):
        for bad in ("markdown", "auto", "Markdown", ""):
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

    def test_ticket_update_rejects_markdown(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_write.run_sync"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_ticket_update(
                    self.mock_client,
                    {
                        "ticket_id": 1,
                        "comment": "c",
                        "format": "markdown",
                    },
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_wiki_create_rejects_markdown(self):
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_wiki_create(
                    self.mock_client,
                    {
                        "page_name": "P",
                        "content": "c",
                        "format": "markdown",
                    },
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_wiki_update_rejects_markdown(self):
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
                        "format": "markdown",
                    },
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_batch_create_rejects_markdown(self):
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
                        "format": "markdown",
                    },
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_batch_update_rejects_markdown(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_batch_update(
                    self.mock_client,
                    {
                        "updates": [{"ticket_id": 1, "comment": "c"}],
                        "format": "markdown",
                    },
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_ticket_get_rejects_raw_false(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_ticket_get(
                    self.mock_client, {"ticket_id": 7, "raw": False}
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_ticket_changelog_rejects_raw_false(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_changelog(
                    self.mock_client, {"ticket_id": 7, "raw": False}
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_wiki_get_rejects_raw_false(self):
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync_limited"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_wiki_get(
                    self.mock_client, {"page_name": "P", "raw": False}
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_wiki_search_rejects_raw_false(self):
        with patch(
            "trac_mcp_server.mcp.tools.wiki_read.run_sync"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_wiki_search(
                    self.mock_client, {"query": "q", "raw": False}
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_milestone_get_rejects_raw_false(self):
        with patch(
            "trac_mcp_server.mcp.tools.milestone.run_sync"
        ) as mock_run_sync:
            result = asyncio.run(
                _handle_milestone_get(
                    self.mock_client, {"name": "m1", "raw": False}
                )
            )
            self._assert_rejected(result, mock_run_sync)

    def test_format_tracwiki_is_accepted_as_a_no_op(self):
        """Every caller already following the store rule keeps working."""
        with patch(
            "trac_mcp_server.mcp.tools.wiki_write.run_sync"
        ) as mock_run_sync:
            mock_run_sync.return_value = {"name": "P", "version": 3}
            result = asyncio.run(
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
            self.assertFalse(result.isError)
            self.assertEqual(mock_run_sync.call_args[0][2], SEEDED)

    def test_raw_true_is_accepted_as_a_no_op(self):
        with patch(
            "trac_mcp_server.mcp.tools.ticket_read.run_sync"
        ) as mock_run_sync:
            mock_run_sync.side_effect = [_ticket_row(READ_BAIT), []]
            result = asyncio.run(
                _handle_ticket_get(
                    self.mock_client, {"ticket_id": 7, "raw": True}
                )
            )
            self.assertFalse(result.isError)
            self.assertIn(READ_BAIT, result.content[0].text)


class TestConverterIsUnreachable(unittest.TestCase):
    """Not bypassed by configuration -- absent from the path.

    ``auto_convert`` would pass source==target through untouched, so a
    server that merely declared TracWiki to the converter would satisfy
    a byte-equality test. Routing content into a converter at all is the
    habit #69 removes, so the call itself is what is asserted on.
    """

    def setUp(self):
        self.mock_client = _client()

    def test_wiki_write_never_calls_auto_convert(self):
        with (
            patch(
                "trac_mcp_server.mcp.tools.wiki_write.run_sync"
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.tools.wiki_write.auto_convert",
                create=True,
            ) as mock_convert,
        ):
            mock_run_sync.return_value = {"name": "P", "version": 3}
            asyncio.run(
                _handle_wiki_update(
                    self.mock_client,
                    {"page_name": "P", "content": SEEDED, "version": 2},
                )
            )
            mock_convert.assert_not_called()

    def test_ticket_write_never_calls_the_converter(self):
        with (
            patch(
                "trac_mcp_server.mcp.tools.ticket_write.run_sync"
            ) as mock_run_sync,
            patch(
                "trac_mcp_server.mcp.tools.ticket_write.markdown_to_tracwiki",
                create=True,
            ) as mock_convert,
        ):
            mock_run_sync.return_value = 42
            asyncio.run(
                _handle_ticket_create(
                    self.mock_client,
                    {"summary": "s", "description": SEEDED},
                )
            )
            mock_convert.assert_not_called()

    def test_inline_write_modules_do_not_import_a_converter(self):
        """The import is the thing that makes reintroducing a conversion
        arm a one-line edit. Gone, so it is not.
        """
        import trac_mcp_server.mcp.tools.ticket_batch as batch
        import trac_mcp_server.mcp.tools.ticket_write as tw
        import trac_mcp_server.mcp.tools.wiki_write as ww

        for module in (tw, ww, batch):
            with self.subTest(module=module.__name__):
                self.assertFalse(
                    hasattr(module, "markdown_to_tracwiki")
                    or hasattr(module, "auto_convert"),
                    f"{module.__name__} still imports a converter",
                )

    def test_inline_read_modules_do_not_import_a_converter(self):
        import trac_mcp_server.mcp.tools.milestone as ms
        import trac_mcp_server.mcp.tools.ticket_read as tr
        import trac_mcp_server.mcp.tools.wiki_read as wr

        for module in (tr, wr, ms):
            with self.subTest(module=module.__name__):
                self.assertFalse(
                    hasattr(module, "tracwiki_to_markdown"),
                    f"{module.__name__} still imports a converter",
                )


if __name__ == "__main__":
    unittest.main()
